"""Finding-level HITL surface (Spec 004 v0 vertical slice).

Scope:

- Structured resolutions (CONFIRM / DISMISS; CORRECT returns 501 for v0).
- ``feedback_seed.v1`` — every DISMISS/CORRECT appends a
  :class:`FeedbackSample` so Spec 005's rule-authoring skill can consume
  them later.
- ``report_project.v1`` — group findings by BPCR step / document scope
  with ALCOA / GMP / Checklist sub-sections.
- Export gate + audit report revisions (filesystem-backed).
- PDF/HTML rendering via a pluggable :class:`ReportRenderer`.

This package deliberately lives alongside (not inside) the existing
``app/compliance/`` subsystem so v0 has zero blast radius on existing
pipelines (Constitution VII).
"""

from app.bmr.hitl.models import (
    AuditReportRevision,
    DismissReasonType,
    ExportGateStatus,
    FeedbackSample,
    GroupedReport,
    GroupKind,
    ReportSection,
    ResolutionAction,
    ResolutionSubSection,
    StructuredResolution,
)
from app.bmr.hitl.reporting_config import (
    ReportingConfig,
    ReportingConfigError,
    ReportSectionsConfig,
    SeverityGatingConfig,
    load_reporting_config,
)

__all__ = [
    "AuditReportRevision",
    "DismissReasonType",
    "ExportGateStatus",
    "FeedbackSample",
    "GroupKind",
    "GroupedReport",
    "ReportSection",
    "ReportSectionsConfig",
    "ReportingConfig",
    "ReportingConfigError",
    "ResolutionAction",
    "ResolutionSubSection",
    "SeverityGatingConfig",
    "StructuredResolution",
    "load_reporting_config",
]
