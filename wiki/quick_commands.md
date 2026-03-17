# Quick Commands Reference

Copy-paste commands for common development tasks.

> **Tip:** The root [Makefile](../Makefile) wraps most of these — run `make help` to see all targets.

---

## First-Time Setup

```bash
make setup                                # Full setup: venv + deps + infra
```

Or step by step:

```bash
make venv                                 # Create Python virtual environment
make install                              # Install backend + frontend deps
make infra-up                             # Start PostgreSQL + Ollama containers
cp backend/.env.example backend/.env      # Create .env from template
cp frontend/.env.example frontend/.env.local  # Create frontend .env
# Edit backend/.env with your Azure DI credentials
```

---

## Development

```bash
make dev                                  # Start backend + frontend (Ctrl+C stops both)
make backend                              # Start backend only (port 8000)
make frontend                             # Start frontend only (port 3000)
```

---

## Pipeline Mode

```bash
# Switch to Azure DI (default — fast, no local ML)
export AT_PIPELINE__MODE=azure_di

# Switch to Marker + Docling (fully offline)
export AT_PIPELINE__MODE=marker_docling
```

Or set in `backend/.env`:
```
AT_PIPELINE__MODE=azure_di
```

---

## Processing Documents

```bash
# Health check
curl http://localhost:8000/api/documents/health

# Upload and process a PDF
curl -X POST http://localhost:8000/api/documents/process-file \
  -F "file=@path/to/document.pdf"

# Check results (replace DOC_ID)
curl http://localhost:8000/api/documents/DOC_ID

# List all documents
curl http://localhost:8000/api/documents/
```

---

## Testing

```bash
make test                                 # Run unit tests
make test-all                             # Run unit + integration tests
make test-cov                             # Run with coverage report
```

---

## Code Quality

```bash
make lint                                 # Lint backend (ruff)
make lint-fix                             # Auto-fix lint issues
make format                               # Auto-format backend
make format-check                         # Check formatting (no changes)
make lint-frontend                        # Lint frontend (ESLint)
make check-all                            # Run ALL checks (lint + format + types + tests)
```

---

## Infrastructure

```bash
make infra-up                             # Start PostgreSQL + Ollama
make infra-down                           # Stop containers
make infra-status                         # Show container status
make infra-logs                           # Tail all container logs
```

---

## Database

```bash
make db-shell                             # Open psql shell
make db-logs                              # Tail PostgreSQL logs
make db-reset                             # Drop and recreate database (destructive!)
```

---

## Ollama (marker_docling mode only)

```bash
make ollama-pull                          # Pull required models (gemma2:9b + 2b)
make ollama-list                          # List installed models
make ollama-logs                          # Tail Ollama logs
```

---

## Docker (Production)

```bash
make docker-build                         # Build backend Docker image
make docker-up                            # Start full stack in Docker
make docker-down                          # Stop full stack
make docker-logs                          # Tail backend container logs
```

---

## Troubleshooting

```bash
make kill                                 # Kill dev servers + clear stale lock files
make clean                                # Remove caches and build artifacts
```

If `make dev` fails with "Unable to acquire lock", run `make kill` first — it removes the stale Next.js lock file even when no process is running.

---

## Cleanup

```bash
make clean                                # Remove caches and build artifacts
make deep-clean                           # Also remove venv + node_modules
make reset                                # Full reset (deps + infra + database)
```

---

## Environment

```bash
export AT_ENV=dev                         # Set environment (dev/staging/prod/test)
cat backend/.env                          # Check current env vars
```

---

## Useful URLs (Local Dev)

| Service | URL |
|---------|-----|
| Backend API | <http://localhost:8000> |
| Health check | <http://localhost:8000/api/documents/health> |
| Swagger docs | <http://localhost:8000/docs> |
| ReDoc | <http://localhost:8000/redoc> |
| Frontend | <http://localhost:3000> |
| Ollama | <http://localhost:11434> |
