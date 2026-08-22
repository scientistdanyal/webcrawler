import asyncio
import os
import re
from types import TracebackType
from typing import TypedDict
from urllib.parse import urldefrag, urljoin, urlsplit

import aiohttp
import cloudscraper
from bs4 import BeautifulSoup, Tag

from modules.email_extractor import extract_emails_from_html
from modules.captcha import (
    detect_captcha_type,
    log_captcha,
    wait_seconds_for_captcha,
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/58.0.3029.110 Safari/537.3"
)

ERROR_BACKOFF_SECONDS = 5
BLOCK_STATUSES = {403, 429, 503}
MAX_PATH_DEPTH = 6
MAX_SEGMENT_REPEATS = 2

SKIP_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".zip",
    ".rar",
    ".7z",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".css",
    ".js",
    ".mjs",
    ".map",
    ".json",
    ".xml",
    ".rss",
    ".mp3",
    ".mp4",
    ".avi",
    ".mov",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
}

TRAP_PATTERNS = (
    # Calendar & date loops
    r"/calendar(/|$)",
    r"/events?/\d{4}",
    r"/\d{4}/\d{1,2}/\d{1,2}",
    r"/\d{4}/\d{2}(/|$)",
    r"/month/\d{4}-\d{2}",
    r"tribe_paged=",
    r"eventdisplay=",
    r"eventdate=",
    # Deep pagination & endless feeds
    r"/page/\d{2,}",
    r"/p/\d{2,}",
    r"/feed(/|$)",
    r"/comments/feed",
    r"/trackback",
    # Tags & author archives
    r"/tag/[^/]+(/|$)",
    r"/author/[^/]+(/|$)",
    r"/archives?/\d{4}",
    # E-commerce, carts, accounts, auth, search
    r"/cart(/|$)",
    r"/checkout(/|$)",
    r"/my-account",
    r"/account(/|$)",
    r"/login(/|$)",
    r"/signup(/|$)",
    r"/register(/|$)",
    r"/wp-json(/|$)",
    r"/wp-admin",
    r"/xmlrpc\.php",
    r"[?&](s|search|filter|sort|order|orderby)=",
)

PRIORITY_KEYWORDS = (
    "contact",
    "about",
    "staff",
    "team",
    "leadership",
    "board",
    "directory",
    "location",
    "connect",
    "reach",
    "people",
    "executive",
    "office",
)

URL_IN_TEXT_RE = re.compile(
    r"""https?://[^\s<>\"'\)\]\},]+""",
    re.IGNORECASE,
)


class PageData(TypedDict):
    url: str
    emails: list[str]


def strip_www(host: str) -> str:
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def normalize_url(url: str) -> str:
    url, _fragment = urldefrag(url)
    parsed_url = urlsplit(url)
    host = strip_www(parsed_url.netloc)
    path = parsed_url.path or "/"
    # Drop default index filenames that cause duplicate paths
    for index_name in ("/index.html", "/index.htm", "/index.php"):
        if path.lower().endswith(index_name):
            path = path[: -len(index_name) + 1]  # keep trailing slash then rstrip
            break
    full_path = f"{host}{path}".rstrip("/").lower()
    return full_path


def is_spider_trap(url: str) -> bool:
    """Detect infinite repeating directory segments or excessively deep paths."""
    try:
        parsed = urlsplit(url)
        path = (parsed.path or "").strip("/").lower()
        if not path:
            return False
        segments = [s for s in path.split("/") if s]
        if len(segments) > MAX_PATH_DEPTH:
            return True

        counts: dict[str, int] = {}
        for s in segments:
            counts[s] = counts.get(s, 0) + 1
            if counts[s] >= MAX_SEGMENT_REPEATS:
                return True
        return False
    except Exception:
        return True


def get_link_priority(url: str) -> int:
    """Assign lower numbers (higher priority) to contact/staff/about URLs."""
    path = urlsplit(url).path.lower()
    for i, keyword in enumerate(PRIORITY_KEYWORDS):
        if keyword in path:
            return i
    return len(PRIORITY_KEYWORDS) + 1


def is_crawlable_url(url: str, base_domain: str) -> bool:
    try:
        parsed = urlsplit(url)
    except Exception:
        return False

    if parsed.scheme not in ("http", "https"):
        return False
    if strip_www(parsed.netloc) != strip_www(base_domain):
        return False

    path = (parsed.path or "").lower()
    for ext in SKIP_EXTENSIONS:
        if path.endswith(ext):
            return False

    if is_spider_trap(url):
        return False

    url_lower = url.lower()
    for pattern in TRAP_PATTERNS:
        if re.search(pattern, url_lower):
            return False

    return True


def get_heading_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    h_tag = soup.find("h1") or soup.find("h2")
    return h_tag.get_text(strip=True) if isinstance(h_tag, Tag) else ""


