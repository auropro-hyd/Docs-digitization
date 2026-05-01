---
name: doc-page-classifier
description: >
  Classify pharmaceutical document pages from Azure Document Intelligence result.json output.
  For each page, identifies: document name, document type, section name, section type,
  sub-section, and page role. Pauses to confirm with the user whenever a new document type
  or section type is encountered for the first time (showing the page content). Produces two
  output files: classification.yaml (full page-by-page results) and schema_reference.yaml
  (LLM-ready pattern catalog with examples and detection hints). Use this skill whenever the
  user provides a document folder or result.json path and wants to label, classify, or
  understand the structure of pharma GMP document pages. Also trigger when the user mentions
  BPCR pages, document section labeling, page classification, or wants to build a schema from
  pharma documents.
---

# Document Page Classifier

Classify pages in pharmaceutical GMP documents by reading Azure DI `result.json` output.
Build a living schema as you go, confirming new patterns with the user.

## Input

The user provides a path — either:
- A single document folder containing `result.json`
- A parent folder containing multiple document subfolders, each with `result.json`

Discover all `result.json` files under the given path. Process each one in turn.

## Core Workflow

### Step 1 — Load known patterns

Load `document_profiles.yaml` as the reference for valid canonical IDs and aliases.
This tells you what IDs are valid — but it does **not** pre-confirm anything.

Check if a `schema_reference.yaml` already exists in the output folder.
If it does, load it as your confirmed registry so you don't re-ask about patterns
already confirmed in a previous run on the same folder.

Initialize two registries (in memory), starting **empty** each run unless
`schema_reference.yaml` exists:
```
confirmed_pairs: set of (document_type, section_type) tuples already confirmed this session
```

The registries start empty even for types that appear in `document_profiles.yaml`.
The canonical list is a validation reference, not a skip list — every pair must be
confirmed by the user the first time it appears in the current document.

### Step 2 — Scan and classify all pages

Read all pages in `azure_di_results` (in numeric order) and classify each one — see
"Classification Logic" below. Do this in a single pass before asking the user anything.

Track document boundaries as you go: when "Page X of N" resets to a new N (or a new
document title appears), you've crossed into a new logical document within the file.
Group your classifications by logical document.

Blank pages (`blank_page` role) are classified silently — never need confirmation.

**Low-confidence pages:** When automated signal detection returns no clear match
(e.g. no headings, no known column patterns, document type is ambiguous):
1. First, read the full page markdown yourself and reason about the content.
2. If you can now identify it — classify and continue.
3. If it is still ambiguous after reading — mark it `unclassified` and include it
   in the confirmation block for that document so the user can label it.
   Never silently drop or skip ambiguous pages.

**Page ordering:** Present documents strictly in physical page order. Never skip a
document or page group — every physical page must appear in exactly one confirmation
block, even if you must group it as `unclassified`.

### Step 3 — Confirm per document

For each logical document detected (in page order), present **one** aggregated
confirmation block showing all sections found — see "Confirmation Protocol". Wait
for the user to confirm or correct before moving to the next document.

### Step 4 — Write output files

After all documents are confirmed, write the following files into the same folder as `result.json`:
- `document_profiles.yaml` — merge-ready document profile patch (same schema as the canonical `document_profiles.yaml`)
<!-- DISABLED FOR TESTING:
- `classification.yaml` — all page results
- `schema_reference.yaml` — LLM-ready pattern catalog
-->

---

## Classification Logic

For each page markdown, determine:

| Field | What to identify |
|---|---|
| `document_name` | The human-readable document identifier (e.g., "UIIBEHSII03 Rev 04") |
| `document_type_id` | Snake-case type ID (e.g., `batch_production_control_record`) |
| `document_type_label` | Human label (e.g., "Batch Production and Control Record") |
| `section_name` | The section title as it appears or can be inferred (e.g., "List of Raw Materials and Weighing Details") |
| `section_type_id` | Snake-case type ID (e.g., `raw_materials_weighing`) |
| `section_type_label` | Human label |
| `sub_section` | Finer division within the section, if present (e.g., "Raw Materials for Equipment Cleaning", "Step 9–18", "Ethyl Acetate entries") |
| `page_role` | One of: `header_page`, `continuation`, `signature_page`, `blank_page`, `chart_page`, `data_entry_page`, `summary_page` |
| `detection_notes` | 1–2 sentences explaining what in the markdown led you to this classification — this becomes a detection hint for future LLM use |

