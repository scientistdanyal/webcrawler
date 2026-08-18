from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CrawlTracker:
    """Persist crawl status so batch runs can skip or redo URLs."""

    def __init__(self, path: str | Path = "tracking.json") -> None:
        self.path = Path(path)
        self._data: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self._data = {}
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            self._data = loaded if isinstance(loaded, dict) else {}
        except (json.JSONDecodeError, OSError):
            self._data = {}

    def save(self) -> None:
        self.path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def is_done(self, website: str) -> bool:
        entry = self._data.get(website)
        return bool(entry and entry.get("status") == "done")

    def mark(
        self,
        website: str,
        *,
        name: str,
        status: str,
        emails_found: int = 0,
        error: str | None = None,
    ) -> None:
        self._data[website] = {
            "name": name,
            "status": status,
            "emails_found": emails_found,
            "error": error,
            "last_run": _now(),
        }
        self.save()

    def clear(self, website: str) -> None:
        if website in self._data:
            del self._data[website]
            self.save()

    def find_website(self, query: str) -> str | None:
        """Resolve a redo target by exact website URL or branch name."""
        if query in self._data:
            return query
        query_lower = query.lower()
        for website, entry in self._data.items():
            if website.lower() == query_lower:
                return website
            if str(entry.get("name", "")).lower() == query_lower:
                return website
        return None
