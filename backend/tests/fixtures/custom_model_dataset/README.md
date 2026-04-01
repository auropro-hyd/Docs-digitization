# Custom Model Pilot Dataset

This fixture set is used for custom-model pilot evaluation (`OCR-023/024`).

Structure:

- `manifest.json`: sample index and split assignment.
- `labels/`: expected gold labels for each sample.
- `predictions_baseline/`: baseline extractor outputs.
- `predictions_custom/`: candidate custom-model outputs.

The pilot evaluator compares baseline vs custom on exact normalized-value match.