### Page role definitions

- `header_page` — first page of a section; contains the section title prominently
- `continuation` — same section continues from previous page; no new section heading
- `signature_page` — page is primarily approval/signature rows
- `blank_page` — little or no content
- `chart_page` — page is primarily a graph, chart, or ADS data plot
- `data_entry_page` — structured table for recording measurements, done-by/checked-by columns
- `summary_page` — aggregated results, batch release, certificates

A page can have a compound role if needed (e.g., `header_page+data_entry_page`).

### Full-page scanning

Read the **entire** `markdown` field for every page — no character or line limits.
Section headings, table names, and column headers can appear anywhere on the page:
after a long repeating document header, mid-table, or near the bottom. Truncating
to the first N characters will silently drop sections that start later on the page.

When using a script to extract signals (headings, bold text, table headers), scan
the full string — never slice with `[:N]` or `head`. Apply any length limits only
to the *display excerpt* shown to the user in the confirmation block, not to the
classification logic itself.

### Signals to look for

**Document type signals:**
- Header table structure: "BATCH PRODUCTION AND CONTROL RECORD", BPCR Number, MPCR Number
- "Page X of N" anywhere on the page — N gives total doc length (BPCR = ~35 internal pages, but result.json can be 185 physical pages covering multiple docs stapled together)
- Document number format (e.g., `UIIBEHSII03`, `CRF-UII-XXXXXX`)
- Title keywords anywhere: "CERTIFICATE", "ANALYSIS REPORT", "CHECKLIST", "SOP", "BATCH RELEASE"

**Section type signals (scan the full page):**
- Markdown headings (`##`, `###`, `####`) anywhere in the page — not just at the top
- Named tables appearing mid-page (e.g., "LIST OF RAW MATERIALS FOR EQUIPMENT CLEANING", "REVISION SUMMARY", "YIELD DETAILS", "SIFTING RECORD") — these often follow a long repeating document header
- Table column headers anywhere (S.No., Raw Material Name, Batch No./Lot No., UOM = raw materials)
- Numbered step list → manufacturing operations
- Checkbox columns (Yes/No/NA) → checklist / line clearance
- "Prepared By / Reviewed By / Approved By" table → cover page or signature
- Chart/image-heavy with no table content → chart page
- Minimal content + page number only → blank/separator
- Chart/image-heavy with no table → chart page
- Minimal content + page number only → blank/separator
- if can't find any signals, classify as "continuation" of previous section (unless it's page 1, then it's likely a new doc starting)

---

## Confirmation Protocol

After classifying all pages of a logical document, present **one** aggregated block showing
all sections found. The user reviews and corrects the whole document at once — no per-page
interruptions.

```
── Document detected ─────────────────────────────────
Document : <document_name>  (type: <document_type_id>)
Pages    : p<first> – p<last> (physical)

Sections identified:
  #   section_type           section_name (as it appears)            pages       sub_sections
  1   cover_page             Cover Page                              p1          —
  2   revision_summary       Revision Summary                        p2          —
  3   material_dispensing    List of Raw Materials & Weighing        p3–4        "Raw Materials for Equipment Cleaning" (p4)
  4   equipment_list         List of Major Equipments & SOP Details  p5          —
  5   manufacturing_ops      Manufacturing Instructions               p6–20       "Sifting Record" (p21), "Micronization" (p27)
  6   yield_calculation      Yield Details                           p20         —
  7   cleaning_log           Equipment Cleaning Details              p33         —
  8   deviation              Description of Deviations               p35         —

Reference excerpts (first occurrence of each section type):
  [1] cover_page (p1):
      BATCH PRODUCTION AND CONTROL RECORD | Page 1 of 35
      VALIDATION BATCH | Effective Date: 09/04/2025
      Prepared By | Y, CHINNA KOTI REDDY | Manufacturing Initiator

  [3] material_dispensing (p3):
      ### LIST OF RAW MATERIALS AND WEIGHING DETAILS
      | S.No. | Raw Material Name | Batch No. | UOM | Gross Qty. | Net Qty. | Done by |
─────────────────────────────────────────────────────
Confirm all? [y] or provide corrections (e.g. "row 3 section_type → raw_material_request"):
```

