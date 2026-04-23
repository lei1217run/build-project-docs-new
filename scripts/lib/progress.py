from __future__ import annotations

import json
import os
import socket
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl  # type: ignore
except Exception:
    fcntl = None


def progress_path(docs_root: Path) -> Path:
    return docs_root / "_progress.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def progress_lock_path(docs_root: Path) -> Path:
    return docs_root / "_progress.lock"


class ProgressLockError(RuntimeError):
    def __init__(self, *, lock_path: Path, holder: str | None = None) -> None:
        super().__init__(f"progress locked: {lock_path}")
        self.lock_path = lock_path
        self.holder = holder


def build_run_identity(*, agent_id: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "startedAt": _now_iso(),
    }
    if agent_id:
        out["agentId"] = agent_id
    return out


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return False


def _parse_started_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _try_read_lock_holder(lock_path: Path) -> dict[str, Any] | None:
    try:
        raw = lock_path.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return {"raw": raw}


def _should_reclaim_lock(*, holder: dict[str, Any] | None, lease_seconds: int) -> tuple[bool, str | None]:
    if not holder:
        return False, None
    host = str(holder.get("hostname") or "")
    pid = holder.get("pid")
    started_at = _parse_started_at(str(holder.get("startedAt") or ""))
    if host and host != socket.gethostname():
        return False, None
    if isinstance(pid, int) and pid > 0:
        if not _pid_is_alive(pid):
            return True, "holder pid not alive"
        return False, None
    if started_at is not None and lease_seconds > 0:
        age = (datetime.now(timezone.utc) - started_at.astimezone(timezone.utc)).total_seconds()
        if age > float(lease_seconds):
            return True, "lock lease expired"
    return False, None


