"""
Test suite for unpaywall.py.

Structure:
  - Pure unit tests: always work, no I/O
  - httpx mock tests: test download_pdf() via the async API
  - CLI tests: test the external contract via main() with monkeypatched argv
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

import unpaywall
from unpaywall import download_pdf, sanitize_filename

# ---------------------------------------------------------------------------
# Constants shared across tests
# ---------------------------------------------------------------------------

EMAIL = "test@example.com"
DOI = "10.1038/nature12373"
PDF_URL = "https://example.com/paper.pdf"

OA_API_RESPONSE = {
    "doi": DOI,
    "is_oa": True,
    "title": "Test Paper",
    "oa_status": "gold",
    "best_oa_location": {"url_for_pdf": PDF_URL, "url": PDF_URL},
}

CLOSED_API_RESPONSE = {
    "doi": DOI,
    "is_oa": False,
    "title": "Test Paper",
    "oa_status": "closed",
    "best_oa_location": None,
}

NO_PDF_URL_RESPONSE = {
    "doi": DOI,
    "is_oa": True,
    "title": "Test Paper",
    "oa_status": "bronze",
    "best_oa_location": {"url_for_pdf": None, "url": "https://example.com/landing"},
}

PDF_BYTES = b"%PDF-1.4 fake content"
HTML_BYTES = b"<html><body>Access denied</body></html>"


# ---------------------------------------------------------------------------
# Pure function tests
# ---------------------------------------------------------------------------

def test_sanitize_filename_slash():
    assert sanitize_filename("10.1038/nature12373") == "10.1038+nature12373.pdf"


def test_sanitize_filename_preserves_hyphen_and_dot():
    assert sanitize_filename("10.1038/s41586-021-03819-2") == "10.1038+s41586-021-03819-2.pdf"


def test_sanitize_filename_no_slashes_in_result():
    result = sanitize_filename("10.1038/nature12373")
    assert "/" not in result


def test_sanitize_filename_ends_with_pdf():
    assert sanitize_filename("10.1038/nature12373").endswith(".pdf")


# ---------------------------------------------------------------------------
# _is_valid_pdf — placeholder tests, uncomment in step 4 (enhancement 1)
# ---------------------------------------------------------------------------

def test_is_valid_pdf_valid(tmp_path):
    from unpaywall import _is_valid_pdf
    f = tmp_path / "test.pdf"
    f.write_bytes(b"%PDF-1.4 content")
    assert _is_valid_pdf(f) is True


def test_is_valid_pdf_html_content(tmp_path):
    from unpaywall import _is_valid_pdf
    f = tmp_path / "test.pdf"
    f.write_bytes(HTML_BYTES)
    assert _is_valid_pdf(f) is False


def test_is_valid_pdf_missing_file(tmp_path):
    from unpaywall import _is_valid_pdf
    assert _is_valid_pdf(tmp_path / "nonexistent.pdf") is False


# ---------------------------------------------------------------------------
# httpx mock tests — async API
# ---------------------------------------------------------------------------

async def _run_download_pdf(httpx_mock, doi, email, out_path, **kwargs):
    """Helper: create client + semaphore and call download_pdf."""
    sem = asyncio.Semaphore(3)
    async with httpx.AsyncClient() as client:
        return await download_pdf(client, sem, doi, email, str(out_path), **kwargs)


@pytest.mark.asyncio
async def test_not_open_access(httpx_mock, tmp_path):
    httpx_mock.add_response(json=CLOSED_API_RESPONSE)
    result = await _run_download_pdf(httpx_mock, DOI, EMAIL, tmp_path / "out.pdf")
    assert result["success"] is False
    assert "open-access" in result["error"].lower()


@pytest.mark.asyncio
async def test_no_pdf_url(httpx_mock, tmp_path):
    httpx_mock.add_response(json=NO_PDF_URL_RESPONSE)
    result = await _run_download_pdf(httpx_mock, DOI, EMAIL, tmp_path / "out.pdf")
    assert result["success"] is False
    assert result.get("landing_page") == "https://example.com/landing"


@pytest.mark.asyncio
async def test_api_request_fails(httpx_mock, tmp_path):
    httpx_mock.add_exception(httpx.ConnectError("connection refused"))
    result = await _run_download_pdf(httpx_mock, DOI, EMAIL, tmp_path / "out.pdf")
    assert result["success"] is False
    assert "API request failed" in result["error"]


@pytest.mark.asyncio
async def test_successful_download(httpx_mock, tmp_path):
    httpx_mock.add_response(json=OA_API_RESPONSE)
    httpx_mock.add_response(content=PDF_BYTES)
    out = tmp_path / "out.pdf"
    result = await _run_download_pdf(httpx_mock, DOI, EMAIL, out)
    assert result["success"] is True
    assert result["method"] == "httpx"
    assert result["doi"] == DOI
    assert Path(result["file_path"]).exists()
    assert Path(result["file_path"]).read_bytes() == PDF_BYTES


@pytest.mark.asyncio
async def test_camoufox_fallback(httpx_mock, tmp_path):
    """httpx gets a 403; camoufox fallback should succeed."""
    httpx_mock.add_response(json=OA_API_RESPONSE)
    httpx_mock.add_response(status_code=403)
    out = tmp_path / "camoufox.pdf"

    async def fake_camoufox(pdf_url, output_path, quiet=False):
        output_path.write_bytes(PDF_BYTES)
        return True

    with patch.object(unpaywall, "download_with_camoufox", new=fake_camoufox):
        result = await _run_download_pdf(httpx_mock, DOI, EMAIL, out)

    assert result["success"] is True
    assert result["method"] == "camoufox"


@pytest.mark.asyncio
async def test_invalid_pdf_content_treated_as_failure(httpx_mock, tmp_path):
    """When server returns HTML instead of a PDF, download should fail and not leave a file."""
    httpx_mock.add_response(json=OA_API_RESPONSE)
    httpx_mock.add_response(content=HTML_BYTES)
    out = tmp_path / "out.pdf"
    result = await _run_download_pdf(httpx_mock, DOI, EMAIL, out)
    assert result["success"] is False
    assert not out.exists()


# ---------------------------------------------------------------------------
# CLI contract tests — stable across all enhancements
# These call main() directly with monkeypatched sys.argv.
# ---------------------------------------------------------------------------

def test_cli_no_email_exits_nonzero(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["unpaywall", "--doi", DOI])
    monkeypatch.delenv("UNPAYWALL_EMAIL", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        unpaywall.main()
    assert exc_info.value.code != 0


def test_cli_no_doi_exits_nonzero(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["unpaywall", "--email", EMAIL])
    monkeypatch.delenv("UNPAYWALL_EMAIL", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        unpaywall.main()
    assert exc_info.value.code != 0


def test_cli_email_from_env(httpx_mock, monkeypatch, tmp_path):
    """--email flag should be optional when UNPAYWALL_EMAIL is set."""
    monkeypatch.setattr(sys, "argv", [
        "unpaywall", "--doi", DOI, "--output", str(tmp_path / "out.pdf")
    ])
    monkeypatch.setenv("UNPAYWALL_EMAIL", EMAIL)
    httpx_mock.add_response(json=OA_API_RESPONSE)
    httpx_mock.add_response(content=PDF_BYTES)
    unpaywall.main()   # success → returns normally, no SystemExit


def test_cli_exit_0_on_success(httpx_mock, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", [
        "unpaywall", "--doi", DOI, "--email", EMAIL,
        "--output", str(tmp_path / "out.pdf"),
    ])
    monkeypatch.delenv("UNPAYWALL_EMAIL", raising=False)
    httpx_mock.add_response(json=OA_API_RESPONSE)
    httpx_mock.add_response(content=PDF_BYTES)
    unpaywall.main()   # success → returns normally, no SystemExit


def test_cli_exit_1_on_failure(httpx_mock, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", [
        "unpaywall", "--doi", DOI, "--email", EMAIL,
        "--output", str(tmp_path / "out.pdf"),
    ])
    monkeypatch.delenv("UNPAYWALL_EMAIL", raising=False)
    httpx_mock.add_response(json=CLOSED_API_RESPONSE)
    with pytest.raises(SystemExit) as exc_info:
        unpaywall.main()
    assert exc_info.value.code == 1


def test_cli_json_result_structure(httpx_mock, monkeypatch, tmp_path, capsys):
    """stdout must include valid JSON with a 'results' array."""
    monkeypatch.setattr(sys, "argv", [
        "unpaywall", "--doi", DOI, "--email", EMAIL,
        "--output", str(tmp_path / "out.pdf"),
    ])
    monkeypatch.delenv("UNPAYWALL_EMAIL", raising=False)
    httpx_mock.add_response(json=OA_API_RESPONSE)
    httpx_mock.add_response(content=PDF_BYTES)
    unpaywall.main()   # success → returns normally
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "results" in data
    assert isinstance(data["results"], list)
    assert data["results"][0]["success"] is True


def test_cli_json_only_on_stdout(httpx_mock, monkeypatch, tmp_path, capsys):
    """After enhancement 2, stdout must contain ONLY valid JSON — no progress lines."""
    monkeypatch.setattr(sys, "argv", [
        "unpaywall", "--doi", DOI, "--email", EMAIL,
        "--output", str(tmp_path / "out.pdf"),
    ])
    monkeypatch.delenv("UNPAYWALL_EMAIL", raising=False)
    httpx_mock.add_response(json=OA_API_RESPONSE)
    httpx_mock.add_response(content=PDF_BYTES)
    unpaywall.main()   # success → returns normally
    captured = capsys.readouterr()
    data = json.loads(captured.out)   # must be pure JSON, no leading garbage
    assert "results" in data
    assert captured.err != ""         # progress went to stderr


def test_cli_quiet_suppresses_stderr(httpx_mock, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", [
        "unpaywall", "--doi", DOI, "--email", EMAIL,
        "--output", str(tmp_path / "out.pdf"), "--quiet",
    ])
    monkeypatch.delenv("UNPAYWALL_EMAIL", raising=False)
    httpx_mock.add_response(json=OA_API_RESPONSE)
    httpx_mock.add_response(content=PDF_BYTES)
    unpaywall.main()   # success → returns normally
    captured = capsys.readouterr()
    assert captured.err == ""
    data = json.loads(captured.out)
    assert data["results"][0]["success"] is True


def test_cli_doi_file(httpx_mock, monkeypatch, tmp_path, capsys):
    doi_file = tmp_path / "dois.txt"
    doi_file.write_text(f"# comment\n{DOI}\n\n")
    monkeypatch.setattr(sys, "argv", [
        "unpaywall", "--doi-file", str(doi_file), "--email", EMAIL,
        "--output", str(tmp_path),
    ])
    monkeypatch.delenv("UNPAYWALL_EMAIL", raising=False)
    httpx_mock.add_response(json=OA_API_RESPONSE)
    httpx_mock.add_response(content=PDF_BYTES)
    unpaywall.main()   # success → returns normally
