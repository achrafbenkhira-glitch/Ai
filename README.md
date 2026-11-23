# PROJECT GENIUS — MY PERSONAL AI
Phase 1 starter: secure-backend-connected chat, SQLite conversation history, streaming replies, responsive UI.

## Current scope
Implemented in this Phase 1 scaffold:
- Responsive desktop/mobile chat UI with dark/light toggle
- Conversation creation and history
- SQLite persistence
- Real OpenAI-compatible Chat Completions integration (streaming)
- API key kept on the server in `.env`
- Basic health endpoint and provider error handling

Not implemented yet: agents, long-term memory, project builder, file tools, external actions, approvals, voice, web research. These belong to later phases. Phase 1 does not execute external actions.

## Requirements
Python 3.11+ recommended.

## Run locally
```bash
python -m venv .venv
# Windows:
.venv\\Scripts\\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```
Edit `.env` and set `AI_API_KEY` to your own provider key. Keep it private; do not commit `.env`.

Start:
```bash
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000

## Provider configuration
`AI_BASE_URL` must be an OpenAI-compatible API base ending in `/v1` (or equivalent provider base).
`AI_MODEL` must be a model name available to your account. The default is a sample; verify model availability with your provider.

## Docker
```bash
cp .env.example .env
# Set AI_API_KEY in .env
docker compose up --build
```

## Tests / checks
Run:
```bash
python -m unittest discover -s tests -v
```
The tests cover database initialization and persistence logic. They do not verify a live provider call; that requires a valid API key and internet access.

## Security notes
- Never expose the API key in frontend JavaScript.
- Use HTTPS and add authentication before exposing this service beyond localhost.
- Phase 1 has no shell, filesystem, email, publishing, purchase, or deployment tools.
- This starter is not yet production-ready for public deployment. Add user authentication, CSRF protections as applicable, rate limiting, stronger logging/redaction, and security review in later iterations.
