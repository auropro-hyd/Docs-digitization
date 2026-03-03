# Local Development Setup

Step-by-step guide to running the Auto Transcription application locally.

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| **Python** | 3.13+ | Required for the FastAPI backend |
| **Node.js** | 20+ | Required for the Next.js frontend |
| **Docker** & Docker Compose | Latest | Runs PostgreSQL and Ollama containers |
| **Ollama** | Latest | Optional if using Docker for model serving |

## 1. Clone the Repository

```bash
git clone <repo-url>
cd Auto_Transcription
```

## 2. Start Infrastructure Services

The backend includes a Docker Compose file that starts PostgreSQL and Ollama:

```bash
cd backend
docker compose up -d
```

This starts:
- **PostgreSQL 17** on port `5432` (user: `postgres`, password: `postgres`, database: `autotranscription`)
- **Ollama** on port `11434` with 8 GB memory limit

Verify both services are healthy:

```bash
docker compose ps
```

## 3. Pull Ollama Models

If using Docker Ollama:

```bash
docker compose exec ollama ollama pull gemma2:9b
```

If using a local Ollama installation:

```bash
ollama pull gemma2:9b
```

> `gemma2:9b` is the default model for both Marker OCR LLM features and the main LLM provider. The model name is configurable in settings — see [Settings](../backend/configuration/settings.md).

## 4. Install Backend Dependencies

```bash
cd backend
pip install -e ".[dev]"
```

This installs the backend package in editable mode along with development dependencies (ruff, pytest, etc.).

## 5. Configure Environment Variables

Create a `.env` file in the `backend/` directory:

```bash
# backend/.env
AT_ENV=dev

# Azure Document Intelligence (required for dual-engine OCR)
AZURE_DI_ENDPOINT=https://<your-resource>.cognitiveservices.azure.com
AZURE_DI_API_KEY=<your-api-key>
```

**Getting Azure DI credentials:**
1. Go to [Azure AI Foundry](https://ai.azure.com/) or the Azure Portal
2. Create or select a Document Intelligence resource
3. Copy the **Endpoint** and **Key** from the resource's "Keys and Endpoint" page

Create a `.env.local` file in the `frontend/` directory:

```bash
# frontend/.env.local
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_WS_URL=ws://localhost:8000
```

## 6. Start the Backend

```bash
cd backend
uvicorn app.main:app --reload
```

The API server starts at `http://localhost:8000`. The `--reload` flag enables auto-restart on code changes.

Verify the backend is running:

```bash
curl http://localhost:8000/docs
```

This should return the FastAPI Swagger UI.

## 7. Install Frontend Dependencies

```bash
cd frontend
npm install
```

## 8. Start the Frontend

```bash
cd frontend
npm run dev
```

The Next.js development server starts at `http://localhost:3000`.

## 9. Verify the Setup

1. Open `http://localhost:3000` in your browser
2. You should see the upload page with a drag-and-drop zone
3. Upload a PDF file
4. Watch the processing dashboard update in real-time via WebSocket

## Environment Variables Reference

### Backend (`backend/.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `AT_ENV` | `dev` | Environment name — loads `config/settings.{env}.yaml` |
| `AT_DEBUG` | `true` | Enable debug mode |
| `AT_HOST` | `0.0.0.0` | Server bind address |
| `AT_PORT` | `8000` | Server bind port |
| `AT_OCR__PRIMARY_ENGINE` | `marker` | Primary OCR engine (`marker` or `azure_di`) |
| `AT_OCR__SECONDARY_ENGINE` | `azure_di` | Secondary OCR engine |
| `AT_OCR__QUALITY_SCORER` | `docling` | Quality scoring engine |
| `AT_MARKER__OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama URL for Marker |
| `AT_MARKER__OLLAMA_MODEL` | `gemma2:9b` | Ollama model for Marker |
| `AT_AZURE_DI__ENDPOINT` | (placeholder) | Azure Document Intelligence endpoint |
| `AT_AZURE_DI__API_KEY` | (empty) | Azure Document Intelligence API key |
| `AT_LLM__PROVIDER` | `ollama` | LLM provider (`ollama` or `azure_openai`) |
| `AT_LLM__BASE_URL` | `http://localhost:11434` | LLM provider base URL |
| `AT_LLM__MODEL` | `gemma2:9b` | LLM model name |
| `AT_DATABASE__URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/autotranscription` | Async database URL |
| `AT_STORAGE__BACKEND` | `filesystem` | Storage backend (`filesystem` or `azure_blob`) |
| `AT_STORAGE__BASE_PATH` | `./data/documents` | Local file storage path |
| `AT_HITL__AUTO_APPROVE_THRESHOLD` | `0.9` | Confidence above which pages are auto-approved |
| `AT_HITL__REVIEW_THRESHOLD` | `0.7` | Confidence below which pages require review |

> Environment variables use `AT_` prefix with double underscores for nesting. See [Settings](../backend/configuration/settings.md) for the full configuration system.

### Frontend (`frontend/.env.local`)

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | REST API base URL |
| `NEXT_PUBLIC_WS_URL` | `ws://localhost:8000` | WebSocket server URL |

## Docker Compose Services

The `backend/docker-compose.yml` defines:

```yaml
services:
  postgres:
    image: postgres:17-alpine
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: autotranscription
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]

  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    deploy:
      resources:
        limits:
          memory: 8G
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `Connection refused` on port 5432 | Check `docker compose ps` — PostgreSQL may not be healthy yet |
| `Connection refused` on port 11434 | Ollama container may still be starting — wait 10–20 seconds |
| `Model not found` error | Run `ollama pull gemma2:9b` (or via Docker exec) |
| Azure DI returns 401 | Verify `AZURE_DI_ENDPOINT` and `AZURE_DI_API_KEY` in `.env` |
| Frontend can't reach backend | Confirm `NEXT_PUBLIC_API_URL` matches the backend address |
| WebSocket not connecting | Confirm `NEXT_PUBLIC_WS_URL` and check browser console for errors |

## Related Pages

- [Settings](../backend/configuration/settings.md) — Full configuration reference
- [Dependency Injection](../backend/configuration/dependency-injection.md) — How adapters are wired
- [Azure DevOps Pipeline](./azure-devops-pipeline.md) — CI/CD setup
- [Frontend Overview](../frontend/overview.md) — Frontend architecture