@contextmanager
def progress_run_lock(docs_root: Path, *, identity: dict[str, Any], lease_seconds: int = 900) -> Any:
    docs_root.mkdir(parents=True, exist_ok=True)
    lp = progress_lock_path(docs_root)
    if fcntl is None:
        for attempt in range(2):
            try:
                fd = os.open(str(lp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                break
            except FileExistsError as e:
                holder_obj = _try_read_lock_holder(lp)
                reclaim, reason = _should_reclaim_lock(holder=holder_obj, lease_seconds=lease_seconds)
                if attempt == 0 and reclaim:
                    try:
                        lp.unlink(missing_ok=True)
                    except Exception:
                        pass
                    continue
                holder = None
                try:
                    holder = lp.read_text(encoding="utf-8", errors="ignore").strip() or None
                except Exception:
                    holder = None
                raise ProgressLockError(lock_path=lp, holder=holder) from e
        try:
            os.write(fd, json.dumps(identity, ensure_ascii=False).encode("utf-8"))
            os.write(fd, b"\n")
            yield
        finally:
            try:
                os.close(fd)
            finally:
                try:
                    lp.unlink(missing_ok=True)
                except Exception:
                    pass
        return

    f = lp.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            holder = None
            try:
                f.seek(0)
                holder = f.read().strip() or None
            except Exception:
                holder = None
            raise ProgressLockError(lock_path=lp, holder=holder) from e
        f.seek(0)
        f.truncate(0)
        f.write(json.dumps(identity, ensure_ascii=False) + "\n")
        f.flush()
        yield
    finally:
        try:
            if fcntl is not None:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
        finally:
            f.close()


@dataclass(frozen=True)
class ProgressState:
    schemaVersion: str
    generatorVersion: str
    runId: str
    runIdentity: dict[str, Any]
    mode: str
    configSnapshot: dict[str, Any]
    stages: list[dict[str, Any]]
    verification: dict[str, Any]
    extensions: dict[str, Any]

    @staticmethod
    def new(mode: str, output_root: str, index_file: str, *, run_identity: dict[str, Any] | None = None) -> "ProgressState":
        run_id = f"run-{int(datetime.now(timezone.utc).timestamp())}"
        stages = []
        if mode == "docs":
            for i in range(1, 9):
                stages.append({"stageId": f"docs-{i}", "status": "pending"})
        elif mode == "new-project":
            for i in range(1, 6):
                stages.append({"stageId": f"new-{i}", "status": "pending"})
        else:
            stages.append({"stageId": "unknown", "status": "pending"})

        return ProgressState(
            schemaVersion="1.0.0",
            generatorVersion="0.1",
            runId=run_id,
            runIdentity=dict(run_identity or {}),
            mode=mode,
            configSnapshot={"configPriority": ["cli", "yaml", "env"], "outputRoot": output_root, "indexFile": index_file, "extensions": {}},
            stages=stages,
            verification={"resultsSummary": {"blockingFailures": 0, "warnings": 0}},
            extensions={},
        )

    @staticmethod
    def load_or_new(
        docs_root: Path,
        mode: str,
        output_root: str,
        index_file: str,
        *,
        run_identity: dict[str, Any] | None = None,
    ) -> "ProgressState":
        p = progress_path(docs_root)
        if not p.exists():
            return ProgressState.new(mode=mode, output_root=output_root, index_file=index_file, run_identity=run_identity)
        data = json.loads(p.read_text(encoding="utf-8"))
        return ProgressState(
            schemaVersion=str(data.get("schemaVersion", "1.0.0")),
            generatorVersion=str(data.get("generatorVersion", "0.1")),
            runId=str(data.get("runId", f"run-{int(datetime.now(timezone.utc).timestamp())}")),
            runIdentity=dict(data.get("runIdentity", run_identity or {})),
            mode=str(data.get("mode", mode)),
            configSnapshot=dict(data.get("configSnapshot", {"configPriority": ["cli", "yaml", "env"], "outputRoot": output_root, "indexFile": index_file, "extensions": {}})),
            stages=list(data.get("stages", [])),
            verification=dict(data.get("verification", {"resultsSummary": {"blockingFailures": 0, "warnings": 0}})),
            extensions=dict(data.get("extensions", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schemaVersion,
            "generatorVersion": self.generatorVersion,
            "runId": self.runId,
            "runIdentity": self.runIdentity,
            "mode": self.mode,
            "configSnapshot": self.configSnapshot,
            "stages": self.stages,
            "verification": self.verification,
            "extensions": self.extensions,
        }

    def with_stage_status(self, stage_id: str, status: str) -> "ProgressState":
        stages = []
        found = False
        for s in self.stages:
            if s.get("stageId") == stage_id:
                ns = dict(s)
                ns["status"] = status
                if status == "running" and "startedAt" not in ns:
                    ns["startedAt"] = _now_iso()
                if status in ["done", "skipped", "blocked"]:
                    ns["finishedAt"] = _now_iso()
                stages.append(ns)
                found = True
            else:
                stages.append(s)
        if not found:
            stages.append({"stageId": stage_id, "status": status, "startedAt": _now_iso()})
        return replace(self, stages=stages)

    def with_stage_note(self, stage_id: str, note: str) -> "ProgressState":
        stages = []
        found = False
        for s in self.stages:
            if s.get("stageId") == stage_id:
                ns = dict(s)
                ns["notes"] = note
                stages.append(ns)
                found = True
            else:
                stages.append(s)
        if not found:
            stages.append({"stageId": stage_id, "status": "pending", "notes": note})
        return replace(self, stages=stages)

    def get_module_task(self, stage_id: str, module_id: str) -> dict[str, Any] | None:
        for s in self.stages:
            if s.get("stageId") != stage_id:
                continue
            for t in s.get("moduleTasks", []) or []:
                if t.get("moduleId") == module_id:
                    return dict(t)
        return None

    def upsert_module_task(
        self,
        stage_id: str,
        module_id: str,
        status: str,
        *,
        artifacts: list[str] | None = None,
        lastEvidenceHash: str | None = None,
    ) -> "ProgressState":
        stages = []
        for s in self.stages:
            if s.get("stageId") != stage_id:
                stages.append(s)
                continue
            ns = dict(s)
            tasks = list(ns.get("moduleTasks", []))
            replaced = False
            for i, t in enumerate(tasks):
                if t.get("moduleId") == module_id:
                    nt = dict(t)
                    nt["status"] = status
                    if artifacts is not None:
                        nt["artifacts"] = artifacts
                    if lastEvidenceHash is not None:
                        nt["lastEvidenceHash"] = lastEvidenceHash
                    tasks[i] = nt
                    replaced = True
                    break
            if not replaced:
                tasks.append(
                    {
                        "moduleId": module_id,
                        "status": status,
                        "artifacts": artifacts or [],
                        "lastEvidenceHash": lastEvidenceHash,
                        "extensions": {},
                    }
                )
            ns["moduleTasks"] = tasks
            stages.append(ns)
        return replace(self, stages=stages)

    def with_verification(self, report: dict[str, Any]) -> "ProgressState":
        v = dict(self.verification)
        v["lastRun"] = _now_iso()
        v["resultsSummary"] = {
            "blockingFailures": int(report.get("blockingFailures", 0)),
            "warnings": int(report.get("warnings", 0)),
        }
        return replace(self, verification=v)

    def with_run_identity(self, run_identity: dict[str, Any]) -> "ProgressState":
        return replace(self, runIdentity=dict(run_identity))

    def with_extension(self, key: str, value: Any) -> "ProgressState":
        ex = dict(self.extensions)
        ex[key] = value
        return replace(self, extensions=ex)


def write_progress(docs_root: Path, state: ProgressState) -> None:
    _atomic_write_json(progress_path(docs_root), state.to_dict())
