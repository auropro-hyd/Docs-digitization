# VLM-Powered Visual Compliance Audit — Technical Specification

> **Status**: Implemented (Phase 1-2, Phase 4 complete) | Phase 3, 5 deferred
> **Date**: 2026-04-13 | **Updated**: 2026-04-15
> **Authors**: Engineering Team
> **Scope**: Backend service architecture, compliance rule integration, infrastructure, and frontend enhancements

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement](#2-problem-statement)
3. [Current Architecture Gaps](#3-current-architecture-gaps)
4. [Visual Compliance Rule Inventory](#4-visual-compliance-rule-inventory)
5. [VLM Provider Analysis](#5-vlm-provider-analysis)
6. [Architecture Design](#6-architecture-design)
7. [VLM Service Port & Adapters](#7-vlm-service-port--adapters)
8. [Page Image Pipeline](#8-page-image-pipeline)
9. [Compliance Evaluator Integration](#9-compliance-evaluator-integration)
10. [Configuration & Settings](#10-configuration--settings)
11. [Infrastructure & Container Deployment](#11-infrastructure--container-deployment)
12. [Frontend Enhancements](#12-frontend-enhancements)
13. [Migration & Rollout Plan](#13-migration--rollout-plan)
14. [Testing Strategy](#14-testing-strategy)
15. [Cost & Performance Estimates](#15-cost--performance-estimates)
16. [Open Questions](#16-open-questions)

---

## 1. Executive Summary

The current compliance evaluation pipeline processes scanned pharmaceutical batch records using OCR-extracted text plus structured metadata (signatures, key-value pairs, selection marks). This approach fundamentally **cannot** assess visual properties such as strikethroughs, ink color, physical stamps, watermarks, correction fluid, overwriting, barcode readability, or chart/chromatogram integrity.

This spec introduces a **VLM (Vision Language Model) service** that sends page raster images to a multimodal model for visual compliance checks. The service is designed as a pluggable port-adapter extension of the existing hexagonal architecture, supporting:

- **Gemini 2.5 Pro** (cloud API — immediate, no infrastructure)
- **Qwen3-VL / InternVL3** (container-hosted via vLLM — full data sovereignty)
- Future providers via the same port interface

The VLM service runs **alongside** the existing text-based LLM evaluator, not replacing it. Rules are tagged with an `evaluation_strategy` field that determines whether they are evaluated via text-only, vision-only, or a combined text+vision approach.

---

## 2. Problem Statement

### 2.1 What Cannot Be Assessed Today

The evaluator system prompt (`evaluator.py:51-82`) explicitly tells the LLM:

> *"You cannot assess smudges, fading, or unapproved inks from OCR text alone."*

Rules that hit this wall are currently **defaulted to compliant** — meaning the system silently passes documents that may have serious GMP violations. In a 21 CFR Part 11 / EU Annex 11 regulated environment, this is an audit risk.

### 2.2 Scale of the Problem

From the full rule inventory analysis:

| Category | Rules needing vision | Rules defaulting compliant |
|----------|---------------------|---------------------------|
| Strong visual requirement (cannot be assessed from text) | **17** | 10 |
| Partial visual requirement (text helps but vision needed for strict compliance) | **15** | 5 |
| **Total** | **32 of ~82 active rules** | **15 silent passes** |

---

## 3. Current Architecture Gaps

### 3.1 No Page Images Persisted

The Azure DI adapter (`azure_di.py`) calls `begin_analyze_document` with `output_content_format=MARKDOWN`. The PDF is sent as bytes but **no page raster images are ever extracted or stored**. The `OCRPageResult.images` field exists but is never populated for Azure DI.

The quality gate (`document_quality_gate.py`) renders pages via `pypdfium2` for contrast/resolution metrics but discards the bitmaps immediately.

### 3.2 No Vision-Capable LLM Port

The `LLMProvider` protocol (`core/ports/llm.py`) supports only `generate(prompt)` and `generate_structured(prompt, schema)` — both text-only. There is no method accepting image inputs.

### 3.3 No Rule-Level Evaluation Strategy Tag

Rules in YAML have `evaluation_mode` (`llm` | `cannot_evaluate`) but no concept of "this rule needs a page image." Rules that need vision are handled by writing `notes: "VLM recommended"` or by pass_criteria that say "default compliant" — informal workarounds.

### 3.4 No VLM in the DI Container

The `Container` class wires OCR engines and LLM providers but has no slot for a VLM provider.

### 3.5 Context Builder Has No Image Channel

`build_enriched_context()` constructs a text-only prompt. There is no pathway to attach a page image alongside the text context.

---

## 4. Visual Compliance Rule Inventory

### 4.1 Tier 1 — Strong Visual Requirement (Cannot Be Assessed from Text)

These rules **must** have a page image to produce a meaningful evaluation. Today they default compliant or produce unreliable results.

| Rule ID | Rule Description | Visual Requirement | Current Behavior |
|---------|-----------------|-------------------|-----------------|
| **ALC-ATT6** | Corrections: single-line strikethrough, original readable, initials + date | Strike-through geometry, line thickness, legibility of text underneath | Defaults compliant; OCR cannot detect strikethrough |
| **ALC-LEG16** | No smudges, fading, damage, pencil use | Physical document condition, graphite vs ink distinction | Defaults compliant; YAML says "VISUAL inspection" |
| **ALC-LEG17** | No sticky notes, temp annotations, unapproved inks | Adhesive artifacts, ink color classification | Defaults compliant unless `[DRAFT]` in text |
| **ALC-LEG18** | Attachments affixed without covering text | Spatial occlusion detection (overlapping regions) | Defaults compliant |
| **ALC-LEG20** | Barcodes/labels readable | Machine-graphic decoding, print quality | Skipped if no barcodes in text; no visual decode |
| **ALC-LEG22** | Handwriting in permanent ink (blue/black) | Ink color classification from raster | Defaults compliant; YAML says "VISUAL" |
| **ALC-LEG24** | Charts/graphs labeled with scales | Raster chart analysis, axis label detection | Skipped if no figure markers |
| **ALC-ORI34** | Original BMR maintained, "ORIGINAL" stamps | Stamp/seal detection, impression quality | Partial text match only |
| **ALC-ORI40** | No unauthorized duplication, watermarks | Watermark detection, photocopy artifact analysis | Defaults compliant |
| **ALC-ORI41** | Chromatograms/spectra in original format | Raster image analysis of embedded spectra | Skipped if no figure content |
| **ALC-END69** | Records protected, stored properly | Binding marks, physical damage, archive stamps | Defaults compliant |
| **ALC-END70** | Permanent ink, archival paper quality | Paper/ink material assessment from scan | Defaults compliant |
| **GMP-COR14** | Single-line strikethrough, original readable | Strike-through geometry (same as ALC-ATT6) | Defaults compliant |
| **GMP-COR17** | No correction fluid, erasure, overwriting | White-out detection, erasure marks, layered text | Defaults compliant; YAML says "VISUAL" |
| **CHE-ATT17** | Required attachments present | Physical presence of stapled/taped documents | Text reference only |
| **CHE-ATT18** | Attachments properly labeled | Label position, text on attachment vs host doc | Text reference only |
| **CHE-ATT19** | Attachments securely affixed | Physical attachment integrity (tape, staple marks) | `notes: "VLM for attachment integrity"` |

### 4.2 Tier 2 — Partial Visual Requirement (Text + Vision Combined)

These rules can partially be assessed from OCR text and metadata but need vision for strict GMP compliance.

| Rule ID | Rule Description | Visual Enhancement | Current Gap |
|---------|-----------------|-------------------|-------------|
| **ALC-ATT1** | Each entry has clear initials/signature + date | Distinguish wet signature from typed text | Accepts any text in column as "signature" |
| **ALC-ATT5** | Critical steps: Done by + Checked by both present | Verify second-person review is a real signature | Same as ATT1 |
| **ALC-ATT12** | Deviation/OOS: completion + sign-offs | Signature verification on deviation forms | Text presence only |
| **ALC-LEG15** | Entries clear, not overwritten | Detect text layers, overwriting artifacts | Cannot detect overwriting from OCR |
| **ALC-LEG19** | Page numbering legible and complete | Verify footer/header pagination from layout | OCR text matching only |
| **ALC-LEG21** | Copies/printouts meet legibility standards | Print quality, toner density, contrast | Text cannot assess copy quality |
| **ALC-ORI36** | Controlled copies per procedure | "COPY X of Y" stamp detection | Text keyword match only |
| **ALC-ACC42** | Calculations verified/countersigned | Second signature verification | Text presence only |
| **ALC-COM52** | No blank fields; N/A properly marked | True blank vs OCR miss detection | KV metadata helps but misses handwritten fills |
| **ALC-COM53** | Signatures, dates, equipment IDs present | Equipment sticker verification | Text-only |
| **ALC-COM57** | All pages accounted for | Physical page count vs printed pagination | OCR text pattern only |
| **ALC-END72** | Binding prevents lost pages | Binding marks, perforation, staple holes | Text-only |
| **CHE-CHE1** | Checklist items checked or N/A | Verify checkbox state beyond DI selection_marks | Relies on DI checkbox detection |
| **CHE-SIG5-9** | Done/Checked/Approved/QA signatures + dates | Wet signature vs typed text distinction | Any text = valid |
| **GMP-YIE26** | Yield verified with second-person sign-off | Second signature verification | Text presence only |

### 4.3 Visual Check Capability Matrix

Each VLM visual check maps to one or more rules and represents a distinct visual analysis capability:

| Visual Check ID | Capability | Rules Served | Difficulty |
|----------------|-----------|-------------|-----------|
| `VC-STRIKE` | Strikethrough detection (single-line, scribble, type) | ALC-ATT6, GMP-COR14, GMP-COR15 | Medium |
| `VC-SIGNATURE` | Wet signature vs typed text detection | ALC-ATT1, ATT5, ATT12, ACC42, COM53, CHE-SIG5-9, GMP-YIE26 | Medium |
| `VC-INK-COLOR` | Ink color classification (blue/black/red/pencil) | ALC-LEG22, ALC-END70 | Medium-Hard |
| `VC-CORRECTION` | Correction fluid / erasure / overwriting detection | GMP-COR17, ALC-LEG15 | Hard |
| `VC-STAMP-SEAL` | Official stamp / seal / watermark detection | ALC-ORI34, ALC-ORI36, ALC-ORI40 | Medium |
| `VC-ATTACHMENT` | Physical attachment presence & integrity | ALC-LEG18, CHE-ATT17-20 | Hard |
| `VC-BARCODE` | Barcode/label visual quality assessment | ALC-LEG20 | Easy |
| `VC-BLANK-FIELD` | True blank vs filled field detection | ALC-COM52, CHE-BLA14-16 | Medium |
| `VC-DOC-QUALITY` | Smudges, fading, damage, pencil marks | ALC-LEG16, ALC-LEG21, ALC-END69 | Medium |
| `VC-CHART` | Chart/graph axis labels and scale verification | ALC-LEG24 | Medium |
| `VC-CHROMATOGRAM` | Spectra/chromatogram image integrity | ALC-ORI41 | Hard |
| `VC-CHECKBOX` | Enhanced checkbox/tickmark verification | CHE-CHE1-4, CHE-CRO21-23, CHE-EQU24-26 | Easy-Medium |
| `VC-PAGINATION` | Page number layout verification from image | ALC-LEG19, ALC-COM57, ALC-END72 | Easy |
| `VC-STICKY-NOTE` | Sticky note / temporary annotation detection | ALC-LEG17 | Medium |

---

## 5. VLM Provider Analysis

### 5.1 Provider Comparison

| Provider | Type | Container Hosting | DocVQA Score | Key Strengths | Key Limitations |
|----------|------|------------------|-------------|---------------|----------------|
| **Gemini 2.5 Pro** | Closed (API) | Google Distributed Cloud only | SOTA | Best-in-class multimodal reasoning; native structured output; massive context window | Cloud-only (no self-host without GDC); data leaves network |
| **Qwen3-VL (72B)** | Open-weight | vLLM / LMDeploy container | 94.5+ ANLS | Leads open-source document understanding; strong OCR-free reading; excellent structured output | Requires 4xA100 80GB for 72B; 8B variant fits single GPU |
| **InternVL3 (78B)** | Open-weight | vLLM / LMDeploy container | 93+ ANLS | Strong industrial/spatial reasoning; 3D perception; good at layouts | Similar GPU requirements to Qwen3-VL 72B |
| **Gemma 3 (27B)** | Open-weight | Ollama / vLLM | ~90 ANLS | Pan & Scan for high-res; multilingual OCR; lightweight | Trails Qwen3-VL and InternVL3 on document benchmarks |
| **DeepSeek-OCR** | Open-weight | vLLM | ~93 ANLS | Optimized for dense text/layout; "Contexts Optical Compression" | Newer, less production-tested |

### 5.2 Recommended Strategy

**Phase 1 (Immediate — VERIFIED READY)**: Gemini 2.5 Flash via Google Generative Language API
- **Project**: `prj-auropro-dev` (billing enabled, APIs enabled)
- **API Key**: Dedicated `docs-digitization-vlm` key created (restricted to `generativelanguage.googleapis.com`)
- **Auth path**: API key via `generativelanguage.googleapis.com/v1beta` (Vertex AI requires IAM role grant from project admin)
- **Verified capabilities**: vision input, structured JSON output, 5 visual checks per call
- **Default model**: `gemini-2.5-flash` (1.5s latency, $0.004/doc) — best cost/speed balance
- **Fallback model**: `gemini-2.5-pro` (28s latency, $0.06/doc) — for accuracy-critical checks
- **Available models**: gemini-2.5-pro, gemini-2.5-flash, gemini-2.5-flash-lite, gemini-2.0-flash

**Phase 2 (Container)**: Qwen3-VL-8B-Instruct via vLLM container
- Self-hosted, full data sovereignty
- Single A100 or 2xA10G sufficient for 8B model
- Docker image: `vllm/vllm-openai` with `--model Qwen/Qwen3-VL-8B-Instruct`

**Phase 3 (Scale)**: Qwen3-VL-72B or InternVL3-78B for production accuracy
- 4xA100 80GB cluster
- Kubernetes with KServe for auto-scaling
- AWQ/FP8 quantization for memory optimization

### 5.3 Why Qwen3-VL for Container Over Others

1. **Best document understanding accuracy** among open models (94.5+ DocVQA ANLS)
2. **Native structured output** support — critical for compliance rule evaluation
3. **vLLM first-class support** — production-tested container deployment
4. **8B → 72B scaling path** — start small on a single GPU, scale up to cluster
5. **Strong at fine-grained visual tasks**: handwriting distinction, layout understanding, table structure
6. **Active community and rapid iteration** — most frequently updated document VLM

---

## 6. Architecture Design

### 6.1 High-Level Architecture

```
                                    ┌─────────────────────────┐
                                    │     PDF Upload          │
                                    └──────────┬──────────────┘
                                               │
                                    ┌──────────▼──────────────┐
                                    │   Document Processing   │
                                    │   (LangGraph Workflow)  │
                                    └──────────┬──────────────┘
                                               │
                              ┌────────────────┼────────────────┐
                              │                │                │
                   ┌──────────▼────────┐  ┌────▼─────┐  ┌──────▼──────────┐
                   │  OCR Extraction   │  │ Quality  │  │ Page Image      │
                   │  (Azure DI/       │  │  Gate    │  │ Extraction      │
                   │   Marker)         │  │          │  │ (NEW)           │
                   └──────────┬────────┘  └──────────┘  └──────┬──────────┘
                              │                                │
                              │                    ┌───────────▼──────────┐
                              │                    │  Image Store         │
                              │                    │  (filesystem/blob)   │
                              │                    └───────────┬──────────┘
                              │                                │
                   ┌──────────▼────────────────────────────────▼──────────┐
                   │              Compliance Evaluation Pipeline          │
                   │  ┌───────────────┐  ┌────────────────────────────┐   │
                   │  │ Text-only     │  │ Vision-augmented           │   │
                   │  │ Evaluator     │  │ Evaluator (NEW)            │   │
                   │  │ (existing     │  │                            │   │
                   │  │  LLM flow)    │  │  ┌─────────┐ ┌─────────┐  │   │
                   │  │               │  │  │ Gemini  │ │ Qwen3   │  │   │
                   │  │               │  │  │ Adapter │ │ -VL     │  │   │
                   │  │               │  │  │ (API)   │ │ Adapter │  │   │
                   │  │               │  │  │         │ │ (vLLM)  │  │   │
                   │  │               │  │  └─────────┘ └─────────┘  │   │
                   │  └───────────────┘  └────────────────────────────┘   │
                   └──────────────────────────────────────────────────────┘
```

### 6.2 Design Principles

1. **Port-Adapter Pattern** — VLM service is a new port with pluggable provider adapters
2. **Opt-in Per Rule** — Rules declare their `evaluation_strategy` (`text` / `vision` / `text_and_vision`)
3. **Graceful Degradation** — If VLM is unavailable, vision rules fall back to current text-only behavior with a `vlm_unavailable` trace tag
4. **Image-Once, Use-Many** — Page images are rendered once during processing and stored; compliance evaluation reads them from storage
5. **Parallel Evaluation** — Text and vision evaluations can run concurrently for the same page
6. **Provider Agnostic** — Same evaluation logic regardless of Gemini, Qwen3-VL, or future providers

---

## 7. VLM Service Port & Adapters

### 7.1 VLM Provider Port

New file: `backend/app/core/ports/vlm.py`

```python
from __future__ import annotations
from typing import Protocol
from pydantic import BaseModel


class VLMProvider(Protocol):
    """Port for Vision-Language Model inference."""

    async def analyze_image(
        self,
        image: bytes,
        prompt: str,
        *,
        system: str | None = None,
        mime_type: str = "image/png",
    ) -> str:
        """Analyze an image with a text prompt, returning free-text response."""
        ...

    async def analyze_image_structured(
        self,
        image: bytes,
        prompt: str,
        schema: type[BaseModel],
        *,
        system: str | None = None,
        mime_type: str = "image/png",
    ) -> BaseModel:
        """Analyze an image with structured output conforming to a Pydantic schema."""
        ...

    async def analyze_multi_image(
        self,
        images: list[tuple[bytes, str]],
        prompt: str,
        schema: type[BaseModel],
        *,
        system: str | None = None,
    ) -> BaseModel:
        """Analyze multiple images (for cross-page visual checks).

        Each tuple is (image_bytes, mime_type).
        """
        ...

    def supports_structured_output(self) -> bool:
        """Whether this provider natively supports JSON schema / structured output."""
        ...

    def max_image_resolution(self) -> tuple[int, int]:
        """Maximum image resolution (width, height) this provider accepts."""
        ...
```

### 7.2 Gemini Adapter

New file: `backend/app/adapters/vlm/gemini.py`

- Uses `google-generativeai` SDK
- Configurable model name (default: `gemini-2.5-pro`)
- Supports native structured output via `response_mime_type="application/json"` + `response_schema`
- Rate limiting via `VLMConfig.max_rpm` / `max_concurrent`
- Retry with exponential backoff on 429/503
- Image preprocessing: resize to max 3072x3072 if needed, convert to PNG

### 7.3 vLLM / OpenAI-Compatible Adapter

New file: `backend/app/adapters/vlm/vllm_openai.py`

- Uses `openai` SDK (AsyncOpenAI) pointing at vLLM's OpenAI-compatible server
- Base URL from config (e.g., `http://vlm-service:8000/v1`)
- Sends images as base64-encoded `image_url` in chat messages
- Structured output via `response_format={"type": "json_schema", "json_schema": {...}}`
- Works with any vLLM-hosted VLM: Qwen3-VL, InternVL3, Gemma 3, etc.

### 7.4 Azure AI Vision Adapter (Future)

New file: `backend/app/adapters/vlm/azure_vision.py`

- For Azure-hosted VLMs via Azure ML Managed Online Endpoints
- Same interface, different auth (Azure AD / API key)
- Placeholder for when Azure offers first-party VLM containers

---

## 8. Page Image Pipeline

### 8.1 Image Extraction & Storage

Page images must be extracted from the PDF and stored for later VLM analysis.

**Where it happens**: New workflow node in `backend/app/workflow/nodes.py`

```python
async def extract_page_images(state: dict) -> dict:
    """Render PDF pages to PNG images and store them.

    Runs in parallel with OCR extraction (independent).
    Uses pypdfium2 (already a dependency for quality gate).
    """
```

**Storage layout**:
```
data/documents/{doc_id}/
  ├── *.pdf                    # Original upload (existing)
  ├── result.json              # Extraction results (existing)
  ├── page_images/             # NEW
  │   ├── page_001.png
  │   ├── page_002.png
  │   └── ...
  └── compliance_report.json   # Compliance results (existing)
```

**Rendering parameters**:
- Scale factor: 2.0x (matching quality gate) → ~1700x2400 for A4
- Format: PNG (lossless, VLM-friendly)
- Color: RGB (for ink color detection)
- Optional downscale to `VLMConfig.max_image_width` before sending to API

### 8.2 Image API Endpoint

New route: `GET /api/documents/{doc_id}/pages/{page_num}/image`

```python
@router.get("/{doc_id}/pages/{page_num}/image")
async def get_page_image(doc_id: str, page_num: int):
    """Serve a rendered page image for VLM analysis or frontend display."""
```

This endpoint is dual-purpose:
1. Backend VLM evaluator fetches images from storage (no HTTP call needed)
2. Frontend can display page images for visual HITL review

### 8.3 Lazy vs Eager Rendering

**Option A — Eager (Recommended)**: Render all pages during document processing, before compliance evaluation. Cost: ~0.5s/page for rendering + ~50KB/page for PNG storage. For a typical 50-page batch record: ~25s rendering time, ~2.5MB storage.

**Option B — Lazy**: Render on-demand when a vision rule needs it. Saves storage but adds latency during compliance evaluation and requires PDF access during eval.

**Recommendation**: Eager rendering. The cost is negligible (pypdfium2 is fast, PNG storage is small), and it decouples image availability from PDF file access.

---

## 9. Compliance Evaluator Integration

### 9.1 Rule Evaluation Strategy

Add a new field to `AuditRule` and rule YAML:

```yaml
# New field in rule YAML
evaluation_strategy: text           # text | vision | text_and_vision
visual_checks: []                   # List of VC-* check IDs this rule needs
```

**Evaluation strategies**:

| Strategy | Behavior |
|----------|---------|
| `text` (default) | Current behavior — OCR text + metadata only |
| `vision` | Page image sent to VLM only — no text LLM call for this rule |
| `text_and_vision` | Both text LLM and VLM evaluate; results merged with vision taking precedence for visual aspects |

### 9.2 Updated Rule YAML Examples

```yaml
# alcoa_rules.yaml — rule 6 (strikethrough)
6:
  evaluation_strategy: vision
  visual_checks: [VC-STRIKE]
  pass_criteria: >
    Single-line strikethrough used for corrections. Original text must remain
    readable beneath the strikethrough. Corrections must include initials and
    date next to the struck-through text. Multiple strikethrough lines,
    scribbling out, or rendering original text illegible is non-compliant.

# alcoa_rules.yaml — rule 16 (smudges/damage)
16:
  evaluation_strategy: vision
  visual_checks: [VC-DOC-QUALITY]
  pass_criteria: >
    Page must be free of smudges, fading, water damage, tears, or pencil marks
    that impair readability of any GMP-critical data. Minor cosmetic marks
    that do not affect legibility are acceptable.

# alcoa_rules.yaml — rule 1 (signature attribution — enhanced)
1:
  evaluation_strategy: text_and_vision
  visual_checks: [VC-SIGNATURE]
  pass_criteria: >
    Each entry has clear initials or signature and date. For vision-augmented
    evaluation: verify that signature columns contain actual handwritten marks
    (wet signatures) rather than typed placeholder text.
```

### 9.3 Vision Evaluator

New file: `backend/app/compliance/vision_evaluator.py`

The vision evaluator parallels `RuleBatchEvaluator` but sends page images to the VLM.

```python
class VisionBatchEvaluator:
    """Evaluates vision-tagged rules against page images via VLM."""

    async def evaluate_batch(
        self,
        batch: RuleBatch,
        page_image: bytes,
        page_num: int,
        vlm: VLMProvider,
        text_context: str | None = None,
        section_info: dict | None = None,
    ) -> tuple[str, int, RuleBatchResult]:
        """Evaluate visual rules against a page image.

        text_context: Optional OCR text for text_and_vision rules (gives
        the VLM both image AND text for richer analysis).
        """
```

**Vision system prompts** — domain-specific for pharmaceutical document inspection:

```python
_VISION_SYSTEM_PROMPT = (
    "You are a GxP-trained visual document inspector for pharmaceutical "
    "batch production records. You analyze scanned page images to detect "
    "visual compliance issues that OCR-based text analysis cannot identify.\n\n"
    "Your visual analysis capabilities include:\n"
    "1. STRIKETHROUGH: Detect single-line, double-line, or scribble "
    "corrections. Verify original text readability beneath.\n"
    "2. SIGNATURES: Distinguish wet handwritten signatures from typed text, "
    "rubber stamps, or photocopied signatures.\n"
    "3. INK COLOR: Identify blue, black, red ink or pencil marks.\n"
    "4. CORRECTION ARTIFACTS: Detect white-out/correction fluid, erasure "
    "marks, overwritten text layers.\n"
    "5. STAMPS & SEALS: Identify official stamps, watermarks, controlled-copy "
    "markers.\n"
    "6. PHYSICAL CONDITION: Assess smudges, fading, tears, water damage.\n"
    "7. ATTACHMENTS: Detect stapled/taped additions, overlapping documents.\n"
    "8. BARCODES: Verify barcode print quality and readability.\n"
    "9. CHARTS & SPECTRA: Verify axis labels, scales, and data integrity.\n"
    "10. BLANK FIELDS: Distinguish truly empty fields from filled ones.\n"
    "11. CHECKBOXES: Verify check/tick marks in selection boxes.\n"
    "12. PAGE LAYOUT: Verify pagination, headers, footers from visual layout."
)
```

### 9.4 Orchestration Changes

In `run_agent_evaluation()`, after existing applicability gating:

```python
# Separate rules by evaluation strategy
text_rules = [r for r in applicable_rules if r.evaluation_strategy in ("text", "text_and_vision")]
vision_rules = [r for r in applicable_rules if r.evaluation_strategy in ("vision", "text_and_vision")]

# Run in parallel
text_task = text_evaluator.evaluate_batch(...) if text_rules else None
vision_task = vision_evaluator.evaluate_batch(...) if vision_rules else None

text_result, vision_result = await asyncio.gather(text_task, vision_task)

# Merge results: for text_and_vision rules, combine evidence from both
merged = merge_text_and_vision_results(text_result, vision_result)
```

### 9.5 Result Merging Strategy

For `text_and_vision` rules that get results from both evaluators:

1. **Status**: Use the **more severe** status (vision finding of `non_compliant` overrides text finding of `compliant`)
2. **Confidence**: Use the **lower** confidence (conservative)
3. **Evidence**: Concatenate both evidence blocks with `[TEXT]` / `[VISION]` prefixes
4. **Reasoning**: Concatenate with channel prefixes
5. **New field**: `evaluation_channels: ["text", "vision"]` on the finding

### 9.6 Graceful Degradation

If VLM is not configured or unavailable:

1. Vision-only rules → `status: "not_applicable"` with `applicability_trace: ["vlm_unavailable"]`
2. Text_and_vision rules → fall back to text-only evaluation
3. A warning is logged and an `evaluation_coverage` metric is emitted showing how many rules could not be visually evaluated

---

## 10. Configuration & Settings

### 10.1 New Settings Model

```python
class VLMConfig(BaseModel):
    """Vision Language Model provider settings."""

    enabled: bool = False
    provider: str = "gemini"             # "gemini" | "vllm" | "azure_vision"

    # Gemini settings
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-pro"
    gemini_project: str = ""             # For Vertex AI

    # vLLM / OpenAI-compatible settings
    vllm_base_url: str = "http://localhost:8100"
    vllm_model: str = "Qwen/Qwen3-VL-8B-Instruct"
    vllm_api_key: str = ""               # Optional auth token

    # Azure Vision settings
    azure_vision_endpoint: str = ""
    azure_vision_api_key: str = ""
    azure_vision_deployment: str = ""

    # Image processing
    max_image_width: int = 2048          # Resize before sending to API
    max_image_height: int = 2048
    image_format: str = "png"            # "png" | "jpeg"
    jpeg_quality: int = 95               # Only if format is jpeg

    # Rate limiting
    max_rpm: int = 60
    max_concurrent: int = 5

    # Rendering
    render_scale: float = 2.0            # pypdfium2 render scale
    store_page_images: bool = True       # Persist rendered PNGs
```

### 10.2 AppSettings Integration

```python
class AppSettings(BaseSettings):
    # ... existing fields ...
    vlm: VLMConfig = Field(default_factory=VLMConfig)
```

### 10.3 Environment Variables

```ini
# ── VLM (Vision Language Model) ─────────────────────────────
AT_VLM__ENABLED=true
AT_VLM__PROVIDER=gemini

# Gemini (cloud API — Phase 1)
AT_VLM__GEMINI_API_KEY=your-gemini-api-key
AT_VLM__GEMINI_MODEL=gemini-2.5-pro

# vLLM (container — Phase 2)
# AT_VLM__PROVIDER=vllm
# AT_VLM__VLLM_BASE_URL=http://vlm-service:8000/v1
# AT_VLM__VLLM_MODEL=Qwen/Qwen3-VL-8B-Instruct

# Image processing
# AT_VLM__MAX_IMAGE_WIDTH=2048
# AT_VLM__MAX_IMAGE_HEIGHT=2048
# AT_VLM__RENDER_SCALE=2.0

# Rate limiting
# AT_VLM__MAX_RPM=60
# AT_VLM__MAX_CONCURRENT=5
```

### 10.4 Compliance Config Extension

```python
class ComplianceConfig(BaseModel):
    # ... existing fields ...

    # Vision evaluation settings
    vlm_evaluation_enabled: bool = True     # Master switch for vision rules
    vlm_batch_size: int = 5                 # Rules per VLM call
    vlm_timeout: int = 180                  # Per-call timeout (vision is slower)
    vlm_fallback_to_text: bool = True       # Fall back to text if VLM fails
    vlm_confidence_boost: float = 0.1       # Boost confidence when vision confirms text
```

---

## 11. Infrastructure & Container Deployment

### 11.1 Docker Compose Extension

```yaml
# docker-compose.vlm.yaml — overlay for VLM service
services:
  vlm-service:
    image: vllm/vllm-openai:latest
    runtime: nvidia
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    volumes:
      - vlm-model-cache:/root/.cache/huggingface
    ports:
      - "8200:8000"
    command: >
      --model Qwen/Qwen3-VL-8B-Instruct
      --trust-remote-code
      --gpu-memory-utilization 0.90
      --max-model-len 8192
      --enable-chunked-prefill
    environment:
      - VLLM_ATTENTION_BACKEND=FLASHINFER
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 120s

volumes:
  vlm-model-cache:
```

Usage:
```bash
# Development (Gemini API — no GPU needed)
make dev

# Development with local VLM (requires NVIDIA GPU)
docker compose -f docker-compose.yml -f docker-compose.vlm.yaml up -d
```

### 11.2 Production Kubernetes

```yaml
# k8s/vlm-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vlm-service
spec:
  replicas: 1
  template:
    spec:
      containers:
        - name: vllm
          image: vllm/vllm-openai:v0.8.x
          args:
            - "--model"
            - "Qwen/Qwen3-VL-8B-Instruct"
            - "--trust-remote-code"
            - "--gpu-memory-utilization"
            - "0.90"
          resources:
            limits:
              nvidia.com/gpu: 1
              memory: 32Gi
            requests:
              nvidia.com/gpu: 1
              memory: 24Gi
          ports:
            - containerPort: 8000
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 120
```

### 11.3 GPU Requirements by Model

| Model | GPU Requirement | VRAM | Quantization | Throughput |
|-------|----------------|------|-------------|-----------|
| Qwen3-VL-8B | 1x A10G (24GB) | ~18GB | None needed | ~15 img/min |
| Qwen3-VL-8B-AWQ | 1x T4 (16GB) | ~10GB | AWQ 4-bit | ~10 img/min |
| Qwen3-VL-72B | 4x A100 (80GB) | ~150GB | FP16 | ~5 img/min |
| Qwen3-VL-72B-AWQ | 2x A100 (80GB) | ~40GB | AWQ 4-bit | ~8 img/min |
| InternVL3-78B | 4x A100 (80GB) | ~160GB | FP16 | ~4 img/min |
| Gemma 3-27B | 1x A100 (40GB) | ~30GB | FP16 | ~12 img/min |

### 11.4 Model Download & Caching

- Models are downloaded from HuggingFace on first container start
- Volume mount (`vlm-model-cache`) persists downloads across restarts
- For air-gapped environments: pre-download model to shared storage, mount as read-only volume
- Estimated download size: Qwen3-VL-8B ~16GB, Qwen3-VL-72B ~145GB

---

## 12. Frontend Enhancements

### 12.1 Page Image in Compliance Findings

Currently, compliance findings link to the review page via `window.open(/review?doc=...&page=...)`. With VLM visual evidence, the findings table should:

1. Show a **thumbnail** of the page image inline in the finding row
2. Display **visual annotations** from the VLM (bounding boxes for detected issues)
3. Indicate `[VISION]` vs `[TEXT]` evidence channel

### 12.2 Visual Evidence Overlay

New component: `frontend/src/components/compliance/visual-evidence-overlay.tsx`

- Renders the page image with highlighted regions where visual issues were detected
- Uses the same `highlightRect` pattern as `pdf-viewer-inner.tsx`
- VLM findings include `visual_regions: [{x, y, width, height, label}]` in their evidence

### 12.3 HITL Review with Visual Context

When reviewing a vision-flagged finding:
- Show the page image alongside the finding details
- Highlight the specific region the VLM flagged
- Allow reviewer to zoom into the region
- Add "Visual evidence" tab to the finding detail modal

### 12.4 Evaluation Channel Indicators

In the findings table, add a column/badge showing evaluation source:
- `TEXT` — evaluated by text LLM only
- `VISION` — evaluated by VLM only
- `TEXT+VISION` — evaluated by both, merged result
- `VLM_UNAVAILABLE` — VLM was not available, text-only fallback

---

## 13. Migration & Rollout Plan

### Phase 1 — Foundation (COMPLETED)

| Task | Description | Files | Status |
|------|------------|-------|--------|
| 1.1 | Create `VLMProvider` port | `core/ports/vlm.py` | Done |
| 1.2 | Create `VLMConfig` settings | `config/settings.py` | Done |
| 1.3 | Wire VLM into Container | `config/container.py` | Done |
| 1.4 | Page image extraction / loader | `compliance/page_image_loader.py` | Done |
| 1.5 | Page image storage (filesystem) | On-demand render via pypdfium2 | Done |
| 1.6 | Page image API endpoint | `api/routes/documents.py` — `GET /{doc_id}/pages/{page_num}/image` | Done |
| 1.7 | Add `evaluation_strategy` to AuditRule | `compliance/rules/registry.py` | Done |
| 1.8 | Update `.env.example` with VLM config | `.env.example` | Done |

### Phase 2 — Gemini Integration (COMPLETED)

| Task | Description | Files | Status |
|------|------------|-------|--------|
| 2.1 | Gemini VLM adapter | `adapters/vlm/gemini.py` | Done |
| 2.2 | Vision evaluator (parallel to text evaluator) | `compliance/vision_evaluator.py` | Done |
| 2.3 | Vision-aware context builder | `compliance/context_builder.py` | Done |
| 2.4 | Update rule YAML with evaluation_strategy tags | `compliance/rules/alcoa_rules.yaml`, `gmp_rules.yaml`, `checklist_rules.yaml` | Done |
| 2.5 | Orchestrator integration (parallel text+vision) | `compliance/evaluator.py` | Done |
| 2.6 | Result merger (text + vision) | Integrated in `compliance/evaluator.py` via `_merge_text_vision` | Done |
| 2.7 | Vision system prompts per visual check | `compliance/vision_evaluator.py` | Done |
| 2.8 | Graceful degradation (VLM unavailable) | `compliance/evaluator.py` | Done |

### Phase 3 — Container VLM (PARTIAL — adapter done, infra deferred)

| Task | Description | Files | Status |
|------|------------|-------|--------|
| 3.1 | vLLM/OpenAI-compatible adapter | `adapters/vlm/vllm_openai.py` | Done |
| 3.2 | Docker compose overlay | `docker-compose.vlm.yaml` | Deferred — no GPU available in dev |
| 3.3 | Health check and readiness probe | Built into adapter | Done |
| 3.4 | Model download / cache management | Documentation + Makefile | Deferred |
| 3.5 | Provider switching (Gemini <-> vLLM) via config | `config/container.py` | Done |

### Phase 4 — Frontend & HITL (COMPLETED)

| Task | Description | Files | Status |
|------|------------|-------|--------|
| 4.1 | Page image display in findings (inline viewer + thumbnail) | `components/compliance/findings-table.tsx` | Done |
| 4.2 | Visual evidence viewer dialog with region overlays and zoom | `components/compliance/visual-evidence-viewer.tsx` | Done |
| 4.3 | HITL review with visual context (VLM findings indicator in page divider) | `components/review/review-interface.tsx`, `app/review/page.tsx` | Done |
| 4.4 | Evaluation channel badges (TEXT / VLM / TEXT+VLM) in collapsed finding row | `components/compliance/findings-table.tsx` | Done |
| 4.5 | Page image API client (`getPageImageUrl`) | `lib/api.ts` | Done |

### Phase 5 — Optimization & Hardening (DEFERRED)

| Task | Description | Status |
|------|------------|--------|
| 5.1 | Fine-tune visual prompts based on real batch records | Deferred |
| 5.2 | Add VLM confidence calibration | Deferred |
| 5.3 | Performance benchmarking (throughput, latency, accuracy) | Deferred |
| 5.4 | Cost monitoring dashboards | Deferred |
| 5.5 | Integration tests with mock VLM | Deferred |
| 5.6 | E2E tests with real batch record PDFs | Deferred |

---

## 14. Testing Strategy

### 14.1 Unit Tests

```
tests/unit/
  test_vlm_port.py                    # VLMProvider protocol conformance
  test_vision_evaluator.py            # Vision batch evaluator logic
  test_result_merger.py               # Text + vision merge logic
  test_page_image_extraction.py       # Image rendering and storage
  test_rule_evaluation_strategy.py    # Strategy routing (text/vision/both)
```

### 14.2 Integration Tests

```
tests/integration/
  test_gemini_adapter.py              # Real Gemini API calls (requires key)
  test_vllm_adapter.py                # Real vLLM container calls
  test_vision_compliance_pipeline.py  # Full pipeline with VLM
```

### 14.3 Visual Ground Truth Dataset

Create a curated set of annotated batch record pages:

| Test Case | Page Content | Expected Visual Findings |
|-----------|-------------|------------------------|
| `strike_single_line.png` | Single-line strikethrough with initials | `VC-STRIKE: compliant` |
| `strike_scribble.png` | Scribbled-out text, illegible | `VC-STRIKE: non_compliant` |
| `signature_wet.png` | Handwritten wet signature | `VC-SIGNATURE: compliant` |
| `signature_typed.png` | Typed name in signature field | `VC-SIGNATURE: non_compliant` (minor) |
| `whiteout.png` | Correction fluid applied | `VC-CORRECTION: non_compliant` (critical) |
| `ink_pencil.png` | Pencil entries in form | `VC-INK-COLOR: non_compliant` |
| `ink_blue.png` | Blue ink entries | `VC-INK-COLOR: compliant` |
| `stamp_original.png` | "ORIGINAL" stamp visible | `VC-STAMP-SEAL: compliant` |
| `barcode_clear.png` | Clear, scannable barcode | `VC-BARCODE: compliant` |
| `barcode_smudged.png` | Smudged, unscannable barcode | `VC-BARCODE: non_compliant` |

---

## 15. Cost & Performance Estimates

### 15.1 Gemini API Costs (Phase 1)

| Metric | Value |
|--------|-------|
| Input token cost | ~$1.25 / 1M tokens |
| Output token cost | ~$5.00 / 1M tokens |
| Image tokens per page (~2000x2800 PNG) | ~3,000–5,000 tokens |
| Prompt + schema tokens per call | ~1,500 tokens |
| Output tokens per call | ~500 tokens |
| **Cost per page (vision eval)** | **~$0.008–0.012** |
| 50-page batch record | **~$0.40–0.60** |
| 100 documents/month | **~$40–60/month** |

### 15.2 vLLM Container Costs (Phase 2)

| Resource | Cost |
|----------|------|
| A10G GPU instance (AWS g5.xlarge) | ~$1.00/hour |
| Throughput (Qwen3-VL-8B) | ~15 pages/minute |
| 50-page batch record | ~3.3 minutes |
| **Monthly cost (always-on)** | **~$730/month** |
| **Monthly cost (scale-to-zero, 100 docs)** | **~$6–10/month** |

### 15.3 Latency Impact

| Component | Current | With VLM |
|-----------|---------|---------|
| Page OCR extraction | ~2s/page | ~2s/page (unchanged) |
| Image rendering | N/A | +0.5s/page (parallel with OCR) |
| Text compliance eval (per batch) | ~3s | ~3s (unchanged) |
| Vision compliance eval (per page) | N/A | +5-8s/page (Gemini), +3-5s/page (local vLLM) |
| **Total for 50-page doc** | ~8-10 min | ~12-15 min (+40-50%) |

---

## 16. Open Questions

### 16.1 Architecture Decisions Needed

1. **Vision batch size**: Should vision rules be batched (multiple rules per VLM call) or evaluated individually? Batching reduces API calls but increases prompt complexity. **Recommendation**: Start with batching (5 rules per call), same as text evaluator.

2. **Image resolution vs cost tradeoff**: Higher resolution improves accuracy but increases token count (cost and latency). **Recommendation**: 2048x2048 default with configurable override.

3. **Cross-page visual checks**: Some rules (e.g., pagination continuity) need images from multiple pages. Should these go through the cross-page evaluator? **Recommendation**: Yes, extend cross-page evaluator with multi-image VLM support.

4. **VLM output anchoring**: Should VLM responses include bounding boxes for detected issues? This enables visual evidence overlays but requires VLM to output coordinates. **Recommendation**: Yes, include optional `regions` in VLM output schema.

### 16.2 Model Selection Validation

Before committing to Qwen3-VL for container deployment:
- Run a benchmark using 20 annotated batch record pages
- Compare Qwen3-VL-8B vs Gemini 2.5 Pro accuracy on all 14 visual checks
- Measure latency, token usage, and structured output reliability
- Validate on pages with Indian-language text (Hindi/Gujarati labels sometimes appear)

### 16.3 Regulatory Considerations

- Does the use of cloud VLM (Gemini) for GMP document analysis require qualification/validation under 21 CFR Part 11?
- Should VLM-generated findings carry a different confidence weighting than text-based findings?
- Do visual evidence images need to be retained in the audit trail?

---

## Appendix A — File Change Inventory

| File | Change Type | Description |
|------|------------|-------------|
| `backend/app/core/ports/vlm.py` | **New** | VLMProvider protocol definition |
| `backend/app/adapters/vlm/__init__.py` | **New** | VLM adapter package |
| `backend/app/adapters/vlm/gemini.py` | **New** | Google Gemini adapter |
| `backend/app/adapters/vlm/vllm_openai.py` | **New** | vLLM/OpenAI-compatible adapter |
| `backend/app/adapters/vlm/azure_vision.py` | **New** | Azure Vision adapter (stub) |
| `backend/app/compliance/vision_evaluator.py` | **New** | Vision batch evaluator |
| `backend/app/compliance/result_merger.py` | **New** | Text + vision result merger |
| `backend/app/config/settings.py` | **Modify** | Add `VLMConfig` |
| `backend/app/config/container.py` | **Modify** | Wire VLM provider |
| `backend/app/workflow/nodes.py` | **Modify** | Add page image extraction node |
| `backend/app/compliance/evaluator.py` | **Modify** | Parallel text+vision orchestration |
| `backend/app/compliance/context_builder.py` | **Modify** | Support image attachment |
| `backend/app/compliance/rules/registry.py` | **Modify** | Add `evaluation_strategy` to AuditRule |
| `backend/app/compliance/rules/alcoa_rules.yaml` | **Modify** | Tag 17 rules with vision strategy |
| `backend/app/compliance/rules/gmp_rules.yaml` | **Modify** | Tag 4 rules with vision strategy |
| `backend/app/compliance/rules/checklist_rules.yaml` | **Modify** | Tag 10 rules with vision strategy |
| `backend/app/api/routes/documents.py` | **Modify** | Add page image endpoint |
| `backend/app/compliance/models.py` | **Modify** | Add visual evidence fields to findings |
| `backend/.env.example` | **Modify** | Add VLM configuration section |
| `backend/docker-compose.yml` | **Modify** | Add vlm-service (or separate overlay) |
| `frontend/src/lib/api.ts` | **Modify** | Add page image URL helper |
| `frontend/src/types/compliance.ts` | **Modify** | Add visual evidence types |
| `frontend/src/components/compliance/findings-table.tsx` | **Modify** | Visual evidence display |
| `frontend/src/components/compliance/visual-evidence-overlay.tsx` | **New** | Visual evidence overlay component |
| `Makefile` | **Modify** | Add VLM-related targets |

---

## Appendix B — Visual Check Prompt Templates

Each `VC-*` check has a specialized prompt template optimized for the specific visual analysis task. These are composed into the batch prompt based on which `visual_checks` a rule declares.

### VC-STRIKE — Strikethrough Detection

```
Analyze this page image for correction methodology compliance.

For each correction visible on the page:
1. Is it a SINGLE-LINE strikethrough (GMP-compliant)?
2. Or is it a scribble, multiple lines, or heavy crossing-out (non-compliant)?
3. Is the original text still READABLE beneath the correction?
4. Are there INITIALS and a DATE adjacent to the correction?

Report each correction found with its location (top/middle/bottom of page),
type (single-line/double-line/scribble/other), original text readability
(readable/partially-readable/illegible), and whether initials+date are present.
```

### VC-SIGNATURE — Signature Classification

```
Analyze this page image for signature field compliance.

For each area that appears to be a signature or identity field:
1. Does it contain a HANDWRITTEN signature (wet ink mark)?
2. Or is it TYPED text (e.g., a printed name)?
3. Or is it a RUBBER STAMP impression?
4. Or is it EMPTY/BLANK?
5. Is there an accompanying DATE?

A wet handwritten signature or handwritten initials = compliant.
Typed text alone in a signature field = observation (may need policy review).
Empty signature field where one is required = non-compliant.
```

### VC-INK-COLOR — Ink Color Classification

```
Analyze this page image for ink color compliance.

For handwritten entries visible on this page:
1. What COLOR ink was used? (blue/black/red/green/pencil/other)
2. Are there any entries made in PENCIL (graphite)?
3. Are there entries in non-standard colors (red, green) for non-annotation purposes?

Per GMP requirements:
- Blue or black ink = compliant
- Pencil (graphite) = non-compliant (not permanent)
- Red ink for annotations/corrections only = acceptable
- Red ink for primary entries = observation
```

### VC-CORRECTION — Correction Fluid / Erasure Detection

```
Analyze this page image for prohibited correction methods.

Look for evidence of:
1. WHITE-OUT / CORRECTION FLUID (opaque white patches covering text)
2. ERASURE marks (rubbed/smudged areas, especially on handwritten text)
3. OVERWRITING (new text written directly over old text without strikethrough)
4. TAPE corrections (transparent or opaque tape covering original entries)

Any of these correction methods is a CRITICAL non-compliance finding
in GMP-regulated documents.
```

---

## Appendix C — Structured Output Schema for VLM

```python
class VisualRegion(BaseModel):
    """A region of the page image where a visual finding was detected."""
    x: float           # Normalized 0.0-1.0 from left
    y: float           # Normalized 0.0-1.0 from top
    width: float       # Normalized 0.0-1.0
    height: float      # Normalized 0.0-1.0
    label: str         # e.g., "strikethrough", "signature", "whiteout"

class VisualCheckResult(BaseModel):
    """Result of a single visual check on a page."""
    check_id: str                        # VC-STRIKE, VC-SIGNATURE, etc.
    detected: bool                       # Was the visual element found?
    classification: str                  # e.g., "single_line", "scribble", "wet_signature"
    confidence: float                    # 0.0-1.0
    description: str                     # What was found
    regions: list[VisualRegion] = []     # Where on the page

class VisionRuleEvaluation(BaseModel):
    """VLM evaluation of a single compliance rule."""
    rule_id: str
    status: str                          # compliant | non_compliant | not_applicable | uncertain
    confidence: float
    severity: str = ""                   # critical | major | minor | observation
    reasoning: str
    visual_evidence: str                 # Description of what was visually observed
    visual_checks: list[VisualCheckResult] = []
    regions: list[VisualRegion] = []     # Aggregated regions for this rule

class VisionBatchResult(BaseModel):
    """VLM evaluation results for a batch of rules on one page."""
    evaluations: list[VisionRuleEvaluation]
    page_quality_notes: str = ""         # Overall page quality observations
```
