from __future__ import annotations

import argparse
import asyncio
import sys

from dotenv import load_dotenv

from crawl import crawl_site_async
from json_report import write_json_report
from modules.email_store import EmailStore
from modules.proxies import load_proxies
from modules.tracker import CrawlTracker
from modules.xlsx_loader import BranchRow, load_branches_from_xlsx

load_dotenv()


def collect_emails(page_data: dict, sheet_email: str | None = None) -> list[str]:
    emails: set[str] = set()
    for page in page_data.values():
        emails.update(page.get("emails", []))
    if sheet_email:
        emails.add(sheet_email.strip().lower())
    return sorted(emails)


async def process_branch(
    branch: BranchRow,
    *,
    max_concurrency: int,
    max_pages: int,
    email_store: EmailStore,
    tracker: CrawlTracker,
    proxies: list[str],
    force: bool = False,
) -> None:
    if not force and tracker.is_done(branch.website):
        print(f"Skipping (already done): {branch.name}")
        return

    print(f"\n=== {branch.name} ===")
    print(f"Crawling: {branch.website}")

    try:
        page_data = await crawl_site_async(
            branch.website,
            max_concurrency,
            max_pages,
            proxies=proxies,
        )
        emails = collect_emails(page_data, branch.sheet_email)
        merged = email_store.append(branch.name, emails)
        tracker.mark(
            branch.website,
            name=branch.name,
            status="done",
            emails_found=len(merged),
        )
        print(f"Saved {len(merged)} email(s) for {branch.name}")
    except Exception as e:
        tracker.mark(
            branch.website,
            name=branch.name,
            status="failed",
            error=str(e),
        )
        print(f"Failed {branch.name}: {e}")


async def run_batch(args: argparse.Namespace) -> None:
    branches = load_branches_from_xlsx(args.xlsx)
    email_store = EmailStore(args.emails_out)
    tracker = CrawlTracker(args.tracking)
    proxies = load_proxies(args.proxy_file)

    print(f"Loaded {len(branches)} branches with websites")
    print(f"Proxies available: {len(proxies)}")

    if args.limit is not None:
        branches = branches[: args.limit]

    for branch in branches:
        await process_branch(
            branch,
            max_concurrency=args.concurrency,
            max_pages=args.max_pages,
            email_store=email_store,
            tracker=tracker,
            proxies=proxies,
            force=False,
        )


async def run_redo(args: argparse.Namespace) -> None:
    branches = load_branches_from_xlsx(args.xlsx)
    email_store = EmailStore(args.emails_out)
    tracker = CrawlTracker(args.tracking)
    proxies = load_proxies(args.proxy_file)

    query = args.redo.strip()
    match = next(
        (
            b
            for b in branches
            if b.website.lower() == query.lower() or b.name.lower() == query.lower()
        ),
        None,
    )
    if match is None:
        tracked = tracker.find_website(query)
        if tracked:
            match = next((b for b in branches if b.website == tracked), None)

    if match is None:
        print(f"No branch found for redo target: {query}")
        sys.exit(1)

    tracker.clear(match.website)
    await process_branch(
        match,
        max_concurrency=args.concurrency,
        max_pages=args.max_pages,
        email_store=email_store,
        tracker=tracker,
        proxies=proxies,
        force=True,
    )


async def run_single(args: argparse.Namespace) -> None:
    proxies = load_proxies(args.proxy_file)
    print(f"Starting async crawl of: {args.url}")
    page_data = await crawl_site_async(
        args.url, args.concurrency, args.max_pages, proxies=proxies
    )
    write_json_report(page_data, args.report_out)
    print(f"Wrote {args.report_out}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Crawl websites and extract emails from an XLSX directory or a single URL."
    )
    parser.add_argument(
        "url",
        nargs="?",
        help="Single starting URL (legacy mode)",
    )
    parser.add_argument(
        "concurrency_pos",
        nargs="?",
        type=int,
        help="Max concurrency (legacy positional)",
    )
    parser.add_argument(
        "max_pages_pos",
        nargs="?",
        type=int,
        help="Max pages (legacy positional)",
    )
    parser.add_argument(
        "--xlsx",
        default="YMCA_National_Directory_2021_Branches.xlsx",
        help="Path to XLSX with Website + Branch/Association Name columns",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Max concurrent page fetches (default: 3)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=10,
        help="Max pages to crawl per site (default: 10)",
    )
    parser.add_argument(
        "--emails-out",
        default="emails.json",
        help="Append-only emails JSON output (default: emails.json)",
    )
    parser.add_argument(
        "--tracking",
        default="tracking.json",
        help="Tracking file for completed/failed URLs (default: tracking.json)",
    )
    parser.add_argument(
        "--proxy-file",
        default="ip_list.txt",
        help="Proxy list file used after cloudscraper fails (default: ip_list.txt)",
    )
    parser.add_argument(
        "--report-out",
        default="report.json",
        help="JSON report path for single-URL mode (default: report.json)",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Process all websites from the XLSX (skips URLs already marked done)",
    )
    parser.add_argument(
        "--redo",
        metavar="URL_OR_NAME",
        help="Re-crawl one branch by website URL or Branch/Association Name",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N branches in batch mode",
    )
    return parser


async def async_main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.concurrency_pos is not None:
        args.concurrency = args.concurrency_pos
    if args.max_pages_pos is not None:
        args.max_pages = args.max_pages_pos

    if args.redo:
        await run_redo(args)
        return

    if args.batch:
        await run_batch(args)
        return

    if args.url:
        await run_single(args)
        return

    parser.print_help()
    print(
        "\nExamples:\n"
        "  uv run main.py --batch --concurrency 3 --max-pages 10\n"
        "  uv run main.py --redo 'Camp Cosby Family Branch' --max-pages 10\n"
        "  uv run main.py https://example.com 3 10\n"
    )
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(async_main())
