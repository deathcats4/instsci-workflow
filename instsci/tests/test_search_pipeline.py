import json
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from typer.testing import CliRunner

from instsci import multi_search
from instsci.cli import app
from instsci.search_pipeline import (
    build_search_payload,
    derive_work_identity,
    downgrade_record_to_v1,
    downgrade_search_payload_to_v1,
    load_search_payload,
    parse_selection_indices,
    result_to_record,
    select_doi_records,
    validate_search_payload_contract,
    write_search_payload,
)
from instsci.sources.semantic_scholar import SearchResult


class SearchPipelineTests(TestCase):
    def test_search_payload_round_trips_json_and_csv(self) -> None:
        payload = build_search_payload(
            "pyrite uranium",
            [SearchResult(title="Paper", authors=["A", "B"], year=2024, doi="https://doi.org/10.1000/Test")],
            year_range="2020-",
            source_status={"semantic_scholar": {"status": "success", "count": 1}},
        )
        with TemporaryDirectory() as tmp:
            json_path = write_search_payload(payload, Path(tmp) / "results.json")
            csv_path = write_search_payload(payload, Path(tmp) / "results.csv")
            json_loaded = load_search_payload(json_path)
            csv_loaded = load_search_payload(csv_path)

        self.assertEqual(json_loaded["results"][0]["doi"], "10.1000/test")
        self.assertEqual(json_loaded["source_status"]["semantic_scholar"]["count"], 1)
        self.assertEqual(csv_loaded["results"][0]["authors"], ["A", "B"])

    def test_search_payload_v2_preserves_provenance_and_version_placeholders(self) -> None:
        result = SimpleNamespace(
            title="Hybrid Paper",
            authors=[],
            year=2024,
            doi="10.1000/hybrid",
            arxiv_id="",
            journal="",
            sources=["openalex"],
            citation_count=0,
            citation_counts={},
            s2_url="",
            paper_id="",
            retrieval_provenance=[{"provider": "openalex", "channel": "openalex_semantic", "rank": 1}],
            fusion_score=0.018,
            rank_components={"rrf": 0.018},
            related_versions=[],
            discovery_reasons=[],
        )
        payload = build_search_payload(
            "topic",
            [result],
            source_status={"openalex_semantic:q_semantic_1": {"status": "success", "count": 1}},
            query_plan={"schema": "instsci.query_plan.v1", "strategy": "hybrid"},
        )
        with TemporaryDirectory() as tmp:
            json_path = write_search_payload(payload, Path(tmp) / "results.json")
            loaded = load_search_payload(json_path)

        record = loaded["results"][0]
        self.assertEqual(loaded["schema"], "instsci.search_results.v2")
        self.assertEqual(record["canonical_work_id"], "doi:10.1000/hybrid")
        self.assertEqual(record["version_type"], "unknown")
        self.assertEqual(record["retrieval_provenance"][0]["channel"], "openalex_semantic")
        self.assertEqual(record["rank_components"]["rrf"], 0.018)

    def test_search_payload_keeps_legacy_query_plan_as_v1_compat_output(self) -> None:
        result = SearchResult(title="Legacy Paper", authors=["A"], year=2024, doi="10.1000/legacy")
        payload = build_search_payload(
            "topic",
            [result],
            source_status={"semantic_scholar": {"status": "success", "count": 1}},
            query_plan={"schema": "instsci.query_plan.v1", "strategy": "legacy"},
        )

        self.assertEqual(payload["schema"], "instsci.search_results.v1")
        self.assertNotIn("query_plan", payload)

    def test_search_payload_v2_can_downgrade_to_v1_safe_record(self) -> None:
        payload = {
            "schema": "instsci.search_results.v2",
            "query": "topic",
            "source_status": {"openalex_semantic:q_semantic_1": {"status": "success"}},
            "query_plan": {"strategy": "hybrid"},
            "results": [
                {
                    "index": 1,
                    "source": "openalex",
                    "sources": ["openalex"],
                    "paper_id": "W1",
                    "title": "Hybrid Paper",
                    "authors": ["A Author"],
                    "year": 2024,
                    "abstract": "Abstract",
                    "doi": "https://doi.org/10.1000/HYBRID",
                    "arxiv_id": "arXiv:2401.00001v2",
                    "journal": "Journal",
                    "citation_count": 4,
                    "citation_counts": {"openalex": 4},
                    "url": "https://example.test/paper",
                    "canonical_work_id": "doi:10.1000/hybrid",
                    "version_family_id": "title:hybrid paper",
                    "version_type": "journal",
                    "retrieval_provenance": [{"channel": "openalex_semantic"}],
                    "fusion_score": 0.02,
                    "rank_components": {"rrf": 0.02},
                    "unexpected_future_field": "keep old consumers calm",
                }
            ],
        }

        downgraded_record = downgrade_record_to_v1(payload["results"][0])
        downgraded_payload = downgrade_search_payload_to_v1(payload)

        self.assertEqual(downgraded_record["doi"], "10.1000/hybrid")
        self.assertEqual(downgraded_record["arxiv_id"], "2401.00001")
        self.assertNotIn("canonical_work_id", downgraded_record)
        self.assertNotIn("retrieval_provenance", downgraded_record)
        self.assertNotIn("unexpected_future_field", downgraded_record)
        self.assertEqual(downgraded_payload["schema"], "instsci.search_results.v1")
        self.assertEqual(downgraded_payload["results"], [downgraded_record])
        self.assertNotIn("query_plan", downgraded_payload)

    def test_cli_search_downgrade_writes_v1_safe_payload(self) -> None:
        runner = CliRunner()
        with TemporaryDirectory() as tmp:
            source_path = Path(tmp) / "search_v2.json"
            output_path = Path(tmp) / "search_v1.json"
            source_path.write_text(
                json.dumps(
                    {
                        "schema": "instsci.search_results.v2",
                        "query": "topic",
                        "query_plan": {"strategy": "hybrid"},
                        "results": [
                            {
                                "index": 1,
                                "source": "openalex",
                                "sources": ["openalex"],
                                "title": "Hybrid Paper",
                                "doi": "https://doi.org/10.1000/HYBRID",
                                "canonical_work_id": "doi:10.1000/hybrid",
                                "retrieval_provenance": [{"channel": "openalex_semantic"}],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = runner.invoke(app, ["search-downgrade", str(source_path), "--output", str(output_path)])
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(payload["schema"], "instsci.search_results.v1")
        self.assertEqual(payload["results"][0]["doi"], "10.1000/hybrid")
        self.assertNotIn("query_plan", payload)
        self.assertNotIn("retrieval_provenance", payload["results"][0])

    def test_search_contract_validation_accepts_v2_with_future_fields(self) -> None:
        payload = {
            "schema": "instsci.search_results.v2",
            "query": "topic",
            "count": 1,
            "query_plan": {
                "schema": "instsci.query_plan.v1",
                "strategy": "hybrid",
                "channels": [
                    {
                        "provider": "openalex",
                        "channel": "openalex_semantic",
                        "query_variant": "q_semantic_1",
                        "weight": 1.1,
                    }
                ],
            },
            "source_status": {
                "openalex_semantic:q_semantic_1": {
                    "provider": "openalex",
                    "channel": "openalex_semantic",
                    "query_variant": "q_semantic_1",
                    "status": "success",
                    "count": 1,
                    "retryable": False,
                }
            },
            "results": [
                {
                    "index": 1,
                    "source": "openalex",
                    "sources": ["openalex"],
                    "title": "Hybrid Paper",
                    "doi": "10.1000/hybrid",
                    "canonical_work_id": "doi:10.1000/hybrid",
                    "version_family_id": "title:hybrid paper",
                    "version_type": "journal",
                    "related_versions": [],
                    "retrieval_provenance": [
                        {
                            "provider": "openalex",
                            "channel": "openalex_semantic",
                            "query_variant": "q_semantic_1",
                            "rank": 1,
                            "weight": 1.1,
                        }
                    ],
                    "unexpected_future_field": {"kept": True},
                }
            ],
        }

        report = validate_search_payload_contract(payload)

        self.assertTrue(report["valid"], report)
        self.assertEqual(report["schema"], "instsci.search_contract_validation.v1")
        self.assertEqual(report["summary"]["record_count"], 1)
        self.assertEqual(report["summary"]["channel_status_count"], 1)
        self.assertEqual(report["errors"], [])

    def test_search_contract_validation_checks_retrieval_provenance_rows(self) -> None:
        payload = {
            "schema": "instsci.search_results.v2",
            "query": "topic",
            "count": 1,
            "query_plan": {
                "schema": "instsci.query_plan.v1",
                "strategy": "hybrid",
                "channels": [
                    {
                        "provider": "openalex",
                        "channel": "openalex_semantic",
                        "query_variant": "q_semantic_1",
                        "weight": 1.1,
                    }
                ],
            },
            "source_status": {
                "openalex_semantic:q_semantic_1": {
                    "provider": "openalex",
                    "channel": "openalex_semantic",
                    "query_variant": "q_semantic_1",
                    "status": "success",
                    "count": 1,
                }
            },
            "results": [
                {
                    "index": 1,
                    "source": "openalex",
                    "sources": ["openalex"],
                    "title": "Malformed Provenance",
                    "doi": "10.1000/provenance",
                    "canonical_work_id": "doi:10.1000/provenance",
                    "version_family_id": "title:malformed provenance",
                    "version_type": "journal",
                    "related_versions": [],
                    "retrieval_provenance": [
                        {
                            "channel": "openalex_semantic",
                            "query_variant": "q_semantic_1",
                            "rank": "1",
                            "weight": "1.1",
                        }
                    ],
                }
            ],
        }

        report = validate_search_payload_contract(payload)

        self.assertFalse(report["valid"])
        self.assertIn("results[1].retrieval_provenance[1].provider missing", report["errors"])
        self.assertIn("results[1].retrieval_provenance[1].rank must be a positive integer", report["errors"])
        self.assertIn("results[1].retrieval_provenance[1].weight must be numeric", report["errors"])

    def test_search_contract_validation_requires_provenance_channel_in_query_plan(self) -> None:
        payload = {
            "schema": "instsci.search_results.v2",
            "query": "topic",
            "count": 1,
            "query_plan": {
                "schema": "instsci.query_plan.v1",
                "strategy": "hybrid",
                "channels": [
                    {
                        "provider": "openalex",
                        "channel": "openalex_semantic",
                        "query_variant": "q_semantic_1",
                        "weight": 1.1,
                    }
                ],
            },
            "source_status": {
                "openalex_semantic:q_semantic_1": {
                    "provider": "openalex",
                    "channel": "openalex_semantic",
                    "query_variant": "q_semantic_1",
                    "status": "success",
                    "count": 1,
                }
            },
            "results": [
                {
                    "index": 1,
                    "source": "openalex",
                    "sources": ["openalex"],
                    "title": "Mismatched Provenance",
                    "doi": "10.1000/mismatch",
                    "canonical_work_id": "doi:10.1000/mismatch",
                    "version_family_id": "title:mismatched provenance",
                    "version_type": "journal",
                    "related_versions": [],
                    "retrieval_provenance": [
                        {
                            "provider": "openalex",
                            "channel": "openalex_keyword",
                            "query_variant": "q_semantic_1",
                            "rank": 1,
                            "weight": 1.0,
                        }
                    ],
                }
            ],
        }

        report = validate_search_payload_contract(payload)

        self.assertFalse(report["valid"])
        self.assertIn(
            "results[1].retrieval_provenance[1] has no query_plan channel: openalex_keyword:q_semantic_1",
            report["errors"],
        )

    def test_search_contract_validation_requires_source_status_key_to_match_row_and_query_plan(self) -> None:
        payload = {
            "schema": "instsci.search_results.v2",
            "query": "topic",
            "count": 0,
            "query_plan": {
                "schema": "instsci.query_plan.v1",
                "strategy": "hybrid",
                "channels": [
                    {
                        "provider": "openalex",
                        "channel": "openalex_semantic",
                        "query_variant": "q_semantic_1",
                        "weight": 1.1,
                    }
                ],
            },
            "source_status": {
                "openalex_semantic:q_semantic_1": {
                    "provider": "crossref",
                    "channel": "openalex_keyword",
                    "query_variant": "q_semantic_1",
                    "status": "success",
                    "count": 0,
                }
            },
            "results": [],
        }

        report = validate_search_payload_contract(payload)

        self.assertFalse(report["valid"])
        self.assertIn(
            "source_status.openalex_semantic:q_semantic_1 key does not match row channel/query_variant: openalex_keyword:q_semantic_1",
            report["errors"],
        )
        self.assertIn(
            "source_status.openalex_semantic:q_semantic_1.provider does not match query_plan channel provider",
            report["errors"],
        )

    def test_search_contract_validation_requires_status_for_each_query_plan_channel(self) -> None:
        payload = {
            "schema": "instsci.search_results.v2",
            "query": "topic",
            "count": 0,
            "query_plan": {
                "schema": "instsci.query_plan.v1",
                "strategy": "hybrid",
                "channels": [
                    {
                        "provider": "openalex",
                        "channel": "openalex_semantic",
                        "query_variant": "q_semantic_1",
                        "weight": 1.1,
                    }
                ],
            },
            "source_status": {
                "crossref_keyword:q_keyword_1": {
                    "provider": "crossref",
                    "channel": "crossref_keyword",
                    "query_variant": "q_keyword_1",
                    "status": "success",
                    "count": 0,
                }
            },
            "results": [],
        }

        report = validate_search_payload_contract(payload)

        self.assertFalse(report["valid"])
        self.assertIn(
            "query_plan channel has no source_status entry: openalex_semantic:q_semantic_1",
            report["errors"],
        )
        self.assertIn(
            "source_status entry is not present in query_plan channels: crossref_keyword:q_keyword_1",
            report["warnings"],
        )

    def test_search_contract_validation_requires_query_plan_strategy(self) -> None:
        payload = {
            "schema": "instsci.search_results.v2",
            "query": "topic",
            "count": 0,
            "query_plan": {
                "schema": "instsci.query_plan.v1",
                "channels": [],
            },
            "source_status": {},
            "results": [],
        }

        report = validate_search_payload_contract(payload)

        self.assertFalse(report["valid"])
        self.assertIn("query_plan.strategy missing", report["errors"])

    def test_search_contract_validation_rejects_duplicate_query_plan_channel_keys(self) -> None:
        payload = {
            "schema": "instsci.search_results.v2",
            "query": "topic",
            "count": 0,
            "query_plan": {
                "schema": "instsci.query_plan.v1",
                "strategy": "hybrid",
                "channels": [
                    {
                        "provider": "openalex",
                        "channel": "openalex_semantic",
                        "query_variant": "q_semantic_1",
                        "weight": 1.1,
                    },
                    {
                        "provider": "openalex",
                        "channel": "openalex_semantic",
                        "query_variant": "q_semantic_1",
                        "weight": 1.1,
                    },
                ],
            },
            "source_status": {
                "openalex_semantic:q_semantic_1": {
                    "provider": "openalex",
                    "channel": "openalex_semantic",
                    "query_variant": "q_semantic_1",
                    "status": "success",
                    "count": 0,
                }
            },
            "results": [],
        }

        report = validate_search_payload_contract(payload)

        self.assertFalse(report["valid"])
        self.assertIn(
            "query_plan.channels[2] duplicates channel/query_variant: openalex_semantic:q_semantic_1",
            report["errors"],
        )

    def test_search_contract_validation_requires_provenance_weight_to_match_query_plan(self) -> None:
        payload = {
            "schema": "instsci.search_results.v2",
            "query": "topic",
            "count": 1,
            "query_plan": {
                "schema": "instsci.query_plan.v1",
                "strategy": "hybrid",
                "channels": [
                    {
                        "provider": "openalex",
                        "channel": "openalex_semantic",
                        "query_variant": "q_semantic_1",
                        "weight": 1.1,
                    }
                ],
            },
            "source_status": {
                "openalex_semantic:q_semantic_1": {
                    "provider": "openalex",
                    "channel": "openalex_semantic",
                    "query_variant": "q_semantic_1",
                    "status": "success",
                    "count": 1,
                }
            },
            "results": [
                {
                    "index": 1,
                    "source": "openalex",
                    "sources": ["openalex"],
                    "title": "Mismatched Weight",
                    "doi": "10.1000/weight",
                    "canonical_work_id": "doi:10.1000/weight",
                    "version_family_id": "title:mismatched weight",
                    "version_type": "journal",
                    "related_versions": [],
                    "retrieval_provenance": [
                        {
                            "provider": "openalex",
                            "channel": "openalex_semantic",
                            "query_variant": "q_semantic_1",
                            "rank": 1,
                            "weight": 1.0,
                        }
                    ],
                }
            ],
        }

        report = validate_search_payload_contract(payload)

        self.assertFalse(report["valid"])
        self.assertIn(
            "results[1].retrieval_provenance[1].weight does not match query_plan channel weight: openalex_semantic:q_semantic_1",
            report["errors"],
        )

    def test_search_contract_validation_reports_missing_v2_contract_fields(self) -> None:
        payload = {
            "schema": "instsci.search_results.v2",
            "query": "topic",
            "query_plan": {
                "schema": "instsci.query_plan.v1",
                "strategy": "hybrid",
                "channels": [
                    {
                        "provider": "openalex",
                        "channel": "openalex_semantic",
                        "query_variant": "q_semantic_1",
                        "weight": 1.1,
                    }
                ],
            },
            "source_status": {
                "openalex_semantic:q_semantic_1": {
                    "status": "success",
                    "count": 1,
                }
            },
            "results": [
                {
                    "index": 1,
                    "title": "Broken Hybrid Paper",
                    "doi": "10.1000/broken",
                    "canonical_work_id": "doi:10.1000/broken",
                    "retrieval_provenance": [{"channel": "openalex_semantic", "query_variant": "q_semantic_1"}],
                }
            ],
        }

        report = validate_search_payload_contract(payload)

        self.assertFalse(report["valid"])
        self.assertIn("source_status.openalex_semantic:q_semantic_1.provider missing", report["errors"])
        self.assertIn("source_status.openalex_semantic:q_semantic_1.channel missing", report["errors"])
        self.assertIn("source_status.openalex_semantic:q_semantic_1.query_variant missing", report["errors"])
        self.assertIn("results[1].version_family_id missing", report["errors"])
        self.assertIn("results[1].related_versions must be a list", report["errors"])

    def test_cli_search_validate_writes_contract_report_and_fails_invalid_payload(self) -> None:
        runner = CliRunner()
        with TemporaryDirectory() as tmp:
            search_path = Path(tmp) / "broken_search.json"
            output_path = Path(tmp) / "contract_report.json"
            search_path.write_text(
                json.dumps(
                    {
                        "schema": "instsci.search_results.v2",
                        "query_plan": {"schema": "instsci.query_plan.v1", "strategy": "hybrid", "channels": []},
                        "source_status": {},
                        "results": [{"index": 1, "title": "Missing contract fields"}],
                    }
                ),
                encoding="utf-8",
            )

            result = runner.invoke(app, ["search-validate", str(search_path), "--output", str(output_path)])
            report = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(result.exit_code, 2)
        self.assertFalse(report["valid"])
        self.assertIn("results[1].canonical_work_id missing", report["errors"])
        self.assertIn("Search contract validation", result.output)

    def test_cli_search_validate_rejects_non_search_result_schemas(self) -> None:
        runner = CliRunner()
        with TemporaryDirectory() as tmp:
            expansion_path = Path(tmp) / "expanded.json"
            output_path = Path(tmp) / "contract_report.json"
            expansion_path.write_text(
                json.dumps(
                    {
                        "schema": "instsci.search_expansion.v1",
                        "candidate_count": 0,
                        "acquisition_started": False,
                        "results": [],
                    }
                ),
                encoding="utf-8",
            )

            result = runner.invoke(app, ["search-validate", str(expansion_path), "--output", str(output_path)])
            report = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(result.exit_code, 2)
        self.assertFalse(report["valid"])
        self.assertIn(
            "schema is not a search result contract: instsci.search_expansion.v1",
            report["errors"],
        )

    def test_cli_search_validate_rejects_missing_schema(self) -> None:
        runner = CliRunner()
        with TemporaryDirectory() as tmp:
            search_path = Path(tmp) / "missing_schema.json"
            output_path = Path(tmp) / "contract_report.json"
            search_path.write_text(json.dumps({"results": []}), encoding="utf-8")

            result = runner.invoke(app, ["search-validate", str(search_path), "--output", str(output_path)])
            report = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(result.exit_code, 2)
        self.assertFalse(report["valid"])
        self.assertIn("schema missing", report["errors"])

    def test_work_identity_derives_canonical_family_and_version_type(self) -> None:
        identity = derive_work_identity(
            doi="https://doi.org/10.1000/JOURNAL",
            arxiv_id="2401.00001v2",
            title="Shared Version Study",
            journal="Journal of Testing",
        )

        self.assertEqual(identity["canonical_work_id"], "doi:10.1000/journal")
        self.assertEqual(identity["version_family_id"], "title:shared version study")
        self.assertEqual(identity["version_type"], "journal")

        record = result_to_record(SearchResult(title="Shared Version Study", arxiv_id="arXiv:2401.00001v3"), 1)
        self.assertEqual(record["canonical_work_id"], "arxiv:2401.00001")
        self.assertEqual(record["version_family_id"], "title:shared version study")
        self.assertEqual(record["version_type"], "preprint")

    def test_selection_parser_supports_ranges_and_rejects_out_of_range(self) -> None:
        self.assertEqual(parse_selection_indices("1,3-5", 5), [1, 3, 4, 5])
        self.assertEqual(parse_selection_indices("", 3), [1, 2, 3])
        with self.assertRaisesRegex(ValueError, "out of range"):
            parse_selection_indices("4", 3)

    def test_selection_keeps_unique_dois_and_reports_missing_values(self) -> None:
        payload = {
            "results": [
                {"index": 1, "title": "One", "doi": "10.1000/one"},
                {"index": 2, "title": "Duplicate", "doi": "https://doi.org/10.1000/ONE"},
                {"index": 3, "title": "No DOI", "doi": ""},
            ]
        }
        selected, skipped = select_doi_records(payload, [1, 2, 3])
        self.assertEqual([record["doi"] for record in selected], ["10.1000/one"])
        self.assertEqual([item["reason"] for item in skipped], ["duplicate_doi", "missing_doi"])

    def test_cli_search_export_then_select_writes_papers_input(self) -> None:
        runner = CliRunner()
        results = [
            SearchResult(title="Selected", authors=["A"], year=2024, doi="10.1000/selected"),
            SearchResult(title="No DOI", authors=["B"], year=2023, arxiv_id="2401.00001"),
        ]
        response = multi_search.MultiSearchResponse(
            results=[multi_search._from_provider(result, "semantic_scholar") for result in results],
            source_status={"semantic_scholar": {"status": "success", "count": 2}},
        )
        with TemporaryDirectory() as tmp, patch(
            "instsci.cli.multi_search.search_with_status", return_value=response
        ):
            search_path = Path(tmp) / "search.json"
            doi_path = Path(tmp) / "selected_dois.txt"
            search_result = runner.invoke(app, ["search", "topic", "--output", str(search_path)])
            select_result = runner.invoke(
                app,
                ["select", str(search_path), "--indices", "1-2", "--output", str(doi_path)],
            )
            payload = json.loads(search_path.read_text(encoding="utf-8"))
            dois = doi_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(search_result.exit_code, 0, search_result.output)
        self.assertEqual(select_result.exit_code, 0, select_result.output)
        self.assertEqual(payload["query"], "topic")
        self.assertEqual(payload["source_status"]["semantic_scholar"]["status"], "success")
        self.assertEqual(dois, ["10.1000/selected"])

    def test_readme_and_skill_expose_discovery_to_zotero_flow(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")
        skill = Path("skills/instsci/SKILL.md").read_text(encoding="utf-8")
        for text in (readme, skill):
            self.assertIn("instsci search", text)
            self.assertIn("instsci select", text)
            self.assertIn("instsci papers", text)
            self.assertIn("instsci zotero sync", text)
