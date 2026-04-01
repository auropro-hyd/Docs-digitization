"""Document upload and processing API routes."""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from statistics import mean
from pathlib import Path
from time import perf_counter

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.config.settings import get_settings

router = APIRouter()
logger = logging.getLogger(__name__)


def _pipeline_features() -> dict:
    settings = get_settings()
    if settings.pipeline.mode == "azure_di":
        return {
            "mode": "azure_di",
            "features": list(settings.azure_di.features),
        }
    features = [
        "paginate_output" if settings.marker.paginate_output else "",
        "extract_images" if settings.marker.extract_images else "",
        "llm_assist" if settings.marker.use_llm else "",
    ]
    return {
        "mode": "marker_docling",
        "features": [f for f in features if f],
    }


def _build_extraction_telemetry(result_data: dict, elapsed_ms: int) -> dict:
    scores = result_data.get("confidence_scores", {}) or {}
    pages = []
    for ext in result_data.get("extractions", []) or []:
        page_num = int(ext.get("page_num", 0) or 0)
        pages.append({
            "page_num": page_num,
            "confidence": float(scores.get(page_num, scores.get(str(page_num), 0.0)) or 0.0),
            "parser_repair_count": int(ext.get("parser_repair_count", 0) or 0),
            "parser_repair_severity": ext.get("parser_repair_severity", "none"),
            "handwritten_count": int(ext.get("handwritten_count", 0) or 0),
            "selection_ambiguity": bool((ext.get("selection_semantics") or {}).get("has_ambiguity", False)),
            "anchor_issue_count": len(ext.get("packet_anchor_issues", []) or []),
            "risk_level": (ext.get("corruption_risk") or {}).get("level", "low"),
            "risk_score": float((ext.get("corruption_risk") or {}).get("score", 0.0) or 0.0),
        })
    return {
        "strategy": _pipeline_features(),
        "latency": {
            "total_ms": elapsed_ms,
            "total_pages": len(pages),
            "ms_per_page": round(elapsed_ms / max(1, len(pages)), 2),
        },
        "pages": pages,
    }


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


@router.post("/upload")
async def upload_document(file: UploadFile):
    settings = get_settings()
    doc_id = str(uuid.uuid4())
    upload_dir = Path(settings.storage.base_path) / doc_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    safe_name = Path(file.filename or "document.pdf").name
    file_path = upload_dir / safe_name
    content = await file.read()
    file_path.write_bytes(content)

    return {
        "doc_id": doc_id,
        "filename": safe_name,
        "size_bytes": len(content),
        "pdf_path": str(file_path),
        "status": "uploaded",
    }


@router.post("/{doc_id}/process")
async def process_document(doc_id: str, background_tasks: BackgroundTasks):
    """Trigger the LangGraph processing pipeline for an uploaded document."""
    settings = get_settings()
    upload_dir = Path(settings.storage.base_path) / doc_id

    if not upload_dir.exists():
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")

    pdf_files = list(upload_dir.glob("*.pdf"))
    if not pdf_files:
        raise HTTPException(status_code=404, detail=f"No PDF found for doc_id={doc_id}")

    pdf_path = str(pdf_files[0])
    background_tasks.add_task(_run_pipeline, doc_id, pdf_path)

    return {
        "doc_id": doc_id,
        "pdf_path": pdf_path,
        "status": "processing",
        "message": "Pipeline started in background",
    }


@router.post("/process-file")
async def process_file_directly(file: UploadFile, background_tasks: BackgroundTasks):
    """Upload and immediately process a document (combined endpoint)."""
    settings = get_settings()
    doc_id = str(uuid.uuid4())
    upload_dir = Path(settings.storage.base_path) / doc_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    safe_name = Path(file.filename or "document.pdf").name
    file_path = upload_dir / safe_name
    content = await file.read()
    file_path.write_bytes(content)

    background_tasks.add_task(_run_pipeline, doc_id, str(file_path))

    return {
        "doc_id": doc_id,
        "filename": safe_name,
        "size_bytes": len(content),
        "pdf_path": str(file_path),
        "status": "processing",
        "message": "Upload complete, pipeline started in background",
    }


