"""Human-like crawler with NO browser driver.

Uses cloudscraper/requests sessions only — realistic headers, cookies, referers.
Waits only on captcha / HTTP block (403/429/503).
"""

from __future__ import annotations

import asyncio
import random
from collections import deque
from urllib.parse import urlsplit

import cloudscraper

from crawl import (
    PageData,
    extract_page_data,
    get_urls_from_html,
    is_crawlable_url,
    normalize_url,
)
from modules.captcha import (
    CAPTCHA_WAIT_SECONDS,
    detect_captcha_type,
    log_captcha,
)

BLOCK_STATUSES = {403, 429, 503}

PRIORITY_KEYWORDS = (
    "contact",
    "about",
    "staff",
    "team",
    "directory",
    "location",
    "connect",
    "reach",
)

USER_AGENTS = [
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
]


def _link_priority(url: str) -> int:
    path = urlsplit(url).path.lower()
    for i, keyword in enumerate(PRIORITY_KEYWORDS):
        if keyword in path:
            return i
    return len(PRIORITY_KEYWORDS) + 1


def _headers(user_agent: str, referer: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": user_agent,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none" if referer is None else "same-origin",
        "Sec-Fetch-User": "?1",
    }
    if referer:
        headers["Referer"] = referer
    return headers


class HumanCrawler:
    """Sequential no-driver crawler (cloudscraper session only)."""

    def __init__(
        self,
        base_url: str,
        max_pages: int,
        *,
        branch_name: str = "",
        block_wait: float = 5.0,
    ) -> None:
        self.base_url = base_url
        self.base_domain = urlsplit(base_url).netloc
        self.max_pages = max_pages
        self.branch_name = branch_name or base_url
        self.block_wait = block_wait
        self.page_data: dict[str, PageData] = {}
        self.visited: set[str] = set()
        self.user_agent = random.choice(USER_AGENTS)
        self.scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )

    def _fetch(self, url: str, referer: str | None) -> tuple[str | None, int | None]:
        try:
            response = self.scraper.get(
                url,
                headers=_headers(self.user_agent, referer),
                timeout=20,
            )
            status = response.status_code
            if status in BLOCK_STATUSES:
                print(f"Blocked HTTP {status} for {url}")
                return None, status
            if status > 399:
                print(f"Error: HTTP {status} for {url}")
                return None, status
            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type:
                print(f"Error: Non-HTML content {content_type} for {url}")
                return None, status
            return response.text, status
        except Exception as e:
            print(f"Error fetching {url}: {e}")
            return None, None

    async def _handle_captcha(self, url: str, html: str) -> str:
        captcha_type = detect_captcha_type(html)
        if captcha_type is None:
            return html
        log_captcha(branch=self.branch_name, url=url, captcha_type=captcha_type)
        await asyncio.sleep(CAPTCHA_WAIT_SECONDS)
        return html

    async def crawl(self) -> dict[str, PageData]:
        print("[humanize] no-driver mode (cloudscraper, no Playwright)")
        queue: deque[tuple[str, str | None]] = deque([(self.base_url, None)])

        while queue and len(self.visited) < self.max_pages:
            current_url, referer = queue.popleft()

            if not is_crawlable_url(current_url, self.base_domain):
                continue

            normalized = normalize_url(current_url)
            if normalized in self.visited:
                continue
            self.visited.add(normalized)

            print(
                f"[humanize] Visiting {current_url} "
                f"({len(self.visited)}/{self.max_pages})"
            )

            html, status = await asyncio.to_thread(self._fetch, current_url, referer)
            if html is None:
                if status in BLOCK_STATUSES:
                    print(f"Waiting {self.block_wait}s after block...")
                    await asyncio.sleep(self.block_wait)
                    html, status = await asyncio.to_thread(
                        self._fetch, current_url, referer
                    )
                if html is None:
                    continue

            html = await self._handle_captcha(current_url, html)
            self.page_data[normalized] = extract_page_data(html, current_url)

            next_urls = [
                u
                for u in get_urls_from_html(html, current_url)
                if is_crawlable_url(u, self.base_domain)
            ]
            next_urls.sort(key=_link_priority)

            for next_url in next_urls:
                norm = normalize_url(next_url)
                if norm in self.visited:
                    continue
                if any(normalize_url(u) == norm for u, _ in queue):
                    continue
                queue.append((next_url, current_url))

        return self.page_data


async def crawl_site_human_async(
    base_url: str,
    max_pages: int,
    *,
    branch_name: str = "",
    headless: bool = False,  # kept for call-site compatibility; unused (no browser)
) -> dict[str, PageData]:
    _ = headless
    crawler = HumanCrawler(
        base_url,
        max_pages,
        branch_name=branch_name,
    )
    return await crawler.crawl()
