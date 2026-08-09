# Web Crawler

Async Python web crawler that stays on one domain, extracts page metadata (heading, first paragraph, links, images), and writes a sorted JSON report.

Built with `aiohttp`, Beautiful Soup, and `uv` (Boot.dev guided project).

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
git clone <your-repo-url>
cd web_crawler

# Install dependencies
uv sync

# Create local env file from the example
cp .env.example .env
```

Edit `.env` if you want a custom `USER_AGENT`. Never commit `.env`.

## Usage

```bash
uv run main.py <URL> <max_concurrency> <max_pages>
```

Example:

```bash
uv run main.py https://example.com 3 10
```

| Argument | Description |
|---|---|
| `URL` | Starting URL to crawl (same-domain links only) |
| `max_concurrency` | Max parallel page fetches |
| `max_pages` | Stop after this many unique pages |

After a successful run, results are written to `report.json`.

## Tests

```bash
uv run -m unittest
```

## Project layout

| File | Role |
|---|---|
| `main.py` | CLI entrypoint |
| `crawl.py` | Async crawler + HTML helpers |
| `json_report.py` | Writes `report.json` |
| `test_crawl.py` | Unit tests |
| `.env.example` | Env template (safe to commit) |
| `.env` | Local secrets/config (gitignored) |

## Notes

- Keep `max_concurrency` low (e.g. 3–5) to avoid overloading target sites.
- Use `max_pages` on large sites so the crawl can finish.
- `report.json` is generated output and is gitignored.
