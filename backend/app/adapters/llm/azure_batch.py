"""Azure OpenAI Global Batch API adapter.

Asynchronous batch processing at 50% lower cost with a 24-hour target
turnaround. Uses a separate ``GlobalBatch`` deployment so it does not
consume real-time quota.

Workflow:
  1. Build a JSONL file from compliance evaluation requests.
  2. Upload the file to Azure OpenAI.
  3. Submit a batch job.
  4. Poll for completion.
  5. Parse the output file and return results keyed by ``custom_id``.

This adapter is NOT for real-time use. It is designed for:
  - Background re-evaluation when rules change
  - Bulk processing of document archives
  - Regression testing rule changes
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time

import httpx
from pydantic import BaseModel

from app.adapters.llm.azure_openai import _prepare_schema

logger = logging.getLogger(__name__)

_API_VERSION = "2024-12-01-preview"
_POLL_INITIAL_S = 10.0
_POLL_MAX_S = 120.0
_POLL_TIMEOUT_S = 86400.0  # 24 hours


class BatchRequest:
    """A single request to include in a batch job."""

    __slots__ = ("custom_id", "messages", "schema", "temperature")

    def __init__(
        self,
        custom_id: str,
        messages: list[dict],
        schema: type[BaseModel] | None = None,
        temperature: float = 0.1,
    ) -> None:
        self.custom_id = custom_id
        self.messages = messages
        self.schema = schema
        self.temperature = temperature


class BatchResult:
    """Parsed result from a completed batch job."""

    __slots__ = ("custom_id", "status_code", "body", "error")

    def __init__(
        self,
        custom_id: str,
        status_code: int,
        body: dict | None = None,
        error: str | None = None,
    ) -> None:
        self.custom_id = custom_id
        self.status_code = status_code
        self.body = body
        self.error = error

    @property
    def content(self) -> str | None:
        if self.body:
            try:
                return self.body["choices"][0]["message"]["content"]
            except (KeyError, IndexError):
                return None
        return None

    def parse_structured(self, schema: type[BaseModel]) -> BaseModel | None:
        raw = self.content
        if raw is None:
            return None
        try:
            return schema.model_validate(json.loads(raw))
        except Exception:
            logger.warning("Failed to parse batch result %s", self.custom_id)
            return None


class AzureBatchAdapter:
    """Manages Azure OpenAI Global Batch API jobs."""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        deployment: str,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._deployment = deployment
        self._client = httpx.AsyncClient(timeout=120.0)

    def _headers(self) -> dict[str, str]:
        return {"api-key": self._api_key, "Content-Type": "application/json"}

    def _base_url(self) -> str:
        return f"{self._endpoint}/openai"

    # ── JSONL construction ───────────────────────────────────

    def build_jsonl(self, requests: list[BatchRequest]) -> str:
        """Build a JSONL string from a list of batch requests."""
        lines: list[str] = []
        for req in requests:
            body: dict = {
                "model": self._deployment,
                "messages": req.messages,
                "temperature": req.temperature,
            }

            if req.schema is not None:
                json_schema = req.schema.model_json_schema()
                json_schema = _prepare_schema(json_schema)
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": req.schema.__name__,
                        "strict": True,
                        "schema": json_schema,
                    },
                }

            line = {
                "custom_id": req.custom_id,
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": body,
            }
            lines.append(json.dumps(line, ensure_ascii=False))

        return "\n".join(lines)

    # ── File upload ──────────────────────────────────────────

    async def upload_jsonl(self, jsonl_content: str) -> str:
        """Upload a JSONL string as a batch input file. Returns file ID."""
        url = f"{self._base_url()}/files?api-version={_API_VERSION}"

        file_bytes = jsonl_content.encode("utf-8")
        files = {
            "file": ("batch_input.jsonl", io.BytesIO(file_bytes), "application/jsonl"),
            "purpose": (None, "batch"),
        }

        response = await self._client.post(
            url,
            files=files,
            headers={"api-key": self._api_key},
        )
        response.raise_for_status()
        data = response.json()
        file_id = data["id"]
        logger.info("Uploaded batch input file: %s (%d requests)", file_id, jsonl_content.count("\n") + 1)
        return file_id

    # ── Batch job management ─────────────────────────────────

    async def submit_batch(
        self,
        input_file_id: str,
        *,
        metadata: dict | None = None,
    ) -> str:
        """Submit a batch job. Returns batch ID."""
        url = f"{self._base_url()}/batches?api-version={_API_VERSION}"

        body: dict = {
            "input_file_id": input_file_id,
            "endpoint": "/v1/chat/completions",
            "completion_window": "24h",
        }
        if metadata:
            body["metadata"] = metadata

        response = await self._client.post(url, json=body, headers=self._headers())
        response.raise_for_status()
        data = response.json()
        batch_id = data["id"]
        logger.info("Submitted batch job: %s", batch_id)
        return batch_id

    async def get_batch_status(self, batch_id: str) -> dict:
        """Get the current status of a batch job."""
        url = f"{self._base_url()}/batches/{batch_id}?api-version={_API_VERSION}"
        response = await self._client.get(url, headers=self._headers())
        response.raise_for_status()
        return response.json()

    async def cancel_batch(self, batch_id: str) -> dict:
        """Cancel a running batch job."""
        url = f"{self._base_url()}/batches/{batch_id}/cancel?api-version={_API_VERSION}"
        response = await self._client.post(url, headers=self._headers())
        response.raise_for_status()
        return response.json()

    async def list_batches(self, limit: int = 20) -> list[dict]:
        """List recent batch jobs."""
        url = f"{self._base_url()}/batches?api-version={_API_VERSION}&limit={limit}"
        response = await self._client.get(url, headers=self._headers())
        response.raise_for_status()
        return response.json().get("data", [])

    # ── Polling ──────────────────────────────────────────────

    async def poll_until_complete(
        self,
        batch_id: str,
        *,
        poll_interval: float = _POLL_INITIAL_S,
        timeout: float = _POLL_TIMEOUT_S,
    ) -> dict:
        """Poll a batch job until it reaches a terminal state.

        Returns the final batch status dict.
        """
        start = time.monotonic()
        interval = poll_interval

        while True:
            status = await self.get_batch_status(batch_id)
            state = status.get("status", "unknown")

            if state in ("completed", "failed", "expired", "cancelled"):
                logger.info("Batch %s reached terminal state: %s", batch_id, state)
                return status

            elapsed = time.monotonic() - start
            if elapsed > timeout:
                logger.error("Batch %s timed out after %.0fs", batch_id, elapsed)
                raise TimeoutError(f"Batch {batch_id} did not complete within {timeout}s")

            counts = status.get("request_counts", {})
            logger.info(
                "Batch %s status=%s (completed=%s, failed=%s, total=%s), polling in %.0fs",
                batch_id, state,
                counts.get("completed", "?"),
                counts.get("failed", "?"),
                counts.get("total", "?"),
                interval,
            )

            await asyncio.sleep(interval)
            interval = min(interval * 1.5, _POLL_MAX_S)

    # ── Result retrieval ─────────────────────────────────────

    async def download_results(self, output_file_id: str) -> list[BatchResult]:
        """Download and parse the output file from a completed batch job."""
        url = (
            f"{self._base_url()}/files/{output_file_id}/content"
            f"?api-version={_API_VERSION}"
        )
        response = await self._client.get(url, headers=self._headers())
        response.raise_for_status()

        results: list[BatchResult] = []
        for line in response.text.strip().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                resp = entry.get("response", {})
                error = entry.get("error")
                results.append(BatchResult(
                    custom_id=entry["custom_id"],
                    status_code=resp.get("status_code", 500) if not error else 500,
                    body=resp.get("body") if not error else None,
                    error=json.dumps(error) if error else None,
                ))
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Failed to parse batch output line: %s", exc)

        logger.info("Downloaded %d results from file %s", len(results), output_file_id)
        return results

    # ── High-level convenience ───────────────────────────────

    async def run_batch(
        self,
        requests: list[BatchRequest],
        *,
        metadata: dict | None = None,
        poll_interval: float = _POLL_INITIAL_S,
        timeout: float = _POLL_TIMEOUT_S,
    ) -> list[BatchResult]:
        """End-to-end: build JSONL, upload, submit, poll, download results."""
        if not requests:
            return []

        jsonl = self.build_jsonl(requests)
        file_id = await self.upload_jsonl(jsonl)
        batch_id = await self.submit_batch(file_id, metadata=metadata)
        status = await self.poll_until_complete(
            batch_id, poll_interval=poll_interval, timeout=timeout,
        )

        state = status.get("status", "unknown")
        if state != "completed":
            error_file = status.get("error_file_id")
            raise RuntimeError(
                f"Batch {batch_id} ended with status '{state}'. "
                f"Error file: {error_file}"
            )

        output_file_id = status.get("output_file_id")
        if not output_file_id:
            raise RuntimeError(f"Batch {batch_id} completed but no output_file_id found")

        return await self.download_results(output_file_id)

    async def close(self) -> None:
        await self._client.aclose()
