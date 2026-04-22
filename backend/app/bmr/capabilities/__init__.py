"""Atomic BMR capabilities.

Each capability is a pure function with a declared version. The stage
orchestrator (spec 001) composes capabilities into pipeline nodes.
Capabilities must not call the network or touch the database directly —
they receive pre-resolved inputs from the orchestrator.
"""

from app.bmr.capabilities.aliases import AliasTable, load_alias_table, normalise_name
from app.bmr.capabilities.evidence import EvidenceRegion, FindingDraft, FindingSource, FindingStatus
from app.bmr.capabilities.extracted_data import (
    ExtractedPackage,
    ExtractedPage,
    FieldValue,
)
from app.bmr.capabilities.rule_eval import (
    cross_doc_rule_eval_v1,
    page_aggregate_eval_v1,
    same_page_eval_v1,
)

# NOTE: ``feedback_seed_v1`` and ``report_project_v1`` deliberately live in
# ``app.bmr.capabilities.feedback_seed`` / ``.report_project`` submodules but
# are NOT re-exported at the package root. They depend on ``app.bmr.hitl``
# which in turn depends on ``app.bmr.workflow`` and ``app.bmr.capabilities``,
# so re-exporting here would create a circular import. Import them from their
# submodule paths directly.

__all__ = [
    "AliasTable",
    "EvidenceRegion",
    "ExtractedPackage",
    "ExtractedPage",
    "FieldValue",
    "FindingDraft",
    "FindingSource",
    "FindingStatus",
    "cross_doc_rule_eval_v1",
    "load_alias_table",
    "normalise_name",
    "page_aggregate_eval_v1",
    "same_page_eval_v1",
]
