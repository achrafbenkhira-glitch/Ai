import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("DATABASE_PATH", BASE_DIR / "data" / "genius.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="PROJECT GENIUS — MY PERSONAL AI", version="0.1.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT 'New conversation',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK(role IN ('user','assistant')),
            content TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        """)


@app.on_event("startup")
def startup() -> None:
    init_db()


class NewConversation(BaseModel):
    title: str = Field(default="New conversation", max_length=120)


class ChatRequest(BaseModel):
    conversation_id: str
    message: str = Field(min_length=1, max_length=30000)


def get_provider_config():
    api_key = os.getenv("AI_API_KEY", "").strip()
    base_url = os.getenv("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("AI_MODEL", "gpt-4.1-mini").strip()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="AI_API_KEY is not configured. Copy .env.example to .env and add your provider API key."
        )
    return api_key, base_url, model


def get_messages(conversation_id: str):
    with db() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id",
            (conversation_id,)
        ).fetchall()
    return [{"role": row["role"], "content": row["content"]} for row in rows]


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "app" / "static" / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "phase": 1, "model_configured": bool(os.getenv("AI_API_KEY"))}


@app.get("/api/conversations")
def list_conversations():
    with db() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/conversations")
def create_conversation(payload: NewConversation):
    now = int(time.time())
    cid = str(uuid.uuid4())
    title = payload.title.strip() or "New conversation"
    with db() as conn:
        conn.execute(
            "INSERT INTO conversations(id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (cid, title, now, now)
        )
    return {"id": cid, "title": title, "created_at": now, "updated_at": now}


@app.get("/api/conversations/{conversation_id}/messages")
def conversation_messages(conversation_id: str):
    with db() as conn:
        exists = conn.execute("SELECT 1 FROM conversations WHERE id=?", (conversation_id,)).fetchone()
    if not exists:
        raise HTTPException(status_code=404, detail="Conversation not found")
    with db() as conn:
        rows = conn.execute(
            "SELECT id, role, content, created_at FROM messages WHERE conversation_id=? ORDER BY id",
            (conversation_id,)
        ).fetchall()
    return [dict(row) for row in rows]


async def upstream_stream(api_key: str, base_url: str, model: str, messages: list[dict]):
    endpoint = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "stream": True}
    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=20.0)) as client:
        async with client.stream("POST", endpoint, headers=headers, json=payload) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", errors="replace")
                raise RuntimeError(f"Provider returned HTTP {response.status_code}: {body[:1200]}")
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                    delta = event.get("choices", [{}])[0].get("delta", {}).get("content")
                    if isinstance(delta, str) and delta:
                        yield delta
                except (json.JSONDecodeError, IndexError, KeyError, TypeError):
                    continue


@app.post("/api/chat")
async def chat(payload: ChatRequest, request: Request):
    api_key, base_url, model = get_provider_config()
    user_text = payload.message.strip()
    if not user_text:
        raise HTTPException(status_code=422, detail="Message cannot be empty")

    with db() as conn:
        conversation = conn.execute(
            "SELECT id FROM conversations WHERE id=?", (payload.conversation_id,)
        ).fetchone()
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        now = int(time.time())
        conn.execute(
            "INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,?,?,?)",
            (payload.conversation_id, "user", user_text, now)
        )
        count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id=?",
            (payload.conversation_id,)
        ).fetchone()[0]
        if count == 1:
            title = user_text[:60] + ("…" if len(user_text) > 60 else "")
            conn.execute("UPDATE conversations SET title=?,updated_at=? WHERE id=?",
                         (title, now, payload.conversation_id))
        else:
            conn.execute("UPDATE conversations SET updated_at=? WHERE id=?",
                         (now, payload.conversation_id))

    history = get_messages(payload.conversation_id)
    system_prompt = (
        "You are PROJECT GENIUS, a practical and honest personal AI assistant. "
        "Communicate in the user's language and style. Never claim to have performed "
        "actions, searches, tests, or file changes that you did not actually perform. "
        "Phase 1 is chat only: you have no tools to execute external actions. "
        "Respect privacy and explain uncertainty."
    )
    request_messages = [{"role": "system", "content": system_prompt}] + history

    async def event_stream():
        collected = []
        try:
            async for chunk in upstream_stream(api_key, base_url, model, request_messages):
                if await request.is_disconnected():
                    break
                collected.append(chunk)
                yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
            answer = "".join(collected).strip()
            if answer:
                with db() as conn:
                    conn.execute(
                        "INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,?,?,?)",
                        (payload.conversation_id, "assistant", answer, int(time.time()))
                    )
                    conn.execute("UPDATE conversations SET updated_at=? WHERE id=?",
                                 (int(time.time()), payload.conversation_id))
            yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            # Do not expose credentials. Provider response text may contain diagnostics but no headers.
            safe_message = str(exc).replace(api_key, "[REDACTED]")
            yield f"data: {json.dumps({'error': safe_message}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str):
    with db() as conn:
        cursor = conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Conversation not found")
    return {"deleted": True}
