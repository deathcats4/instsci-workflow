"""Private evidence index and public-data boundary helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Config


PUBLIC_POLICY_PATH = Path(__file__).parent / "data" / "public_data_policy.json"
PRIVATE_INDEX_SCHEMA = "instsci.private_evidence_index.v1"


def load_public_data_policy() -> dict[str, Any]:
    return json.loads(PUBLIC_POLICY_PATH.read_text(encoding="utf-8"))


def private_evidence_root(config: Config) -> Path:
    return Path(config.private_evidence_dir).expanduser().resolve()


def private_index_path(config: Config) -> Path:
    return private_evidence_root(config) / "index.json"


def load_private_index(config: Config) -> dict[str, Any]:
    path = private_index_path(config)
    if not path.exists():
        return {"schema": PRIVATE_INDEX_SCHEMA, "runs": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("runs"), list):
        raise ValueError("Private evidence index must contain a runs array.")
    return payload


def _resolve_manifest(run_dir: Path) -> Path | None:
    candidates = (
        run_dir / "complete" / "manifest.json",
        run_dir / "manifest.json",
        run_dir / "complete" / "manifest.csv",
        run_dir / "manifest.csv",
    )
    return next((path for path in candidates if path.is_file()), None)


def _file_sha256(path: Path | None) -> str:
    if path is None:
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def register_private_run(
    config: Config,
    run_path: str | Path,
    *,
    publisher: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """Register references and hashes without copying run artifacts."""
    run_dir = Path(run_path).expanduser().resolve()
    if not run_dir.is_dir():
        raise ValueError(f"Run directory does not exist: {run_dir}")
    manifest = _resolve_manifest(run_dir)
    registered_at = datetime.now().astimezone().isoformat(timespec="seconds")
    entry_id = hashlib.sha256(f"{run_dir}|{registered_at}".encode("utf-8")).hexdigest()[:16]
    entry = {
        "id": entry_id,
        "registered_at": registered_at,
        "publisher": publisher.strip().lower(),
        "run_path": str(run_dir),
        "manifest_path": str(manifest) if manifest else "",
        "manifest_sha256": _file_sha256(manifest),
        "notes": notes.strip(),
        "storage": "reference_only",
        "artifacts_copied": False,
    }
    index = load_private_index(config)
    index["runs"] = [item for item in index["runs"] if str(item.get("run_path")) != str(run_dir)]
    index["runs"].append(entry)
    index_path = private_index_path(config)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**entry, "index_path": str(index_path)}
