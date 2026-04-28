──────────────────────────────────────────────────
CROSS-DOCUMENT RECONCILIATION RULES — REFERENCE EXEMPLAR
──────────────────────────────────────────────────

This file holds the canonical reference rule for the CROSS-DOCUMENT
evaluation pattern while the rule bank is repopulated against the new
``document_profiles`` taxonomy. The historical rule text is preserved
for reference under ``reconciliation_rules.archived.md`` (and the
matching YAML overlay file).


Category: Material Reconciliation

1. For each raw material referenced in the batch_record material_dispensing section, there must be a matching entry in the raw_material_request material_request section with the same material name (alias-normalized) and the same dispensed quantity (within ±0.5%). Flag any missing material, mismatched lot/batch number, or quantity outside tolerance. This is the CROSS-DOCUMENT pattern: one rule, evidence drawn from a section in document A AND a section in document B, joined on a stable entity key. [sections: material_dispensing, material_request]
