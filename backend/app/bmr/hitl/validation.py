"""Validation for resolution requests.

Keeps validation rules in one place so both the HTTP layer and
programmatic callers enforce the same invariants (Spec 004 §3.1):

- ``CONFIRM`` ⇒ no ``reason_type``, no ``observed_value_on_document``.
- ``DISMISS`` with ``reason_type ∈ {OCR_MISREAD, ACCEPTABLE_VARIANCE}`` ⇒
  ``observed_value_on_document`` required.
- ``DISMISS`` with ``reason_type == DUPLICATE_FINDING`` ⇒
  ``duplicate_of_finding_id`` required.
- ``CORRECT`` is handled via :func:`validate_correction_payload` and goes
  through the dedicated correction workflow (follow-up #4). Callers that
  still route ``CORRECT`` through :func:`validate_resolution_payload`
  receive :class:`CorrectNotSupportedError` so the HTTP layer can return
  a clear 409/501.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.bmr.hitl.models import (
    DismissReasonType,
    ResolutionAction,
    reason_requires_observed_value,
)


class ResolutionValidationError(ValueError):
    """Raised when a resolution request is structurally invalid."""


class CorrectionValidationError(ValueError):
    """Raised when a CORRECT correction payload is malformed."""


class CorrectNotSupportedError(NotImplementedError):
    """Raised when CORRECT is submitted via the resolution validator.

    Kept for backwards compatibility with pre-follow-up callers; the
    correction workflow uses :func:`validate_correction_payload`.
    """


@dataclass(frozen=True)
class ResolutionDraft:
    """Validated inputs for :class:`StructuredResolution` construction."""

    action: ResolutionAction
    reason_type: DismissReasonType | None
    observed_value_on_document: str | None
    reason_comment: str | None
    duplicate_of_finding_id: str | None


def validate_resolution_payload(
    *,
    action: str,
    reason_type: str | None,
    observed_value_on_document: str | None,
    reason_comment: str | None,
    duplicate_of_finding_id: str | None,
) -> ResolutionDraft:
    try:
        parsed_action = ResolutionAction(action)
    except ValueError as exc:
        raise ResolutionValidationError(f"unknown action {action!r}") from exc

    if parsed_action is ResolutionAction.CORRECT:
        raise CorrectNotSupportedError(
            "CORRECT resolutions require the correction workflow (not implemented in v0)"
        )

    if parsed_action is ResolutionAction.CONFIRM:
        if reason_type is not None:
            raise ResolutionValidationError(
                "CONFIRM resolution must not carry a reason_type"
            )
        if observed_value_on_document is not None:
            raise ResolutionValidationError(
                "CONFIRM resolution must not carry observed_value_on_document"
            )
        if duplicate_of_finding_id is not None:
            raise ResolutionValidationError(
                "CONFIRM resolution must not carry duplicate_of_finding_id"
            )
        return ResolutionDraft(
            action=parsed_action,
            reason_type=None,
            observed_value_on_document=None,
            reason_comment=reason_comment,
            duplicate_of_finding_id=None,
        )

    # DISMISS path
    if reason_type is None:
        raise ResolutionValidationError("DISMISS resolution requires reason_type")
    try:
        parsed_reason = DismissReasonType(reason_type)
    except ValueError as exc:
        raise ResolutionValidationError(
            f"unknown reason_type {reason_type!r}"
        ) from exc

    if (
        reason_requires_observed_value(parsed_reason)
        and not observed_value_on_document
    ):
        raise ResolutionValidationError(
            f"reason_type={parsed_reason.value} requires observed_value_on_document"
        )

    if (
        parsed_reason is DismissReasonType.DUPLICATE_FINDING
        and not duplicate_of_finding_id
    ):
        raise ResolutionValidationError(
            "reason_type=DUPLICATE_FINDING requires duplicate_of_finding_id"
        )

    return ResolutionDraft(
        action=parsed_action,
        reason_type=parsed_reason,
        observed_value_on_document=observed_value_on_document,
        reason_comment=reason_comment,
        duplicate_of_finding_id=duplicate_of_finding_id,
    )


@dataclass(frozen=True)
class CorrectionDraft:
    """Validated inputs for a ``CORRECT`` workflow."""

    field: str
    corrected_value: Any
    reason_comment: str
    observed_value_on_document: str | None = None


# Accepted JSON scalar types for CORRECT payloads. Extracted field
# values are scalars (number, string, boolean) — accepting objects or
# lists lets a reviewer inject arbitrary nested structures into
# ExtractedPackage, which would silently short-circuit tolerance
# comparisons downstream.
_SCALAR_TYPES = (str, int, float, bool)

_MAX_CORRECTED_STRING_LEN = 10_000
_MAX_REASON_COMMENT_LEN = 10_000


def validate_correction_payload(
    *,
    field: str | None,
    corrected_value: Any,
    reason_comment: str | None,
    observed_value_on_document: str | None = None,
) -> CorrectionDraft:
    if not isinstance(field, str) or not field.strip():
        raise CorrectionValidationError("CORRECT requires a non-empty field name")
    field = field.strip()
    if corrected_value is None:
        raise CorrectionValidationError(
            "CORRECT requires a concrete corrected_value (null rejected)"
        )
    # Reject bool-by-accident-via-int and vice versa? Python makes bool a
    # subclass of int, so keep the check explicit on the full tuple.
    if not isinstance(corrected_value, _SCALAR_TYPES):
        raise CorrectionValidationError(
            "corrected_value must be a scalar (string, number, or boolean); "
            f"got {type(corrected_value).__name__}"
        )
    if isinstance(corrected_value, str):
        if not corrected_value.strip():
            raise CorrectionValidationError(
                "CORRECT requires a concrete corrected_value (blank string rejected)"
            )
        if len(corrected_value) > _MAX_CORRECTED_STRING_LEN:
            raise CorrectionValidationError(
                f"corrected_value exceeds {_MAX_CORRECTED_STRING_LEN} chars"
            )
    if isinstance(corrected_value, float) and (
        corrected_value != corrected_value or corrected_value in (float("inf"), float("-inf"))
    ):
        raise CorrectionValidationError(
            "corrected_value must be a finite number (NaN/inf rejected)"
        )
    if not reason_comment or not reason_comment.strip():
        raise CorrectionValidationError(
            "CORRECT requires a reason_comment describing the reviewer's rationale"
        )
    if len(reason_comment) > _MAX_REASON_COMMENT_LEN:
        raise CorrectionValidationError(
            f"reason_comment exceeds {_MAX_REASON_COMMENT_LEN} chars"
        )
    return CorrectionDraft(
        field=field,
        corrected_value=corrected_value,
        reason_comment=reason_comment.strip(),
        observed_value_on_document=observed_value_on_document,
    )


__all__ = [
    "CorrectionDraft",
    "CorrectionValidationError",
    "CorrectNotSupportedError",
    "ResolutionDraft",
    "ResolutionValidationError",
    "validate_correction_payload",
    "validate_resolution_payload",
]
