"""Structured search-result export and DOI selection helpers."""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


SEARCH_SCHEMA = "instsci.search_results.v1"
SEARCH_SCHEMA_V2 = "instsci.search_results.v2"
SELECTION_SCHEMA = "instsci.search_selection.v1"
SEARCH_CONTRACT_VALIDATION_SCHEMA = "instsci.search_contract_validation.v1"


def normalize_doi(value: str) -> str:
    """Return a stable bare DOI value suitable for downstream DOI files."""
    normalized = str(value or "").strip()
    lower = normalized.lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if lower.startswith(prefix):
            normalized = normalized[len(prefix):].strip()
            break
    return normalized.lower()


def normalize_arxiv_id(value: str) -> str:
    """Return a stable arXiv identifier without URL, prefix, PDF suffix, or version suffix."""
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    normalized = normalized.split("?", 1)[0].split("#", 1)[0].strip()
    lower = normalized.lower()
    for prefix in (
        "https://arxiv.org/abs/",
        "http://arxiv.org/abs/",
        "https://arxiv.org/pdf/",
        "http://arxiv.org/pdf/",
        "arxiv:",
        "abs/",
        "pdf/",
    ):
        if lower.startswith(prefix):
            normalized = normalized[len(prefix):].strip()
            lower = normalized.lower()
            break
    if lower.endswith(".pdf"):
        normalized = normalized[:-4]
    normalized = re.sub(r"v\d+$", "", normalized, flags=re.IGNORECASE)
    return normalized.lower()


def _title_family_id(title: str) -> str:
    normalized = " ".join("".join(character.lower() if character.isalnum() else " " for character in str(title or "")).split())
    if len(normalized) < 8:
        return ""
    return f"title:{normalized}"


def _normalize_work_id(value: str) -> str:
    normalized = str(value or "").strip()
    lower = normalized.lower()
    if lower.startswith("doi:"):
        doi = normalize_doi(normalized)
        return f"doi:{doi}" if doi else ""
    if lower.startswith("arxiv:"):
        arxiv_id = normalize_arxiv_id(normalized)
        return f"arxiv:{arxiv_id}" if arxiv_id else ""
    if lower.startswith("paper:"):
        paper_id = normalized.split(":", 1)[1].strip().lower()
        return f"paper:{paper_id}" if paper_id else ""
    if lower.startswith("title:"):
        return _title_family_id(normalized.split(":", 1)[1])
    return lower


def derive_work_identity(
    *,
    doi: str = "",
    arxiv_id: str = "",
    paper_id: str = "",
    title: str = "",
    year: int | None = None,
    journal: str = "",
    canonical_work_id: str = "",
    version_family_id: str = "",
    version_type: str = "",
) -> dict[str, str]:
    """Derive stable Search v2 identity fields without doing provider-specific version matching."""
    del year
    doi_value = normalize_doi(doi)
    arxiv_value = normalize_arxiv_id(arxiv_id)
    canonical = _normalize_work_id(canonical_work_id)
    if not canonical:
        if doi_value:
            canonical = f"doi:{doi_value}"
        elif arxiv_value:
            canonical = f"arxiv:{arxiv_value}"
        elif paper_id:
            canonical = f"paper:{str(paper_id).strip().lower()}"
        else:
            canonical = _title_family_id(title)
    family = _normalize_work_id(version_family_id) or _title_family_id(title) or canonical
    explicit_type = str(version_type or "").strip().lower()
    if explicit_type and explicit_type != "unknown":
        inferred_type = explicit_type
    elif arxiv_value and not doi_value:
        inferred_type = "preprint"
    elif doi_value and str(journal or "").strip():
        inferred_type = "journal"
    else:
        inferred_type = "unknown"
    return {
        "canonical_work_id": canonical,
        "version_family_id": family,
        "version_type": inferred_type,
    }


def work_identity_keys(record: dict[str, Any]) -> set[str]:
    """Return canonical and family keys for de-duplicating Search v2 records."""
    identity = derive_work_identity(
        doi=str(record.get("doi") or ""),
        arxiv_id=str(record.get("arxiv_id") or ""),
        paper_id=str(record.get("paper_id") or ""),
        title=str(record.get("title") or ""),
        year=record.get("year") if isinstance(record.get("year"), int) else None,
        journal=str(record.get("journal") or ""),
        canonical_work_id=str(record.get("canonical_work_id") or ""),
        version_family_id=str(record.get("version_family_id") or ""),
        version_type=str(record.get("version_type") or ""),
    )
    return {value for value in (identity["canonical_work_id"], identity["version_family_id"]) if value}


