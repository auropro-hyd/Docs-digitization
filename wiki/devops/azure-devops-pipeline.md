# Azure DevOps CI/CD Pipeline

The project uses Azure Pipelines for continuous integration and deployment. The pipeline builds and tests both backend and frontend, then deploys to Azure App Service on the `develop` branch.

**File:** `infra/azure-pipelines.yml`

## Pipeline Trigger

```yaml
trigger:
  branches:
    include:
      - main
      - develop
```

Pushes to `main` or `develop` trigger the pipeline. Only `develop` triggers the deployment stage.

## Pipeline Stages

### Stage 1: Build & Test

Runs two parallel jobs:

#### BackendBuild

| Step | Command | Description |
|------|---------|-------------|
| Python setup | `UsePythonVersion@0: 3.13` | Install Python 3.13 |
| Install dependencies | `pip install -e ".[dev]"` | Install backend with dev extras |
| Lint | `ruff check app/` | Run Ruff linter on the backend source |
| Unit tests | `pytest tests/unit -v --tb=short` | Run unit test suite |
| Docker build | `Docker@2: build` | Build backend Docker image tagged with `$(Build.BuildId)` |

#### FrontendBuild

| Step | Command | Description |
|------|---------|-------------|
| Node.js setup | `NodeTool@0: 20.x` | Install Node.js 20.x |
| Build | `npm ci && npm run lint && npm run build` | Clean install, lint, and production build |

### Stage 2: Deploy to Staging

**Condition:** Only runs on the `develop` branch after a successful build.

```yaml
condition: and(succeeded(), eq(variables['Build.SourceBranch'], 'refs/heads/develop'))
```

| Step | Task | Target |
|------|------|--------|
| Deploy Backend | `AzureWebApp@1` | `autotranscription-staging` App Service |

Uses the `$(AZURE_SUBSCRIPTION)` pipeline variable for authentication.

## Branching & Deployment Strategy

```
Feature branch
  │
  ▼
develop ────────────────────────────────────────────────┐
  │                                                     │
  ├── Pipeline: Build & Test (backend + frontend)       │
  │                                                     │
  ├── Pipeline: Deploy to Azure App Service (staging)   │
  │                                                     │
  ├── Manual validation on staging environment          │
  │                                                     │
  ▼                                                     │
main ◀──── Merge after validation ─────────────────────┘
  │
  ├── Pipeline: Build & Test only (no deploy)
  │
  └── Tag for on-prem release
```

| Branch | Build | Deploy | Purpose |
|--------|-------|--------|---------|
| `develop` | Yes | Yes (staging) | Integration testing, staging validation |
| `main` | Yes | No | Production-ready code, tagged for release |
| Feature branches | No (not in trigger) | No | Development work |

## Azure Resources

The application requires the following Azure resources for staging/production:

| Resource | Service | Purpose |
|----------|---------|---------|
| **App Service** | Azure App Service | Hosts the FastAPI backend |
| **Database** | Azure Database for PostgreSQL | Document metadata and processing state |
| **Container Instance** | Azure Container Instances | Runs Ollama for local LLM inference |
| **Static Web Apps** | Azure Static Web Apps | Hosts the Next.js frontend |
| **Document Intelligence** | Azure AI Services | Secondary OCR engine |
| **Blob Storage** | Azure Storage | Document file storage (when `storage.backend = azure_blob`) |

## Pipeline Variables

These variables must be configured in the Azure DevOps pipeline settings:

| Variable | Description |
|----------|-------------|
| `AZURE_SUBSCRIPTION` | Azure service connection name |

Additional variables for runtime configuration are set as App Service application settings — see the [environment variables reference](./local-setup.md#environment-variables-reference).

## Pipeline File

The full pipeline configuration:

```yaml
trigger:
  branches:
    include:
      - main
      - develop

pool:
  vmImage: "ubuntu-latest"

stages:
  - stage: Build
    displayName: "Build & Test"
    jobs:
      - job: BackendBuild
        displayName: "Backend"
        steps:
          - task: UsePythonVersion@0
            inputs:
              versionSpec: "3.13"
          - script: |
              cd backend
              pip install -e ".[dev]"
            displayName: "Install dependencies"
          - script: |
              cd backend
              ruff check app/
            displayName: "Lint"
          - script: |
              cd backend
              pytest tests/unit -v --tb=short
            displayName: "Unit tests"
          - task: Docker@2
            displayName: "Build Docker image"
            inputs:
              command: build
              Dockerfile: backend/Dockerfile
              tags: "$(Build.BuildId)"

      - job: FrontendBuild
        displayName: "Frontend"
        steps:
          - task: NodeTool@0
            inputs:
              versionSpec: "20.x"
          - script: |
              cd frontend
              npm ci
              npm run lint
              npm run build
            displayName: "Build frontend"

  - stage: DeployStaging
    displayName: "Deploy to Staging"
    dependsOn: Build
    condition: and(succeeded(), eq(variables['Build.SourceBranch'], 'refs/heads/develop'))
    jobs:
      - deployment: DeployBackend
        displayName: "Deploy Backend to App Service"
        environment: staging
        strategy:
          runOnce:
            deploy:
              steps:
                - task: AzureWebApp@1
                  inputs:
                    azureSubscription: "$(AZURE_SUBSCRIPTION)"
                    appName: "autotranscription-staging"
                    package: "$(Pipeline.Workspace)/backend"
```

## Related Pages

- [Local Setup](./local-setup.md) — Local development environment and environment variables
- [Settings](../backend/configuration/settings.md) — Application configuration system
- [Dependency Injection](../backend/configuration/dependency-injection.md) — How adapters are swapped per environment
