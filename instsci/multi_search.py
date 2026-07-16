"""Multi-provider paper search with DOI-aware metadata merging."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

from .search_pipeline import normalize_doi
from .sources import crossref, openalex, semantic_scholar
from .sources.errors import ProviderSearchError, classify_provider_exception


DEFAULT_SOURCES = ("semantic_scholar", "openalex", "crossref")
OPTIONAL_SOURCES: tuple[str, ...] = ()
DEFAULT_STRATEGY = "legacy"
RRF_K = 60
CHANNEL_WEIGHTS = {
    "openalex_semantic": 1.10,
    "openalex_keyword": 1.00,
    "semantic_scholar_keyword": 1.00,
    "crossref_exact_title": 1.40,
    "crossref_identifier_resolution": 1.40,
    "crossref_keyword": 0.55,
    "legacy_fallback": 1.05,
}


@dataclass
class MergedSearchResult:
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    doi: str = ""
    arxiv_id: str = ""
    journal: str = ""
    citation_count: int = 0
    s2_url: str = ""
    paper_id: str = ""
    sources: list[str] = field(default_factory=list)
    citation_counts: dict[str, int] = field(default_factory=dict)
    retrieval_provenance: list[dict[str, object]] = field(default_factory=list)
    fusion_score: float = 0.0
    rank_components: dict[str, float] = field(default_factory=dict)
    canonical_work_id: str = ""
    version_family_id: str = ""
    version_type: str = "unknown"
    related_versions: list[dict[str, object]] = field(default_factory=list)


@dataclass
class MultiSearchResponse:
    results: list[MergedSearchResult] = field(default_factory=list)
    source_status: dict[str, dict[str, object]] = field(default_factory=dict)
    query_plan: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalChannel:
    key: str
    provider: str
    query_variant: str
    weight: float
    search: Callable[[], list[object]]


def parse_sources(value: str | None) -> list[str]:
    requested = [item.strip().lower().replace("-", "_") for item in (value or "").split(",") if item.strip()]
    sources = requested or list(DEFAULT_SOURCES)
    allowed_sources = set(DEFAULT_SOURCES).union(OPTIONAL_SOURCES)
    unknown = [source for source in sources if source not in allowed_sources]
    if unknown:
        raise ValueError(f"Unknown search source: {unknown[0]}")
    return list(dict.fromkeys(sources))


def _title_key(title: str, year: int | None) -> str:
    normalized = _normalized_title(title)
    return f"title:{normalized}|year:{year or ''}" if normalized else ""


def _normalized_title(title: str) -> str:
    return " ".join("".join(character.lower() if character.isalnum() else " " for character in title).split())


def _version_family_key(title: str) -> str:
    normalized = _normalized_title(title)
    return f"title:{normalized}" if len(normalized) >= 8 else ""


def _canonical_work_id(result: MergedSearchResult) -> str:
    if result.doi:
        return f"doi:{result.doi}"
    if result.arxiv_id:
        return f"arxiv:{result.arxiv_id.lower()}"
    if result.paper_id:
        return f"paper:{result.paper_id.lower()}"
    return _title_key(result.title, result.year)


def _infer_version_type(result: MergedSearchResult) -> str:
    if result.arxiv_id and not result.doi:
        return "preprint"
    if result.doi and result.journal:
        return "journal"
    return "unknown"


def _refresh_version_identity(result: MergedSearchResult) -> None:
    if not result.canonical_work_id:
        result.canonical_work_id = _canonical_work_id(result)
    if not result.version_family_id:
        result.version_family_id = _version_family_key(result.title) or result.canonical_work_id
    if not result.version_type or result.version_type == "unknown":
        result.version_type = _infer_version_type(result)


def _from_provider(
    result: object,
    source: str,
    *,
    channel: str | None = None,
    query_variant: str = "q_keyword_1",
    rank: int = 0,
    channel_weight: float = 1.0,
) -> MergedSearchResult:
    citations = int(getattr(result, "citation_count", 0) or 0)
    channel_name = channel or source
    rrf = channel_weight / (RRF_K + rank) if rank > 0 else 0.0
    merged = MergedSearchResult(
        title=str(getattr(result, "title", "") or ""),
        authors=list(getattr(result, "authors", []) or []),
        year=getattr(result, "year", None),
        abstract=str(getattr(result, "abstract", "") or ""),
        doi=normalize_doi(str(getattr(result, "doi", "") or "")),
        arxiv_id=str(getattr(result, "arxiv_id", "") or ""),
        journal=str(getattr(result, "journal", "") or ""),
        citation_count=citations,
        s2_url=str(getattr(result, "s2_url", "") or ""),
        paper_id=str(getattr(result, "paper_id", "") or ""),
        sources=[source],
        citation_counts={source: citations},
        retrieval_provenance=[
            {
                "provider": source,
                "channel": channel_name,
                "query_variant": query_variant,
                "rank": rank,
                "weight": channel_weight,
            }
        ],
        fusion_score=rrf,
        rank_components={"rrf": rrf},
    )
    merged.canonical_work_id = _canonical_work_id(merged)
    _refresh_version_identity(merged)
    return merged


def _primary_version_rank(result: MergedSearchResult) -> int:
    if result.version_type == "journal":
        return 3
    if result.doi:
        return 2
    if result.version_type == "preprint":
        return 1
    return 0


def _version_summary(result: MergedSearchResult) -> dict[str, object]:
    return {
        "canonical_work_id": result.canonical_work_id or _canonical_work_id(result),
        "version_family_id": result.version_family_id or _version_family_key(result.title),
        "version_type": result.version_type or _infer_version_type(result),
        "title": result.title,
        "year": result.year,
        "doi": result.doi,
        "arxiv_id": result.arxiv_id,
        "sources": list(result.sources),
    }


def _add_related_version(target: MergedSearchResult, summary: dict[str, object]) -> None:
    canonical = str(summary.get("canonical_work_id") or "")
    if not canonical or canonical == target.canonical_work_id:
        return
    if any(str(item.get("canonical_work_id") or "") == canonical for item in target.related_versions):
        return
    target.related_versions.append(summary)


def _merge(target: MergedSearchResult, incoming: MergedSearchResult) -> None:
    target_identity = target.canonical_work_id or _canonical_work_id(target)
    incoming_identity = incoming.canonical_work_id or _canonical_work_id(incoming)
    different_versions = bool(target_identity and incoming_identity and target_identity != incoming_identity)
    promote_incoming = different_versions and _primary_version_rank(incoming) > _primary_version_rank(target)
    related_summary = _version_summary(target if promote_incoming else incoming) if different_versions else {}
    for field_name in ("title", "authors", "year", "abstract", "doi", "arxiv_id", "journal", "s2_url", "paper_id"):
        if not getattr(target, field_name) and getattr(incoming, field_name):
            setattr(target, field_name, getattr(incoming, field_name))
    for source in incoming.sources:
        if source not in target.sources:
            target.sources.append(source)
    target.citation_counts.update(incoming.citation_counts)
    target.citation_count = max(target.citation_counts.values(), default=0)
    target.retrieval_provenance.extend(incoming.retrieval_provenance)
    target.fusion_score += incoming.fusion_score
    target.rank_components["rrf"] = target.fusion_score
    if not target.canonical_work_id:
        target.canonical_work_id = _canonical_work_id(target)
    if promote_incoming:
        target.canonical_work_id = incoming_identity
        target.version_type = incoming.version_type
    _refresh_version_identity(target)
    if related_summary:
        _add_related_version(target, related_summary)


def _can_group_as_versions(target: MergedSearchResult, incoming: MergedSearchResult) -> bool:
    if not target.version_family_id or target.version_family_id != incoming.version_family_id:
        return False
    if (target.canonical_work_id or _canonical_work_id(target)) == (incoming.canonical_work_id or _canonical_work_id(incoming)):
        return True
    version_types = {target.version_type, incoming.version_type}
    return "preprint" in version_types and "journal" in version_types


def _find_merge_target(
    incoming: MergedSearchResult,
    doi_aliases: dict[str, MergedSearchResult],
    title_aliases: dict[str, list[MergedSearchResult]],
    family_aliases: dict[str, list[MergedSearchResult]],
) -> MergedSearchResult | None:
    doi_key = f"doi:{incoming.doi.lower()}" if incoming.doi else ""
    title_key = _title_key(incoming.title, incoming.year)
    family_key = incoming.version_family_id
    target = doi_aliases.get(doi_key) if doi_key else None
    if target is None and title_key:
        eligible = [
            candidate
            for candidate in title_aliases.get(title_key, [])
            if not (candidate.doi and incoming.doi and candidate.doi != incoming.doi)
        ]
        if len(eligible) == 1:
            target = eligible[0]
    if target is None and family_key:
        eligible = [
            candidate
            for candidate in family_aliases.get(family_key, [])
            if _can_group_as_versions(candidate, incoming)
        ]
        if len(eligible) == 1:
            target = eligible[0]
    return target


def _register_aliases(
    result: MergedSearchResult,
    doi_aliases: dict[str, MergedSearchResult],
    title_aliases: dict[str, list[MergedSearchResult]],
    family_aliases: dict[str, list[MergedSearchResult]],
) -> None:
    doi_key = f"doi:{result.doi.lower()}" if result.doi else ""
    title_key = _title_key(result.title, result.year)
    family_key = result.version_family_id
    if doi_key:
        doi_aliases[doi_key] = result
    if title_key and result not in title_aliases.setdefault(title_key, []):
        title_aliases[title_key].append(result)
    if family_key and result not in family_aliases.setdefault(family_key, []):
        family_aliases[family_key].append(result)


def search_with_status(
    query: str,
    limit: int = 10,
    year_range: str | None = None,
    *,
    sources: str | None = None,
    email: str = "",
    strategy: str = DEFAULT_STRATEGY,
    legacy_fallback_results: list[object] | None = None,
) -> MultiSearchResponse:
    strategy_value = (strategy or DEFAULT_STRATEGY).strip().lower().replace("-", "_")
    if strategy_value not in {"legacy", "hybrid"}:
        raise ValueError(f"Unknown search strategy: {strategy}")
    if strategy_value == "hybrid":
        return _hybrid_search_with_status(
            query,
            limit=limit,
            year_range=year_range,
            sources=sources,
            email=email,
            legacy_fallback_results=legacy_fallback_results,
        )
    return _legacy_search_with_status(query, limit=limit, year_range=year_range, sources=sources, email=email)


def _legacy_search_with_status(
    query: str,
    limit: int = 10,
    year_range: str | None = None,
    *,
    sources: str | None = None,
    email: str = "",
) -> MultiSearchResponse:
    selected_sources = parse_sources(sources)
    providers: dict[str, Callable[[], list[object]]] = {
        "semantic_scholar": lambda: semantic_scholar.search(
            query, limit=limit, year_range=year_range, raise_on_error=True
        ),
        "openalex": lambda: openalex.search(
            query, limit=limit, year_range=year_range, email=email, raise_on_error=True
        ),
        "crossref": lambda: crossref.search(
            query, limit=limit, year_range=year_range, email=email, raise_on_error=True
        ),
    }
    provider_results: dict[str, list[object]] = {source: [] for source in selected_sources}
    source_status: dict[str, dict[str, object]] = {
        source: {"status": "pending", "count": 0} for source in selected_sources
    }
    with ThreadPoolExecutor(max_workers=len(selected_sources)) as executor:
        futures = {executor.submit(providers[source]): source for source in selected_sources}
        for future in as_completed(futures):
            source = futures[future]
            try:
                provider_results[source] = future.result()
                source_status[source] = {"status": "success", "count": len(provider_results[source])}
            except ProviderSearchError as exc:
                source_status[source] = {"status": exc.status, "count": 0}
                provider_results[source] = []
            except Exception as exc:
                source_status[source] = {
                    "status": classify_provider_exception(exc),
                    "count": 0,
                }
                provider_results[source] = []

    merged: list[MergedSearchResult] = []
    doi_aliases: dict[str, MergedSearchResult] = {}
    title_aliases: dict[str, list[MergedSearchResult]] = {}
    family_aliases: dict[str, list[MergedSearchResult]] = {}
    max_results = max((len(items) for items in provider_results.values()), default=0)
    for position in range(max_results):
        for source in selected_sources:
            if position >= len(provider_results[source]):
                continue
            raw_result = provider_results[source][position]
            incoming = _from_provider(raw_result, source)
            target = _find_merge_target(incoming, doi_aliases, title_aliases, family_aliases)
            if target is None:
                target = incoming
                merged.append(target)
            else:
                _merge(target, incoming)
            _register_aliases(target, doi_aliases, title_aliases, family_aliases)
    return MultiSearchResponse(
        results=merged[: max(limit, 0)],
        source_status=source_status,
        query_plan=build_query_plan(query, strategy="legacy", year_range=year_range, sources=selected_sources),
    )


def _hybrid_search_with_status(
    query: str,
    limit: int = 10,
    year_range: str | None = None,
    *,
    sources: str | None = None,
    email: str = "",
    legacy_fallback_results: list[object] | None = None,
) -> MultiSearchResponse:
    selected_sources = parse_sources(sources)
    channels = _build_channels(
        query,
        limit=limit,
        year_range=year_range,
        sources=selected_sources,
        email=email,
        legacy_fallback_results=legacy_fallback_results,
    )
    channel_results: dict[str, list[object]] = {_status_key(channel): [] for channel in channels}
    source_status: dict[str, dict[str, object]] = {
        _status_key(channel): {
            "provider": channel.provider,
            "channel": channel.key,
            "query_variant": channel.query_variant,
            "status": "pending",
            "count": 0,
            "retryable": False,
        }
        for channel in channels
    }
    if not channels:
        return MultiSearchResponse(
            results=[],
            source_status=source_status,
            query_plan=build_query_plan(query, strategy="hybrid", year_range=year_range, sources=selected_sources, channels=channels),
        )

    with ThreadPoolExecutor(max_workers=len(channels)) as executor:
        futures = {executor.submit(channel.search): channel for channel in channels}
        for future in as_completed(futures):
            channel = futures[future]
            key = _status_key(channel)
            try:
                channel_results[key] = future.result()
                source_status[key] = {
                    **source_status[key],
                    "status": "success",
                    "count": len(channel_results[key]),
                }
            except ProviderSearchError as exc:
                source_status[key] = {
                    **source_status[key],
                    "status": exc.status,
                    "count": 0,
                    "detail": exc.detail,
                    "retryable": exc.status in {"rate_limited", "timeout", "network_error", "service_unavailable"},
                }
                channel_results[key] = []
            except Exception as exc:
                source_status[key] = {
                    **source_status[key],
                    "status": classify_provider_exception(exc),
                    "count": 0,
                    "detail": str(exc),
                    "retryable": True,
                }
                channel_results[key] = []

    merged = _merge_ranked_channel_results(channel_results, channels)
    merged.sort(key=_hybrid_sort_key)
    merged = _apply_legacy_recall_floor(
        merged,
        channel_results.get("legacy_fallback:q_legacy_fallback_1", []),
        limit=limit,
    )
    return MultiSearchResponse(
        results=merged[: max(limit, 0)],
        source_status=source_status,
        query_plan=build_query_plan(query, strategy="hybrid", year_range=year_range, sources=selected_sources, channels=channels),
    )


def _build_channels(
    query: str,
    *,
    limit: int,
    year_range: str | None,
    sources: list[str],
    email: str,
    legacy_fallback_results: list[object] | None = None,
) -> list[RetrievalChannel]:
    channels: list[RetrievalChannel] = []
    if "openalex" in sources:
        channels.append(
            RetrievalChannel(
                key="openalex_keyword",
                provider="openalex",
                query_variant="q_keyword_1",
                weight=CHANNEL_WEIGHTS["openalex_keyword"],
                search=lambda: openalex.search(
                    query, limit=limit, year_range=year_range, email=email, raise_on_error=True
                ),
            )
        )
        channels.append(
            RetrievalChannel(
                key="openalex_semantic",
                provider="openalex",
                query_variant="q_semantic_1",
                weight=CHANNEL_WEIGHTS["openalex_semantic"],
                search=lambda: openalex.search_semantic(
                    query, limit=limit, year_range=year_range, email=email, raise_on_error=True
                ),
            )
        )
    if "semantic_scholar" in sources:
        channels.append(
            RetrievalChannel(
                key="semantic_scholar_keyword",
                provider="semantic_scholar",
                query_variant="q_keyword_1",
                weight=CHANNEL_WEIGHTS["semantic_scholar_keyword"],
                search=lambda: semantic_scholar.search(
                    query, limit=limit, year_range=year_range, raise_on_error=True
                ),
            )
        )
    if "crossref" in sources:
        identifier_doi = _query_identifier_doi(query)
        if identifier_doi:
            channels.append(
                RetrievalChannel(
                    key="crossref_identifier_resolution",
                    provider="crossref",
                    query_variant="q_identifier_1",
                    weight=CHANNEL_WEIGHTS["crossref_identifier_resolution"],
                    search=lambda: crossref.resolve_identifier(
                        identifier_doi, email=email, raise_on_error=True
                    ),
                )
            )
        channels.append(
            RetrievalChannel(
                key="crossref_exact_title",
                provider="crossref",
                query_variant="q_exact_title_1",
                weight=CHANNEL_WEIGHTS["crossref_exact_title"],
                search=lambda: crossref.search_exact_title(
                    query, limit=min(limit, 5), year_range=year_range, email=email, raise_on_error=True
                ),
            )
        )
        channels.append(
            RetrievalChannel(
                key="crossref_keyword",
                provider="crossref",
                query_variant="q_keyword_1",
                weight=CHANNEL_WEIGHTS["crossref_keyword"],
                search=lambda: crossref.search(
                    query, limit=limit, year_range=year_range, email=email, raise_on_error=True
                ),
            )
        )
    if channels:
        fallback_results = list(legacy_fallback_results) if legacy_fallback_results is not None else None
        channels.append(
            RetrievalChannel(
                key="legacy_fallback",
                provider="instsci",
                query_variant="q_legacy_fallback_1",
                weight=CHANNEL_WEIGHTS["legacy_fallback"],
                search=(
                    (lambda results=fallback_results: results)
                    if fallback_results is not None
                    else lambda: _legacy_search_with_status(
                        query,
                        limit=limit,
                        year_range=year_range,
                        sources=",".join(sources),
                        email=email,
                    ).results
                ),
            )
        )
    return channels


def _status_key(channel: RetrievalChannel) -> str:
    return f"{channel.key}:{channel.query_variant}"


def _query_identifier_doi(query: str) -> str:
    doi = normalize_doi(query)
    return doi if doi.lower().startswith("10.") and "/" in doi else ""


def _merge_ranked_channel_results(
    channel_results: dict[str, list[object]],
    channels: list[RetrievalChannel],
) -> list[MergedSearchResult]:
    merged: list[MergedSearchResult] = []
    doi_aliases: dict[str, MergedSearchResult] = {}
    title_aliases: dict[str, list[MergedSearchResult]] = {}
    family_aliases: dict[str, list[MergedSearchResult]] = {}
    for channel in channels:
        channel_key = _status_key(channel)
        raw_results = channel_results.get(channel_key, [])
        for rank, raw_result in enumerate(raw_results, 1):
            incoming = _from_provider(
                raw_result,
                channel.provider,
                channel=channel.key,
                query_variant=channel.query_variant,
                rank=rank,
                channel_weight=channel.weight,
            )
            target = _find_merge_target(incoming, doi_aliases, title_aliases, family_aliases)
            if target is None:
                target = incoming
                merged.append(target)
            elif channel.key != "legacy_fallback":
                _merge(target, incoming)
            _register_aliases(target, doi_aliases, title_aliases, family_aliases)
    return merged


def _hybrid_sort_key(result: MergedSearchResult) -> tuple[float, int, int, str, str]:
    return (
        -round(result.fusion_score, 12),
        -len(result.retrieval_provenance),
        -(result.year or 0),
        result.doi or result.arxiv_id or result.paper_id,
        result.title.lower(),
    )


def _result_match_keys(result: object) -> set[str]:
    doi = normalize_doi(str(getattr(result, "doi", "") or ""))
    arxiv_id = str(getattr(result, "arxiv_id", "") or "").strip().lower()
    paper_id = str(getattr(result, "paper_id", "") or "").strip().lower()
    title = str(getattr(result, "title", "") or "")
    year = getattr(result, "year", None)
    keys: set[str] = set()
    if doi:
        keys.add(f"doi:{doi}")
    if arxiv_id:
        keys.add(f"arxiv:{arxiv_id}")
    if paper_id:
        keys.add(f"paper:{paper_id}")
    title_key = _title_key(title, year if isinstance(year, int) else None)
    if title_key:
        keys.add(title_key)
    return keys


def _apply_legacy_recall_floor(
    ranked: list[MergedSearchResult],
    legacy_fallback_results: list[object],
    *,
    limit: int,
) -> list[MergedSearchResult]:
    """Keep legacy top-N membership while preserving hybrid order inside that set."""
    max_results = max(limit, 0)
    if max_results == 0 or not legacy_fallback_results:
        return ranked

    fallback_keys: set[str] = set()
    for result in legacy_fallback_results[:max_results]:
        fallback_keys.update(_result_match_keys(result))
    if not fallback_keys:
        return ranked

    required: list[MergedSearchResult] = []
    for result in ranked:
        if _result_match_keys(result) & fallback_keys:
            required.append(result)

    selected = list(ranked[:max_results])
    selected_ids = {id(result) for result in selected}
    required_ids = {id(result) for result in required}
    for result in required:
        if id(result) in selected_ids:
            continue
        replace_index = next(
            (index for index in range(len(selected) - 1, -1, -1) if id(selected[index]) not in required_ids),
            None,
        )
        if replace_index is None:
            break
        selected_ids.remove(id(selected[replace_index]))
        selected[replace_index] = result
        selected_ids.add(id(result))

    return [result for result in ranked if id(result) in selected_ids] + [
        result for result in ranked if id(result) not in selected_ids
    ]


def build_query_plan(
    query: str,
    *,
    strategy: str,
    year_range: str | None,
    sources: list[str],
    channels: list[RetrievalChannel] | None = None,
) -> dict[str, object]:
    variants = [
        {"id": "q_keyword_1", "type": "keyword", "text": query, "generated_by": "user"},
    ]
    if strategy == "hybrid" and "openalex" in sources:
        variants.append({"id": "q_semantic_1", "type": "semantic", "text": query, "generated_by": "deterministic"})
    if strategy == "hybrid" and "crossref" in sources:
        identifier_doi = _query_identifier_doi(query)
        if identifier_doi:
            variants.append(
                {"id": "q_identifier_1", "type": "identifier", "text": identifier_doi, "generated_by": "deterministic"}
            )
        variants.append({"id": "q_exact_title_1", "type": "exact_title", "text": query, "generated_by": "deterministic"})
    if strategy == "hybrid" and channels and any(channel.key == "legacy_fallback" for channel in channels):
        variants.append({"id": "q_legacy_fallback_1", "type": "keyword", "text": query, "generated_by": "legacy_fallback"})
    channel_plan = [
        {
            "provider": channel.provider,
            "channel": channel.key,
            "query_variant": channel.query_variant,
            "weight": channel.weight,
        }
        for channel in (channels or [])
    ]
    return {
        "schema": "instsci.query_plan.v1",
        "intent": "exploratory",
        "strategy": strategy,
        "original_query": query,
        "generated_by": "deterministic",
        "planner_version": "v1",
        "variants": variants,
        "channels": channel_plan,
        "filters": {"year_range": year_range or ""},
        "warnings": [],
        "unresolved_terms": [],
    }


def search(
    query: str,
    limit: int = 10,
    year_range: str | None = None,
    *,
    sources: str | None = None,
    email: str = "",
    strategy: str = DEFAULT_STRATEGY,
) -> list[MergedSearchResult]:
    """Compatibility wrapper returning only merged results."""
    return search_with_status(
        query,
        limit=limit,
        year_range=year_range,
        sources=sources,
        email=email,
        strategy=strategy,
    ).results