**Responses:**
- `y` or blank → accept all rows
- Correction on a row (e.g. "row 3 section_type → raw_material_request") → update that row,
  re-display the corrected table, ask to confirm again
- `skip <row>` → mark those pages as `unclassified`

**Excerpts:** Only show reference excerpts for section types not previously confirmed in this
session. For already-confirmed types, omit the excerpt to keep the block compact.

**Single-page documents** with no distinct sections: show one row with the document type
and section as `~` if no section heading is present.

After confirmation, do not ask again for any `(document_type, section_type)` pair already
confirmed this session — subsequent documents reuse the confirmed labels silently.

---

## Output Schemas

### classification.yaml

```yaml
# Document Page Classification
# Generated by doc-page-classifier
metadata:
  source_file: "<path to result.json>"
  document_folder: "<uuid folder name>"
  total_pages: <N>
  classified_pages: <N>
  generated_at: "<ISO date>"

pages:
  - page_number: 1
    document_name: "UIIBEHSII03 Rev 04"
    document_type_id: batch_production_control_record
    document_type_label: "Batch Production and Control Record"
    section_name: "Cover Page"
    section_type_id: cover_page
    section_type_label: "Cover Page"
    sub_section: ~
    page_role: header_page+signature_page
    detection_notes: >
      Page 1 of 35 in header. Contains Prepared By / Reviewed By / Approved By
      signature table with VALIDATION BATCH and STABILITY BATCH annotations.

  - page_number: 2
    document_name: "UIIBEHSII03 Rev 04"
    document_type_id: batch_production_control_record
    document_type_label: "Batch Production and Control Record"
    section_name: "Revision Summary"
    section_type_id: revision_summary
    section_type_label: "Revision Summary"
    sub_section: ~
    page_role: summary_page
    detection_notes: >
      ## REVISION SUMMARY heading present. Table has columns BPCR Number,
      Revision Number, Effective Date, Reason for Revision.
```

### schema_reference.yaml

This file is designed to be injected as context into an LLM prompt for future classification tasks.

```yaml
# GMP Document Classification Schema Reference
# Purpose: LLM context file — inject into classification prompts to identify
#          document types, section types, sub-sections, and page roles from OCR markdown.
# Source: Confirmed by human review during doc-page-classifier runs.
# Last updated: <ISO date>

instructions_for_llm: >
  Use the document_types and section_types below to classify each document page.
  Match detection_signals against the page's markdown content. When multiple types
  could match, prefer the one whose structural_patterns match most specifically.
  Assign sub_section where the page content clearly belongs to a named sub-division.
  Use page_role to describe how this page functions within its section.

document_types:
  - id: batch_production_control_record
    label: "Batch Production and Control Record (BPCR)"
    description: >
      GMP manufacturing record documenting all batch production steps, material usage,
      process parameters, and quality checks for a pharmaceutical API batch.
    detection_signals:
      text_patterns:
        - "BATCH PRODUCTION AND CONTROL RECORD"
        - "BPCR Number"
        - "MPCR Number"
      structural_patterns:
        - "Header table row: Product Name | <value> | Market Code | <letter>"
        - "Header table row: BPCR Number | <code> | Revision Number | <N>"
        - "Header table row: Batch No. | <number> | Batch Size | <N> Kg"
        - "'Page X of 35' in header (35 is typical internal BPCR page count)"
      contextual_patterns:
        - "Sequential numbered manufacturing steps (1, 2, 3... or 1.1, 1.2...)"
        - "Done by / Checked by columns with signature fields"
    example_page_fragment: |
      # BATCH PRODUCTION AND CONTROL RECORD
      | Product Name | Sertraline Hydrochloride (Micronized Grade – I) | Market Code | J |
      | BPCR Number  | UIIBEHSII03 | Revision Number | 04 |
      | Batch No.    | 2538104192  | Batch Size      | 400.0 Kg |

section_types:
  - id: cover_page
    label: "Cover Page"
    description: "Title page with document identity and approval signatures."
    detection_signals:
      text_patterns:
        - "Prepared By"
        - "Reviewed By"
        - "Approved By"
        - "VALIDATION BATCH"
        - "STABILITY BATCH"
        - "Effective Date"
      structural_patterns:
        - "Signature table with Name | Title | Date columns"
        - "Page 1 of N in header"
    typical_page_roles: [header_page, signature_page]
    sub_sections: []
    example_page_fragment: |
      **VALIDATION BATCH**
      | Effective Date | 09/04/2025 |
      | Prepared By | Y, CHINNA KOTI REDDY (21977) | Manufacturing Initiator | 04/04/2025 |
      | Reviewed By | B, Ravi (22219) | Manufacturing HOD | 04/04/2025 |
      | Approved By | YEDDULA, RAMESH (5578) | Quality Assurance HOD | 07/04/2025 |

  # <additional confirmed section types appended here as they are encountered>

page_roles:
  - id: header_page
    description: "First page of a section; section title appears prominently."
  - id: continuation
    description: "Same section continues from previous page; no new heading."
  - id: signature_page
    description: "Page is primarily approval/signature rows."
  - id: blank_page
    description: "Little or no content."
  - id: chart_page
    description: "Page is primarily a graph, chart, or ADS data plot."
  - id: data_entry_page
    description: "Structured table for recording measurements with done-by/checked-by columns."
  - id: summary_page
    description: "Aggregated results, batch release notes, or certificates."
```

