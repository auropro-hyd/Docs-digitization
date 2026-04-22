"""Unit tests for HITL resolution validation."""

from __future__ import annotations

import pytest

from app.bmr.hitl.models import DismissReasonType, ResolutionAction
from app.bmr.hitl.validation import (
    CorrectionValidationError,
    CorrectNotSupportedError,
    ResolutionValidationError,
    validate_correction_payload,
    validate_resolution_payload,
)


def _payload(**overrides):
    base = {
        "action": "CONFIRM",
        "reason_type": None,
        "observed_value_on_document": None,
        "reason_comment": None,
        "duplicate_of_finding_id": None,
    }
    base.update(overrides)
    return base


def test_confirm_with_note_only_is_valid():
    draft = validate_resolution_payload(**_payload(reason_comment="looks right"))
    assert draft.action is ResolutionAction.CONFIRM
    assert draft.reason_type is None
    assert draft.reason_comment == "looks right"


def test_confirm_rejects_reason_type():
    with pytest.raises(ResolutionValidationError):
        validate_resolution_payload(**_payload(reason_type="OTHER"))


def test_dismiss_requires_reason_type():
    with pytest.raises(ResolutionValidationError, match="requires reason_type"):
        validate_resolution_payload(**_payload(action="DISMISS"))


def test_dismiss_ocr_misread_requires_observed_value():
    with pytest.raises(ResolutionValidationError, match="observed_value_on_document"):
        validate_resolution_payload(
            **_payload(action="DISMISS", reason_type="OCR_MISREAD")
        )


def test_dismiss_ocr_misread_happy_path():
    draft = validate_resolution_payload(
        **_payload(
            action="DISMISS",
            reason_type="OCR_MISREAD",
            observed_value_on_document="12.7 kg",
            reason_comment="7 looks like a 5 on scan",
        )
    )
    assert draft.action is ResolutionAction.DISMISS
    assert draft.reason_type is DismissReasonType.OCR_MISREAD
    assert draft.observed_value_on_document == "12.7 kg"


def test_dismiss_duplicate_requires_duplicate_of_finding_id():
    with pytest.raises(ResolutionValidationError, match="duplicate_of_finding_id"):
        validate_resolution_payload(
            **_payload(action="DISMISS", reason_type="DUPLICATE_FINDING")
        )


def test_dismiss_other_reason_does_not_require_observed_value():
    draft = validate_resolution_payload(
        **_payload(action="DISMISS", reason_type="OUT_OF_SCOPE")
    )
    assert draft.reason_type is DismissReasonType.OUT_OF_SCOPE
    assert draft.observed_value_on_document is None


def test_correct_raises_not_supported():
    with pytest.raises(CorrectNotSupportedError):
        validate_resolution_payload(**_payload(action="CORRECT"))


def test_unknown_action_rejected():
    with pytest.raises(ResolutionValidationError, match="unknown action"):
        validate_resolution_payload(**_payload(action="BOGUS"))


def test_unknown_reason_type_rejected():
    with pytest.raises(ResolutionValidationError, match="unknown reason_type"):
        validate_resolution_payload(
            **_payload(action="DISMISS", reason_type="NOPE")
        )


# ── correction payload validation ────────────────────────────────────────────


def test_correction_happy_path():
    draft = validate_correction_payload(
        field="dispensed_weight_kg",
        corrected_value=10.0,
        reason_comment="  re-read from paper record  ",
        observed_value_on_document="10.0 kg",
    )
    assert draft.field == "dispensed_weight_kg"
    assert draft.corrected_value == 10.0
    assert draft.reason_comment == "re-read from paper record"
    assert draft.observed_value_on_document == "10.0 kg"


@pytest.mark.parametrize("bad_field", [None, "", "   "])
def test_correction_rejects_missing_field(bad_field):
    with pytest.raises(CorrectionValidationError, match="field"):
        validate_correction_payload(
            field=bad_field,
            corrected_value=1.0,
            reason_comment="why",
        )


@pytest.mark.parametrize("bad_value", [None, "", "   "])
def test_correction_rejects_blank_value(bad_value):
    with pytest.raises(CorrectionValidationError, match="corrected_value"):
        validate_correction_payload(
            field="dispensed_weight_kg",
            corrected_value=bad_value,
            reason_comment="why",
        )


@pytest.mark.parametrize("bad_reason", [None, "", "   "])
def test_correction_rejects_blank_reason(bad_reason):
    with pytest.raises(CorrectionValidationError, match="reason_comment"):
        validate_correction_payload(
            field="dispensed_weight_kg",
            corrected_value=10.0,
            reason_comment=bad_reason,
        )