def get_first_paragraph_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    main_section = soup.find("main")
    if isinstance(main_section, Tag):
        first_p = main_section.find("p")
    else:
        first_p = soup.find("p")

    return first_p.get_text(strip=True) if isinstance(first_p, Tag) else ""


def _add_url(candidate: str, page_url: str, found: dict[str, str]) -> None:
    href = candidate.strip()
    if not href or href.startswith("#"):
        return
    lower = href.lower()
    if lower.startswith(("mailto:", "tel:", "javascript:", "data:")):
        return
    try:
        absolute = urljoin(page_url, href)
        absolute, _ = urldefrag(absolute)
    except Exception as e:
        print(f"{str(e)}: {href}")
        return

    key = normalize_url(absolute)
    if key and key not in found:
        found[key] = absolute


def get_urls_from_html(html: str, page_url: str) -> list[str]:
    """Collect unique same-page links via several HTML techniques."""
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, str] = {}

    # 1) Classic anchors + image maps
    for tag in soup.find_all(["a", "area"]):
        if not isinstance(tag, Tag):
            continue
        href = tag.get("href")
        if isinstance(href, str):
            _add_url(href, page_url, found)

    # 2) Canonical / alternate links (skip stylesheets/icons)
    for tag in soup.find_all("link"):
        if not isinstance(tag, Tag):
            continue
        href = tag.get("href")
        if not isinstance(href, str):
            continue
        rel = tag.get("rel")
        rel_values = []
        if isinstance(rel, list):
            rel_values = [str(r).lower() for r in rel]
        elif isinstance(rel, str):
            rel_values = [rel.lower()]
        if any(r in {"stylesheet", "icon", "preload", "dns-prefetch"} for r in rel_values):
            continue
        _add_url(href, page_url, found)

    # 3) Meta refresh redirects
    for tag in soup.find_all("meta"):
        if not isinstance(tag, Tag):
            continue
        http_equiv = tag.get("http-equiv")
        if not isinstance(http_equiv, str) or http_equiv.lower() != "refresh":
            continue
        content = tag.get("content")
        if not isinstance(content, str):
            continue
        match = re.search(r"url=([^\s;]+)", content, flags=re.IGNORECASE)
        if match:
            _add_url(match.group(1).strip("'\""), page_url, found)

    # 4) Absolute URLs embedded in raw HTML / scripts
    for match in URL_IN_TEXT_RE.findall(html):
        cleaned = match.rstrip(".,;:)]}>'\"")
        _add_url(cleaned, page_url, found)

    return list(found.values())


def get_images_from_html(html: str, base_url: str) -> list[str]:
    image_urls = []
    soup = BeautifulSoup(html, "html.parser")
    images = soup.find_all("img")

    for img in images:
        if not isinstance(img, Tag):
            continue
        src = img.get("src")
        if isinstance(src, str) and src:
            try:
                absolute_url = urljoin(base_url, src)
                image_urls.append(absolute_url)
            except Exception as e:
                print(f"{str(e)}: {src}")

    return image_urls


def extract_page_data(html: str, page_url: str) -> PageData:
    return {
        "url": page_url,
        "emails": extract_emails_from_html(html),
    }


