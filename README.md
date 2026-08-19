# Web Crawler

Async Python web crawler that stays on one domain, extracts emails from pages, and can batch-process an XLSX directory of websites.

Built with `aiohttp`, Beautiful Soup, `cloudscraper`, and `uv`.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)

## Setup

Put your spreadsheet in the project root (default name below), or pass `--xlsx`.

```bash
cd web_crawler
uv sync
cp .env.example .env
```

## Batch crawl from XLSX

Reads `Branch/Association Name` + `Website` (+ sheet `Email` if present).

```bash
# Bot crawler (fast async)
uv run main.py --batch --crawler bot --concurrency 3 --max-pages 10

# Humanize crawler (no browser driver — cloudscraper + human headers)
uv run main.py --batch --crawler humanize --max-pages 10 --limit 5

# Smoke-test first 5 rows only
uv run main.py --batch --limit 5 --concurrency 2 --max-pages 5
```

Results append to `emails.json`:

```json
{
  "Camp Cosby Family Branch": ["campcosby@ymcabham.org", "info@campcosby.org"]
}
```

Progress is stored in `tracking.json` so re-running `--batch` skips completed sites.

## Redo one URL / branch

```bash
uv run main.py --redo "Camp Cosby Family Branch" --concurrency 3 --max-pages 10
uv run main.py --redo "http://www.campcosby.org" --max-pages 10
```

## Single URL (legacy)

```bash
uv run main.py https://example.com 3 10
```

Writes `report.json` for that crawl.

## Fetch fallback

For each page request:

1. `aiohttp` (15s timeout)
2. if that fails → `cloudscraper` (20s timeout)

Dead sites are skipped quickly; spreadsheet emails are still saved.
## Tests

```bash
uv run -m unittest
```

## Project layout

| File | Role |
|---|---|
| `main.py` | CLI (`--batch`, `--redo`, single URL) |
| `crawl.py` | Bot/async crawler + URL helpers |
| `modules/human_crawler.py` | No-driver humanize crawler (cloudscraper) |
| `modules/email_extractor.py` | Emails from text + `mailto:` |
| `modules/email_store.py` | Append `emails.json` by branch name |
| `modules/tracker.py` | Skip/redo tracking |
| `modules/xlsx_loader.py` | Load branches from XLSX |

## Notes

- Keep concurrency low (about 2–5).
- `emails.json`, `tracking.json`, `.env`, and `*.xlsx` are gitignored.
