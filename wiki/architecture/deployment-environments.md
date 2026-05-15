# Deployment Environments

The platform runs in three environments, each with different adapter configurations. The same application code runs everywhere -- only the YAML config file changes.

## Environment Overview

| Component | Local Dev | Azure Staging | On-Prem Production |
|-----------|-----------|---------------|-------------------|
| **FastAPI** | `uvicorn` (localhost:8100) | Azure App Service | Docker / systemd |
| **Pipeline Mode** | `azure_di` or `marker_docling` (configurable) | `azure_di` | `azure_di` or `marker_docling` |
| **OCR Engine** | Marker (GPU/CPU) or Azure DI (cloud API) | Azure DI (cloud API) | Azure DI (disconnected container) or Marker (GPU) |
| **Quality Scoring (Docling)** | Local (CPU) | Local in App Service container | Local (CPU) |
| **LLM** | Ollama local (gemma2:9b) | Ollama on ACI | Ollama local (gemma2:9b) |
| **Database** | PostgreSQL Docker | Azure DB for PostgreSQL | PostgreSQL local |
| **Storage** | Filesystem (`./data/`) | Azure Blob Storage | Filesystem |
| **Frontend** | `next dev` (localhost:3100) | Azure Static Web Apps | Nginx serving static build |
| **Notifications** | WebSocket (single worker) | PG LISTEN/NOTIFY + WebSocket | WebSocket (single worker) |

## Deployment Flow

```mermaid
flowchart LR
    localDev["Local Dev<br/>(developer machine)"]
    cicd["GitHub Actions<br/>CI (tests + build)"]
    staging["Azure Staging<br/>(App Service + ACI)"]
    validation["Validated &<br/>Manual Approval"]
    onPrem["On-Prem Production<br/>(air-gapped / restricted)"]

    localDev -->|"open PR to main"| cicd
    cicd -->|"manual deploy"| staging
    staging -->|"QA + UAT"| validation
    validation -->|"release package"| onPrem
```

CI runs on **GitHub Actions** (see [GitHub Actions CI](../devops/github-actions-ci.md)). There are three workflows:

- **CI** (`.github/workflows/ci.yml`): Backend tests (pytest with WeasyPrint native deps) + Frontend type-check / lint / build, gated by a `dorny/paths-filter` sentinel so docs-only PRs short-circuit in ~30s.
- **PR Quality** (`.github/workflows/pr-quality.yml`): Typos, semantic PR title, path-based auto-labels, PR-size labels.
- **Maintenance** (`.github/workflows/maintenance.yml`): Weekly markdown link-check + stale issue/PR closer.

**Deployment is currently performed manually outside of CI.** The legacy `infra/azure-pipelines.yml` exists from the original scaffold (March 2026) and is not wired to any Azure DevOps service connection; it is kept for historical reference only. On-prem deployment receives a validated release package (Docker images + config) and is deployed manually.

---

## Config Strategy

Configuration is loaded by `app/config/settings.py` with this priority (highest wins):

1. **OS environment variables** -- prefixed with `AT_`, using `__` as nested delimiter (e.g., `AT_LLM__PROVIDER=azure_openai`)
2. **`.env` file** -- local development secrets (`backend/.env`)
3. **YAML file** -- `config/settings.{env}.yaml` where `env` comes from `AT_ENV` (defaults to `dev`)
4. **Pydantic field defaults** -- code-level fallbacks in `settings.py`

### Example: `settings.dev.yaml`

```yaml
env: dev
debug: true

pipeline:
  mode: azure_di   # "azure_di" or "marker_docling"

marker:
  use_llm: true
  paginate_output: true
  extract_images: true
  ollama_base_url: http://localhost:11434
  ollama_model: gemma2:9b

# Endpoint + key from env vars: AT_AZURE_DI__ENDPOINT, AT_AZURE_DI__API_KEY
azure_di:
  features:
    - barcodes
    - keyValuePairs

llm:
  provider: ollama
  base_url: http://localhost:11434
  model: gemma2:9b

database:
  url: postgresql+asyncpg://postgres:postgres@localhost:5432/autotranscription
  echo: false

storage:
  backend: filesystem
  base_path: ./data/documents

hitl:
  auto_approve_threshold: 0.9
  review_threshold: 0.7
  batch_review_enabled: true
```