def result_to_record(result: Any, index: int, *, source: str = "semantic_scholar") -> dict[str, Any]:
    """Convert a provider result object into the public search-result contract."""
    title = str(getattr(result, "title", "") or "")
    doi = normalize_doi(str(getattr(result, "doi", "") or ""))
    arxiv_id = normalize_arxiv_id(str(getattr(result, "arxiv_id", "") or ""))
    journal = str(getattr(result, "journal", "") or "")
    year = getattr(result, "year", None)
    identity = derive_work_identity(
        doi=doi,
        arxiv_id=arxiv_id,
        paper_id=str(getattr(result, "paper_id", "") or ""),
        title=title,
        year=year,
        journal=journal,
        canonical_work_id=str(getattr(result, "canonical_work_id", "") or ""),
        version_family_id=str(getattr(result, "version_family_id", "") or ""),
        version_type=str(getattr(result, "version_type", "") or ""),
    )
    return {
        "index": index,
        "source": (list(getattr(result, "sources", []) or [source]))[0],
        "sources": list(getattr(result, "sources", []) or [source]),
        "paper_id": str(getattr(result, "paper_id", "") or ""),
        "title": title,
        "authors": list(getattr(result, "authors", []) or []),
        "year": year,
        "abstract": str(getattr(result, "abstract", "") or ""),
        "doi": doi,
        "arxiv_id": arxiv_id,
        "journal": journal,
        "citation_count": int(getattr(result, "citation_count", 0) or 0),
        "citation_counts": dict(getattr(result, "citation_counts", {}) or {}),
        "url": str(getattr(result, "s2_url", "") or ""),
        "canonical_work_id": identity["canonical_work_id"],
        "version_family_id": identity["version_family_id"],
        "version_type": identity["version_type"],
        "related_versions": list(getattr(result, "related_versions", []) or []),
        "retrieval_provenance": list(getattr(result, "retrieval_provenance", []) or []),
        "fusion_score": float(getattr(result, "fusion_score", 0.0) or 0.0),
        "rank_components": dict(getattr(result, "rank_components", {}) or {}),
        "discovery_reasons": list(getattr(result, "discovery_reasons", []) or []),
        "access_hint": dict(getattr(result, "access_hint", {}) or {"oa_status": "unknown", "pdf_known": False}),
    }



V1_RECORD_FIELDS = (
    "index", "source", "sources", "paper_id", "title", "authors", "year", "abstract",
    "doi", "arxiv_id", "journal", "citation_count", "citation_counts", "url",
)


def downgrade_record_to_v1(record: dict[str, Any]) -> dict[str, Any]:
    """Return a v1-safe search record, dropping v2 and future-only fields."""
    authors = record.get("authors") or []
    if isinstance(authors, str):
        authors = [part.strip() for part in authors.split(";") if part.strip()]
    sources = record.get("sources") or []
    if isinstance(sources, str):
        sources = [part.strip() for part in sources.split(";") if part.strip()]
    year_value = record.get("year")
    year = int(year_value) if str(year_value or "").isdigit() else None
    citation_counts = record.get("citation_counts") if isinstance(record.get("citation_counts"), dict) else {}
    return {
        "index": int(record.get("index") or 0),
        "source": str(record.get("source") or (sources[0] if sources else "")),
        "sources": list(sources) if isinstance(sources, list) else [],
        "paper_id": str(record.get("paper_id") or ""),
        "title": str(record.get("title") or ""),
        "authors": list(authors) if isinstance(authors, list) else [],
        "year": year,
        "abstract": str(record.get("abstract") or ""),
        "doi": normalize_doi(str(record.get("doi") or "")),
        "arxiv_id": normalize_arxiv_id(str(record.get("arxiv_id") or "")),
        "journal": str(record.get("journal") or ""),
        "citation_count": int(record.get("citation_count") or 0),
        "citation_counts": dict(citation_counts),
        "url": str(record.get("url") or ""),
    }


