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
    CAPTCHA_WAIT_SECONDS,
    detect_captcha_type,
    log_captcha,
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/58.0.3029.110 Safari/537.3"
)

ERROR_BACKOFF_SECONDS = 5
BLOCK_STATUSES = {403, 429, 503}

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
    def __init__(
        self,
        base_url: str,
        max_concurrency: int,
        max_pages: int,
        *,
        branch_name: str = "",
    ) -> None:
        self.base_url = base_url
        self.base_domain = urlsplit(base_url).netloc
        self.page_data: dict[str, PageData] = {}
        self.visited: set[str] = set()
        self.lock = asyncio.Lock()
        self.max_concurrency = max_concurrency
        self.max_pages = max_pages
        self.should_stop = False
        self.all_tasks: set[asyncio.Task[None]] = set()
        self.semaphore = asyncio.Semaphore(self.max_concurrency)
        self.session: aiohttp.ClientSession | None = None
        self.branch_name = branch_name or base_url

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

    async def add_page_visit(self, normalized_url: str) -> bool:
        """Reserve a URL immediately so concurrent tasks cannot duplicate it."""
        async with self.lock:
            if self.should_stop:
                return False
            if normalized_url in self.visited:
                return False
            if len(self.visited) >= self.max_pages:
                self.should_stop = True
                print("Reached maximum number of pages to crawl.")
                for task in self.all_tasks:
                    task.cancel()
                return False
            self.visited.add(normalized_url)
            return True

    async def _backoff(self, reason: str) -> None:
        print(f"Backing off {ERROR_BACKOFF_SECONDS}s ({reason})")
        await asyncio.sleep(ERROR_BACKOFF_SECONDS)

    async def _handle_captcha(self, url: str, html: str) -> str | None:
        captcha_type = detect_captcha_type(html)
        if captcha_type is None:
            return html
        log_captcha(branch=self.branch_name, url=url, captcha_type=captcha_type)
        await asyncio.sleep(CAPTCHA_WAIT_SECONDS)
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
        html, status = await self._fetch_aiohttp(url)
        if html is not None:
            return await self._handle_captcha(url, html)

        if status in BLOCK_STATUSES or status is None:
            await self._backoff(f"aiohttp failed status={status}")

        print(f"Retrying with cloudscraper: {url}")
        html, status = await asyncio.to_thread(_fetch_with_cloudscraper, url)
        if html is not None:
            return await self._handle_captcha(url, html)

        await self._backoff(f"cloudscraper failed status={status}")
        return None

    async def crawl_page(self, current_url: str) -> None:
        if self.should_stop:
            return

        if not is_crawlable_url(current_url, self.base_domain):
            return

        normalized_url = normalize_url(current_url)
        is_new = await self.add_page_visit(normalized_url)
        if not is_new:
            return

        async with self.semaphore:
            if self.should_stop:
                return

            print(
                f"Crawling {current_url} "
                f"(visited {len(self.visited)}/{self.max_pages}, "
                f"active {self.max_concurrency - self.semaphore._value})"
            )
            html = await self.get_html(current_url)
            if html is None:
                return

            page_info = extract_page_data(html, current_url)
            async with self.lock:
                self.page_data[normalized_url] = page_info

            # Resolve relative links against the current page, not only the seed URL
            next_urls = [
                u
                for u in get_urls_from_html(html, current_url)
                if is_crawlable_url(u, self.base_domain)
            ]

        # Deduplicate outgoing links before spawning tasks
        unique_next: list[str] = []
        seen_norms: set[str] = set()
        for next_url in next_urls:
            norm = normalize_url(next_url)
            if norm in seen_norms or norm in self.visited:
                continue
            seen_norms.add(norm)
            unique_next.append(next_url)

        tasks: list[asyncio.Task[None]] = []
        for next_url in unique_next:
            if self.should_stop:
                break
            task = asyncio.create_task(self.crawl_page(next_url))
            self.all_tasks.add(task)
            tasks.append(task)

        if tasks:
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                for task in tasks:
                    self.all_tasks.discard(task)

    async def crawl(self) -> dict[str, PageData]:
        await self.crawl_page(self.base_url)
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