@router.get("/{doc_id}/pdf")
async def get_document_pdf(doc_id: str):
    """Serve the original uploaded PDF file."""
    settings = get_settings()
    upload_dir = Path(settings.storage.base_path) / doc_id

    if not upload_dir.exists():
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")

    pdf_files = list(upload_dir.glob("*.pdf"))
    if not pdf_files:
        raise HTTPException(status_code=404, detail=f"No PDF found for doc_id={doc_id}")

    return FileResponse(
        pdf_files[0],
        media_type="application/pdf",
        filename=pdf_files[0].name,
    )


@router.delete("/{doc_id}")
async def delete_document(doc_id: str):
    """Delete a document and all its associated data."""
    settings = get_settings()
    upload_dir = Path(settings.storage.base_path) / doc_id

    if not upload_dir.exists():
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")

    shutil.rmtree(upload_dir)
    return {"doc_id": doc_id, "status": "deleted"}


@router.get("/{doc_id}")
async def get_document(doc_id: str):
    """Get the current state/results of a document."""
    settings = get_settings()
    upload_dir = Path(settings.storage.base_path) / doc_id

    if not upload_dir.exists():
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")

    pdf_files = list(upload_dir.glob("*.pdf"))
    result_file = upload_dir / "result.json"
    lock_file = upload_dir / "processing.lock"

    response: dict = {
        "doc_id": doc_id,
        "filename": pdf_files[0].name if pdf_files else None,
        "pdf_path": str(pdf_files[0]) if pdf_files else None,
        "has_results": result_file.exists(),
        "is_processing": lock_file.exists(),
    }

    if result_file.exists():
        response["results"] = json.loads(result_file.read_text())

    return response


@router.get("/")
async def list_documents():
    settings = get_settings()
    base = Path(settings.storage.base_path)
    if not base.exists():
        return {"documents": [], "total": 0}

    docs = []
    for d in base.iterdir():
        if d.is_dir():
            pdfs = list(d.glob("*.pdf"))
            result_file = d / "result.json"
            lock_file = d / "processing.lock"

            status = "uploaded"
            total_pages = 0

            if lock_file.exists() and not result_file.exists():
                status = "processing"
            elif result_file.exists():
                try:
                    result_data = json.loads(result_file.read_text())
                    if result_data.get("status") == "error":
                        status = "error"
                    elif result_data.get("extractions"):
                        total_pages = len(result_data["extractions"])
                        decisions = result_data.get("hitl_decisions", [])
                        if decisions and all(
                            isinstance(dec, dict) and dec.get("status") in ("approved", "edited")
                            for dec in decisions
                        ) and len(decisions) >= total_pages:
                            status = "completed"
                        elif any(
                            isinstance(dec, dict) and dec.get("status") == "flagged"
                            for dec in decisions
                        ):
                            status = "needs_review"
                        else:
                            status = "processed"
                    else:
                        status = "processed"
                except (json.JSONDecodeError, KeyError):
                    status = "error"

            created_at = d.stat().st_ctime

            docs.append({
                "doc_id": d.name,
                "filename": pdfs[0].name if pdfs else None,
                "status": status,
                "total_pages": total_pages,
                "created_at": created_at,
            })

    docs.sort(key=lambda x: x["created_at"], reverse=True)

    return {"documents": docs, "total": len(docs)}


