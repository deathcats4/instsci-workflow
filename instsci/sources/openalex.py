"""Paper metadata search through the OpenAlex Works API."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from ..http_utils import request_with_retry
from .errors import ProviderSearchError, classify_provider_exception
from .semantic_scholar import SearchResult


logger = logging.getLogger(__name__)
OPENALEX_WORKS_API = "https://api.openalex.org/works"
OPENALEX_RATE_LIMIT_API = "https://api.openalex.org/rate-limit"


def _abstract_from_inverted_index(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    positioned: list[tuple[int, str]] = []
    for word, positions in value.items():
        if not isinstance(positions, list):
            continue
        for position in positions:
            if isinstance(position, int):
                positioned.append((position, str(word)))
    return " ".join(word for _, word in sorted(positioned))


def _year_filter(year_range: str | None) -> str:
    if not year_range:
        return ""
    start, separator, end = year_range.partition("-")
    filters: list[str] = []
    if start.strip().isdigit():
        filters.append(f"from_publication_date:{start.strip()}-01-01")
    if separator and end.strip().isdigit():
        filters.append(f"to_publication_date:{end.strip()}-12-31")
    return ",".join(filters)


def _resolved_api_key(api_key: str = "") -> str:
    return api_key or os.environ.get("OPENALEX_API_KEY", "")


def get_rate_limit_status(*, api_key: str = "", email: str = "") -> dict[str, Any]:
    """Return a redacted OpenAlex quota/rate-limit preflight report."""
    resolved_key = _resolved_api_key(api_key)
    params: dict[str, object] = {}
    if resolved_key:
        params["api_key"] = resolved_key
    if email:
        params["mailto"] = email
    headers = {"User-Agent": f"instsci/0.2.0a2{f' (mailto:{email})' if email else ''}"}
    report: dict[str, Any] = {
        "provider": "openalex",
        "endpoint": OPENALEX_RATE_LIMIT_API,
        "api_key_configured": bool(resolved_key),
        "status": "unknown",
        "http_status": 0,
        "headers": {},
        "body": {},
    }
    try:
        response = request_with_retry(
            "GET",
            OPENALEX_RATE_LIMIT_API,
            params=params,
            headers=headers,
            timeout=30,
            max_retries=0,
        )
        report["http_status"] = int(getattr(response, "status_code", 0) or 0)
        report["headers"] = _rate_limit_headers(getattr(response, "headers", {}) or {})
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if isinstance(payload, dict):
            report["body"] = _redact_rate_limit_body(payload)
        response.raise_for_status()
        remaining = _remaining_from_report(report)
        report["status"] = "quota_exhausted" if remaining == 0 else "success"
    except Exception as exc:
        report["status"] = _classify_openalex_exception(exc)
        response = getattr(exc, "response", None)
        if response is not None:
            report["http_status"] = int(getattr(response, "status_code", 0) or 0)
            report["headers"] = _rate_limit_headers(getattr(response, "headers", {}) or {})
        report["detail"] = _openalex_error_detail(exc)
    return report


def _rate_limit_headers(headers: Any) -> dict[str, str]:
    allowed = {
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
        "retry-after",
    }
    return {str(key): str(value) for key, value in dict(headers).items() if str(key).lower() in allowed}


def _redact_rate_limit_body(payload: dict[str, Any]) -> dict[str, Any]:
    blocked = {"api_key", "apikey", "key", "token"}
    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        key_text = str(key)
        if key_text.lower() in blocked:
            continue
        if isinstance(value, dict):
            redacted[key_text] = _redact_rate_limit_body(value)
        else:
            redacted[key_text] = value
    return redacted


def _remaining_from_report(report: dict[str, Any]) -> float | None:
    headers = report.get("headers") if isinstance(report.get("headers"), dict) else {}
    body = report.get("body") if isinstance(report.get("body"), dict) else {}
    rate_limit = body.get("rate_limit") if isinstance(body.get("rate_limit"), dict) else {}
    candidates = [
        headers.get("X-RateLimit-Remaining"),
        headers.get("x-ratelimit-remaining"),
        body.get("remaining"),
        body.get("requests_remaining"),
        rate_limit.get("credits_remaining"),
        rate_limit.get("daily_remaining_usd"),
        rate_limit.get("prepaid_remaining_usd"),
    ]
    for value in candidates:
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _classify_openalex_exception(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code in {403, 429} and _openalex_quota_exhausted(response, exc):
        return "quota_exhausted"
    if status_code in {401, 403}:
        return "authentication_required"
    return classify_provider_exception(exc)


def _openalex_quota_exhausted(response: Any, exc: BaseException) -> bool:
    headers = getattr(response, "headers", {}) or {}
    remaining = None
    for key, value in dict(headers).items():
        if str(key).lower() == "x-ratelimit-remaining":
            try:
                remaining = int(value)
            except (TypeError, ValueError):
                remaining = None
            break
    if remaining == 0:
        return True
    detail = _openalex_error_detail(exc).lower()
    return "daily limit" in detail or "quota" in detail or "limit exceeded" in detail


def _openalex_error_detail(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return _redact_sensitive(str(exc))
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        for key in ("error", "message", "detail"):
            if payload.get(key):
                return _redact_sensitive(str(payload[key]))
    text = str(getattr(response, "text", "") or "").strip()
    return _redact_sensitive(text or str(exc))


def _redact_sensitive(value: str) -> str:
    if not value:
        return ""
    return re.sub(r"(?i)(api_?key|apikey|key|token)=([^&\s]+)", r"\1=<redacted>", value)


def search(
    query: str,
    limit: int = 10,
    year_range: str | None = None,
    *,
    email: str = "",
    api_key: str = "",
    raise_on_error: bool = False,
) -> list[SearchResult]:
    params: dict[str, object] = {"search": query, "per-page": min(max(limit, 1), 100)}
    filter_value = _year_filter(year_range)
    if filter_value:
        params["filter"] = filter_value
    api_key = _resolved_api_key(api_key)
    if api_key:
        params["api_key"] = api_key
    if email:
        params["mailto"] = email
    headers = {"User-Agent": f"instsci/0.2.0a2{f' (mailto:{email})' if email else ''}"}
    try:
        response = request_with_retry("GET", OPENALEX_WORKS_API, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("OpenAlex search failed: %s", _openalex_error_detail(exc))
        if raise_on_error:
            raise ProviderSearchError("openalex", _classify_openalex_exception(exc), _openalex_error_detail(exc)) from exc
        return []

    return _parse_works(payload.get("results", []))


def search_semantic(
    query: str,
    limit: int = 10,
    year_range: str | None = None,
    *,
    email: str = "",
    api_key: str = "",
    raise_on_error: bool = False,
) -> list[SearchResult]:
    """Search OpenAlex Works using its semantic search channel.

    OpenAlex semantic search uses a separate query parameter from lexical
    search, so callers should treat it as a distinct retrieval channel.
    """
    api_key = _resolved_api_key(api_key)
    if not api_key:
        if raise_on_error:
            raise ProviderSearchError(
                "openalex",
                "authentication_required",
                "OpenAlex semantic search requires OPENALEX_API_KEY.",
            )
        return []

    params: dict[str, object] = {"search.semantic": query, "per-page": min(max(limit, 1), 100), "api_key": api_key}
    filter_value = _year_filter(year_range)
    if filter_value:
        params["filter"] = filter_value
    if email:
        params["mailto"] = email
    headers = {"User-Agent": f"instsci/0.2.0a2{f' (mailto:{email})' if email else ''}"}
    try:
        response = request_with_retry("GET", OPENALEX_WORKS_API, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("OpenAlex semantic search failed: %s", _openalex_error_detail(exc))
        if raise_on_error:
            raise ProviderSearchError("openalex", _classify_openalex_exception(exc), _openalex_error_detail(exc)) from exc
        return []

    return _parse_works(payload.get("results", []))


def _parse_works(items: object) -> list[SearchResult]:
    results: list[SearchResult] = []
    works = items if isinstance(items, list) else []
    for item in works:
        if not isinstance(item, dict):
            continue
        ids = item.get("ids") or {}
        doi = str(item.get("doi") or ids.get("doi") or "")
        doi = doi.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
        authors = []
        for authorship in item.get("authorships") or []:
            author = authorship.get("author") or {}
            name = str(author.get("display_name") or "")
            if name:
                authors.append(name)
        location = item.get("primary_location") or {}
        source = location.get("source") or {}
        results.append(
            SearchResult(
                title=str(item.get("display_name") or item.get("title") or ""),
                authors=authors,
                year=item.get("publication_year"),
                abstract=_abstract_from_inverted_index(item.get("abstract_inverted_index")),
                doi=doi,
                journal=str(source.get("display_name") or ""),
                citation_count=int(item.get("cited_by_count") or 0),
                s2_url=str(item.get("id") or ""),
                paper_id=str(item.get("id") or "").rsplit("/", 1)[-1],
            )
        )
    return results
