# Enhancement plan

Nine steps in dependency order. Tests are written first; each subsequent change is verified
against them before moving on.

| # | Step | Status |
|---|------|--------|
| 1 | Tests — pytest suite covering current behaviour | [ ] |
| 2 | Route progress output to stderr; JSON stays on stdout | [ ] |
| 3 | `eprint()` helper + `--quiet` / `-q` flag | [ ] |
| 4 | `_is_valid_pdf()` — detect HTML error pages written as PDFs | [ ] |
| 5 | `--output` directory fix for single DOI | [ ] |
| 6 | `--doi-file` flag; make `--doi` non-required | [ ] |
| 7 | Retry with backoff in `download_with_httpx` | [ ] |
| 8 | Async rewrite (`asyncio` + `httpx.AsyncClient` + `AsyncCamoufox`) | [ ] |
| 9 | `--concurrency` flag + `asyncio.Semaphore` + 0.1s stagger | [ ] |

## New CLI flags (after all steps)

| Flag | Default | Notes |
|------|---------|-------|
| `--doi-file PATH` | — | Text file of DOIs, one per line, `#` lines ignored |
| `--concurrency N` / `-c N` | `3` | Max parallel downloads |
| `--quiet` / `-q` | off | Suppress all stderr progress; JSON still on stdout |

Existing flags (`--doi`, `--output`, `--email`, `--force-camoufox`, `--version`) are unchanged.

## Test commands

```bash
uv run pytest -v           # full suite
uv run pytest -v -k cli    # CLI contract tests only
```
