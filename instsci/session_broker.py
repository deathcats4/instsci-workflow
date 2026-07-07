"""Long-lived publisher browser session broker.

The broker keeps one CloakBrowser context alive per publisher/profile and
accepts DOI batch jobs through a small file queue. It intentionally stores no
cookie values in the broker state.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from uuid import uuid4

from .config import DEFAULT_BASE_DIR


BROKER_ROOT = DEFAULT_BASE_DIR / "brokers"


@dataclass
class BrokerState:
    publisher: str
    profile_dir: str
    pid: int
    queue_dir: str
    started_at: str
    ttl_seconds: int
    heartbeat_at: str = ""


def broker_key(publisher: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in publisher.strip().lower())


def broker_dir(publisher: str) -> Path:
    return BROKER_ROOT / broker_key(publisher)


def broker_state_path(publisher: str) -> Path:
    return broker_dir(publisher) / "state.json"


def broker_stop_path(publisher: str) -> Path:
    return broker_dir(publisher) / "stop"


def load_broker_state(publisher: str) -> dict[str, Any] | None:
    path = broker_state_path(publisher)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_broker_state(state: BrokerState) -> None:
    path = broker_state_path(state.publisher)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")


def archive_pending_jobs(publisher: str, *, reason: str = "aborted") -> list[dict[str, str]]:
    """Move unfinished broker jobs out of the active queue.

    Foreground CLI runs are synchronous: the newly submitted job should be the
    next job processed. If a previous run was interrupted, stale ``*.json`` jobs
    can otherwise be picked up first and pollute the visible browser state.
    Detached jobs and resume workflows call ``enqueue_broker_job`` directly and
    keep normal queue semantics.
    """
    state = load_broker_state(publisher)
    if not state:
        return []
    queue_dir = Path(str(state.get("queue_dir") or ""))
    if not queue_dir.exists():
        return []
    archive_dir = queue_dir / "aborted"
    archived: list[dict[str, str]] = []
    for job_path in sorted(queue_dir.glob("*.json")):
        if job_path.name.endswith(".done.json"):
            continue
        archive_dir.mkdir(parents=True, exist_ok=True)
        try:
            payload = json.loads(job_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        payload["aborted_at"] = datetime.now().isoformat(timespec="seconds")
        payload["abort_reason"] = reason
        target = archive_dir / job_path.name
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        job_path.unlink(missing_ok=True)
        archived.append({"id": str(payload.get("id") or job_path.stem), "path": str(target)})
    return archived


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _pid_is_running_windows(pid)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _pid_is_running_windows(pid: int) -> bool:
    """Probe a Windows process without sending a signal."""
    try:
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, ValueError):
        return False


def broker_is_running(publisher: str) -> bool:
    state = load_broker_state(publisher)
    if not state:
        return False
    return pid_is_running(int(state.get("pid") or 0))


def start_broker_process(
    *,
    publisher: str,
    browser_profile: str,
    institution: str,
    ttl_seconds: int,
    cwd: str | Path,
) -> subprocess.Popen[Any]:
    root = broker_dir(publisher)
    root.mkdir(parents=True, exist_ok=True)
    broker_stop_path(publisher).unlink(missing_ok=True)
    stdout = root / "broker.out.log"
    stderr = root / "broker.err.log"
    args = [
        sys.executable,
        "-m",
        "instsci.cli",
        "session-broker-run",
        "--publisher",
        publisher,
        "--browser-profile",
        browser_profile,
        "--institution",
        institution,
        "--ttl",
        str(ttl_seconds),
    ]
    return subprocess.Popen(
        args,
        cwd=str(cwd),
        stdout=stdout.open("a", encoding="utf-8"),
        stderr=stderr.open("a", encoding="utf-8"),
        stdin=subprocess.DEVNULL,
    )


def submit_broker_job(
    *,
    publisher: str,
    records: list[dict[str, str]],
    output_dir: str,
    institution: str,
    login_timeout: int,
    pdf_timeout: int,
    post_login_hold: int,
    post_run_hold: int,
    carsi_portal_preauth: bool,
    pause_on_blocker: bool,
    timeout_seconds: int,
    institution_aliases: list[str] | None = None,
    discard_pending: bool = True,
) -> dict[str, Any]:
    if discard_pending:
        archive_pending_jobs(publisher, reason="superseded_by_foreground_submit")
    queued = enqueue_broker_job(
        publisher=publisher,
        records=records,
        output_dir=output_dir,
        institution=institution,
        institution_aliases=institution_aliases or [],
        login_timeout=login_timeout,
        pdf_timeout=pdf_timeout,
        post_login_hold=post_login_hold,
        post_run_hold=post_run_hold,
        carsi_portal_preauth=carsi_portal_preauth,
        pause_on_blocker=pause_on_blocker,
    )
    return wait_for_broker_job(
        publisher=publisher,
        job_id=str(queued["id"]),
        done_path=Path(str(queued["done_path"])),
        timeout_seconds=timeout_seconds,
    )


def enqueue_broker_job(
    *,
    publisher: str,
    records: list[dict[str, str]],
    output_dir: str,
    institution: str,
    login_timeout: int,
    pdf_timeout: int,
    post_login_hold: int,
    post_run_hold: int,
    carsi_portal_preauth: bool = False,
    pause_on_blocker: bool = True,
    institution_aliases: list[str] | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Write a broker queue job without waiting for completion."""
    state = load_broker_state(publisher)
    if not state:
        raise RuntimeError(f"No broker state for {publisher}")
    queue_dir = Path(str(state["queue_dir"]))
    queue_dir.mkdir(parents=True, exist_ok=True)
    job_id = job_id or uuid4().hex
    job_path = queue_dir / f"{job_id}.json"
    done_path = queue_dir / f"{job_id}.done.json"
    job = {
        "id": job_id,
        "publisher": publisher,
        "records": records,
        "output_dir": output_dir,
        "institution": institution,
        "institution_aliases": list(institution_aliases or []),
        "login_timeout": login_timeout,
        "pdf_timeout": pdf_timeout,
        "post_login_hold": post_login_hold,
        "post_run_hold": post_run_hold,
        "carsi_portal_preauth": bool(carsi_portal_preauth),
        "pause_on_blocker": bool(pause_on_blocker),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "id": job_id,
        "publisher": publisher,
        "job_path": str(job_path),
        "done_path": str(done_path),
        "queue_dir": str(queue_dir),
        "created_at": job["created_at"],
    }


def wait_for_broker_job(
    *,
    publisher: str,
    job_id: str,
    done_path: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Wait for a queued broker job to finish and return its summary."""
    deadline = time.time() + max(1, timeout_seconds)
    while time.time() < deadline:
        if done_path.exists():
            return json.loads(done_path.read_text(encoding="utf-8"))
        if not broker_is_running(publisher):
            raise RuntimeError(f"Broker for {publisher} stopped before job completed")
        time.sleep(2)
    raise TimeoutError(f"Broker job timed out after {timeout_seconds}s: {job_id}")