def downgrade_search_payload_to_v1(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a v1-safe payload for consumers that do not understand Search v2 fields."""
    records = [
        downgrade_record_to_v1(record)
        for record in (payload.get("results") or [])
        if isinstance(record, dict)
    ]
    sources = list(
        dict.fromkeys(
            item
            for record in records
            for item in (record.get("sources") or [record.get("source")])
            if item
        )
    )
    downgraded = {
        "schema": SEARCH_SCHEMA,
        "query": str(payload.get("query") or ""),
        "year_range": str(payload.get("year_range") or ""),
        "sources": sources,
        "count": len(records),
        "results": records,
    }
    if payload.get("generated_at"):
        downgraded["generated_at"] = payload["generated_at"]
    if isinstance(payload.get("source_status"), dict):
        downgraded["source_status"] = dict(payload["source_status"])
    return downgraded


def validate_search_payload_contract(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the saved search-result contract without mutating or upgrading it.

    The validator is intentionally additive: unknown fields are accepted so
    future Search v2 producers do not break older checks, while required v2
    contract fields are reported explicitly for PR/release gates.
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(payload, dict):
        errors.append("payload must be a JSON object")
        payload = {}

    source_schema = str(payload.get("schema") or "")
    results = payload.get("results")
    records = results if isinstance(results, list) else []
    if not isinstance(results, list):
        errors.append("results must be a list")
    declared_count = payload.get("count")
    if declared_count is not None:
        try:
            if int(declared_count) != len(records):
                warnings.append("count does not match results length")
        except (TypeError, ValueError):
            errors.append("count must be an integer")

    source_status = payload.get("source_status")
    status_rows = source_status if isinstance(source_status, dict) else {}
    if source_status is not None and not isinstance(source_status, dict):
        errors.append("source_status must be an object")

    query_plan = payload.get("query_plan")
    plan = query_plan if isinstance(query_plan, dict) else {}
    channels = plan.get("channels") if isinstance(plan.get("channels"), list) else []
    if not source_schema:
        errors.append("schema missing")
    elif source_schema == SEARCH_SCHEMA_V2:
        if not isinstance(query_plan, dict):
            errors.append("query_plan must be an object")
        elif plan.get("schema") != "instsci.query_plan.v1":
            errors.append("query_plan.schema must be instsci.query_plan.v1")
        strategy = str(plan.get("strategy") or "").strip()
        if not strategy:
            errors.append("query_plan.strategy missing")
        elif strategy not in {"hybrid", "chinese_visible"}:
            errors.append(f"query_plan.strategy unsupported: {strategy}")
        if source_status is None:
            errors.append("source_status must be an object")
        query_plan_channel_providers = _query_plan_channel_providers(channels)
        query_plan_channel_weights = _query_plan_channel_weights(channels)
        _validate_query_plan_channels(channels, errors)
        _validate_channel_statuses(status_rows, errors, query_plan_channel_providers)
        _validate_v2_records(records, errors, query_plan_channel_providers, query_plan_channel_weights)
        _validate_channel_status_coverage(channels, status_rows, errors, warnings)
    elif source_schema and source_schema != SEARCH_SCHEMA:
        errors.append(f"schema is not a search result contract: {source_schema}")

    return {
        "schema": SEARCH_CONTRACT_VALIDATION_SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "valid": not errors,
        "source_schema": source_schema,
        "summary": {
            "record_count": len(records),
            "channel_status_count": len(status_rows),
            "query_plan_channel_count": len(channels),
            "error_count": len(errors),
            "warning_count": len(warnings),
        },
        "errors": errors,
        "warnings": warnings,
    }


def _validate_query_plan_channels(channels: list[Any], errors: list[str]) -> None:
    seen_keys: set[str] = set()
    for position, channel in enumerate(channels, 1):
        if not isinstance(channel, dict):
            errors.append(f"query_plan.channels[{position}] must be an object")
            continue
        for field_name in ("provider", "channel", "query_variant"):
            if not str(channel.get(field_name) or "").strip():
                errors.append(f"query_plan.channels[{position}].{field_name} missing")
        weight = channel.get("weight")
        if not isinstance(weight, (int, float)) or isinstance(weight, bool):
            errors.append(f"query_plan.channels[{position}].weight must be numeric")
        channel_name = str(channel.get("channel") or "").strip()
        query_variant = str(channel.get("query_variant") or "").strip()
        if channel_name and query_variant:
            channel_key = f"{channel_name}:{query_variant}"
            if channel_key in seen_keys:
                errors.append(f"query_plan.channels[{position}] duplicates channel/query_variant: {channel_key}")
            seen_keys.add(channel_key)


def _query_plan_channel_providers(channels: list[Any]) -> dict[str, str]:
    providers: dict[str, str] = {}
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        channel_name = str(channel.get("channel") or "").strip()
        query_variant = str(channel.get("query_variant") or "").strip()
        if channel_name and query_variant:
            providers[f"{channel_name}:{query_variant}"] = str(channel.get("provider") or "").strip()
    return providers


def _query_plan_channel_weights(channels: list[Any]) -> dict[str, float]:
    weights: dict[str, float] = {}
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        channel_name = str(channel.get("channel") or "").strip()
        query_variant = str(channel.get("query_variant") or "").strip()
        weight = channel.get("weight")
        if channel_name and query_variant and isinstance(weight, (int, float)) and not isinstance(weight, bool):
            weights[f"{channel_name}:{query_variant}"] = float(weight)
    return weights


def _validate_channel_statuses(
    source_status: dict[Any, Any],
    errors: list[str],
    query_plan_channels: dict[str, str],
) -> None:
    for key, status in source_status.items():
        status_key = str(key)
        if not isinstance(status, dict):
            errors.append(f"source_status.{status_key} must be an object")
            continue
        for field_name in ("provider", "channel", "query_variant"):
            if not str(status.get(field_name) or "").strip():
                errors.append(f"source_status.{status_key}.{field_name} missing")
        row_channel = str(status.get("channel") or "").strip()
        row_query_variant = str(status.get("query_variant") or "").strip()
        row_key = f"{row_channel}:{row_query_variant}" if row_channel and row_query_variant else ""
        if row_key and row_key != status_key:
            errors.append(f"source_status.{status_key} key does not match row channel/query_variant: {row_key}")
        expected_provider = query_plan_channels.get(status_key)
        row_provider = str(status.get("provider") or "").strip()
        if expected_provider is not None and row_provider and expected_provider and row_provider != expected_provider:
            errors.append(f"source_status.{status_key}.provider does not match query_plan channel provider")
        if not str(status.get("status") or "").strip():
            errors.append(f"source_status.{status_key}.status missing")
        if "count" not in status:
            errors.append(f"source_status.{status_key}.count missing")
        elif not isinstance(status.get("count"), int):
            errors.append(f"source_status.{status_key}.count must be an integer")


def _validate_v2_records(
    records: list[Any],
    errors: list[str],
    query_plan_channels: dict[str, str],
    query_plan_channel_weights: dict[str, float],
) -> None:
    for position, record in enumerate(records, 1):
        label = f"results[{position}]"
        if not isinstance(record, dict):
            errors.append(f"{label} must be an object")
            continue
        for field_name in ("canonical_work_id", "version_family_id", "version_type"):
            if not str(record.get(field_name) or "").strip():
                errors.append(f"{label}.{field_name} missing")
        for field_name in ("related_versions", "retrieval_provenance"):
            if not isinstance(record.get(field_name), list):
                errors.append(f"{label}.{field_name} must be a list")
        provenance = record.get("retrieval_provenance")
        if isinstance(provenance, list):
            _validate_retrieval_provenance(
                provenance,
                label,
                errors,
                query_plan_channels,
                query_plan_channel_weights,
            )
        if record.get("rank_components") is not None and not isinstance(record.get("rank_components"), dict):
            errors.append(f"{label}.rank_components must be an object")
        if record.get("discovery_reasons") is not None and not isinstance(record.get("discovery_reasons"), list):
            errors.append(f"{label}.discovery_reasons must be a list")
        if record.get("access_hint") is not None and not isinstance(record.get("access_hint"), dict):
            errors.append(f"{label}.access_hint must be an object")


def _validate_retrieval_provenance(
    rows: list[Any],
    record_label: str,
    errors: list[str],
    query_plan_channels: dict[str, str],
    query_plan_channel_weights: dict[str, float],
) -> None:
    for position, row in enumerate(rows, 1):
        label = f"{record_label}.retrieval_provenance[{position}]"
        if not isinstance(row, dict):
            errors.append(f"{label} must be an object")
            continue
        for field_name in ("provider", "channel", "query_variant"):
            if not str(row.get(field_name) or "").strip():
                errors.append(f"{label}.{field_name} missing")
        rank = row.get("rank")
        if not isinstance(rank, int) or isinstance(rank, bool) or rank <= 0:
            errors.append(f"{label}.rank must be a positive integer")
        weight = row.get("weight")
        if not isinstance(weight, (int, float)) or isinstance(weight, bool):
            errors.append(f"{label}.weight must be numeric")
        channel_name = str(row.get("channel") or "").strip()
        query_variant = str(row.get("query_variant") or "").strip()
        if channel_name and query_variant:
            channel_key = f"{channel_name}:{query_variant}"
            expected_provider = query_plan_channels.get(channel_key)
            if expected_provider is None:
                errors.append(f"{label} has no query_plan channel: {channel_key}")
            else:
                provider = str(row.get("provider") or "").strip()
                if provider and expected_provider and provider != expected_provider:
                    errors.append(f"{label}.provider does not match query_plan channel provider: {channel_key}")
                expected_weight = query_plan_channel_weights.get(channel_key)
                if (
                    expected_weight is not None
                    and isinstance(weight, (int, float))
                    and not isinstance(weight, bool)
                    and abs(float(weight) - expected_weight) > 1e-12
                ):
                    errors.append(f"{label}.weight does not match query_plan channel weight: {channel_key}")


def _validate_channel_status_coverage(
    channels: list[Any],
    source_status: dict[Any, Any],
    errors: list[str],
    warnings: list[str],
) -> None:
    expected_keys = {
        f"{channel.get('channel')}:{channel.get('query_variant')}"
        for channel in channels
        if isinstance(channel, dict) and channel.get("channel") and channel.get("query_variant")
    }
    actual_keys = {str(key) for key in source_status}
    for key in sorted(expected_keys - actual_keys):
        errors.append(f"query_plan channel has no source_status entry: {key}")
    for key in sorted(actual_keys - expected_keys):
        warnings.append(f"source_status entry is not present in query_plan channels: {key}")


def build_search_payload(
    query: str,
    results: Iterable[Any],
    *,
    year_range: str = "",
    source: str = "semantic_scholar",
    source_status: dict[str, dict[str, Any]] | None = None,
    query_plan: dict[str, Any] | None = None,
    channel_results: dict[str, Iterable[Any]] | None = None,
) -> dict[str, Any]:
    records = [result_to_record(result, index, source=source) for index, result in enumerate(results, 1)]
    sources = list(
        dict.fromkeys(
            item
            for record in records
            for item in (record.get("sources") or [record.get("source")])
            if item
        )
    ) or [source]
    use_v2_contract = _uses_search_v2_contract(query_plan)
    payload = {
        "schema": SEARCH_SCHEMA_V2 if use_v2_contract else SEARCH_SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "query": query,
        "year_range": year_range,
        "sources": sources,
        "source_status": dict(source_status or {}),
        "count": len(records),
        "results": records,
    }
    if use_v2_contract:
        payload["query_plan"] = dict(query_plan or {})
        if channel_results:
            payload["channel_results"] = {
                str(channel): [
                    result_to_record(result, index, source=source)
                    for index, result in enumerate(channel_records, 1)
                ]
                for channel, channel_records in channel_results.items()
            }
    return payload


def _uses_search_v2_contract(query_plan: dict[str, Any] | None) -> bool:
    if not query_plan:
        return False
    strategy = str(query_plan.get("strategy") or "").strip().lower()
    return strategy != "legacy"


def resolve_search_output(path: str | Path) -> Path:
    output = Path(path)
    if not output.suffix:
        output = output.with_suffix(".json")
    if output.suffix.lower() not in {".json", ".csv"}:
        raise ValueError("Search output must use .json or .csv.")
    return output


def write_search_payload(payload: dict[str, Any], path: str | Path) -> Path:
    output = resolve_search_output(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".json":
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return output

    fieldnames = [
        "index", "source", "sources", "paper_id", "title", "authors", "year", "abstract",
        "doi", "arxiv_id", "journal", "citation_count", "citation_counts", "url",
        "canonical_work_id", "version_family_id", "version_type", "related_versions",
        "retrieval_provenance", "fusion_score", "rank_components", "discovery_reasons",
        "feedback_adjustments", "access_hint",
    ]
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in payload.get("results", []):
            row = dict(record)
            row["authors"] = "; ".join(row.get("authors") or [])
            row["sources"] = "; ".join(row.get("sources") or [])
            row["citation_counts"] = json.dumps(row.get("citation_counts") or {}, ensure_ascii=False, sort_keys=True)
            for key in ("related_versions", "retrieval_provenance", "rank_components", "discovery_reasons", "feedback_adjustments", "access_hint"):
                row[key] = json.dumps(row.get(key) or ([] if key != "rank_components" and key != "access_hint" else {}), ensure_ascii=False, sort_keys=True)
            writer.writerow({key: row.get(key, "") for key in fieldnames})
    return output


def load_search_payload(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if source.suffix.lower() == ".json":
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("Search JSON must contain a results array.")
        return payload
    if source.suffix.lower() == ".csv":
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        records: list[dict[str, Any]] = []
        for position, row in enumerate(rows, 1):
            records.append(
                {
                    **row,
                    "index": int(row.get("index") or position),
                    "authors": [part.strip() for part in str(row.get("authors") or "").split(";") if part.strip()],
                    "sources": [part.strip() for part in str(row.get("sources") or "").split(";") if part.strip()],
                    "year": int(row["year"]) if str(row.get("year") or "").isdigit() else None,
                    "citation_count": int(row.get("citation_count") or 0),
                    "citation_counts": json.loads(row.get("citation_counts") or "{}"),
                    "doi": normalize_doi(str(row.get("doi") or "")),
                    "canonical_work_id": str(row.get("canonical_work_id") or ""),
                    "version_family_id": str(row.get("version_family_id") or ""),
                    "version_type": str(row.get("version_type") or "unknown"),
                    "related_versions": json.loads(row.get("related_versions") or "[]"),
                    "retrieval_provenance": json.loads(row.get("retrieval_provenance") or "[]"),
                    "fusion_score": float(row.get("fusion_score") or 0),
                    "rank_components": json.loads(row.get("rank_components") or "{}"),
                    "discovery_reasons": json.loads(row.get("discovery_reasons") or "[]"),
                    "feedback_adjustments": json.loads(row.get("feedback_adjustments") or "[]"),
                    "access_hint": json.loads(row.get("access_hint") or "{}"),
                }
            )
        return {"schema": SEARCH_SCHEMA, "query": "", "count": len(records), "results": records}
    raise ValueError("Search input must use .json or .csv.")


def parse_selection_indices(value: str, total: int) -> list[int]:
    """Parse one-based values such as ``1,3-5``; an empty value selects all."""
    if total < 0:
        raise ValueError("Result count cannot be negative.")
    if not value.strip():
        return list(range(1, total + 1))
    selected: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            if not start_text.isdigit() or not end_text.isdigit():
                raise ValueError(f"Invalid selection range: {token}")
            start, end = int(start_text), int(end_text)
            if start > end:
                raise ValueError(f"Selection range is reversed: {token}")
            selected.update(range(start, end + 1))
        elif token.isdigit():
            selected.add(int(token))
        else:
            raise ValueError(f"Invalid selection index: {token}")
    invalid = sorted(index for index in selected if index < 1 or index > total)
    if invalid:
        raise ValueError(f"Selection index out of range: {invalid[0]}")
    return sorted(selected)


def select_doi_records(payload: dict[str, Any], indices: list[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    results = payload.get("results") or []
    by_index = {int(record.get("index") or position): record for position, record in enumerate(results, 1)}
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index in indices:
        record = dict(by_index[index])
        doi = normalize_doi(str(record.get("doi") or ""))
        if not doi:
            skipped.append({"index": index, "reason": "missing_doi", "title": record.get("title", "")})
            continue
        key = doi.lower()
        if key in seen:
            skipped.append({"index": index, "reason": "duplicate_doi", "doi": doi, "title": record.get("title", "")})
            continue
        seen.add(key)
        record["doi"] = doi
        selected.append(record)
    return selected, skipped


def write_selection(
    search_path: str | Path,
    payload: dict[str, Any],
    indices: list[int],
    output: str | Path,
) -> tuple[Path, Path, dict[str, Any]]:
    selected, skipped = select_doi_records(payload, indices)
    doi_path = Path(output)
    doi_path.parent.mkdir(parents=True, exist_ok=True)
    doi_path.write_text("".join(f"{record['doi']}\n" for record in selected), encoding="utf-8")
    report = {
        "schema": SELECTION_SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "search_input": str(Path(search_path)),
        "requested_indices": indices,
        "selected_count": len(selected),
        "skipped_count": len(skipped),
        "selected": selected,
        "skipped": skipped,
        "doi_file": str(doi_path),
    }
    report_path = doi_path.with_suffix(doi_path.suffix + ".selection.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return doi_path, report_path, report
