──────────────────────────────────────────────────
CROSS-PAGE RECONCILIATION RULES
──────────────────────────────────────────────────

Category: Material Reconciliation

1. Verify total quantity of each raw material used in manufacturing matches the dispatched quantity. List each material with quantities from both sections. [sections: manufacturing, raw_material]

2. Verify raw material lot/batch numbers are consistent between manufacturing records and dispatch records. [sections: manufacturing, raw_material]

---

Category: Equipment Verification

3. Verify each equipment inspection/cleaning step has a corresponding completed checklist or cleaning record. [sections: manufacturing, equipment_cleaning]

4. Verify equipment IDs are consistent across manufacturing steps and equipment logs. [sections: manufacturing, equipment_usage_log]

---

Category: QC/IPC Reconciliation

5. Verify QC test values in manufacturing steps match values in QC reports. Flag any discrepancies including potential OCR misreads. [sections: manufacturing, qc_report]

6. Verify in-process control results match between step logs and IPC reports. [sections: manufacturing, in_process_report]

---

Category: Document Completeness

7. Verify all referenced attachments, reports, and sub-documents are present in the document packet. [sections: *]

8. Verify all sampling steps have corresponding analytical reports attached. [sections: manufacturing, qc_report, in_process_report]

---

Category: Cross-Section Consistency

9. Verify batch/lot numbers are consistent across all sections. [sections: *]

10. Verify chronological order of timestamps across manufacturing steps. [sections: manufacturing]

11. Verify that conditional steps were correctly followed based on IPC results (e.g., if pH is within range, subsequent steps should reflect the correct path). [sections: manufacturing, in_process_report]