def _default_headers() -> dict[str, str]:
    return {
        "User-Agent": os.getenv("USER_AGENT", DEFAULT_USER_AGENT),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def _fetch_with_cloudscraper(url: str) -> tuple[str | None, int | None]:
    scraper = cloudscraper.create_scraper()
    try:
        response = scraper.get(url, headers=_default_headers(), timeout=20)
        if response.status_code > 399:
            print(f"Error: HTTP {response.status_code} for {url} (cloudscraper)")
            return None, response.status_code
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type:
            print(f"Error: Non-HTML content {content_type} for {url}")
            return None, response.status_code
        return response.text, response.status_code
    except Exception as e:
        print(f"Error fetching {url} with cloudscraper: {e}")
        return None, None


class AsyncCrawler:
    """BFS priority-queue async crawler with circuit breakers and trap protection."""

    def __init__(
        self,
        base_url: str,
        max_concurrency: int,
        max_pages: int,
        *,
        branch_name: str = "",
        max_consecutive_blocks: int = 5,
    ) -> None:
        self.base_url = base_url
        self.base_domain = urlsplit(base_url).netloc
        self.page_data: dict[str, PageData] = {}
        self.visited: set[str] = set()
        self.enqueued: set[str] = set()
        self.lock = asyncio.Lock()
        self.max_concurrency = max(1, max_concurrency)
        self.max_pages = max(1, max_pages)
        self.should_stop = False
        self.session: aiohttp.ClientSession | None = None
        self.branch_name = branch_name or base_url
        self.max_consecutive_blocks = max_consecutive_blocks
        self.consecutive_blocks = 0
        self._seq = 0
        self.queue: asyncio.PriorityQueue[tuple[int, int, str]] = (
            asyncio.PriorityQueue()
        )

    async def __aenter__(self) -> "AsyncCrawler":
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self.session is not None:
            await self.session.close()

    def _enqueue(self, url: str) -> bool:
        if not is_crawlable_url(url, self.base_domain):
            return False
        norm = normalize_url(url)
        if norm in self.visited or norm in self.enqueued:
            return False
        self.enqueued.add(norm)
        priority = get_link_priority(url)
        self._seq += 1
        self.queue.put_nowait((priority, self._seq, url))
        return True

    async def _backoff(self, reason: str) -> None:
        print(f"Backing off {ERROR_BACKOFF_SECONDS}s ({reason})")
        await asyncio.sleep(ERROR_BACKOFF_SECONDS)

    async def _handle_captcha(self, url: str, html: str) -> str | None:
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
        return html

    async def _fetch_aiohttp(self, url: str) -> tuple[str | None, int | None]:
        if self.session is None:
            return None, None
        try:
            async with self.session.get(
                url, headers=_default_headers(), timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                if response.status > 399:
                    print(f"Error: HTTP {response.status} for {url}")
                    return None, response.status

                content_type = response.headers.get("content-type", "")
                if "text/html" not in content_type:
                    print(f"Error: Non-HTML content {content_type} for {url}")
                    return None, response.status

                return await response.text(), response.status
        except Exception as e:
            print(f"Error fetching {url}: {e}")
            return None, None

    async def get_html(self, url: str) -> str | None:
        if self.should_stop:
            return None

        html, status = await self._fetch_aiohttp(url)
        if html is not None:
            return await self._handle_captcha(url, html)

        if status in BLOCK_STATUSES:
            await self._backoff(f"blocked status={status}")

        if self.should_stop:
            return None

        print(f"Retrying with cloudscraper: {url}")
        try:
            html, status = await asyncio.wait_for(
                asyncio.to_thread(_fetch_with_cloudscraper, url),
                timeout=25,
            )
        except asyncio.TimeoutError:
            print(f"Cloudscraper timed out for {url}")
            return None
        except Exception as e:
            print(f"Cloudscraper error for {url}: {e}")
            return None

        if html is not None:
            return await self._handle_captcha(url, html)

        if status in BLOCK_STATUSES:
            await self._backoff(f"cloudscraper blocked status={status}")
        return None

    async def _worker(self) -> None:
        while not self.should_stop:
            try:
                _priority, _seq, current_url = await self.queue.get()
            except asyncio.CancelledError:
                break

            normalized_url = normalize_url(current_url)

            async with self.lock:
                if self.should_stop or len(self.visited) >= self.max_pages:
                    self.should_stop = True
                    self.queue.task_done()
                    break
                if normalized_url in self.visited:
                    self.queue.task_done()
                    continue
                self.visited.add(normalized_url)
                current_count = len(self.visited)

            print(
                f"Crawling {current_url} "
                f"(visited {current_count}/{self.max_pages}, "
                f"queue {self.queue.qsize()})"
            )

            try:
                html = await self.get_html(current_url)
            except Exception as e:
                print(f"Error fetching {current_url}: {e}")
                html = None

            if html is not None:
                self.consecutive_blocks = 0
                page_info = extract_page_data(html, current_url)
                async with self.lock:
                    self.page_data[normalized_url] = page_info

                if len(self.visited) >= self.max_pages:
                    self.should_stop = True
                    self.queue.task_done()
                    break

                next_urls = get_urls_from_html(html, current_url)
                for next_url in next_urls:
                    if self.should_stop:
                        break
                    self._enqueue(next_url)
            else:
                self.consecutive_blocks += 1
                if self.consecutive_blocks >= self.max_consecutive_blocks:
                    print(
                        f"Circuit breaker triggered ({self.consecutive_blocks} consecutive blocked/failed requests). "
                        f"Stopping crawl for {self.base_domain}."
                    )
                    self.should_stop = True

            self.queue.task_done()

            if self.should_stop:
                break

    async def crawl(self) -> dict[str, PageData]:
        self._enqueue(self.base_url)
        if self.queue.empty():
            return self.page_data

        worker_count = min(self.max_concurrency, self.max_pages)
        tasks = [
            asyncio.create_task(self._worker(), name=f"crawler-worker-{i}")
            for i in range(worker_count)
        ]

        queue_join_task = asyncio.create_task(self.queue.join())

        while not self.should_stop and not self.queue.empty():
            done, _ = await asyncio.wait(
                [queue_join_task],
                timeout=0.5,
            )
            if queue_join_task in done:
                break
            if len(self.visited) >= self.max_pages or self.should_stop:
                self.should_stop = True
                break

        self.should_stop = True
        if not queue_join_task.done():
            queue_join_task.cancel()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        return self.page_data


async def crawl_site_async(
    base_url: str,
    max_concurrency: int,
    max_pages: int,
    *,
    branch_name: str = "",
) -> dict[str, PageData]:
    async with AsyncCrawler(
        base_url, max_concurrency, max_pages, branch_name=branch_name
    ) as crawler:
        return await crawler.crawl()

