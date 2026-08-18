from __future__ import annotations

import json
from pathlib import Path


class EmailStore:
    """Append-only email store keyed by branch/association name."""

    def __init__(self, path: str | Path = "emails.json") -> None:
        self.path = Path(path)
        self._data: dict[str, list[str]] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self._data = {}
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self._data = {
                    str(name): sorted({str(e).lower() for e in emails})
                    for name, emails in loaded.items()
                    if isinstance(emails, list)
                }
            else:
                self._data = {}
        except (json.JSONDecodeError, OSError):
            self._data = {}

    def save(self) -> None:
        self.path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def append(self, name: str, emails: list[str]) -> list[str]:
        existing = set(self._data.get(name, []))
        for email in emails:
            cleaned = email.strip().lower()
            if cleaned:
                existing.add(cleaned)
        merged = sorted(existing)
        self._data[name] = merged
        self.save()
        return merged
