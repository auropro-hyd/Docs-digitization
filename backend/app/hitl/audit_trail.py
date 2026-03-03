"""Audit trail for human corrections.

Every correction is logged: who, when, original value, new value, reason.
Critical for regulatory compliance in pharmaceutical environments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class AuditEntry:
    doc_id: str
    page_num: int
    field_path: str
    original_value: str
    new_value: str
    reason: str
    reviewer: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    entry_id: str = ""

    def __post_init__(self):
        if not self.entry_id:
            import uuid

            self.entry_id = str(uuid.uuid4())


class AuditTrail:
    """In-memory audit trail. Replace with DB-backed for production."""

    def __init__(self):
        self._entries: list[AuditEntry] = []

    def log_correction(
        self,
        doc_id: str,
        page_num: int,
        field_path: str,
        original_value: str,
        new_value: str,
        reason: str,
        reviewer: str,
    ) -> AuditEntry:
        entry = AuditEntry(
            doc_id=doc_id,
            page_num=page_num,
            field_path=field_path,
            original_value=original_value,
            new_value=new_value,
            reason=reason,
            reviewer=reviewer,
        )
        self._entries.append(entry)
        return entry

    def get_entries(self, doc_id: str) -> list[AuditEntry]:
        return [e for e in self._entries if e.doc_id == doc_id]

    def get_page_entries(self, doc_id: str, page_num: int) -> list[AuditEntry]:
        return [e for e in self._entries if e.doc_id == doc_id and e.page_num == page_num]

    def export(self, doc_id: str) -> list[dict]:
        """Export audit trail as dicts for reporting."""
        return [
            {
                "entry_id": e.entry_id,
                "page_num": e.page_num,
                "field_path": e.field_path,
                "original_value": e.original_value,
                "new_value": e.new_value,
                "reason": e.reason,
                "reviewer": e.reviewer,
                "timestamp": e.timestamp.isoformat(),
            }
            for e in self.get_entries(doc_id)
        ]
