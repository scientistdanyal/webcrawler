"""Humanize crawler using nodriver (undetected Chrome, no chromedriver/Selenium).

Docs: https://ultrafunkamsterdam.github.io/nodriver/
Opens a real Chrome window (headless=False).
Fast by default — waits only when a real Cloudflare/captcha challenge is present.
"""

from __future__ import annotations

import asyncio
from collections import deque
from urllib.parse import urlsplit

import nodriver as uc

from crawl import (
    PageData,
    extract_page_data,
    get_urls_from_html,
    is_crawlable_url,
    normalize_url,
)
from modules.captcha import (
    detect_captcha_type,
    log_captcha,
    wait_seconds_for_captcha,
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
    """Visible undetected Chrome via nodriver (no Selenium/chromedriver)."""

    def __init__(
        self,
        base_url: str,
        max_pages: int,
        *,
        branch_name: str = "",
        headless: bool = False,
    ) -> None:
        self.base_url = base_url
        self.base_domain = urlsplit(base_url).netloc
        self.max_pages = max_pages
        self.branch_name = branch_name or base_url
        self.headless = headless
        self.page_data: dict[str, PageData] = {}
        self.visited: set[str] = set()

    async def _get_html(self, tab: uc.Tab) -> str:
        try:
            html = await tab.get_content()
            if isinstance(html, str) and html.strip():
                return html
        except Exception:
            pass
        try:
            html = await tab.evaluate("document.documentElement.outerHTML")
            if isinstance(html, str):
                return html
        except Exception:
            pass
        return ""

    async def _scroll_page(self, tab: uc.Tab) -> None:
        """Scroll to bottom in steps so lazy-loaded DOM (emails/links) can appear."""
        try:
            total_height = await tab.evaluate(
                "Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
            )
            if not isinstance(total_height, (int, float)) or total_height <= 0:
                total_height = 3000

            viewport = await tab.evaluate("window.innerHeight")
            if not isinstance(viewport, (int, float)) or viewport <= 0:
                viewport = 800

            position = 0
            steps = 0
            max_steps = 25
            while position < total_height and steps < max_steps:
                position = min(position + int(viewport * 0.85), int(total_height))
                await tab.evaluate(f"window.scrollTo(0, {position})")
                await asyncio.sleep(0.25)
                steps += 1
                # Height can grow as lazy content loads
                new_height = await tab.evaluate(
                    "Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
                )
                if isinstance(new_height, (int, float)) and new_height > total_height:
                    total_height = new_height

            # Jump to absolute bottom, then brief settle for network/DOM updates
            await tab.evaluate(
                "window.scrollTo(0, Math.max(document.body.scrollHeight, document.documentElement.scrollHeight))"
            )
            await asyncio.sleep(0.6)
            # Back to top so next navigation feels clean
            await tab.evaluate("window.scrollTo(0, 0)")
        except Exception as e:
            print(f"  (scroll skipped: {e})")

    async def _maybe_wait_for_captcha(self, tab: uc.Tab, url: str, html: str) -> str:
        captcha_type = detect_captcha_type(html)
        if captcha_type is None:
            return html

        wait_s = wait_seconds_for_captcha(captcha_type)
        log_captcha(
            branch=self.branch_name,
            url=url,
            captcha_type=captcha_type,
            wait_seconds=wait_s,
        )
        await asyncio.sleep(wait_s)
        return await self._get_html(tab)

    async def _goto(
        self, browser: uc.Browser, tab: uc.Tab | None, url: str
    ) -> tuple[uc.Tab | None, str | None]:
        try:
            if tab is None:
                tab = await browser.get(url)
            else:
                tab = await tab.get(url)

            html = await self._get_html(tab)
            if not html:
                print(f"Error: empty page content for {url}")
                return tab, None

            # Don't scroll challenge pages; wait first if Cloudflare/captcha
            if detect_captcha_type(html) is not None:
                html = await self._maybe_wait_for_captcha(tab, url, html)
                if detect_captcha_type(html) is not None:
                    return tab, html

            print("  scrolling page for lazy-loaded content...")
            await self._scroll_page(tab)
            html = await self._get_html(tab)
            return tab, html
        except Exception as e:
            print(f"Error fetching {url}: {e}")
            return tab, None

    async def crawl(self) -> dict[str, PageData]:
        print("[humanize] starting nodriver Chrome (undetected, headless=False)")
        queue: deque[str] = deque([self.base_url])
        browser = await uc.start(headless=self.headless)
        tab: uc.Tab | None = None

        try:
            while queue and len(self.visited) < self.max_pages:
                current_url = queue.popleft()

                if not is_crawlable_url(current_url, self.base_domain):
                    continue

                normalized = normalize_url(current_url)
                if normalized in self.visited:
                    continue
                self.visited.add(normalized)

                print(
                    f"[humanize/nodriver] Visiting {current_url} "
                    f"({len(self.visited)}/{self.max_pages})"
                )

                tab, html = await self._goto(browser, tab, current_url)
                if html is None:
                    # Quick retry with no forced wait
                    tab, html = await self._goto(browser, tab, current_url)
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
            try:
                browser.stop()
            except Exception:
                pass

        return self.page_data


async def crawl_site_human_async(
    base_url: str,
    max_pages: int,
    *,
    branch_name: str = "",
    headless: bool = False,
) -> dict[str, PageData]:
    crawler = HumanCrawler(
        base_url,
        max_pages,
        branch_name=branch_name,
        headless=headless,
    )
    return await crawler.crawl()
