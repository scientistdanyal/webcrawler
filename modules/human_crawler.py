"""Humanize crawler using nodriver (undetected Chrome, no chromedriver/Selenium).

Docs: https://ultrafunkamsterdam.github.io/nodriver/
Opens a real Chrome window (headless=False).
Fast by default — waits only when a real Cloudflare/captcha challenge is present.
"""

from __future__ import annotations

import asyncio
import glob
import os
import shutil
from collections import deque
from urllib.parse import urlsplit

import nodriver as uc

from crawl import (
    PageData,
    extract_page_data,
    get_link_priority,
    get_urls_from_html,
    is_crawlable_url,
    normalize_url,
)
from modules.captcha import (
    detect_captcha_type,
    log_captcha,
    wait_seconds_for_captcha,
)

BROWSER_ARGS = [
    "--disk-cache-size=1048576",        # Limit disk cache to 1MB
    "--media-cache-size=1048576",       # Limit media cache to 1MB
    "--disable-gpu-shader-disk-cache",  # Do not write shader binaries to disk
    "--disable-application-cache",
    "--disable-component-update",
    "--no-crash-upload",
    "--disable-breakpad",
]


def cleanup_stale_uc_temp_dirs() -> None:
    """Remove leftover nodriver temp directories from %TEMP% / $TMPDIR."""
    import tempfile
    temp_dir = tempfile.gettempdir()
    for uc_dir in glob.glob(os.path.join(temp_dir, "uc_*")):
        try:
            shutil.rmtree(uc_dir, ignore_errors=True)
        except Exception:
            pass


class HumanCrawler:
    """Visible undetected Chrome via nodriver (no Selenium/chromedriver)."""

    def __init__(
        self,
        base_url: str,
        max_pages: int,
        *,
        branch_name: str = "",
        headless: bool = False,
        max_consecutive_blocks: int = 5,
    ) -> None:
        self.base_url = base_url
        self.base_domain = urlsplit(base_url).netloc
        self.max_pages = max(1, max_pages)
        self.branch_name = branch_name or base_url
        self.headless = headless
        self.max_consecutive_blocks = max_consecutive_blocks
        self.consecutive_blocks = 0
        self.page_data: dict[str, PageData] = {}
        self.visited: set[str] = set()

    async def _get_html(self, tab: uc.Tab) -> str:
        try:
            html = await asyncio.wait_for(tab.get_content(), timeout=10)
            if isinstance(html, str) and html.strip():
                return html
        except Exception:
            pass
        try:
            html = await asyncio.wait_for(
                tab.evaluate("document.documentElement.outerHTML"), timeout=10
            )
            if isinstance(html, str):
                return html
        except Exception:
            pass
        return ""

    async def _scroll_page(self, tab: uc.Tab) -> None:
        """Scroll to bottom in bounded steps so lazy-loaded DOM (emails/links) can appear."""
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
            max_steps = 15
            start_time = asyncio.get_event_loop().time()
            max_duration = 5.0

            while position < total_height and steps < max_steps:
                if asyncio.get_event_loop().time() - start_time > max_duration:
                    break
                position = min(position + int(viewport * 0.85), int(total_height))
                await tab.evaluate(f"window.scrollTo(0, {position})")
                await asyncio.sleep(0.2)
                steps += 1
                new_height = await tab.evaluate(
                    "Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
                )
                if isinstance(new_height, (int, float)) and new_height > total_height:
                    # Bound maximum scrollable height to avoid infinite scroll loops
                    total_height = min(new_height, 12000)

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
                tab = await asyncio.wait_for(browser.get(url), timeout=25)
            else:
                tab = await asyncio.wait_for(tab.get(url), timeout=25)

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
            await asyncio.wait_for(self._scroll_page(tab), timeout=8)
            html = await self._get_html(tab)
            return tab, html
        except asyncio.TimeoutError:
            print(f"Timeout fetching {url} in browser")
            return tab, None
        except Exception as e:
            print(f"Error fetching {url}: {e}")
            return tab, None

    async def crawl(self) -> dict[str, PageData]:
        print("[humanize] starting nodriver Chrome (undetected, headless=False)")
        cleanup_stale_uc_temp_dirs()
        queue: deque[str] = deque([self.base_url])
        browser: uc.Browser | None = None
        user_data_dir: str | None = None
        tab: uc.Tab | None = None

        try:
            browser = await uc.start(
                headless=self.headless,
                browser_args=BROWSER_ARGS,
            )
            user_data_dir = getattr(
                getattr(browser, "config", None), "user_data_dir", None
            )

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
                        self.consecutive_blocks += 1
                        if self.consecutive_blocks >= self.max_consecutive_blocks:
                            print(
                                f"Circuit breaker triggered ({self.consecutive_blocks} consecutive fails). "
                                f"Stopping humanize crawl for {self.base_domain}."
                            )
                            break
                        continue

                self.consecutive_blocks = 0
                self.page_data[normalized] = extract_page_data(html, current_url)

                if len(self.visited) >= self.max_pages:
                    break

                next_urls = [
                    u
                    for u in get_urls_from_html(html, current_url)
                    if is_crawlable_url(u, self.base_domain)
                ]
                next_urls.sort(key=get_link_priority)

                for next_url in next_urls:
                    norm = normalize_url(next_url)
                    if norm in self.visited:
                        continue
                    if any(normalize_url(u) == norm for u in queue):
                        continue
                    queue.append(next_url)
        finally:
            if browser is not None:
                try:
                    browser.stop()
                except Exception:
                    pass
            # Force clean up user data directory if nodriver left it behind
            if user_data_dir and os.path.exists(user_data_dir):
                for _ in range(5):
                    try:
                        shutil.rmtree(user_data_dir, ignore_errors=True)
                        if not os.path.exists(user_data_dir):
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(0.2)

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


