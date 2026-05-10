#!/usr/bin/env python3
"""
Unpaywall PDF Downloader with httpx Primary + Camoufox Stealth Backup
Supports multiple DOIs via repeated --doi flag or --doi-file.
--email is optional (falls back to UNPAYWALL_EMAIL environment variable).

Usage examples:
  # Email via CLI
  python unpaywall.py --doi 10.1038/nature12373 --email you@example.com

  # Email via environment variable (recommended for agents)
  export UNPAYWALL_EMAIL=you@example.com
  python unpaywall.py --doi 10.1038/nature12373

  # Batch mode with concurrency
  python unpaywall.py --doi 10.1 --doi 10.2 --output ./pdfs/ --concurrency 5
"""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import httpx

# Optional Camoufox import (graceful degradation)
try:
    from camoufox.async_api import AsyncCamoufox
    CAMOUFOX_AVAILABLE = True
except ImportError:
    CAMOUFOX_AVAILABLE = False

VERSION = "0.0.2"
RETRY_DELAYS = [1.0, 2.0]


def eprint(*args, quiet: bool = False, **kwargs):
    if not quiet:
        print(*args, file=sys.stderr, **kwargs)


def _is_valid_pdf(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"%PDF"
    except OSError:
        return False


def sanitize_filename(doi: str) -> str:
    """Turn DOI into a safe filename."""
    filename = doi.replace("/", "+")
    filename = re.sub(r"[^a-zA-Z0-9.+-]+", "-", filename)
    return filename + ".pdf"


async def fetch_unpaywall_metadata(
    client: httpx.AsyncClient, doi: str, email: str, headers: dict
) -> dict:
    api_url = f"https://api.unpaywall.org/v2/{doi}?email={email}"
    resp = await client.get(api_url, headers=headers, timeout=15.0, follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


async def download_with_httpx(
    client: httpx.AsyncClient,
    pdf_url: str,
    headers: dict,
    output_path: Path,
    quiet: bool = False,
) -> bool:
    """Primary download using httpx (streaming), with retry on transient errors."""
    last_exc = None
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            async with client.stream(
                "GET", pdf_url, headers=headers, timeout=30.0, follow_redirects=True
            ) as response:
                response.raise_for_status()
                with open(output_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        f.write(chunk)
            if not _is_valid_pdf(output_path):
                output_path.unlink(missing_ok=True)
                raise ValueError("Downloaded file is not a valid PDF (got HTML or error page)")
            return True
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_exc = e
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                last_exc = e
            else:
                raise   # 4xx: no retry, let caller fall back to camoufox
        if attempt < len(RETRY_DELAYS):
            await asyncio.sleep(RETRY_DELAYS[attempt])
    raise last_exc


async def download_with_camoufox(
    pdf_url: str, output_path: Path, quiet: bool = False
) -> bool:
    """Stealth fallback using Camoufox (anti-detect Firefox + Playwright)."""
    if not CAMOUFOX_AVAILABLE:
        raise ImportError("Camoufox not installed. Run: pip install camoufox[geoip]")

    eprint("🔄 httpx failed → falling back to Camoufox (stealth browser)...", quiet=quiet)
    async with AsyncCamoufox(headless=True) as browser:
        page = await browser.new_page()
        response = await page.request.get(pdf_url, timeout=60000)
        if not response.ok:
            raise Exception(
                f"Camoufox request failed: {response.status} "
                f"{(await response.text())[:200]}"
            )
        body = await response.body()
    with open(output_path, "wb") as f:
        f.write(body)
    if not _is_valid_pdf(output_path):
        output_path.unlink(missing_ok=True)
        raise ValueError("Downloaded file is not a valid PDF (got HTML or error page)")
    return True


async def download_pdf(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    doi: str,
    email: str,
    output_path: str | None = None,
    force_camoufox: bool = False,
    quiet: bool = False,
) -> dict:
    doi = doi.strip().lower().replace("https://doi.org/", "").replace("doi.org/", "")
    headers = {"User-Agent": f"UnpaywallDownloader/{VERSION} ({email})"}

    try:
        data = await fetch_unpaywall_metadata(client, doi, email, headers)
    except Exception as e:
        return {"success": False, "error": f"API request failed: {e}", "doi": doi}

    if not data.get("is_oa"):
        return {"success": False, "error": "No open-access version found", "doi": doi}

    best = data.get("best_oa_location")
    if not best or not best.get("url_for_pdf"):
        return {
            "success": False,
            "error": "No direct PDF link available",
            "doi": doi,
            "landing_page": best.get("url") if best else None,
        }

    pdf_url = best["url_for_pdf"]

    if not output_path:
        output_path = sanitize_filename(doi)
    final_path = Path(output_path).expanduser().resolve()
    final_path.parent.mkdir(parents=True, exist_ok=True)

    success = False
    error_msg = None
    method_used = "httpx"

    async with sem:
        if not force_camoufox:
            try:
                success = await download_with_httpx(client, pdf_url, headers, final_path, quiet)
            except Exception as e:
                error_msg = f"httpx failed: {e}"

        if not success:
            if CAMOUFOX_AVAILABLE or force_camoufox:
                try:
                    success = await download_with_camoufox(pdf_url, final_path, quiet)
                    method_used = "camoufox"
                except Exception as e:
                    error_msg = f"Camoufox fallback also failed: {e}"
            else:
                error_msg = error_msg or "Camoufox not available for fallback"

    if success:
        return {
            "success": True,
            "file_path": str(final_path),
            "doi": doi,
            "pdf_url": pdf_url,
            "title": data.get("title"),
            "oa_status": data.get("oa_status"),
            "method": method_used,
        }
    return {"success": False, "error": error_msg or "Unknown download error",
            "doi": doi, "pdf_url": pdf_url}


async def _download_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    doi: str,
    email: str,
    out_path: Path | None,
    force_camoufox: bool,
    quiet: bool,
    index: int,
    total: int,
) -> dict:
    eprint(f"\n[{index}/{total}] Processing DOI: {doi}", quiet=quiet)
    result = await download_pdf(
        client, sem, doi, email,
        str(out_path) if out_path else None,
        force_camoufox, quiet,
    )
    if result["success"]:
        eprint(f"✅ Success via {result['method']}: {result['file_path']}", quiet=quiet)
    else:
        eprint(f"❌ Failed: {result['error']}", quiet=quiet)
        if "landing_page" in result:
            eprint(f"   Landing page: {result['landing_page']}", quiet=quiet)
    return result


async def async_main(args, dois: list[str], email: str) -> None:
    sem = asyncio.Semaphore(args.concurrency)
    quiet = args.quiet

    async with httpx.AsyncClient() as client:
        tasks = []
        for i, doi in enumerate(dois):
            if i > 0:
                await asyncio.sleep(0.1)   # stagger: avoid hammering the API simultaneously
            if args.output:
                p = Path(args.output)
                if p.is_dir() or args.output.endswith("/"):
                    out_path = p / sanitize_filename(doi)
                else:
                    out_path = p if len(dois) == 1 else p / sanitize_filename(doi)
            else:
                out_path = None
            tasks.append(asyncio.create_task(
                _download_one(client, sem, doi, email, out_path,
                              args.force_camoufox, quiet, i + 1, len(dois))
            ))
        results = list(await asyncio.gather(*tasks))

    success_count = sum(1 for r in results if r["success"])
    eprint("\n" + "=" * 60, quiet=quiet)
    eprint(f"BATCH SUMMARY: {success_count}/{len(dois)} PDFs downloaded successfully", quiet=quiet)
    eprint("=" * 60, quiet=quiet)

    print(json.dumps({"results": results}, indent=2))

    if success_count < len(dois):
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description=f"Unpaywall PDF Downloader v{VERSION}. Download open-access PDFs."
    )
    parser.add_argument("--doi", action="append", default=[],
                        help="DOI of the article (repeat for batch; or use --doi-file)")
    parser.add_argument("--doi-file", metavar="PATH",
                        help="Text file of DOIs, one per line (# lines ignored)")
    parser.add_argument("--output", "-o",
                        help="For single DOI: exact output filename. For batch: output directory.")
    parser.add_argument("--email", "-e", required=False,
                        help="Your email for Unpaywall API (optional – falls back to UNPAYWALL_EMAIL)")
    parser.add_argument("--force-camoufox", action="store_true",
                        help="Skip httpx and use Camoufox immediately (for testing)")
    parser.add_argument("--concurrency", "-c", type=int, default=3, metavar="N",
                        help="Max parallel downloads (default: 3)")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="Suppress progress output; JSON still goes to stdout")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}",
                        help="Show the installed version and exit")
    args = parser.parse_args()

    email = args.email or os.getenv("UNPAYWALL_EMAIL")
    if not email:
        print("Error: --email flag or UNPAYWALL_EMAIL environment variable is required",
              file=sys.stderr)
        sys.exit(1)

    dois_from_file = []
    if args.doi_file:
        try:
            text = Path(args.doi_file).read_text()
        except OSError as e:
            print(f"Error reading --doi-file: {e}", file=sys.stderr)
            sys.exit(1)
        dois_from_file = [
            line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    dois = [d.strip() for d in (args.doi or []) + dois_from_file if d.strip()]
    if not dois:
        print("Error: at least one --doi or a non-empty --doi-file is required", file=sys.stderr)
        sys.exit(1)

    asyncio.run(async_main(args, dois, email))


if __name__ == "__main__":
    main()
