"""Quick validation of Data Lab API output against our adapter logic.

Sends 3 representative pages (cover, revision history, raw materials table)
to the Data Lab Convert + Extract APIs and validates the response structure.

Secrets are loaded from .env — nothing is printed to stdout beyond results.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


PDF_PATH = str(
    Path(__file__).resolve().parents[2]
    / "context"
    / "2538104192 1"
    / "2538104192-EHSII03.pdf"
)

PAGES_TO_TEST = "0-2"


async def test_convert(client) -> str | None:
    """Test 1: Convert API (markdown + paginate)."""
    from datalab_sdk import ConvertOptions

    print("=" * 60)
    print("TEST 1: Convert API (markdown + paginate)")
    print("=" * 60)

    opts = ConvertOptions(
        mode="accurate",
        paginate=True,
        extras="new_block_types,table_row_bboxes,chart_understanding",
        page_range=PAGES_TO_TEST,
        save_checkpoint=True,
    )
    opts.use_llm = True

    result = await client.convert(
        file_path=PDF_PATH,
        options=opts,
        max_polls=300,
        poll_interval=2.0,
    )

    md = getattr(result, "markdown", "") or ""
    images = getattr(result, "images", {}) or {}
    quality = getattr(result, "parse_quality_score", None)
    checkpoint_id = getattr(result, "checkpoint_id", None)
    runtime = getattr(result, "runtime", None)

    print(f"  Markdown length:     {len(md)} chars")
    print(f"  Images returned:     {len(images)}")
    print(f"  Quality score:       {quality}")
    print(f"  Checkpoint ID:       {'(present)' if checkpoint_id else '(none)'}")
    print(f"  Runtime:             {runtime}s")

    separator = "\n\n---\n\n"
    pages = md.split(separator) if separator in md else [md]
    print(f"  Pages in markdown:   {len(pages)}")
    for i, p in enumerate(pages):
        preview = p.strip()[:120].replace("\n", " ")
        print(f"    Page {i}: {len(p)} chars — {preview}...")

    has_tables = "|" in md and "---" in md
    has_bold = "**" in md or "<b>" in md
    has_headers = any(md.startswith(f"{'#' * i} ") for i in range(1, 5)) or "\n# " in md or "\n## " in md
    print(f"\n  Content analysis:")
    print(f"    Tables:            {has_tables}")
    print(f"    Bold formatting:   {has_bold}")
    print(f"    Headers:           {has_headers}")

    out_dir = Path(__file__).parent / "datalab_output"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "convert_markdown.md").write_text(md, encoding="utf-8")
    print(f"\n  Saved full markdown to: {out_dir / 'convert_markdown.md'}")

    return checkpoint_id


async def test_extract(client, checkpoint_id: str | None) -> None:
    """Test 2: Extract API (structured KV extraction)."""
    print()
    print("=" * 60)
    print("TEST 2: Extract API (structured KV extraction)")
    print("=" * 60)

    from app.adapters.ocr.datalab import _load_extraction_schema

    schema = _load_extraction_schema("bpr_core")
    n_props = len(schema.get("properties", {}))
    print(f"  Schema loaded:       bpr_core ({n_props} top-level properties)")

    from datalab_sdk.models import ExtractOptions

    if checkpoint_id:
        ext_opts = ExtractOptions(
            mode="accurate",
            page_schema=json.dumps(schema),
            save_checkpoint=True,
            checkpoint_id=checkpoint_id,
        )
        result = await client.extract(
            options=ext_opts,
            max_polls=300,
            poll_interval=2.0,
        )
    else:
        ext_opts = ExtractOptions(
            mode="accurate",
            page_schema=json.dumps(schema),
            save_checkpoint=True,
            page_range=PAGES_TO_TEST,
        )
        result = await client.extract(
            file_path=PDF_PATH,
            options=ext_opts,
            max_polls=300,
            poll_interval=2.0,
        )

    raw = getattr(result, "extraction_schema_json", None)
    ext_json: dict = {}
    if isinstance(raw, str):
        ext_json = json.loads(raw)
    elif isinstance(raw, dict):
        ext_json = raw
    print(f"  Extract API returned: {len(ext_json)} top-level keys")

    out_dir = Path(__file__).parent / "datalab_output"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "extract_raw.json").write_text(json.dumps(ext_json, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Saved raw extract to: {out_dir / 'extract_raw.json'}")

    if ext_json:
        for key, val in ext_json.items():
            if key.startswith("_"):
                continue
            if isinstance(val, list):
                print(f"    {key}: [{len(val)} items]")
                for i, item in enumerate(val[:2]):
                    if isinstance(item, dict):
                        fields = {k: str(v)[:50] for k, v in item.items() if not k.startswith("_")}
                        print(f"      [{i}]: {fields}")
            elif isinstance(val, dict):
                fields = {k: str(v)[:50] for k, v in val.items() if not k.startswith("_")}
                print(f"    {key}: {fields}")
            else:
                display = str(val)[:80] if val else "(empty)"
                print(f"    {key}: {display}")

        from app.adapters.ocr.datalab import (
            _enrich_attestations,
            _enrich_critical_steps,
            _map_extraction_to_kv_pairs,
        )

        kv_pairs = _map_extraction_to_kv_pairs(ext_json, 1)
        kv_pairs = _enrich_attestations(kv_pairs)
        kv_pairs = _enrich_critical_steps(kv_pairs)

        att_count = sum(1 for p in kv_pairs if "Attested" in p.key)
        crit_count = sum(1 for p in kv_pairs if "Critical" in p.key)
        regular = len(kv_pairs) - att_count - crit_count
        print(f"\n  Mapped to {len(kv_pairs)} KeyValuePair objects:")
        print(f"    Regular KV pairs:  {regular}")
        print(f"    Attestation flags: {att_count}")
        print(f"    Critical flags:    {crit_count}")

        print(f"\n  Sample KV pairs (first 20):")
        for kv in kv_pairs[:20]:
            v = kv.value[:60] if kv.value else "(empty)"
            print(f"    {kv.key:50s} = {v}  [conf={kv.confidence:.2f}]")

        if att_count:
            print(f"\n  Attestation flags:")
            for kv in kv_pairs:
                if "Attested" in kv.key:
                    print(f"    {kv.key:50s} = {kv.value}")

        if crit_count:
            print(f"\n  Critical step flags (first 10):")
            shown = 0
            for kv in kv_pairs:
                if "Critical" in kv.key and shown < 10:
                    print(f"    {kv.key:50s} = {kv.value}")
                    shown += 1


async def test_full_adapter() -> None:
    """Test 3: Full DatalabOCRAdapter.extract() — 3 pages."""
    print()
    print("=" * 60)
    print("TEST 3: Full DatalabOCRAdapter.extract() — 3 pages")
    print("=" * 60)

    api_key = os.getenv("AT_DATALAB__API_KEY", "")
    base_url = os.getenv("AT_DATALAB__BASE_URL", "https://www.datalab.to")

    from app.adapters.ocr.datalab import DatalabOCRAdapter
    from app.config.settings import DatalabConfig

    config = DatalabConfig(
        api_key=api_key,
        base_url=base_url,
        timeout=300,
        mode="accurate",
        extras="new_block_types,table_row_bboxes,chart_understanding",
        use_llm=True,
        save_checkpoint=True,
        enable_extraction=True,
        extraction_schema_family="bpr_core",
        chunk_pages=50,
    )

    adapter = DatalabOCRAdapter(config)

    def _progress(pct: int, label: str) -> None:
        print(f"    [{pct:3d}%] {label}")

    ocr_result = await adapter.extract(
        PDF_PATH,
        pages=[1, 2, 3],
        progress_callback=_progress,
    )

    print(f"\n  OCRResult summary:")
    print(f"    Total pages:       {ocr_result.total_pages}")
    print(f"    Full markdown:     {len(ocr_result.full_markdown)} chars")
    print(f"    Key-value pairs:   {len(ocr_result.key_value_pairs)}")
    print(f"    Signatures:        {len(ocr_result.signatures)}")
    print(f"    Table metadata:    {len(ocr_result.table_metadata)}")

    for page in ocr_result.pages:
        hw = sum(1 for w in page.words if w.is_handwritten)
        print(f"    Page {page.page_num}: {len(page.markdown)} chars, "
              f"{len(page.words)} words ({hw} handwritten), "
              f"{len(page.selection_marks)} checkboxes, "
              f"{len(page.formulas)} formulas")

    if ocr_result.key_value_pairs:
        att = [p for p in ocr_result.key_value_pairs if "Attested" in p.key]
        crit = [p for p in ocr_result.key_value_pairs if "Critical" in p.key]
        regular = [p for p in ocr_result.key_value_pairs
                   if "Attested" not in p.key and "Critical" not in p.key]
        print(f"\n  KV pairs from adapter:")
        print(f"    Regular: {len(regular)}, Attestation: {len(att)}, Critical: {len(crit)}")
        for kv in regular[:15]:
            v = kv.value[:50] if kv.value else "(empty)"
            print(f"    {kv.key:45s} = {v}")

    if ocr_result.signatures:
        print(f"\n  Signatures:")
        for s in ocr_result.signatures[:5]:
            print(f"    Page {s.page_num}: {s.status} — {s.label[:60] if s.label else '(no label)'}")

    out_dir = Path(__file__).parent / "datalab_output"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "adapter_markdown.md").write_text(
        ocr_result.full_markdown, encoding="utf-8"
    )
    print(f"\n  Saved adapter markdown to: {out_dir / 'adapter_markdown.md'}")

    print()
    print("=" * 60)
    print("ALL TESTS COMPLETE")
    print("=" * 60)


async def main() -> None:
    from datalab_sdk import AsyncDatalabClient

    api_key = os.getenv("AT_DATALAB__API_KEY", "")
    base_url = os.getenv("AT_DATALAB__BASE_URL", "https://www.datalab.to")
    if not api_key:
        print("ERROR: AT_DATALAB__API_KEY not set in .env")
        sys.exit(1)

    print(f"PDF: {PDF_PATH}")
    print(f"Pages: {PAGES_TO_TEST} (0-indexed)")
    print(f"Base URL: {base_url}")
    print()

    client = AsyncDatalabClient(api_key=api_key, base_url=base_url, timeout=300)

    checkpoint_id = await test_convert(client)
    await test_extract(client, checkpoint_id)
    await test_full_adapter()


if __name__ == "__main__":
    asyncio.run(main())
