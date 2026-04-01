# Azure Document Intelligence Setup Guide

This guide covers running Azure Document Intelligence (DI) in two modes:
**cloud API** for dev/staging and **disconnected container** for on-prem production.

Both modes use the same adapter code — only the `endpoint` in config changes.

---

## Architecture Overview

```
Dev/Staging (Cloud):
  App → HTTPS → Azure DI Cloud API (*.cognitiveservices.azure.com)

Production (On-Prem):
  App → HTTP → Azure DI Container (localhost:5000) → No data leaves network
```

---

## Option 1: Cloud API (Dev / Staging)

### Prerequisites

1. Azure subscription with Azure AI Foundry access
2. A Document Intelligence resource provisioned in a supported region

### Step-by-Step

1. **Create a Document Intelligence resource** in Azure Portal or AI Foundry:
   - Service: "Document Intelligence" (formerly Form Recognizer)
   - Pricing tier: S0 (Standard) — free tier has limited quota
   - Region: Choose `centralindia` or `eastus` (see [region availability](https://learn.microsoft.com/azure/ai-services/document-intelligence/service-limits))

2. **Get your credentials**:
   - Go to the resource → **Keys and Endpoint**
   - Copy **Endpoint** (e.g., `https://your-resource.cognitiveservices.azure.com`)
   - Copy **Key 1** (your API key)

3. **Configure the app**:

   In `backend/.env`:
   ```env
   AT_AZURE_DI__ENDPOINT=https://your-resource.cognitiveservices.azure.com
   AT_AZURE_DI__API_KEY=your-api-key-here
   AT_PIPELINE__MODE=azure_di
   ```

   Or in `config/settings.dev.yaml`:
   ```yaml
   pipeline:
     mode: azure_di

   azure_di:
     endpoint: "https://your-resource.cognitiveservices.azure.com"
     api_key: "your-api-key"
   ```

4. **Test it**:
   ```bash
   make backend  # Start the backend
   # In another terminal:
   curl -X POST http://localhost:8100/api/documents/process-file \
     -F "file=@path/to/your/document.pdf"
   ```

---

## Option 2: Disconnected Container (On-Prem Production)

Azure DI disconnected containers run entirely on your infrastructure. After initial
license activation, they work fully offline — no data ever leaves your network.

### Prerequisites

1. Docker or container orchestrator (Kubernetes, Docker Compose)
2. An Azure DI resource (for billing/license — the container phones home periodically
   for license validation only, NOT for processing data)
3. Minimum hardware: 8 CPU cores, 8 GB RAM, SSD storage

### Step-by-Step

1. **Pull the container image**:
   ```bash
   # Layout model (recommended — handles text, tables, figures, selection marks)
   docker pull mcr.microsoft.com/azure-cognitive-services/form-recognizer/layout-3.1

   # Or the Read model (text-only, lighter)
   docker pull mcr.microsoft.com/azure-cognitive-services/form-recognizer/read-3.1
   ```

2. **Get billing credentials** from your Azure DI resource:
   - **Billing endpoint**: Your resource's endpoint URL
   - **API Key**: Key 1 from your resource

3. **Run the container**:
   ```bash
   docker run -d \
     --name azure-di-layout \
     -p 5000:5000 \
     -e Eula=accept \
     -e Billing=https://your-resource.cognitiveservices.azure.com \
     -e ApiKey=your-api-key-here \
     -e Mounts:Output=/output \
     -v /path/to/local/output:/output \
     mcr.microsoft.com/azure-cognitive-services/form-recognizer/layout-3.1
   ```

   **Important flags**:
   - `Eula=accept` — Required to accept the license
   - `Billing` — Your Azure DI resource endpoint (for license validation)
   - `ApiKey` — Your Azure DI API key
   - The container processes documents **locally**. Billing endpoint is only used for periodic license checks.

4. **Verify the container is running**:
   ```bash
   # Health check
   curl http://localhost:5000/ready

   # Should return: {"status":"ready"}
   ```

5. **Configure the app for on-prem**:

   In `config/settings.prod.yaml`:
   ```yaml
   pipeline:
     mode: azure_di

   azure_di:
     endpoint: "http://localhost:5000"
     api_key: "your-api-key"   # Same key used to start the container
   ```

   Or via environment variables:
   ```env
   AT_AZURE_DI__ENDPOINT=http://localhost:5000
   AT_AZURE_DI__API_KEY=your-api-key-here
   AT_PIPELINE__MODE=azure_di
   ```

6. **Docker Compose** (recommended for production):
   ```yaml
   services:
     azure-di:
       image: mcr.microsoft.com/azure-cognitive-services/form-recognizer/layout-3.1
       ports:
         - "5000:5000"
       environment:
         - Eula=accept
         - Billing=https://your-resource.cognitiveservices.azure.com
         - ApiKey=${AZURE_DI_API_KEY}
       deploy:
         resources:
           limits:
             cpus: '8'
             memory: 8G
       restart: unless-stopped
       healthcheck:
         test: ["CMD", "curl", "-f", "http://localhost:5000/ready"]
         interval: 30s
         timeout: 10s
         retries: 5

     backend:
       build: ./backend
       depends_on:
         azure-di:
           condition: service_healthy
       environment:
         - AT_AZURE_DI__ENDPOINT=http://azure-di:5000
         - AT_AZURE_DI__API_KEY=${AZURE_DI_API_KEY}
         - AT_PIPELINE__MODE=azure_di
   ```

---

## Switching Between Cloud and Container

The **only difference** is the endpoint URL:

| Mode | Endpoint | Data leaves network? |
|------|----------|---------------------|
| Cloud API | `https://your-resource.cognitiveservices.azure.com` | Yes |
| Disconnected Container | `http://localhost:5000` | No |

Your code, adapters, and pipeline logic remain **identical**.

---

## Switching to Fully Offline (Marker + Docling)

If you cannot use Azure at all (no billing endpoint connectivity):

```yaml
pipeline:
  mode: marker_docling
```

This uses Marker OCR + Docling quality scoring + Ollama LLM, with zero cloud
dependency. Requires ~7 GB of local model downloads on first run.

See [Pipeline Modes](./pipeline-modes.md) for details.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `401 Unauthorized` | Check API key in `.env` |
| `Connection refused (localhost:5000)` | Container not running — check `docker ps` |
| Container exits immediately | Check `docker logs azure-di-layout` — usually missing `Eula=accept` |
| Slow first request | Container loads models on startup. First request may take 30-60s. |
| License validation fails | Container needs outbound HTTPS to `*.cognitiveservices.azure.com` periodically |
