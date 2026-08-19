from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

CAPTCHA_WAIT_SECONDS = 10

# Order matters: more specific checks first
CAPTCHA_SIGNATURES: list[tuple[str, tuple[str, ...]]] = [
    (
        "cloudflare",
        (
            "cf-browser-verification",
            "cf-challenge",
            "cf-turnstile",
            "challenges.cloudflare.com",
            "cdn-cgi/challenge-platform",
            "attention required! | cloudflare",
            "checking your browser before accessing",
            "just a moment...",
            "cf-please-wait",
            "data-cfasync",
            "_cf_chl",
        ),
    ),
    (
        "recaptcha",
        (
            "google.com/recaptcha",
            "recaptcha/api",
            "g-recaptcha",
            "grecaptcha",
            "recaptcha-checkbox",
        ),
    ),
    (
        "hcaptcha",
        (
            "hcaptcha.com",
            "h-captcha",
            "hcaptcha-box",
        ),
    ),
    (
        "funcaptcha",
        (
            "funcaptcha",
            "arkoselabs",
            "arkose",
        ),
    ),
    (
        "datadome",
        (
            "datadome",
            "captcha-delivery.com",
        ),
    ),
    (
        "perimeterx",
        (
            "perimeterx",
            "px-captcha",
            "human challenge",
        ),
    ),
    (
        "akamai",
        (
            "akamai",
            "ak-challenge",
            "_abck",
        ),
    ),
    (
        "aws_waf",
        (
            "aws-waf",
            "awswaf",
            "amazon web services",
        ),
    ),
    (
        "generic_captcha",
        (
            "verify you are human",
            "are you a robot",
            "complete the captcha",
            "solve the captcha",
            "captcha",
            "bot detection",
        ),
    ),
]


def detect_captcha_type(html: str) -> str | None:
    """Return captcha vendor/type if page HTML looks like a challenge."""
    if not html:
        return None
    lowered = html.lower()
    for captcha_type, needles in CAPTCHA_SIGNATURES:
        if any(needle in lowered for needle in needles):
            return captcha_type
    return None


def log_captcha(
    *,
    branch: str,
    url: str,
    captcha_type: str,
    path: str | Path = "captcha_log.csv",
) -> None:
    """Append captcha sighting: branch, url, type, timestamp."""
    file_path = Path(path)
    write_header = not file_path.exists() or file_path.stat().st_size == 0
    with file_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["timestamp", "branch", "url", "captcha_type"])
        writer.writerow(
            [
                datetime.now(timezone.utc).isoformat(),
                branch,
                url,
                captcha_type,
            ]
        )
    print(
        f"[captcha] branch={branch!r} url={url} type={captcha_type} "
        f"(waiting {CAPTCHA_WAIT_SECONDS}s)"
    )
