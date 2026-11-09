# CLAUDE.md

Context for Claude Code when working in this repo.

## What this is

A static new-book tracker. One Python script pulls from free public book APIs according to `watch.yaml`, writes `data/books.json` + `feed.xml`, and a single-file frontend (`index.html`) renders it. GitHub Actions runs the script daily and commits the result. Deployed via GitHub Pages from the repo root. No server, no database, no build step.

## Layout

- `watch.yaml` — the only thing the owner edits day to day: categories / publishers / authors to track, plus thresholds.
- `scripts/fetch_books.py` — fetch → normalize → dedupe → merge with existing JSON → write JSON + RSS. Pure stdlib + `requests` + `pyyaml`.
- `data/books.json` — generated. Committed by the Action. Do not hand-edit.
- `feed.xml` — generated RSS. Same.
- `index.html` — vanilla HTML/CSS/JS, no framework. Filters are built from `book.matched` at runtime.
- `.github/workflows/update.yml` — daily cron + manual trigger.

## Conventions

- Keep the frontend dependency-free and in one file unless there's a strong reason; GitHub Pages serves it as-is.
- Keep the script runnable with `python scripts/fetch_books.py --dry-run` offline-ish (network failures are logged and skipped, never fatal).
- Dedup key is ISBN-13; fall back to sha1(title|sorted authors). Don't change the `id` scheme without migrating `books.json`.
- `first_seen` is the source of truth for "new today". API `publishedDate` values are unreliable (year-only, future-dated, etc.).
- Each source lives in its own function returning the normalized dict shape in `google_books()` — a new source should do the same and get wired into `collect()`.
- Comments and UI strings are in Chinese; code identifiers in English.

## Adding a data source

1. Write `def my_source(params, cfg) -> list[dict]` in `fetch_books.py` returning the same keys as `google_books()`.
2. Add a `sources.my_source: true` toggle to `watch.yaml` and check it in `collect()`.
3. Be polite: `time.sleep` between calls, set the User-Agent, handle 429.

Candidates worth adding:
- Publisher RSS / "forthcoming" pages (Springer, MIT Press, O'Reilly) — most accurate dates.
- RSSHub routes for Chinese publishers (中信, 机械工业, 人民邮电 etc.) — Google Books/Open Library coverage of Chinese titles is poor.
- arXiv isn't books, but an `arxiv` source for monographs/lecture notes could reuse the same pipeline.

## Roadmap (not started)

- [x] Per-category RSS feeds: `feed/<slug>.xml` + `feed/index.json` (ASCII names use a readable slug, others `c-<sha1[:8]>`). Publisher / author feeds not done.
- [ ] "Mark as read / saved" using localStorage (wrap in try/catch).
- [ ] Split `books.json` by month once it passes ~5 MB.
- [ ] Optional LLM pass to auto-tag or summarize new entries (would need an API key secret).
- [ ] Email / push digest from the Action when `new_today > 0`.

## Testing

No test suite yet. Smoke test: `python scripts/fetch_books.py --dry-run` should print a list without tracebacks; `python -m http.server` then open `index.html` and check filters populate.
