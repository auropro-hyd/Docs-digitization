──────────────────────────────────────────────────
CROSS-DOCUMENT RECONCILIATION RULES — REFERENCE EXEMPLAR
──────────────────────────────────────────────────

This file holds three canonical reference rules for the CROSS-DOCUMENT
evaluation pattern while the rule bank is repopulated against the new
``document_profiles`` taxonomy. The same-page individual and
aggregated-within-document patterns live in ``alcoa_rules.md``.
Historical rule text is preserved under
``reconciliation_rules.archived.md`` (and the matching YAML overlay).

  Rule 1 (material_reconciliation): 2-way join — BPCR
          material_dispensing ↔ raw_material_request material_request.
          Quantity match.
  Rule 2 (manufacturing_step_traceability): 3-way join, anchored on a
          manufacturing step's raw-material reference. Joins the
          step's mention to the dispensing weighing AND the request /
          issue allotment. Akhilesh's 2026-04-30 ask, scenario A.
  Rule 3 (in_process_value_traceability): cross-document join from a
          BPCR manufacturing step's measured value (pH, temperature,
          assay, etc.) to the ipc_report's matching entry. Akhilesh's
          2026-04-30 ask, scenario B.


Category: Material Reconciliation

1. For each raw material referenced in the batch_record material_dispensing section, there must be a matching entry in the raw_material_request material_request section with the same material name (alias-normalized) and the same dispensed quantity (within ±0.5%). Flag any missing material, mismatched lot/batch number, or quantity outside tolerance. This is the CROSS-DOCUMENT pattern: one rule, evidence drawn from a section in document A AND a section in document B, joined on a stable entity key. [sections: material_dispensing, material_request]


Category: Manufacturing Step Traceability

2. For every manufacturing operations step that names a raw material (e.g. "Step 3: Add Sodium Chloride 10.0 kg"), three checks must hold simultaneously. (A) The same material name must appear as a row in the batch_record material_dispensing section, and the dispensing row's Net/Dispensed Weight must equal the manufacturing step's quantity within ±0.5%. (B) The same material must appear in the raw_material_request material_request section, and the request's Issued/Allotted/Quantity Issued column must be ≥ the step's quantity (the request must cover at least what was used); when both rows carry a lot or batch number, the lots must match. (C) The dispensing quantity from (A) and the request quantity from (B) must agree within ±0.5%. Surface each violation as a separate finding: missing dispensing entry for a step's material, missing request entry for a step's material, lot mismatch between dispensing and request, quantity drift outside ±0.5% on any leg, or a manufacturing step referencing a quantity higher than the requested allotment (regulatory red flag). This is the 3-WAY CROSS-DOCUMENT pattern, anchored on a manufacturing step. The rule does not apply to non-material operations (cleaning, equipment setup, sampling) or to in-process intermediates carried over from a prior step. [sections: manufacturing_operations, material_dispensing, material_request]


Category: In Process Value Traceability

3. Every manufacturing operations step row whose Remarks/Observation/Reading/Result column carries a numeric measurement (pH, temperature, moisture, assay, % loss-on-drying, particle-size, etc.) must have a matching entry in the ipc_report document. Match by parameter name (alias-normalized, case-insensitive: pH = pH; "temp." = temperature; "LOD" = loss on drying), batch identifier (the manufacturing step's batch number must equal the ipc_report's batch identifier), and value (within the parameter's tolerance: pH ±0.1; temperature ±0.5°C; moisture/LOD ±0.05% w/w absolute; assay ±0.5% relative; particle size per the SOP-defined window). Surface each violation as a separate finding: a manufacturing step recorded a measurement with no corresponding ipc_report entry (documentation gap); the ipc_report value differs from the manufacturing step value beyond tolerance (data integrity red flag — possible transcription error or after-the-fact correction); or the ipc_report cites a batch number that doesn't match the BPCR's batch number (cross-batch contamination of data). The rule does not apply when the Remarks column is empty or carries non-numeric text only ("OK", "Satisfactory", "as per SOP") — the cross-check is gated on a numeric measurement being present in the source. This is the VALUE-MATCH CROSS-DOCUMENT pattern, joined on (parameter_name, batch_id) with a numerical-tolerance verdict. [sections: manufacturing_operations]
