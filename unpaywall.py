#!/usr/bin/env python3
"""
Unpaywall PDF Downloader with httpx Primary + Camoufox Stealth Backup
Supports multiple DOIs via repeated --doi flag.
--email is now optional (falls back to UNPAYWALL_EMAIL environment variable).

Usage examples:
  # Email via CLI
  python unpaywall_downloader.py --doi 10.1038/nature12373 --email you@example.com

  # Email via environment variable (recommended for agents)
  export UNPAYWALL_EMAIL=you@example.com
  python unpaywall_downloader.py --doi 10.1038/nature12373

  # Batch mode
  python unpaywall_downloader.py --doi 10.1 --doi 10.2 --output ./pdfs/
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import httpx

# Optional Camoufox import (graceful degradation)
try:
    from camoufox.sync_api import Camoufox
    CAMOUFOX_AVAILABLE = True
except ImportError:
    CAMOUFOX_AVAILABLE = False

VERSION = "0.0.2"

def sanitize_filename(doi: str) -> str:
    """Turn DOI into a safe filename."""
    filename = doi.replace("/", "+")
    filename = re.sub(r"[^a-zA-Z0-9.+-]+", "-", filename)
    return filename + ".pdf"

def download_with_httpx(pdf_url: str, headers: dict, output_path: Path):
    """Primary fast download using httpx (streaming for large PDFs)."""
    with httpx.stream(
        "GET",
        pdf_url,
        headers=headers,
        timeout=30.0,
        follow_redirects=True,
    ) as response:
        response.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in response.iter_bytes(chunk_size=8192):
                f.write(chunk)
    return True

def download_with_camoufox(pdf_url: str, output_path: Path):
    """Stealth fallback using Camoufox (anti-detect Firefox + Playwright)."""
    if not CAMOUFOX_AVAILABLE:
        raise ImportError("Camoufox not installed. Run: pip install camoufox[geoip]")

    print("🔄 httpx failed → falling back to Camoufox (stealth browser)...")
    with Camoufox(headless=True) as browser:
        page = browser.new_page()
        response = page.request.get(pdf_url, timeout=60000)
        if not response.ok:
            raise Exception(f"Camoufox request failed: {response.status} {response.text()[:200]}")
        
        with open(output_path, "wb") as f:
            f.write(response.body())
    return True

def download_pdf(doi: str, email: str, output_path: str = None, force_camoufox: bool = False):
    # Clean DOI
    doi = doi.strip().lower().replace("https://doi.org/", "").replace("doi.org/", "")

    # Query Unpaywall API
    api_url = f"https://api.unpaywall.org/v2/{doi}?email={email}"
    headers = {"User-Agent": f"UnpaywallDownloader/3.2-optional-email ({email})"}
    
    try:
        api_resp = httpx.get(api_url, headers=headers, timeout=15.0, follow_redirects=True)
        api_resp.raise_for_status()
        data = api_resp.json()
    except Exception as e:
        return {"success": False, "error": f"API request failed: {e}", "doi": doi}

    if not data.get("is_oa"):
        return {"success": False, "error": "No open-access version found", "doi": doi}

    best = data.get("best_oa_location")
    if not best or not best.get("url_for_pdf"):
        return {"success": False, "error": "No direct PDF link available", "doi": doi,
                "landing_page": best.get("url") if best else None}

    pdf_url = best["url_for_pdf"]

    # Determine final output path
    if not output_path:
        output_path = sanitize_filename(doi)
    final_path = Path(output_path).expanduser().resolve()
    final_path.parent.mkdir(parents=True, exist_ok=True)

    success = False
    error_msg = None
    method_used = "httpx"

    # PRIMARY: httpx
    if not force_camoufox:
        try:
            success = download_with_httpx(pdf_url, headers, final_path)
        except Exception as e:
            error_msg = f"httpx failed: {e}"

    # FALLBACK: Camoufox (or forced)
    if not success:
        if CAMOUFOX_AVAILABLE or force_camoufox:
            try:
                success = download_with_camoufox(pdf_url, final_path)
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
            "method": method_used
        }
    else:
        return {"success": False, "error": error_msg or "Unknown download error",
                "doi": doi, "pdf_url": pdf_url}

def main():
    parser = argparse.ArgumentParser(
        description=(
            f"Unpaywall PDF Downloader v{VERSION}. Download open-access PDFs."
        )
    )
    parser.add_argument("--doi", action="append", required=True,
                        help="DOI of the article (repeat this flag for batch mode)")
    parser.add_argument("--output", "-o", help="For single DOI: exact output filename. For batch: output directory.")
    parser.add_argument("--email", "-e", required=False,
                        help="Your email for Unpaywall API (optional – falls back to UNPAYWALL_EMAIL environment variable)")
    parser.add_argument("--force-camoufox", action="store_true",
                        help="Skip httpx and use Camoufox immediately (for testing)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}",
                        help="Show the installed version and exit")
    args = parser.parse_args()

    # Get email: CLI flag first, then environment variable
    email = args.email or os.getenv("UNPAYWALL_EMAIL")
    if not email:
        print("Error: --email flag or UNPAYWALL_EMAIL environment variable is required", file=sys.stderr)
        sys.exit(1)

    dois = [d.strip() for d in args.doi if d.strip()]
    if not dois:
        print("Error: At least one --doi is required", file=sys.stderr)
        sys.exit(1)

    results = []
    success_count = 0

    for i, doi in enumerate(dois, 1):
        print(f"\n[{i}/{len(dois)}] Processing DOI: {doi}")
        
        # Smart output handling
        if len(dois) == 1 and args.output:
            out_path = args.output
        elif len(dois) > 1 and args.output:
            out_path = Path(args.output) / sanitize_filename(doi)
        else:
            out_path = None  # auto-name in current dir

        result = download_pdf(doi, email, str(out_path) if out_path else None, args.force_camoufox)
        results.append(result)

        if result["success"]:
            success_count += 1
            print(f"✅ Success via {result['method']}: {result['file_path']}")
        else:
            print(f"❌ Failed: {result['error']}")
            if "landing_page" in result:
                print(f"   Landing page: {result['landing_page']}")

    # Batch summary
    print("\n" + "="*60)
    print(f"BATCH SUMMARY: {success_count}/{len(dois)} PDFs downloaded successfully")
    print("="*60)

    # Structured output for AI agents
    print(json.dumps({"results": results}, indent=2))

    if success_count < len(dois):
        sys.exit(1)  # non-zero exit if any failed

if __name__ == "__main__":
    main()