### Example: `settings.staging.yaml`

```yaml
env: staging
debug: false

pipeline:
  mode: azure_di

# Endpoint + key from env vars: AT_AZURE_DI__ENDPOINT, AT_AZURE_DI__API_KEY
azure_di:
  features:
    - barcodes
    - keyValuePairs

llm:
  provider: ollama
  base_url: http://ollama-aci.eastus.azurecontainer.io:11434
  model: gemma2:9b

# DB credentials from env vars: AT_DATABASE__URL
database:
  echo: false

# Connection string from env var: AT_STORAGE__AZURE_CONNECTION_STRING
storage:
  backend: azure_blob
  azure_container: documents

hitl:
  auto_approve_threshold: 0.9
  review_threshold: 0.7
```

### Example: `settings.prod.yaml`

```yaml
env: prod
debug: false

pipeline:
  mode: azure_di   # or "marker_docling" for sites without Azure DI

marker:
  use_llm: true
  paginate_output: true
  extract_images: true
  ollama_base_url: http://localhost:11434
  ollama_model: gemma2:9b

azure_di:
  # Disconnected container running locally
  endpoint: http://localhost:5080
  # API key from env var: AT_AZURE_DI__API_KEY
  features:
    - barcodes
    - keyValuePairs

llm:
  provider: ollama
  base_url: http://localhost:11434
  model: gemma2:9b

# DB credentials from env var: AT_DATABASE__URL
database:
  echo: false

storage:
  backend: filesystem
  base_path: /opt/autotranscription/data

hitl:
  auto_approve_threshold: 0.9
  review_threshold: 0.7
```

---

## Environment-Specific Details

### Local Dev

**Purpose:** Developer iteration with fast feedback loops.

- **Pipeline mode** is configurable — `azure_di` (default) or `marker_docling`
- **Marker** runs locally when `pipeline.mode` is `marker_docling` (uses GPU if available, falls back to CPU)
- **Azure DI** calls the cloud API via Azure AI Foundry when `pipeline.mode` is `azure_di` (requires `AT_AZURE_DI__ENDPOINT` and `AT_AZURE_DI__API_KEY` in env)
- **Ollama** runs locally with `gemma2:9b` pulled via `infra/scripts/pull-ollama-models.sh`
- **PostgreSQL** runs in Docker (see `infra/docker/`)
- **Docling** runs locally on CPU (used in both modes)
- **Frontend** runs via `next dev` with hot reload, connecting to `localhost:8100`

Setup script: `infra/scripts/setup-local.sh`

### Azure Staging

**Purpose:** Integration testing and UAT before on-prem deployment.

- **FastAPI** deployed to Azure App Service (Linux container)
- **Azure DI** uses the same cloud API as dev but with a dedicated staging resource
- **Ollama** runs on Azure Container Instances (ACI) with GPU support
- **PostgreSQL** uses Azure Database for PostgreSQL Flexible Server
- **Storage** uses Azure Blob Storage for document persistence
- **Frontend** deployed to Azure Static Web Apps
- **Notifications** use PG LISTEN/NOTIFY for cross-worker coordination (App Service can scale to multiple instances)

### On-Prem Production

**Purpose:** Air-gapped or restricted network pharma environments.

- **Everything runs locally** -- no cloud dependencies
- **Azure DI** runs as a disconnected Docker container (requires a one-time license pull)
- **Ollama** runs on the local GPU server
- **PostgreSQL** runs on the local database server
- **Storage** uses the local filesystem at `/opt/autotranscription/data`
- **No internet access required** after initial setup and model pulls

The key architectural insight: because all external systems are behind ports, switching from cloud to local is purely a config change. The workflow code is identical across all environments.

---

## Related Pages

- [Architecture Overview](overview.md) -- How the DI container wires adapters per environment
- [Ports & Adapters](ports-and-adapters.md) -- Full adapter documentation
- [Data Flow](data-flow.md) -- Processing pipeline that runs identically in all environments
- [Back to Wiki Home](../README.md)
