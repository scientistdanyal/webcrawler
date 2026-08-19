from __future__ import annotations

import csv
import json
import re
from pathlib import Path

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

JUNK_NEEDLES = (
    "example.com",
    "sentry.io",
    "wixpress.com",
    "schema.org",
    "localhost",
    "yourdomain",
    "domain.com",
    "email.com",
    "test.com",
)


def normalize_email(raw: str) -> str | None:
    email = raw.strip().lower().rstrip(".,;")
    if not email:
        return None

    # Common scrape truncations / glued text
    fixes = {
        ".or": ".org",
        ".og": ".org",
    }
    for bad, good in fixes.items():
        if email.endswith(bad):
            email = email[: -len(bad)] + good

    # e.g. membership@ymcali.org.ymca -> membership@ymcali.org
    if email.endswith(".org.ymca"):
        email = email[: -len(".ymca")]

    # e.g. name@domain.orgevan.mauthe -> name@domain.org
    m = re.match(r"^(.+@.+\.org)[a-z0-9._%+\-]+$", email)
    if m and not EMAIL_RE.fullmatch(email):
        email = m.group(1)

    if not EMAIL_RE.fullmatch(email):
        return None

    domain = email.rsplit("@", 1)[1]
    if any(needle in domain for needle in JUNK_NEEDLES):
        return None
    if domain.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js")):
        return None

    return email


def postprocess_emails(
    input_path: str | Path = "emails.json",
    *,
    cleaned_json: str | Path = "emails.json",
    csv_path: str | Path = "emails.csv",
    no_emails_path: str | Path = "no_emails.txt",
) -> dict[str, int]:
    raw = json.loads(Path(input_path).read_text(encoding="utf-8"))

    cleaned: dict[str, list[str]] = {}
    no_email_branches: list[str] = []
    removed = 0
    kept = 0

    for name, emails in raw.items():
        if not isinstance(emails, list):
            continue
        unique: list[str] = []
        seen: set[str] = set()
        for item in emails:
            normalized = normalize_email(str(item))
            if normalized is None:
                removed += 1
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            unique.append(normalized)
            kept += 1

        unique.sort()
        cleaned[name] = unique
        if not unique:
            no_email_branches.append(name)

    Path(cleaned_json).write_text(
        json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    rows: list[tuple[str, str]] = []
    for name, emails in cleaned.items():
        for email in emails:
            rows.append((name, email))

    with Path(csv_path).open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Branch/Association Name", "email"])
        writer.writerows(rows)

    no_email_branches.sort(key=str.lower)
    with Path(no_emails_path).open("w", encoding="utf-8") as f:
        f.write(f"Branches with 0 emails: {len(no_email_branches)}\n\n")
        for name in no_email_branches:
            f.write(f"{name}\n")

    return {
        "branches": len(cleaned),
        "emails_kept": kept,
        "emails_removed": removed,
        "csv_rows": len(rows),
        "no_email_branches": len(no_email_branches),
    }


if __name__ == "__main__":
    stats = postprocess_emails()
    print(json.dumps(stats, indent=2))
