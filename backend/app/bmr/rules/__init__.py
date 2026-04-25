"""BMR audit rule subsystem.

- ``schema.py``: locate + load versioned JSON Schemas for BMR rules.
- ``validator.py``: deterministic, LLM-free schema validation with
  author-facing error shaping.
- ``loader.py``: load rule YAMLs from disk into :class:`LoadedRule` objects
  ready for the rule engine (specs 003 + 005).

No runtime imports should perform network or database calls.
"""

from app.bmr.rules.loader import LoadedRule, RuleBank, load_rule_bank, load_rule_file
from app.bmr.rules.validator import (
    RuleValidationError,
    RuleValidationReport,
    validate_rule_mapping,
)

__all__ = [
    "LoadedRule",
    "RuleBank",
    "RuleValidationError",
    "RuleValidationReport",
    "load_rule_bank",
    "load_rule_file",
    "validate_rule_mapping",
]