When writing `schema_reference.yaml`, populate the `section_types` list with every confirmed section type, using the `example_page_fragment` from an actual page that was confirmed for that type.

### document_profiles.yaml

Follows the **exact same schema** as `backend/app/compliance/rules/document_profiles.yaml` — it is a merge-ready patch file, not a replacement. Write only the document types and sections actually observed and confirmed in this run.

```yaml
version: 1

document_profiles:
  batch_record:
    aliases:
      - bmr
      - bpcr
      - batch production and control record
    expected_sections:
      - section_type: cover_page          # mark required: true if must be present
        display_name: Cover Page
        required: true
        aliases:
          - title page
      - section_type: manufacturing_operations
        display_name: Manufacturing Operations
        required: true
        aliases:
          - manufacturing instructions
      # ... one entry per confirmed section type, in document order

  certificate:
    aliases:
      - coa
      - certificate of analysis
    expected_sections:
      - section_type: certificate_of_analysis
        display_name: Certificate of Analysis
        required: true

  other:
    aliases: []
    expected_sections:
      # ... sections found in attached/supporting documents

section_aliases:
  # Existing canonical aliases (copy from document_profiles.yaml)
  manufacturing_operation: manufacturing_operations
  coa: certificate_of_analysis
  # New aliases discovered in this run:
  yield_details: yield_calculation
  sampling_checklist: sampling
  bpcr_review: bpcr_review_checklist
  # ... one entry per new alias found
```

**Rules for this file:**
- `required: true` only for sections that must be present for the document to be valid (e.g. cover page, manufacturing operations)
- Include `aliases` on a section entry only when OCR/LLM commonly uses a different name for that section
- In `section_aliases`, copy all existing canonical aliases from the reference file, then append new ones discovered in this run
- Omit document types not observed (e.g. `sop`, `logbook`) — this is a patch, not a full replacement

---

## Efficiency tips

- Process pages in batches of 10–20 internally (read ahead, classify, then check for new patterns). This reduces back-and-forth context switching.
- The document header repeats on every physical page — don't re-confirm the document type after page 1. Only re-check if the header changes (a new document starts mid-file).
- Use the `sub_section` field to avoid creating spurious new `section_type_id` values. For example, "List of Raw Materials for Equipment Cleaning" is a `sub_section` of `raw_materials_weighing`, not a new section type.
- If a page is blank or just contains the repeated header with no body content, classify it as `blank_page` without confirmation.

## Known pharma GMP section types (seed list)

Use these as your initial registry if no `schema_reference.yaml` exists yet. Confirm if you see something not on this list.

```
bpcr, cover_page, revision_summary, raw_materials_weighing, equipment_cleaning_materials,
equipment_list, manufacturing_steps, sifting_record, pin_milling_mixing, micronization,
co_mill_operation, metal_detection, equipment_cleaning_record, deviations,
packing_material_requisition, process_parameter_recording, checklist,
packaging_instruction, in_process_analysis, ads_chart, certificate_of_analysis,
batch_release_note, qa_review_checklist, analytical_data_review, sampling_checklist,
pre_analysis_checklist, recovered_solvent_certificate, finished_product_weighing,
intermediate_transfer_note, sample_request
```
