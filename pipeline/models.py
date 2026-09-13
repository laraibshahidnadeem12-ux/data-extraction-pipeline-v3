from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass
class ExtractedRecord:
    source_file: str
    document_type: str
    name: str
    date: str
    date_normalized: str
    fields: dict = field(default_factory=dict)
    engine: str = ""
    confidence: float | None = None

    def to_row(self) -> list:
        return [
            self.timestamp_iso(),
            self.source_file,
            self.document_type,
            self.name,
            self.date,
            self.date_normalized,
            json.dumps(self.fields, ensure_ascii=False, default=str),
            self.engine,
            self.confidence,
        ]

    def timestamp_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def COLUMNS(cls) -> list[str]:
        return [
            "timestamp",
            "source_file",
            "document_type",
            "name",
            "date",
            "date_normalized",
            "fields",
            "engine",
            "confidence",
        ]