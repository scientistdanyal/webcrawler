import re
from urllib.parse import unquote

from bs4 import BeautifulSoup, Tag

EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)


def extract_emails_from_html(html: str) -> list[str]:
    """Extract unique email addresses from page HTML and mailto links."""
    emails: set[str] = set()
    soup = BeautifulSoup(html, "html.parser")

    for anchor in soup.find_all("a"):
        if not isinstance(anchor, Tag):
            continue
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        href = unquote(href).strip()
        if href.lower().startswith("mailto:"):
            address = href[7:].split("?", 1)[0].strip()
            if EMAIL_PATTERN.fullmatch(address):
                emails.add(address.lower())

    for match in EMAIL_PATTERN.findall(soup.get_text(" ", strip=True)):
        emails.add(match.lower())

    return sorted(emails)
