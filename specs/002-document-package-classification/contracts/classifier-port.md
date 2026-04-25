# Port Contract: DocumentClassifier

**Feature**: 002 | **Version**: v1

## Purpose

A pluggable port that classifies a `DocumentRef` into a `DocumentRole`. The default adapter
is a hybrid (filename + header + VLM tiebreak). Alternate adapters (e.g. fine-tuned model)
MUST satisfy this contract and MUST NOT leak adapter-specific signals into shared types.

## Python interface (illustrative)

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass(frozen=True)
class ClassifierCandidate:
    role: str
    score: float  # 0..1

@dataclass(frozen=True)
class ClassifierOutcome:
    top_role: str
    confidence: float
    candidates: list[ClassifierCandidate]
    decision_source: str  # ClassificationDecisionSource
    needs_review: bool    # True iff confidence below per-adapter threshold

class DocumentClassifier(ABC):
    @abstractmethod
    async def classify(
        self,
        *,
        document_ref_id: str,
        physical_file_id: str,
        page_range: tuple[int, int] | None,
        candidate_roles: list[str],        # from manifest
        hint_role: str | None = None,      # reviewer hint at upload
    ) -> ClassifierOutcome: ...
```

## Contract requirements

1. **Determinism envelope**: Given identical inputs and identical adapter config, outcome
   MUST be stable within a minor version. Adapter config is YAML (prompts, thresholds).
2. **Candidate list bounds**: `candidates` MUST be a subset of `candidate_roles ∪ {OTHER}`.
3. **`needs_review` semantics**: True iff `confidence < adapter.threshold_accept`. The
   package state machine uses this flag, not the raw score, to branch to the
   classification-review UI.
4. **No side effects**: The port MUST NOT write to `ClassificationResult`; the caller
   (pipeline orchestrator) is responsible for persistence and audit trail.
5. **Timeout budget**: Implementations MUST respect a per-call budget provided via context
   (default 10 s for heuristics, 60 s when VLM tiebreak is invoked).
6. **Observability**: Implementations MUST emit `classifier.invoked` and
   `classifier.decision` events with `decision_source`, `confidence`, and candidate scores.

## Adapter: `HybridClassifier`

Layered strategy:

1. Filename heuristic → candidates with score from a YAML-declared alias table.
2. Header-text heuristic (first page / first 1500 chars) → candidate boost / tiebreak.
3. If top-1 confidence < `threshold_accept`, invoke a VLM with the candidate-role list and
   a first-page rendered image; merge VLM output as a new candidate set.
4. Emit final `ClassifierOutcome`.

Config keys (`config/bmr/pilot-classifier.yaml`):
- `threshold_accept: 0.85`
- `threshold_vlm_fallback: 0.70`
- `vlm_provider: azure_openai | ollama | gemini | vllm` (reuses existing VLM providers)
- `filename_aliases: {BPCR: [bpcr*, batch*production*], ...}`
- `header_aliases: {BPCR: ["batch production", "bpcr"], ...}`
