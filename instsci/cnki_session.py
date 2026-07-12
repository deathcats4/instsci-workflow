"""Visible, persistent CNKI browser session setup.

This module deliberately stores the complete Chromium profile outside the
source tree.  It never exports cookies or tries to bypass CNKI verification.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .cloakbrowser_compat import prepare_cloakbrowser_runtime
from .config import Config


CNKI_HOME_URL = "https://www.cnki.net/"
CNKI_HOST_SUFFIXES = ("cnki.net", "cnki.com.cn")


def load_cnki_batch(path: str | Path) -> list[dict[str, str]]:
    """Load and validate a JSON array of CNKI article records."""
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list):
        raise ValueError("CNKI batch input must be a JSON array.")
    records: list[dict[str, str]] = []
    for index, raw in enumerate(payload, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"CNKI batch row {index} must be an object.")
        url = str(raw.get("url") or "").strip()
        record_id = str(raw.get("record_id") or "").strip()
        title = str(raw.get("title") or "").strip()
        host = (urlparse(url).hostname or "").lower()
        if not url or not any(host == suffix or host.endswith(f".{suffix}") for suffix in CNKI_HOST_SUFFIXES):
            raise ValueError(f"CNKI batch row {index} has an invalid CNKI URL.")
        if not record_id or not re.fullmatch(r"[A-Za-z0-9._-]+", record_id):
            raise ValueError(f"CNKI batch row {index} has an unsafe record_id.")
        if not title:
            raise ValueError(f"CNKI batch row {index} is missing title.")
        records.append(
            {
                "record_id": record_id,
                "title": title,
                "url": url,
                "zotero_item_key": str(raw.get("zotero_item_key") or "").strip(),
            }
        )
    return records


def safe_page_url(url: str) -> str:
    """Return a report-safe URL without query parameters or fragments."""
    parsed = urlparse(str(url or ""))
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def classify_cnki_session(url: str, title: str = "") -> str:
    """Classify visible CNKI state without treating it as a PDF verdict."""
    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    title_lower = str(title or "").lower()
    if "/verify/" in path or "captcha" in path or "安全验证" in title_lower:
        return "human_verification_required"
    if any(host == suffix or host.endswith(f".{suffix}") for suffix in CNKI_HOST_SUFFIXES):
        return "session_ready"
    return "unexpected_page"


def cnki_verification_visible(page: Any) -> bool:
    """Return whether CNKI is visibly asking for a human verification step."""
    if classify_cnki_session(str(getattr(page, "url", "") or ""), page.title()) == "human_verification_required":
        return True
    # CNKI keeps hidden CAPTCHA markup in otherwise authorized article pages.
    # Inspect visibility instead of searching the complete body text.
    for marker in ("请完成安全验证", "请依次点击", "人机验证"):
        try:
            matches = page.get_by_text(marker, exact=False)
            for index in range(min(matches.count(), 5)):
                if matches.nth(index).is_visible():
                    return True
        except Exception:
            continue
    return False


def capture_cnki_pdf(
    page: Any,
    *,
    output_path: str | Path,
    timeout_ms: int = 90_000,
) -> dict[str, object]:
    """Click CNKI's visible PDF control and capture the browser download."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    button = page.get_by_text("PDF下载", exact=True).first
    button.wait_for(state="visible", timeout=30_000)
    detail = {
        "button_url": safe_page_url(button.get_attribute("href") or ""),
        "button_title": button.get_attribute("title") or "",
    }
    try:
        with page.expect_download(timeout=timeout_ms) as event:
            button.click(timeout=30_000)
        download = event.value
        download.save_as(str(target))
    except PlaywrightTimeoutError as exc:
        detail["error"] = f"TimeoutError: {exc}"
        detail["verification_required"] = cnki_verification_visible(page)
        return detail

    time.sleep(2)
    data = target.read_bytes() if target.exists() else b""
    detail.update(
        {
            "pdf_path": str(target),
            "size_bytes": len(data),
            "pdf_header_valid": data.startswith(b"%PDF-"),
        }
    )
    return detail


def open_cnki_login_session(
    config: Config,
    *,
    url: str = CNKI_HOME_URL,
    output_dir: str | Path,
) -> tuple[Any, Any, Path]:
    """Open a visible persistent CNKI session and return it to the CLI.

    The caller owns the returned browser context and must close it after the
    user finishes any CAPTCHA, institution check, or login step.
    """
    prepare_cloakbrowser_runtime()
    from cloakbrowser import launch_persistent_context

    profile_dir = Path(config.cnki_profile_dir).expanduser()
    profile_dir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    context = launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=False,
    )
    # A persistent Chromium profile may restore an about:blank tab or a tab
    # from the previous session. Always create one deterministic target tab,
    # then close restored tabs so the user sees the page they were asked to
    # verify instead of an active blank tab.
    page = context.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    for other in list(context.pages):
        if other is page:
            continue
        try:
            other.close()
        except Exception:
            pass
    page.bring_to_front()
    return context, page, run_dir


def write_cnki_session_report(page: Any, run_dir: Path, profile_dir: str | Path) -> dict[str, object]:
    """Save screenshot-backed session state without cookies or URL tokens."""
    screenshot = run_dir / "cnki_session.png"
    page.screenshot(path=str(screenshot), full_page=False)
    title = page.title()
    current_url = str(getattr(page, "url", "") or "")
    report: dict[str, object] = {
        "schema": "instsci.cnki_session.v1",
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "session_status": classify_cnki_session(current_url, title),
        "page_url": safe_page_url(current_url),
        "page_title": title,
        "profile_dir": str(Path(profile_dir)),
        "screenshot": str(screenshot),
        "pdf_verdict": "not_tested",
        "cookies_exported": False,
    }
    report_path = run_dir / "cnki_session.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report"] = str(report_path)
    return report
