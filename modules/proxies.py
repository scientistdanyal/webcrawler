from __future__ import annotations

from pathlib import Path


def load_proxies(path: str | Path = "ip_list.txt") -> list[str]:
    """Load proxy URLs from a text file (one per line).

    Accepted formats:
      ip:port
      http://ip:port
      http://user:pass@ip:port
    """
    file_path = Path(path)
    if not file_path.exists():
        return []

    proxies: list[str] = []
    for raw in file_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "://" not in line:
            line = f"http://{line}"
        proxies.append(line)
    return proxies
