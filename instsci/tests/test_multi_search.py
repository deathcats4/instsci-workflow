import asyncio
from unittest import TestCase
from unittest.mock import patch

from instsci import multi_search
from instsci.search_pipeline import result_to_record
from instsci.sources.semantic_scholar import SearchResult
from instsci.sources.errors import ProviderSearchError


class MultiSearchTests(TestCase):
    def test_merges_same_doi_and_preserves_source_citation_counts(self) -> None:
        s2 = SearchResult(title="Shared title", authors=["A"], year=2024, doi="10.1000/ABC", citation_count=5)
        oa = SearchResult(title="Shared title", authors=["A", "B"], year=2024, doi="https://doi.org/10.1000/abc", journal="Journal", citation_count=8)
        cr = SearchResult(title="Shared title", year=2024, doi="10.1000/abc", citation_count=3)
        with (
            patch("instsci.multi_search.semantic_scholar.search", return_value=[s2]),
            patch("instsci.multi_search.openalex.search", return_value=[oa]),
            patch("instsci.multi_search.crossref.search", return_value=[cr]),
        ):
            results = multi_search.search("topic", limit=10)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].doi, "10.1000/abc")
        self.assertEqual(results[0].sources, ["semantic_scholar", "openalex", "crossref"])
        self.assertEqual(results[0].citation_counts, {"semantic_scholar": 5, "openalex": 8, "crossref": 3})
        self.assertEqual(results[0].citation_count, 8)

    def test_title_and_year_merge_record_when_one_source_lacks_doi(self) -> None:
        without_doi = SearchResult(title="A Study: Example", year=2023)
        with_doi = SearchResult(title="A Study Example", year=2023, doi="10.1000/example")
        with (
            patch("instsci.multi_search.semantic_scholar.search", return_value=[without_doi]),
            patch("instsci.multi_search.openalex.search", return_value=[with_doi]),
        ):
            results = multi_search.search("topic", sources="semantic_scholar,openalex")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].doi, "10.1000/example")

    def test_same_title_and_year_with_different_dois_stays_separate(self) -> None:
        first = SearchResult(title="Same title", year=2024, doi="10.1000/first")
        second = SearchResult(title="Same title", year=2024, doi="10.1000/second")
        with (
            patch("instsci.multi_search.semantic_scholar.search", return_value=[first]),
            patch("instsci.multi_search.openalex.search", return_value=[second]),
        ):
            results = multi_search.search("topic", sources="semantic_scholar,openalex")

        self.assertEqual([result.doi for result in results], ["10.1000/first", "10.1000/second"])

    def test_same_title_distinct_journal_dois_do_not_group_as_versions(self) -> None:
        first = SearchResult(title="Collision Study", year=2024, doi="10.1000/first", journal="Journal A")
        second = SearchResult(title="Collision Study", year=2023, doi="10.1000/second", journal="Journal B")
        with (
            patch("instsci.multi_search.openalex.search", return_value=[first]),
            patch("instsci.multi_search.crossref.search", return_value=[second]),
        ):
            results = multi_search.search("topic", sources="openalex,crossref", limit=10)

        self.assertEqual([result.doi for result in results], ["10.1000/first", "10.1000/second"])

    def test_provider_failure_degrades_to_remaining_sources(self) -> None:
        crossref_result = SearchResult(title="Available", doi="10.1000/available")
        with (
            patch("instsci.multi_search.semantic_scholar.search", side_effect=RuntimeError("offline")),
            patch("instsci.multi_search.crossref.search", return_value=[crossref_result]),
        ):
            response = multi_search.search_with_status("topic", sources="semantic_scholar,crossref")
        self.assertEqual([result.doi for result in response.results], ["10.1000/available"])
        self.assertEqual(response.source_status["semantic_scholar"]["status"], "error")
        self.assertEqual(response.source_status["crossref"], {"status": "success", "count": 1})

    def test_provider_rate_limit_is_distinct_from_zero_results(self) -> None:
        with patch(
            "instsci.multi_search.semantic_scholar.search",
            side_effect=ProviderSearchError("semantic_scholar", "rate_limited", "HTTP 429"),
        ):
            response = multi_search.search_with_status("topic", sources="semantic_scholar")

        self.assertEqual(response.results, [])
        self.assertEqual(response.source_status["semantic_scholar"]["status"], "rate_limited")

    def test_hybrid_search_uses_channel_status_and_weighted_rrf(self) -> None:
        keyword = SearchResult(title="Keyword", year=2024, doi="10.1000/keyword")
        semantic = SearchResult(title="Shared", year=2024, doi="10.1000/shared")
        s2 = SearchResult(title="Shared", year=2024, doi="10.1000/shared")
        crossref = SearchResult(title="Crossref", year=2023, doi="10.1000/crossref")
        with (
            patch("instsci.multi_search.openalex.search", return_value=[keyword, semantic]),
            patch("instsci.multi_search.openalex.search_semantic", return_value=[semantic]),
            patch("instsci.multi_search.semantic_scholar.search", return_value=[s2]),
            patch("instsci.multi_search.crossref.search_exact_title", return_value=[]),
            patch("instsci.multi_search.crossref.search", return_value=[crossref]),
        ):
            response = multi_search.search_with_status("topic", limit=10, strategy="hybrid")

        self.assertIn("openalex_keyword:q_keyword_1", response.source_status)
        self.assertIn("openalex_semantic:q_semantic_1", response.source_status)
        self.assertEqual(response.query_plan["strategy"], "hybrid")
        self.assertEqual(
            response.query_plan["channels"],
            [
                {"provider": "openalex", "channel": "openalex_keyword", "query_variant": "q_keyword_1", "weight": 1.0},
                {"provider": "openalex", "channel": "openalex_semantic", "query_variant": "q_semantic_1", "weight": 1.1},
                {"provider": "semantic_scholar", "channel": "semantic_scholar_keyword", "query_variant": "q_keyword_1", "weight": 1.0},
                {"provider": "crossref", "channel": "crossref_exact_title", "query_variant": "q_exact_title_1", "weight": 1.4},
                {"provider": "crossref", "channel": "crossref_keyword", "query_variant": "q_keyword_1", "weight": 0.55},
                {"provider": "instsci", "channel": "legacy_fallback", "query_variant": "q_legacy_fallback_1", "weight": 1.05},
            ],
        )
        self.assertEqual(
            sorted(response.source_status),
            sorted(f"{item['channel']}:{item['query_variant']}" for item in response.query_plan["channels"]),
        )
        self.assertEqual(response.results[0].doi, "10.1000/shared")
        self.assertGreater(response.results[0].fusion_score, response.results[1].fusion_score)
        self.assertEqual(
            [item["channel"] for item in response.results[0].retrieval_provenance],
            ["openalex_keyword", "openalex_semantic", "semantic_scholar_keyword"],
        )

    def test_hybrid_channel_failure_still_returns_partial_results(self) -> None:
        keyword = SearchResult(title="Keyword", year=2024, doi="10.1000/keyword")
        with (
            patch("instsci.multi_search.openalex.search", return_value=[keyword]),
            patch(
                "instsci.multi_search.openalex.search_semantic",
                side_effect=ProviderSearchError("openalex", "authentication_required", "missing key"),
            ),
            patch("instsci.multi_search.semantic_scholar.search", return_value=[]),
            patch("instsci.multi_search.crossref.search_exact_title", return_value=[]),
            patch("instsci.multi_search.crossref.search", return_value=[]),
        ):
            response = multi_search.search_with_status("topic", limit=10, strategy="hybrid")

        self.assertEqual([result.doi for result in response.results], ["10.1000/keyword"])
        self.assertEqual(response.source_status["openalex_semantic:q_semantic_1"]["status"], "authentication_required")

    def test_hybrid_uses_legacy_fallback_channel_for_recall(self) -> None:
        keyword = SearchResult(title="Keyword", year=2024, doi="10.1000/keyword")
        fallback = multi_search.MergedSearchResult(title="Legacy only", year=2023, doi="10.1000/fallback")
        with (
            patch("instsci.multi_search.openalex.search", return_value=[keyword]),
            patch("instsci.multi_search.openalex.search_semantic", return_value=[]),
            patch(
                "instsci.multi_search._legacy_search_with_status",
                return_value=multi_search.MultiSearchResponse(results=[fallback]),
            ),
        ):
            response = multi_search.search_with_status("topic", limit=10, sources="openalex", strategy="hybrid")

        self.assertIn("legacy_fallback:q_legacy_fallback_1", response.source_status)
        self.assertIn(
            {"id": "q_legacy_fallback_1", "type": "keyword", "text": "topic", "generated_by": "legacy_fallback"},
            response.query_plan["variants"],
        )
        self.assertIn(
            {
                "provider": "instsci",
                "channel": "legacy_fallback",
                "query_variant": "q_legacy_fallback_1",
                "weight": multi_search.CHANNEL_WEIGHTS["legacy_fallback"],
            },
            response.query_plan["channels"],
        )
        self.assertIn("10.1000/fallback", [result.doi for result in response.results])
        fallback_result = next(result for result in response.results if result.doi == "10.1000/fallback")
        self.assertEqual(fallback_result.retrieval_provenance[-1]["channel"], "legacy_fallback")
        self.assertEqual(fallback_result.sources[-1], "instsci")

    def test_hybrid_can_use_external_legacy_fallback_results(self) -> None:
        keyword = SearchResult(title="Keyword", year=2024, doi="10.1000/keyword")
        fallback = multi_search.MergedSearchResult(title="External legacy", year=2023, doi="10.1000/external")
        with (
            patch("instsci.multi_search.openalex.search", return_value=[keyword]),
            patch("instsci.multi_search.openalex.search_semantic", return_value=[]),
            patch("instsci.multi_search._legacy_search_with_status") as internal_legacy,
        ):
            response = multi_search.search_with_status(
                "topic",
                limit=10,
                sources="openalex",
                strategy="hybrid",
                legacy_fallback_results=[fallback],
            )

        internal_legacy.assert_not_called()
        self.assertIn("legacy_fallback:q_legacy_fallback_1", response.source_status)
        self.assertEqual(response.source_status["legacy_fallback:q_legacy_fallback_1"]["status"], "success")
        self.assertIn("10.1000/external", [result.doi for result in response.results])

    def test_hybrid_recall_floor_keeps_legacy_top_n_candidates(self) -> None:
        hybrid_only = multi_search.MergedSearchResult(
            title="Hybrid only",
            doi="10.1000/hybrid",
            fusion_score=1.0,
            retrieval_provenance=[{"channel": "openalex_keyword"}],
        )
        legacy_first = multi_search.MergedSearchResult(
            title="Legacy first",
            doi="10.1000/legacy-first",
            fusion_score=0.1,
            retrieval_provenance=[{"channel": "legacy_fallback"}],
        )
        legacy_second = multi_search.MergedSearchResult(
            title="Legacy second",
            doi="10.1000/legacy-second",
            fusion_score=0.05,
            retrieval_provenance=[{"channel": "legacy_fallback"}],
        )
        ranked = [hybrid_only, legacy_first, legacy_second]

        floored = multi_search._apply_legacy_recall_floor(ranked, [legacy_first, legacy_second], limit=2)

        self.assertEqual([result.doi for result in floored[:2]], ["10.1000/legacy-first", "10.1000/legacy-second"])

    def test_hybrid_keeps_same_channel_distinct_query_variants(self) -> None:
        first = multi_search.RetrievalChannel(
            key="semantic_scholar_keyword",
            provider="semantic_scholar",
            query_variant="q_keyword_1",
            weight=1.0,
            search=lambda: [SearchResult(title="Shared", year=2024, doi="10.1000/shared")],
        )
        second = multi_search.RetrievalChannel(
            key="semantic_scholar_keyword",
            provider="semantic_scholar",
            query_variant="q_keyword_2",
            weight=0.5,
            search=lambda: [SearchResult(title="Shared", year=2024, doi="10.1000/shared")],
        )
        with patch("instsci.multi_search._build_channels", return_value=[first, second]):
            response = multi_search.search_with_status("topic", limit=10, strategy="hybrid")

        self.assertEqual(
            sorted(response.source_status),
            ["semantic_scholar_keyword:q_keyword_1", "semantic_scholar_keyword:q_keyword_2"],
        )
        self.assertEqual(len(response.results), 1)
        self.assertEqual(
            [item["query_variant"] for item in response.results[0].retrieval_provenance],
            ["q_keyword_1", "q_keyword_2"],
        )
        self.assertAlmostEqual(response.results[0].fusion_score, (1.0 / 61) + (0.5 / 61))

    def test_hybrid_crossref_exact_title_is_distinct_high_weight_channel(self) -> None:
        exact = SearchResult(title="Exact title paper", year=2024, doi="10.1000/exact")
        keyword = SearchResult(title="Keyword paper", year=2024, doi="10.1000/keyword")
        with (
            patch("instsci.multi_search.openalex.search", return_value=[]),
            patch("instsci.multi_search.openalex.search_semantic", return_value=[]),
            patch("instsci.multi_search.semantic_scholar.search", return_value=[]),
            patch("instsci.multi_search.crossref.search_exact_title", return_value=[exact]) as exact_search,
            patch("instsci.multi_search.crossref.search", return_value=[keyword]),
        ):
            response = multi_search.search_with_status("Exact title paper", limit=10, strategy="hybrid")

        exact_search.assert_called_once()
        self.assertIn("crossref_exact_title:q_exact_title_1", response.source_status)
        self.assertIn("crossref_keyword:q_keyword_1", response.source_status)
        self.assertEqual(response.results[0].doi, "10.1000/exact")
        self.assertEqual(response.results[0].retrieval_provenance[0]["channel"], "crossref_exact_title")
        self.assertEqual(response.results[0].retrieval_provenance[0]["weight"], 1.40)
        self.assertGreater(response.results[0].fusion_score, response.results[1].fusion_score)

    def test_hybrid_crossref_identifier_resolution_only_for_doi_query(self) -> None:
        resolved = SearchResult(title="Resolved DOI paper", year=2024, doi="10.1000/resolved")
        with (
            patch("instsci.multi_search.openalex.search", return_value=[]),
            patch("instsci.multi_search.openalex.search_semantic", return_value=[]),
            patch("instsci.multi_search.semantic_scholar.search", return_value=[]),
            patch("instsci.multi_search.crossref.resolve_identifier", return_value=[resolved]) as identifier_search,
            patch("instsci.multi_search.crossref.search_exact_title", return_value=[]),
            patch("instsci.multi_search.crossref.search", return_value=[]),
        ):
            response = multi_search.search_with_status(
                "https://doi.org/10.1000/resolved",
                limit=10,
                strategy="hybrid",
            )

        identifier_search.assert_called_once()
        self.assertIn("crossref_identifier_resolution:q_identifier_1", response.source_status)
        self.assertIn(
            {"id": "q_identifier_1", "type": "identifier", "text": "10.1000/resolved", "generated_by": "deterministic"},
            response.query_plan["variants"],
        )
        self.assertEqual(response.results[0].doi, "10.1000/resolved")
        self.assertEqual(response.results[0].retrieval_provenance[0]["channel"], "crossref_identifier_resolution")
        self.assertEqual(response.results[0].retrieval_provenance[0]["weight"], 1.40)

    def test_hybrid_merge_is_stable_when_channel_results_arrive_in_different_order(self) -> None:
        first = multi_search.RetrievalChannel(
            key="openalex_keyword",
            provider="openalex",
            query_variant="q_keyword_1",
            weight=1.0,
            search=lambda: [],
        )
        second = multi_search.RetrievalChannel(
            key="semantic_scholar_keyword",
            provider="semantic_scholar",
            query_variant="q_keyword_1",
            weight=1.0,
            search=lambda: [],
        )
        first_results = {
            "openalex_keyword:q_keyword_1": [
                SearchResult(title="Beta Study", year=2024, doi="10.1000/beta"),
                SearchResult(title="Alpha Study", year=2024, doi="10.1000/alpha"),
            ],
            "semantic_scholar_keyword:q_keyword_1": [
                SearchResult(title="Alpha Study", year=2024, doi="10.1000/alpha"),
                SearchResult(title="Beta Study", year=2024, doi="10.1000/beta"),
            ],
        }
        second_results = {
            "semantic_scholar_keyword:q_keyword_1": first_results["semantic_scholar_keyword:q_keyword_1"],
            "openalex_keyword:q_keyword_1": first_results["openalex_keyword:q_keyword_1"],
        }

        merged_first = multi_search._merge_ranked_channel_results(first_results, [first, second])
        merged_second = multi_search._merge_ranked_channel_results(second_results, [first, second])
        merged_first.sort(key=multi_search._hybrid_sort_key)
        merged_second.sort(key=multi_search._hybrid_sort_key)

        self.assertEqual(
            [(result.doi, [item["channel"] for item in result.retrieval_provenance]) for result in merged_first],
            [
                ("10.1000/alpha", ["openalex_keyword", "semantic_scholar_keyword"]),
                ("10.1000/beta", ["openalex_keyword", "semantic_scholar_keyword"]),
            ],
        )
        self.assertEqual(
            [(result.doi, [item["channel"] for item in result.retrieval_provenance]) for result in merged_second],
            [(result.doi, [item["channel"] for item in result.retrieval_provenance]) for result in merged_first],
        )

    def test_mcp_reports_source_specific_citations_and_provider_status(self) -> None:
        from instsci import mcp_server

        response = multi_search.MultiSearchResponse(
            results=[
                multi_search.MergedSearchResult(
                    title="Paper",
                    doi="10.1000/paper",
                    sources=["semantic_scholar", "openalex"],
                    citation_count=15,
                    citation_counts={"semantic_scholar": 12, "openalex": 15},
                )
            ],
            source_status={
                "semantic_scholar": {"status": "rate_limited", "count": 0},
                "openalex": {"status": "success", "count": 1},
            },
        )
        with patch("instsci.mcp_server.multi_search.search_with_status", return_value=response):
            text = asyncio.run(mcp_server.search_papers("topic"))

        self.assertIn("semantic_scholar: rate_limited", text)
        self.assertIn("Semantic Scholar 12; Openalex 15", text)
        self.assertNotIn("**Citations:** 15", text)

    def test_export_record_includes_sources_and_citation_counts(self) -> None:
        result = multi_search.MergedSearchResult(
            title="Paper",
            doi="10.1000/paper",
            sources=["openalex", "crossref"],
            citation_counts={"openalex": 4, "crossref": 2},
        )
        record = result_to_record(result, 1)
        self.assertEqual(record["sources"], ["openalex", "crossref"])
        self.assertEqual(record["citation_counts"], {"openalex": 4, "crossref": 2})
