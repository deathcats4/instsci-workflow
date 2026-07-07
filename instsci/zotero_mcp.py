"""Build and execute Zotero import handoffs from InstSci manifests."""

from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path
from typing import Any, Protocol


SUCCESS_STATUSES = {"success"}
DEFAULT_TAGS = ("instsci", "instsci-import")
ZOTERO_SYNC_FIELDS = (
    "zotero_status",
    "zotero_item_key",
    "zotero_attachment_key",
    "zotero_attachment_mode",
    "zotero_pdf_path",
    "zotero_sync_error",
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def resolve_manifest_path(path: str | Path) -> Path:
    """Resolve a manifest path, accepting a run dir, complete dir, JSON, or CSV."""
    candidate = Path(path)
    if candidate.is_dir():
        if (candidate / "complete" / "manifest.json").exists():
            return candidate / "complete" / "manifest.json"
        if (candidate / "manifest.json").exists():
            return candidate / "manifest.json"
        if (candidate / "complete" / "manifest.csv").exists():
            return candidate / "complete" / "manifest.csv"
        if (candidate / "manifest.csv").exists():
            return candidate / "manifest.csv"
    if candidate.suffix.lower() == ".csv" and candidate.with_suffix(".json").exists():
        return candidate.with_suffix(".json")
    return candidate


def load_manifest_rows(path: str | Path) -> tuple[Path, list[dict[str, Any]]]:
    """Load InstSci manifest rows from JSON or CSV."""
    manifest_path = resolve_manifest_path(path)
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    if manifest_path.suffix.lower() == ".json":
        data = _read_json(manifest_path)
        if not isinstance(data, list):
            raise ValueError(f"Manifest JSON must be a list: {manifest_path}")
        rows = [dict(row) for row in data if isinstance(row, dict)]
    elif manifest_path.suffix.lower() == ".csv":
        rows = _read_csv(manifest_path)
    else:
        raise ValueError(f"Unsupported manifest type: {manifest_path}")
    return manifest_path, rows


def doi_to_url(doi: str) -> str:
    doi = (doi or "").strip()
    if not doi:
        return ""
    if doi.lower().startswith(("http://", "https://")):
        return doi
    return f"https://doi.org/{doi}"


def _split_csvish(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _row_status(row: dict[str, Any]) -> str:
    return str(row.get("standard_status") or row.get("status") or "").strip()


def _row_file_status(row: dict[str, Any]) -> str:
    return str(row.get("file_status") or row.get("status") or "").strip()


def _row_pdf_path(row: dict[str, Any]) -> str:
    return str(row.get("pdf_path") or "").strip()


def resolve_pdf_path(path_value: str, manifest_path: Path) -> Path | None:
    if not path_value:
        return None
    candidate = Path(path_value)
    if not candidate.is_absolute():
        candidate = manifest_path.parent / candidate
    return candidate


def _has_existing_pdf(path_value: str, manifest_path: Path) -> bool:
    candidate = resolve_pdf_path(path_value, manifest_path)
    return bool(candidate and candidate.exists() and candidate.is_file())


def _row_tags(row: dict[str, Any], extra_tags: list[str]) -> list[str]:
    tags = list(DEFAULT_TAGS)
    publisher = str(row.get("publisher") or "").strip()
    evidence = str(row.get("result_evidence") or "").strip()
    status = _row_status(row)
    if publisher:
        tags.append(f"publisher/{publisher}")
    if evidence:
        tags.append(f"evidence/{evidence}")
    if status:
        tags.append(f"status/{status}")
    tags.extend(extra_tags)
    return list(dict.fromkeys(tags))


def build_zotero_mcp_handoff(
    manifest: str | Path,
    *,
    statuses: list[str] | None = None,
    tags: list[str] | None = None,
    collections: list[str] | None = None,
    attach_mode: str = "none",
    include_missing: bool = False,
) -> dict[str, Any]:
    """Create a Zotero MCP action queue from an InstSci manifest.

    The queue is intentionally MCP-client agnostic: an agent can execute each
    item with the Zotero MCP tools, while InstSci stays independent of one
    specific MCP runtime.
    """
    manifest_path, rows = load_manifest_rows(manifest)
    wanted_statuses = set(statuses or SUCCESS_STATUSES)
    extra_tags = tags or []
    collection_values = collections or []
    normalized_attach_mode = attach_mode if attach_mode in {"auto", "none", "required"} else "none"
    actions: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for index, row in enumerate(rows, 1):
        doi = str(row.get("doi") or "").strip()
        url = doi_to_url(doi)
        status = _row_status(row)
        file_status = _row_file_status(row)
        pdf_path = _row_pdf_path(row)
        if not doi:
            skipped.append({"index": index, "reason": "missing_doi", "row": row})
            continue
        if not include_missing and (file_status != "success" or status not in wanted_statuses):
            skipped.append({"index": index, "doi": doi, "reason": "status_not_selected", "status": status, "file_status": file_status})
            continue
        if normalized_attach_mode == "required" and not _has_existing_pdf(pdf_path, manifest_path):
            skipped.append({"index": index, "doi": doi, "reason": "required_pdf_missing", "status": status, "file_status": file_status, "pdf_path": pdf_path})
            continue
        add_params: dict[str, Any] = {
            "url": url,
            "tags": _row_tags(row, extra_tags),
            "attach_mode": normalized_attach_mode,
        }
        if collection_values:
            add_params["collections"] = collection_values
        actions.append(
            {
                "index": index,
                "doi": doi,
                "title": row.get("title") or "",
                "kind": "metadata_import",
                "tool": "zotero_add_by_url",
                "params": add_params,
                "source": {
                    "manifest": str(manifest_path),
                    "pdf_path": pdf_path,
                    "standard_status": status,
                    "file_status": file_status,
                    "result_evidence": row.get("result_evidence") or "",
                },
            }
        )
    return {
        "schema": "instsci.zotero_mcp_handoff.v1",
        "manifest": str(manifest_path),
        "selected_statuses": sorted(wanted_statuses),
        "attach_mode": normalized_attach_mode,
        "actions": actions,
        "skipped": skipped,
        "summary": {
            "rows": len(rows),
            "actions": len(actions),
            "metadata_imports": sum(1 for action in actions if action["kind"] == "metadata_import"),
            "skipped": len(skipped),
        },
    }


def write_zotero_mcp_handoff(payload: dict[str, Any], output: str | Path) -> Path:
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


class ZoteroSyncBackend(Protocol):
    """Backend contract used by the Zotero sync executor."""

    def add_by_url(
        self,
        url: str,
        *,
        tags: list[str],
        collections: list[str],
        attach_mode: str,
    ) -> str:
        """Create or match a Zotero item and return its item key."""

    def attach_linked_file(
        self,
        item_key: str,
        pdf_path: Path,
        *,
        title: str,
        tags: list[str],
    ) -> str:
        """Attach a local linked PDF file and return the attachment key."""


def _zotero_env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _normalize_library_type(value: str) -> str:
    normalized = (value or "user").strip().lower()
    if normalized in {"group", "groups"}:
        return "group"
    return "user"


def _extract_key(value: Any) -> str:
    """Best-effort Zotero item key extraction across pyzotero response shapes."""
    if isinstance(value, str):
        match = re.search(r"\b([A-Z0-9]{8})\b", value)
        return match.group(1) if match else value.strip()
    if isinstance(value, dict):
        for key_name in ("key", "itemKey"):
            if value.get(key_name):
                return str(value[key_name])
        if isinstance(value.get("data"), dict):
            extracted = _extract_key(value["data"])
            if extracted:
                return extracted
        for container in ("successful", "success"):
            entries = value.get(container)
            if isinstance(entries, dict) and entries:
                first = next(iter(entries.values()))
                extracted = _extract_key(first)
                if extracted:
                    return extracted
            if isinstance(entries, list) and entries:
                extracted = _extract_key(entries[0])
                if extracted:
                    return extracted
    if isinstance(value, list) and value:
        return _extract_key(value[0])
    return ""


class _SilentZoteroContext:
    """Small context object for calling Zotero MCP functions outside MCP."""

    def info(self, message: str) -> None:
        return None

    def warning(self, message: str) -> None:
        return None

    def error(self, message: str) -> None:
        return None


class PyzoteroSyncBackend:
    """Zotero backend for metadata import and linked-file attachments.

    This backend deliberately avoids Zotero's local SQLite database and does not
    create notes. It prefers Zotero MCP's DOI/URL import path for rich metadata,
    then creates a linked_file PDF attachment under that item through Zotero's
    Web API.
    """

    def __init__(
        self,
        *,
        library_id: str | None = None,
        library_type: str | None = None,
        api_key: str | None = None,
    ) -> None:
        try:
            from pyzotero import zotero as pyzotero_zotero
        except ImportError as exc:  # pragma: no cover - exercised in real env only.
            raise RuntimeError(
                "pyzotero is required for `instsci zotero sync`. Install it in the active "
                "runtime or keep using `instsci zotero handoff` for MCP execution."
            ) from exc

        resolved_library_id = library_id or _zotero_env("ZOTERO_LIBRARY_ID")
        resolved_api_key = api_key or _zotero_env("ZOTERO_API_KEY")
        resolved_library_type = _normalize_library_type(library_type or _zotero_env("ZOTERO_LIBRARY_TYPE"))
        if not resolved_library_id or not resolved_api_key:
            raise RuntimeError(
                "ZOTERO_LIBRARY_ID and ZOTERO_API_KEY are required for Zotero sync."
            )
        self.zot = pyzotero_zotero.Zotero(resolved_library_id, resolved_library_type, resolved_api_key)
        self._mcp_add_by_url = None
        self._mcp_context: _SilentZoteroContext | None = None
        try:
            from zotero_mcp.tools.write import add_by_url as mcp_add_by_url

            self._mcp_add_by_url = mcp_add_by_url
            self._mcp_context = _SilentZoteroContext()
        except Exception:
            # Zotero MCP is optional at runtime. pyzotero remains enough for
            # linked-file attachment and a minimal metadata fallback.
            self._mcp_add_by_url = None
            self._mcp_context = None

    def add_by_url(
        self,
        url: str,
        *,
        tags: list[str],
        collections: list[str],
        attach_mode: str,
    ) -> str:
        if self._mcp_add_by_url is not None and self._mcp_context is not None:
            result = self._mcp_add_by_url(
                url=url,
                collections=collections,
                tags=tags,
                attach_mode=attach_mode,
                ctx=self._mcp_context,
            )
            item_key = _extract_key(result)
            if item_key:
                return item_key
            raise RuntimeError(f"Unable to determine Zotero item key from MCP response: {result!r}")

        template = self.zot.item_template("journalArticle")
        template["url"] = url
        doi = url.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
        if doi != url:
            template["DOI"] = doi
        if tags:
            template["tags"] = [{"tag": tag} for tag in tags]
        if collections:
            template["collections"] = collections
        created = self.zot.create_items([template])
        item_key = _extract_key(created)
        if not item_key:
            raise RuntimeError(f"Unable to determine Zotero item key from response: {created!r}")
        return item_key

    def attach_linked_file(
        self,
        item_key: str,
        pdf_path: Path,
        *,
        title: str,
        tags: list[str],
    ) -> str:
        attachment = self.zot.item_template("attachment", linkmode="linked_file")
        attachment["parentItem"] = item_key
        attachment["title"] = title or pdf_path.name
        attachment["path"] = str(pdf_path)
        attachment["contentType"] = "application/pdf"
        attachment["linkMode"] = "linked_file"
        if tags:
            attachment["tags"] = [{"tag": tag} for tag in tags]
        created = self.zot.create_items([attachment])
        attachment_key = _extract_key(created)
        if not attachment_key:
            raise RuntimeError(f"Unable to determine Zotero attachment key from response: {created!r}")
        return attachment_key


def _load_handoff_or_build(
    manifest_or_handoff: str | Path,
    *,
    statuses: list[str] | None,
    tags: list[str] | None,
    collections: list[str] | None,
    attach_mode: str,
    include_missing: bool,
) -> dict[str, Any]:
    path = Path(manifest_or_handoff)
    if path.is_file() and path.suffix.lower() == ".json":
        data = _read_json(path)
        if isinstance(data, dict) and data.get("schema") == "instsci.zotero_mcp_handoff.v1":
            return data
    return build_zotero_mcp_handoff(
        manifest_or_handoff,
        statuses=statuses,
        tags=tags,
        collections=collections,
        attach_mode=attach_mode,
        include_missing=include_missing,
    )


def _write_manifest_rows(manifest_path: Path, rows: list[dict[str, Any]]) -> None:
    if manifest_path.suffix.lower() == ".json":
        manifest_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    if manifest_path.suffix.lower() != ".csv":
        raise ValueError(f"Unsupported manifest type for writeback: {manifest_path}")
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    for key in ZOTERO_SYNC_FIELDS:
        if key not in fieldnames:
            fieldnames.append(key)
    with manifest_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def execute_zotero_sync(
    manifest_or_handoff: str | Path,
    *,
    backend: ZoteroSyncBackend | None = None,
    statuses: list[str] | None = None,
    tags: list[str] | None = None,
    collections: list[str] | None = None,
    attach_mode: str = "required",
    attachment_mode: str = "linked_file",
    include_missing: bool = False,
    write_back: bool = True,
    dry_run: bool = False,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    """Execute Zotero import + PDF linked attachment sync and optionally write back.

    `attach_mode` controls row selection for handoff generation. `attachment_mode`
    controls how the local PDF is attached to Zotero; the supported stable mode is
    `linked_file`.
    """
    if attachment_mode != "linked_file":
        raise ValueError("Only attachment_mode='linked_file' is currently supported.")
    payload = _load_handoff_or_build(
        manifest_or_handoff,
        statuses=statuses,
        tags=tags,
        collections=collections,
        attach_mode=attach_mode,
        include_missing=include_missing,
    )
    manifest_path = Path(payload["manifest"])
    _, rows = load_manifest_rows(manifest_path)
    active_backend = backend if backend is not None else (None if dry_run else PyzoteroSyncBackend())
    results: list[dict[str, Any]] = []

    for action in payload.get("actions", []):
        row_index = int(action.get("index") or 0)
        row = rows[row_index - 1] if 1 <= row_index <= len(rows) else {}
        params = dict(action.get("params") or {})
        source = dict(action.get("source") or {})
        pdf_path_value = str(source.get("pdf_path") or row.get("pdf_path") or "").strip()
        pdf_path = resolve_pdf_path(pdf_path_value, manifest_path)
        result = {
            "index": row_index,
            "doi": action.get("doi") or row.get("doi") or "",
            "status": "pending",
            "item_key": "",
            "attachment_key": "",
            "attachment_mode": attachment_mode,
            "pdf_path": str(pdf_path or pdf_path_value),
        }
        if not pdf_path or not pdf_path.exists() or not pdf_path.is_file():
            result["status"] = "skipped"
            result["reason"] = "pdf_missing"
            results.append(result)
            if row:
                row["zotero_status"] = "skipped"
                row["zotero_sync_error"] = "pdf_missing"
            continue
        if dry_run:
            result["status"] = "dry_run"
            results.append(result)
            continue
        try:
            assert active_backend is not None
            item_key = str(
                row.get("zotero_item_key")
                or active_backend.add_by_url(
                    str(params.get("url") or doi_to_url(str(row.get("doi") or ""))),
                    tags=list(params.get("tags") or []),
                    collections=list(params.get("collections") or []),
                    attach_mode="none",
                )
            )
            attachment_key = str(
                row.get("zotero_attachment_key")
                or active_backend.attach_linked_file(
                    item_key,
                    pdf_path,
                    title=str(action.get("title") or row.get("title") or pdf_path.name),
                    tags=list(params.get("tags") or []),
                )
            )
            result.update(
                {
                    "status": "success",
                    "item_key": item_key,
                    "attachment_key": attachment_key,
                    "pdf_path": str(pdf_path),
                }
            )
            if row:
                row.update(
                    {
                        "zotero_status": "success",
                        "zotero_item_key": item_key,
                        "zotero_attachment_key": attachment_key,
                        "zotero_attachment_mode": attachment_mode,
                        "zotero_pdf_path": str(pdf_path),
                        "zotero_sync_error": "",
                    }
                )
        except Exception as exc:  # pragma: no cover - error shape covered via fake backend.
            result["status"] = "error"
            result["error"] = str(exc)
            if row:
                row["zotero_status"] = "error"
                row["zotero_sync_error"] = str(exc)
        results.append(result)

    summary = {
        "rows": len(rows),
        "actions": len(payload.get("actions", [])),
        "success": sum(1 for result in results if result["status"] == "success"),
        "dry_run": sum(1 for result in results if result["status"] == "dry_run"),
        "skipped": sum(1 for result in results if result["status"] == "skipped"),
        "errors": sum(1 for result in results if result["status"] == "error"),
    }
    report = {
        "schema": "instsci.zotero_sync_report.v1",
        "manifest": str(manifest_path),
        "attachment_mode": attachment_mode,
        "write_back": bool(write_back and not dry_run),
        "dry_run": dry_run,
        "summary": summary,
        "results": results,
        "handoff_summary": payload.get("summary", {}),
    }
    if write_back and not dry_run:
        _write_manifest_rows(manifest_path, rows)
    if report_path:
        write_zotero_mcp_handoff(report, report_path)
    return report
