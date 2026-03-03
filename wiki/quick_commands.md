# Quick Commands Reference

Copy-paste commands for common Auto Transcription development tasks.

> **Tip:** A project-root [Makefile](../Makefile) wraps most of these -- run `make help` to see all targets.

---

## Infrastructure

```bash
cd backend && docker compose up -d          # Start PostgreSQL + Ollama
```

```bash
cd backend && docker compose down           # Stop all Docker services
```

```bash
cd backend && docker compose logs -f postgres   # Tail PostgreSQL logs
```

```bash
cd backend && docker compose logs -f ollama     # Tail Ollama logs
```

---

## Backend

```bash
cd backend && pip install -e ".[dev]"       # Install backend deps (editable)
```

```bash
cd backend && uvicorn app.main:app --reload # Start backend (dev with hot-reload)
```

```bash
cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000   # Start backend (prod-like)
```

```bash
PYTHONPATH=backend pytest backend/tests/unit -v         # Run unit tests
```

```bash
PYTHONPATH=backend pytest backend/tests/integration -v  # Run integration tests
```

```bash
cd backend && ruff check app/              # Lint
```

```bash
cd backend && ruff format app/             # Auto-format
```

```bash
cd backend && pyright                      # Type check
```

---

## Frontend

```bash
cd frontend && npm install                 # Install frontend deps
```

```bash
cd frontend && npm run dev                 # Start frontend (dev at localhost:3000)
```

```bash
cd frontend && npm run build               # Production build
```

```bash
cd frontend && npm run lint                # Lint
```

---

## Ollama Models

```bash
ollama pull gemma2:9b                      # Pull main model
```

```bash
ollama pull gemma2:2b                      # Pull lighter model
```

```bash
ollama list                                # List installed models
```

```bash
curl http://localhost:11434/api/tags       # Check Ollama via API
```

---

## Environment

```bash
export AT_ENV=dev                          # Set environment to dev
```

```bash
export AT_ENV=test                         # Set environment to test
```

```bash
cat backend/.env                           # Check current env vars
```

---

## Docker (Production)

```bash
docker build -t autotranscription-backend backend/     # Build backend image
```

```bash
docker run -p 8000:8000 autotranscription-backend      # Run backend container
```

---

## Useful URLs (Local Dev)

| Service | URL |
|---------|-----|
| Backend API | <http://localhost:8000> |
| Swagger docs | <http://localhost:8000/docs> |
| ReDoc | <http://localhost:8000/redoc> |
| Frontend | <http://localhost:3000> |
| Ollama | <http://localhost:11434> |