@router.get("/quality-dashboard")
async def quality_dashboard():
    """Aggregate extraction quality/latency/feedback metrics for monitoring."""
    settings = get_settings()
    base = Path(settings.storage.base_path)
    docs = []
    if base.exists():
        docs = [d for d in base.iterdir() if d.is_dir()]

    latency_ms_per_page: list[float] = []
    mean_page_conf: list[float] = []
    retraining_triggers = 0
    total_corrections = 0

    for d in docs:
        result_file = d / "result.json"
        if not result_file.exists():
            continue
        try:
            payload = json.loads(result_file.read_text())
        except Exception:
            continue
        telemetry = payload.get("extraction_telemetry", {}) or {}
        latency = telemetry.get("latency", {}) or {}
        if isinstance(latency.get("ms_per_page"), (int, float)):
            latency_ms_per_page.append(float(latency["ms_per_page"]))

        page_scores = [p.get("confidence", 0.0) for p in telemetry.get("pages", []) if isinstance(p, dict)]
        if page_scores:
            mean_page_conf.append(float(sum(page_scores) / len(page_scores)))

        total_corrections += int((payload.get("correction_summary") or {}).get("total_corrections", 0) or 0)
        if bool((payload.get("retraining_trigger") or {}).get("should_trigger_retraining", False)):
            retraining_triggers += 1

    compare_report_path = (
        Path(__file__).resolve().parents[3]
        / "tests"
        / "fixtures"
        / "extraction_benchmark"
        / "reports"
        / "latest_compare_report.json"
    )
    benchmark_compare = {}
    if compare_report_path.exists():
        try:
            benchmark_compare = json.loads(compare_report_path.read_text())
        except Exception:
            benchmark_compare = {}

    return {
        "documents_scanned": len(docs),
        "quality": {
            "mean_page_confidence": round(mean(mean_page_conf), 4) if mean_page_conf else 0.0,
            "benchmark_compare_delta": (benchmark_compare or {}).get("delta", {}),
        },
        "latency": {
            "mean_ms_per_page": round(mean(latency_ms_per_page), 2) if latency_ms_per_page else 0.0,
            "samples": len(latency_ms_per_page),
        },
        "feedback_loop": {
            "total_corrections": total_corrections,
            "documents_triggered_for_retraining": retraining_triggers,
        },
    }


async def _run_pipeline(doc_id: str, pdf_path: str):
    """Execute the LangGraph document processing pipeline."""
    settings = get_settings()
    doc_dir = Path(settings.storage.base_path) / doc_id
    lock_file = doc_dir / "processing.lock"

    lock_file.write_text(doc_id)
    logger.info(f"Starting pipeline for doc_id={doc_id}, pdf={pdf_path}")
    started = perf_counter()

    try:
        from app.workflow.document_graph import build_document_graph

        graph = build_document_graph()

        initial_state = {
            "doc_id": doc_id,
            "pdf_path": pdf_path,
            "status": "uploaded",
        }

        config = {"configurable": {"thread_id": doc_id}}

        from app.api.websocket import manager

        accumulated: dict = {}
        async for event in graph.astream(initial_state, config=config):
            node_names = list(event.keys())
            logger.info(f"[{doc_id}] Graph event: {node_names}")
            for _node_name, value in event.items():
                if isinstance(value, dict):
                    for k, v in value.items():
                        if k in accumulated and isinstance(accumulated[k], dict) and isinstance(v, dict):
                            accumulated[k].update(v)
                        elif k in accumulated and isinstance(accumulated[k], list) and isinstance(v, list):
                            accumulated[k].extend(v)
                        else:
                            accumulated[k] = v
            try:
                await manager.broadcast(doc_id, {
                    "type": "status",
                    "status": node_names[0] if node_names else "processing",
                })
            except Exception:
                pass

        result_path = doc_dir / "result.json"
        result_data = accumulated
        elapsed_ms = int((perf_counter() - started) * 1000)
        result_data["extraction_telemetry"] = _build_extraction_telemetry(result_data, elapsed_ms)
        result_path.write_text(json.dumps(result_data, indent=2, default=str))
        logger.info(f"[{doc_id}] Pipeline complete. Results saved to {result_path}")

        total_pages = len(result_data.get("extractions", []))
        try:
            await manager.broadcast(doc_id, {
                "type": "status",
                "status": "completed",
                "total_pages": total_pages,
            })
        except Exception:
            pass

    except Exception as e:
        logger.exception(f"[{doc_id}] Pipeline failed: {e}")
        try:
            from app.api.websocket import manager

            await manager.broadcast(doc_id, {"type": "error", "error": str(e)})
        except Exception:
            pass
        error_path = doc_dir / "result.json"
        error_path.write_text(json.dumps({"status": "error", "error": str(e)}, indent=2))
    finally:
        if lock_file.exists():
            lock_file.unlink()
