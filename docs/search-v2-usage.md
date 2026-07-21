# Search v2 Usage

Search v2 is the reviewable literature-search path for comparing legacy search
with opt-in hybrid retrieval. It is usable locally, but `legacy` remains the
default until a graded live evaluation passes the release gate.

## Run Hybrid Search

Run these commands from the checked-out InstSci repository with the workspace
virtual environment active. The examples use `python -m instsci.cli` to avoid a
global `instsci` executable on PATH.

```powershell
python -m instsci.cli search `
  "reciprocal rank fusion academic search" `
  --strategy hybrid `
  --limit 20 `
  --output search_hybrid.json
```

Hybrid output records the query plan, channel-level provider status, retrieval
provenance, version placeholder fields, weighted RRF ranking evidence, and raw
per-channel candidate snapshots. The hybrid plan also includes an `instsci`
`legacy_fallback` channel derived from already-fetched keyword channel results
or from the saved live-eval legacy baseline. It protects a legacy safety band
while leaving room for hybrid-only candidates in the final top-N.

If OpenAlex semantic search reports `authentication_required`, set
`OPENALEX_API_KEY` or continue with partial results. The status is diagnostic;
it is not treated as silent zero-result success.

## Configure OpenAlex Access

Set the OpenAlex API key only in your local shell or user environment. Do not
write it into search result JSON, docs, commits, or run notes.

```powershell
$env:OPENALEX_API_KEY = "<your OpenAlex API key>"
```

Check whether the key is being used and whether quota remains:

```powershell
python -m instsci.cli openalex-rate-limit `
  --output openalex_rate_limit.json
```

The report is redacted. It records `api_key_configured`, quota/rate-limit
headers or body fields, and a normalized status such as `success`,
`rate_limited`, or `quota_exhausted`, but it does not include the key value.
OpenAlex keyword and semantic channels both read the same `OPENALEX_API_KEY`.

## Select DOI Records

Use `select` to turn reviewed search results into a DOI list for acquisition.

```powershell
python -m instsci.cli select `
  search_hybrid.json `
  --indices "1-5" `
  --output selected_dois.txt
```

The selection step preserves the existing DOI-based flow into `papers` and
Zotero. Search v2 results can also be downgraded for older consumers:

```powershell
python -m instsci.cli search-downgrade `
  search_hybrid.json `
  --output search_v1.json
```

## Run A Live Evaluation

Use live evaluation to compare `legacy` and `hybrid` on the same query set.

```powershell
python -m instsci.cli search-live-eval `
  queries.json `
  --output eval_run `
  --limit 20 `
  --sources openalex,crossref `
  --legacy-top 20 `
  --hybrid-top 20 `
  --channel-top 10
```

`openalex,crossref` is the recommended first live-eval source set because
Semantic Scholar can rate-limit longer runs. Add `semantic_scholar` in smaller
batches, later runs, or when accepting provider failure diagnostics.

The evaluation writes:

- `manifest.json`
- per-query `legacy.json`
- per-query `hybrid.json`
- per-query `judgments_pool.json`
- `judgments_review_packet.json`

Validate the artifacts:

```powershell
python -m instsci.cli search-live-eval-validate `
  eval_run\manifest.json `
  --output eval_run\manifest_validation.json

python -m instsci.cli search-review-packet-validate `
  eval_run\judgments_review_packet.json `
  --output eval_run\review_packet_validation.json
```

## Grade The Review Packet

Before `hybrid` can become the default, grade each pooled judgment in
`judgments_review_packet.json`:

```text
3 = highly relevant
2 = relevant
1 = marginal
0 = irrelevant
```

The pool is built from legacy top results, hybrid top results, and raw
channel-level candidates saved in `channel_results`. This avoids judging hybrid
only against papers that survived final fusion.

## Run The Release Gate

After grading, run:

```powershell
python -m instsci.cli search-gate `
  eval_run\manifest.json `
  --output eval_run\release_gate.json `
  --markdown-output eval_run\release_gate.md

python -m instsci.cli search-gate-validate `
  eval_run\release_gate.json `
  --output eval_run\release_gate_validation.json
```

The release gate cannot pass while judgments are ungraded. Passing requires
hybrid recall to be no worse than legacy, at least half of evaluated queries to
improve on `nDCG@20`, no severe unchecked ranking regression, and complete
graded judgments. It also records `evaluation_validity.quality_valid=false`
when required provider channels are unavailable because of authentication,
quota, rate-limit, timeout, or network failures. In that case the run may still
be structurally valid, but it is not valid evidence for hybrid retrieval
quality.

When retrieval or pooling logic changes, rebuild the live-eval run and re-grade
any new pooled candidates before treating an older release-gate result as
current evidence.

## Current Rollout Rule

- `legacy` is the default.
- `hybrid` is explicit opt-in with `--strategy hybrid`.
- Do not switch the default until a 10-20 query live evaluation is graded and
  the release gate passes.
