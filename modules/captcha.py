from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from pathlib import Path

CAPTCHA_WAIT_SECONDS = 10
CLOUDFLARE_WAIT_SECONDS = 20


def wait_seconds_for_captcha(captcha_type: str) -> int:
    if captcha_type == "cloudflare":
        return CLOUDFLARE_WAIT_SECONDS
    return CAPTCHA_WAIT_SECONDS


# Strict challenge-only signatures (avoid CDN footprints like data-cfasync)
CLOUDFLARE_CHALLENGE_PATTERNS = (
    r"cdn-cgi/challenge-platform",
    r"challenges\.cloudflare\.com",
    r"cf-browser-verification",
    r"id=[\"']challenge-form[\"']",
    r"checking your browser before accessing",
    r"attention required!\s*\|\s*cloudflare",
    r"<title[^>]*>\s*just a moment\.\.\.\s*</title>",
    r"cf-please-wait",
    r"_cf_chl_opt",
    r"cf-turnstile-response",
    r"managed_challenge",
    r"cf-challenge-running",
)

OTHER_CAPTCHA_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    (
        "recaptcha",
        (
            r"google\.com/recaptcha",
            r"g-recaptcha",
            r"grecaptcha\.execute",
        ),
    ),
    (
        "hcaptcha",
        (
            r"hcaptcha\.com/1/api",
            r"h-captcha",
        ),
    ),
    (
        "funcaptcha",
        (
            r"funcaptcha",
            r"arkoselabs\.com",
        ),
    ),
    (
        "datadome",
        (
            r"captcha-delivery\.com",
            r"datadome\.co/captcha",
        ),
    ),
    (
        "perimeterx",
        (
            r"px-captcha",
            r"perimeterx\.net/.*captcha",
        ),
    ),
]


def detect_captcha_type(html: str) -> str | None:
    """Return captcha type only for real challenge pages (not normal CF-CDN sites)."""
    if not html:
        return None
    lowered = html.lower()

    # Cloudflare: require challenge markers, not generic CF CDN attrs
    for pattern in CLOUDFLARE_CHALLENGE_PATTERNS:
        if re.search(pattern, lowered):
            return "cloudflare"

    # Small challenge pages often have almost no real content
    # (keep as secondary CF signal with title)
    if (
        "<title>just a moment...</title>" in lowered
        or "enable javascript and cookies to continue" in lowered
    ) and ("cloudflare" in lowered or "cf-" in lowered):
        return "cloudflare"

    for captcha_type, patterns in OTHER_CAPTCHA_PATTERNS:
        if any(re.search(pattern, lowered) for pattern in patterns):
            return captcha_type

    # Explicit human-verification phrases only (not bare word "captcha")
    if re.search(
        r"verify you are human|are you a robot|complete the security check",
        lowered,
    ):
        return "generic_captcha"

    return None


def log_captcha(
    *,
    branch: str,
    url: str,
    captcha_type: str,
    path: str | Path = "captcha_log.csv",
    wait_seconds: int | None = None,
) -> None:
    """Append captcha sighting: branch, url, type, timestamp."""
    seconds = (
        wait_seconds
        if wait_seconds is not None
        else wait_seconds_for_captcha(captcha_type)
    )
    file_path = Path(path)
    write_header = not file_path.exists() or file_path.stat().st_size == 0
    with file_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(
                ["timestamp", "branch", "url", "captcha_type", "wait_seconds"]
            )
        writer.writerow(
            [
                datetime.now(timezone.utc).isoformat(),
                branch,
                url,
                captcha_type,
                seconds,
            ]
        )
    print(
        f"[captcha] branch={branch!r} url={url} type={captcha_type} "
        f"(waiting {seconds}s)"
    )
