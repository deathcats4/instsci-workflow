# Literature Search Workflow

Use this reference when an InstSci task starts from a research topic, title,
author, keyword set, or broad literature question rather than a reviewed DOI
list. Keep the main `SKILL.md` as the router; load this file only for discovery
and search-evaluation work.

## Standard Discovery Path

Use the stable discovery-to-library path for normal research work:

```powershell
instsci search "research topic" --limit 50 --year 2020- --output <run-dir>\search.json
instsci select <run-dir>\search.json --indices "1,3-8" --output <run-dir>\selected_dois.txt
instsci papers <run-dir>\selected_dois.txt --publisher auto --output <run-dir>\papers
instsci zotero sync <run-dir>\papers --attachment-mode linked_file
```

- Use `<run-dir>` for the task's approved run directory. In this workspace,
  place CLI outputs under the top-level `runtime/runs/<timestamp>_<name>`
  area, not inside the public source tree.
- `search` queries scholarly metadata providers and writes a reviewable JSON or
  CSV result file.
- `select` uses one-based result indices, removes duplicate DOI values, skips
  rows without a DOI, and writes a neighboring selection report.
- `papers` starts the browser-backed PDF acquisition workflow for selected DOI
  records.
- `zotero sync` keeps Zotero as the long-term library surface, with linked-file
  attachments by default.

Do not silently acquire every search hit when the user asked to review or choose
papers first.

## Interpreting Search Results

Check `source_status` before interpreting zero hits. A provider can be
rate-limited, unavailable, or partially successful; zero returned records are not
the same as a completed search over all providers.

Treat citation counts as source-specific metadata. Do not present the maximum
merged citation count as a single authoritative citation total unless a provider
is explicitly named.

Prefer DOI identity when merging or selecting papers. Title-and-year matching is
only a fallback when at least one record lacks a DOI; conflicting non-empty DOI
values should remain separate until reviewed.

Rows without DOI can be useful for manual review, metadata lookup, Chinese
literature workflows, or Zotero matching, but normal `papers` acquisition works
best from selected DOI records.

## OpenAlex And Provider Keys

If OpenAlex returns authentication, quota, or rate-limit diagnostics, report the
status without exposing API keys. Ask the user to configure `OPENALEX_API_KEY`
only when higher limits or paid OpenAlex features are needed.

Never log, paste, commit, or write API keys, connector URLs, cookies, or
institution-private access details into public repository files or run reports.

## Search v2 / Hybrid Rollout

Keep `legacy` search as the stable default unless the installed CLI explicitly
supports and the user asks for an experimental Search v2 / `hybrid` run.

Use hybrid only for opt-in comparison or review workflows until these conditions
are true in the active codebase:

- OpenAlex temporary rate limits and daily quota exhaustion are distinguished.
- Same-title and same-year records with different DOI/arXiv identities do not
  merge without enough author or version evidence.
- `--resume` verifies evaluation configuration before reusing old artifacts.
- Live evaluation compares rankings from the same saved provider snapshots, or
  clearly labels network drift as part of the measurement.

When running Search v2 experiments, write outputs under `runtime/runs/...` or a
user-chosen research output folder, not inside the public source tree.

## Evaluation And Review Packets

Use live evaluation only to guide rollout decisions, not as final proof that a
search strategy should become the default. Prefer a fresh output directory when
changing query sets, provider sources, limits, pooling depth, or ranking
strategy.

For relevance review, generate blinded review packets, have the user or reviewer
grade candidates, and rerun the release gate before changing defaults.

If an evaluation was resumed, confirm that query text, year range, sources,
limits, pooling settings, and strategy/contract versions match the saved
artifacts before trusting the summary.

## Handoff To PDF And Zotero Workflows

After selection, switch back to the normal InstSci acquisition rules:

- Publisher PDF verdicts require visible CloakBrowser-backed evidence.
- HTTP probes, DOI resolution, route construction, and metadata lookup are
  preflight only for closed-access publisher conclusions.
- Zotero should receive clean item-plus-PDF results; keep acquisition process
  evidence in InstSci manifests and reports.
