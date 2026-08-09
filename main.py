import sys
import asyncio

from dotenv import load_dotenv

from crawl import crawl_site_async
from json_report import write_json_report

load_dotenv()


async def main() -> None:
    args = sys.argv
    if len(args) < 2:
        print("no website provided")
        sys.exit(1)
    if len(args) < 3:
        print("no max concurrency provided")
        sys.exit(1)
    if len(args) < 4:
        print("no max pages provided")
        sys.exit(1)
    if len(args) > 4:
        print("too many arguments provided")
        sys.exit(1)

    base_url = args[1]
    max_concurrency = int(args[2])
    max_pages = int(args[3])

    print(f"Starting async crawl of: {base_url}")

    page_data = await crawl_site_async(base_url, max_concurrency, max_pages)
    write_json_report(page_data)

    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
