"""Human-like browser crawler with a visible window (headless=False).

Uses Playwright Chromium — real browser, not headless — to reduce bot blocks.
"""

from __future__ import annotations

import asyncio
import random
from collections import deque
from urllib.parse import urlsplit

from playwright.async_api import Browser, Page, async_playwright

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


def _link_priority(url: str) -> int:
    path = urlsplit(url).path.lower()
    for i, keyword in enumerate(PRIORITY_KEYWORDS):
        if keyword in path:
            return i
    return len(PRIORITY_KEYWORDS) + 1


class HumanCrawler:
    """Sequential crawler in a real visible browser (headless=False)."""

    def __init__(
        self,
        base_url: str,
        max_pages: int,
        *,
        branch_name: str = "",
        min_delay: float = 1.5,
        max_delay: float = 4.5,
        error_backoff: float = 5.0,
        headless: bool = False,
    ) -> None:
        self.base_url = base_url
        self.base_domain = urlsplit(base_url).netloc
        self.max_pages = max_pages
        self.branch_name = branch_name or base_url
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.error_backoff = error_backoff
        self.headless = headless
        self.page_data: dict[str, PageData] = {}
        self.visited: set[str] = set()

    async def _think(self) -> None:
        if random.random() < 0.12:
            delay = random.uniform(self.max_delay, self.max_delay + 3.0)
        else:
            delay = random.uniform(self.min_delay, self.max_delay)
        print(f"  (human pause {delay:.1f}s)")
        await asyncio.sleep(delay)

    async def _human_scroll(self, page: Page) -> None:
        """Light scroll like a person skimming the page."""
        try:
            for _ in range(random.randint(1, 3)):
                await page.mouse.wheel(0, random.randint(200, 700))
                await asyncio.sleep(random.uniform(0.3, 0.9))
            if random.random() < 0.4:
                await page.mouse.move(
                    random.randint(80, 900),
                    random.randint(80, 600),
                    steps=random.randint(5, 15),
                )
        except Exception:
            pass

    async def _maybe_wait_for_captcha(self, page: Page, url: str, html: str) -> str:
        captcha_type = detect_captcha_type(html)
        if captcha_type is None:
            return html

        log_captcha(branch=self.branch_name, url=url, captcha_type=captcha_type)
        await asyncio.sleep(CAPTCHA_WAIT_SECONDS)
        # Re-read page in case challenge resolved / user solved it
        try:
            return await page.content()
        except Exception:
            return html

    async def _goto(self, page: Page, url: str) -> str | None:
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if response is not None and response.status in {403, 429, 503}:
                print(
                    f"Blocked/rate-limited HTTP {response.status} for {url}; "
                    f"sleeping {self.error_backoff}s"
                )
                await asyncio.sleep(self.error_backoff)
                return None
            if response is not None and response.status > 399:
                print(f"Error: HTTP {response.status} for {url}")
                await asyncio.sleep(self.error_backoff)
                return None

            # Let late JS settle briefly
            await asyncio.sleep(random.uniform(0.8, 1.8))
            await self._human_scroll(page)
            html = await page.content()
            return await self._maybe_wait_for_captcha(page, url, html)
        except Exception as e:
            print(f"Error fetching {url}: {e}")
            await asyncio.sleep(self.error_backoff)
            return None

    async def _launch_browser(self, playwright) -> Browser:
        """Prefer system Chrome/Edge so Windows works without playwright browser download."""
        launch_args = ["--disable-blink-features=AutomationControlled"]
        errors: list[str] = []

        for label, kwargs in (
            ("system Chrome", {"channel": "chrome"}),
            ("system Edge", {"channel": "msedge"}),
            ("Playwright Chromium", {}),
        ):
            try:
                browser = await playwright.chromium.launch(
                    headless=self.headless,
                    args=launch_args,
                    **kwargs,
                )
                print(f"Browser launched via {label} (headless={self.headless})")
                return browser
            except Exception as e:
                errors.append(f"{label}: {e}")

        details = "\n".join(f"  - {err}" for err in errors)
        raise RuntimeError(
            "Could not launch a browser for humanize mode.\n"
            "Install Google Chrome, or run:\n"
            "  uv run playwright install chromium\n"
            f"Tried:\n{details}"
        )

    async def crawl(self) -> dict[str, PageData]:
        queue: deque[str] = deque([self.base_url])

        async with async_playwright() as playwright:
            browser = await self._launch_browser(playwright)
            context = await browser.new_context(
                viewport={"width": 1365, "height": 900},
                locale="en-US",
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
            )
            # Reduce obvious webdriver fingerprint
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            page = await context.new_page()

            try:
                while queue and len(self.visited) < self.max_pages:
                    current_url = queue.popleft()

                    if not is_crawlable_url(current_url, self.base_domain):
                        continue

                    normalized = normalize_url(current_url)
                    if normalized in self.visited:
                        continue
                    self.visited.add(normalized)

                    await self._think()
                    print(
                        f"[humanize/browser] Visiting {current_url} "
                        f"({len(self.visited)}/{self.max_pages})"
                    )

                    html = await self._goto(page, current_url)
                    if html is None:
                        html = await self._goto(page, current_url)
                        if html is None:
                            continue

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
                        if any(normalize_url(u) == norm for u in queue):
                            continue
                        queue.append(next_url)
            finally:
                await context.close()
                await browser.close()

        return self.page_data


async def crawl_site_human_async(
    base_url: str,
    max_pages: int,
    *,
    branch_name: str = "",
    min_delay: float = 1.5,
    max_delay: float = 4.5,
    headless: bool = False,
) -> dict[str, PageData]:
    crawler = HumanCrawler(
        base_url,
        max_pages,
        branch_name=branch_name,
        min_delay=min_delay,
        max_delay=max_delay,
        headless=headless,
    )
    return await crawler.crawl()
