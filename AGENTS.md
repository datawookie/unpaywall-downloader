# AGENTS.md — unpaywall-downloader

Guidance for AI agents working on this repository.

## Project overview

`unpaywall-downloader` is a single-file Python CLI (`unpaywall.py`) that downloads open-access PDFs for given DOIs via the [Unpaywall API](https://unpaywall.org/products/api). It has two download strategies:

1. **Primary**: `httpx` async streaming, with 2 retries on transient errors.
2. **Fallback**: `camoufox` — stealth anti-detect Firefox browser (Playwright-based), used when publishers block simple HTTP clients.

Batch downloads run concurrently (`asyncio` + `asyncio.Semaphore`). The tool is designed to be agent-friendly: it accepts `UNPAYWALL_EMAIL` from the environment, sends all progress to stderr, prints a clean JSON block to stdout, and exits non-zero on any failure.

## Repository layout

```
unpaywall.py          # Entire implementation — one file, one module
tests/
  __init__.py
  test_unpaywall.py   # pytest suite
pyproject.toml        # Package metadata and entry-point declaration
uv.lock               # Pinned dependency tree (do not edit manually)
.bumpversion.cfg      # bump2version config (syncs version in pyproject.toml and unpaywall.py)
README.md             # User-facing install and usage docs
PLAN.md               # Enhancement roadmap (can be removed once complete)
```

## Environment setup

This project uses [uv](https://docs.astral.sh/uv/).

```bash
uv sync          # install all dependencies (including dev: pytest, pytest-httpx, pytest-asyncio)
```

Run during development without installing:

```bash
uv run unpaywall --doi 10.1038/s41586-021-03819-2 --email you@example.com
```

Run tests:

```bash
uv run pytest -v
```

`camoufox` requires a browser binary the first time it is used:

```bash
uv run camoufox fetch   # or: python -m camoufox fetch
```

## CLI reference

```
unpaywall [--doi DOI]... [--doi-file PATH] [--output PATH]
          [--email EMAIL] [--concurrency N] [--quiet] [--force-camoufox] [--version]
```

| Flag | Default | Notes |
|------|---------|-------|
| `--doi` | — (repeatable) | Full DOI or `https://doi.org/…` URL — normalised internally |
| `--doi-file PATH` | — | Text file, one DOI per line; `#` lines and blank lines ignored |
| `--email` / `-e` | `$UNPAYWALL_EMAIL` | Required by Unpaywall API; falls back to env var |
| `--output` / `-o` | CWD (auto-named) | Single DOI: exact filename. Batch or directory path: output directory |
| `--concurrency` / `-c` | `3` | Max parallel downloads |
| `--quiet` / `-q` | off | Suppress all stderr progress; JSON still goes to stdout |
| `--force-camoufox` | off | Skip httpx; go straight to stealth browser (debugging) |
| `--version` | — | Print version and exit |

At least one `--doi` or a non-empty `--doi-file` is required.

Exit codes: `0` = all downloads succeeded; `1` = one or more failed.

## Output format

Progress lines go to **stderr**. Only the JSON block goes to **stdout**, making it pipe-safe:

```bash
unpaywall --doi 10.1038/s41586-021-03819-2 | jq '.results[0].success'
```

JSON schema:

```json
{
  "results": [
    {
      "success": true,
      "file_path": "/abs/path/10.1038+s41586-021-03819-2.pdf",
      "doi": "10.1038/s41586-021-03819-2",
      "pdf_url": "https://...",
      "title": "Article title",
      "oa_status": "gold",
      "method": "httpx"
    }
  ]
}
```

On failure, `success` is `false` and `error` contains the reason. A `landing_page` key may be present when a PDF URL couldn't be found but a landing page exists.

## Key design decisions

- **Single-file module**: The whole implementation lives in `unpaywall.py`. Keep it that way.
- **Async throughout**: `main()` calls `asyncio.run(async_main(...))`. All download functions are coroutines. Shared `httpx.AsyncClient` per batch (reuses connection pool).
- **Concurrency model**: `asyncio.Semaphore(N)` wraps only the download block inside `download_pdf`; metadata fetches run concurrently outside the semaphore. A 0.1s stagger between task launches avoids hammering the Unpaywall API simultaneously.
- **Retry before fallback**: `download_with_httpx` retries on `TimeoutException`, `TransportError`, and 5xx twice (delays: 1s, 2s) before the camoufox fallback is tried. 4xx errors skip retries immediately.
- **PDF validation**: `_is_valid_pdf` checks the first 4 bytes for `%PDF`. If a download yields HTML (e.g. a Cloudflare challenge), the file is deleted and the error path is taken.
- **Graceful camoufox degradation**: `camoufox.async_api.AsyncCamoufox` is imported at module load inside `try/except`. If not installed, `CAMOUFOX_AVAILABLE = False` and httpx failures are surfaced directly.
- **Output split**: `eprint(..., quiet=quiet)` sends all human-readable output to stderr. Only `print(json.dumps(...))` goes to stdout. The `--quiet` flag silences stderr entirely.
- **DOI normalisation**: `download_pdf` strips `https://doi.org/` prefixes and lowercases. Keep normalisation in one place.
- **Filename sanitisation**: `sanitize_filename` replaces `/` with `+` and removes other unsafe chars. The `+` preserves DOI structure in the filename.
- **Version tracked in two places**: `VERSION` in `unpaywall.py` and `version` in `pyproject.toml`. Use `bump2version` to update both atomically:
  ```bash
  uv run bump2version patch   # or minor / major
  ```

## Symbol inventory

| Symbol | Kind | Notes |
|--------|------|-------|
| `CAMOUFOX_AVAILABLE` | bool | set at import time |
| `VERSION` | str | synced with pyproject.toml via bump2version |
| `RETRY_DELAYS` | `list[float]` | `[1.0, 2.0]` — delays between httpx retries |
| `eprint(*a, quiet, **kw)` | sync fn | all stderr output goes through here |
| `_is_valid_pdf(path)` | sync fn | check `%PDF` magic bytes |
| `sanitize_filename(doi)` | sync fn | DOI → safe filename |
| `fetch_unpaywall_metadata(client, doi, email, headers)` | async fn | Unpaywall API call |
| `download_with_httpx(client, url, headers, path, quiet)` | async fn | streaming download with retry |
| `download_with_camoufox(url, path, quiet)` | async fn | stealth browser fallback |
| `download_pdf(client, sem, doi, email, path, force_camoufox, quiet)` | async fn | orchestrates one DOI |
| `_download_one(client, sem, doi, email, path, force_camoufox, quiet, i, n)` | async fn | per-task wrapper with progress output |
| `async_main(args, dois, email)` | async fn | creates client, launches tasks, prints JSON |
| `main()` | sync fn | argparse + `asyncio.run(async_main(...))` |

## Working with the code

- **Adding a new download strategy**: add `async def download_with_<name>(url, path, quiet)`, then wire it into the fallback chain in `download_pdf`. Raise on failure, return `True` on success, and call `_is_valid_pdf` after writing.
- **Changing the Unpaywall API call**: modify `fetch_unpaywall_metadata`. Unpaywall v2 docs: `https://unpaywall.org/products/api`.
- **Structured output for agents**: the final `print(json.dumps(...))` in `async_main` is intentional. Do not move it to stderr.
- **Python version**: `requires-python = ">=3.14"`. No compatibility shims for older versions.

## What not to do

- Do not convert the single-file module into a package unless the file grows substantially.
- Do not hard-code an email address anywhere in the source.
- Do not commit `.pdf` files — they are gitignored.
- Do not amend `uv.lock` by hand; run `uv sync` or `uv add` instead.
- Do not move progress output back to stdout — the stdout/stderr split is intentional.
