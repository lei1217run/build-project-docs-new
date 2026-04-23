#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from lib.config import load_effective_config
from lib.discovery import discover_modules
from lib.changelog import generate_and_update
from lib.extractor import extract_module_ir
from lib.evidence import compute_evidence_hash
from lib.ir_store import load_project_ir, load_module_ir, write_project_ir, write_module_ir, write_project_ir_payload
from lib.progress import ProgressLockError, ProgressState, build_run_identity, progress_run_lock, write_progress
from lib.renderer import render_module, render_project
from lib.verifier import verify_all
from lib.git_tools import GitError, run_git
from lib.new_project import (
    build_plan_from_prd,
    parse_prd_input,
    plan_to_module_ir,
    plan_to_project_ir,
    render_new_project_task_list,
    write_prd_and_plan_ir,
)


class BpdCliError(RuntimeError):
    def __init__(self, *, errorCode: str, reason: str, hint: str | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.errorCode = errorCode
        self.reason = reason
        self.hint = hint
        self.details = details or {}


def _print_json(obj: dict[str, Any]) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _finalize_no_pending(state: ProgressState) -> ProgressState:
    for s in list(state.stages):
        if s.get("status") == "pending":
            state = state.with_stage_status(str(s.get("stageId")), "skipped")
    return state


def _select_modules(modules: list[dict[str, Any]], selector: str | None) -> list[dict[str, Any]]:
    if not selector:
        return list(modules)
    out = []
    for m in modules:
        if str(m.get("moduleId", "")) == selector or str(m.get("displayName", "")) == selector:
            out.append(m)
    if not out:
        raise BpdCliError(errorCode="MODULE_NOT_FOUND", reason="module not found", hint="use moduleId or displayName from project IR", details={"module": selector})
    return out


def _stage_ids_for_mode(mode: str) -> set[str]:
    if mode == "docs":
        return {f"docs-{i}" for i in range(1, 9)}
    if mode == "new-project":
        return {f"new-{i}" for i in range(1, 6)}
    return set()


def _assert_stage_id(mode: str, stage: str) -> None:
    allowed = _stage_ids_for_mode(mode)
    if stage not in allowed:
        raise BpdCliError(errorCode="INVALID_STAGE", reason="invalid stage id for mode", hint=f"allowed: {', '.join(sorted(allowed))}", details={"mode": mode, "stage": stage})


def _resolve_output_root(repo_root: Path, config: dict[str, Any]) -> Path:
    rd = Path(str(config["output"]["rootDir"]))
    return rd if rd.is_absolute() else (repo_root / rd)


def _preflight(repo_root: Path, config: dict[str, Any]) -> Path:
    if sys.version_info < (3, 10):
        raise BpdCliError(errorCode="PYTHON_TOO_OLD", reason="python version too old", hint="require Python 3.10+")
    if not repo_root.exists() or not repo_root.is_dir():
        raise BpdCliError(errorCode="REPO_ROOT_INVALID", reason="repo root is not a directory", hint="pass an existing local directory path", details={"repoRoot": str(repo_root)})
    output_root = _resolve_output_root(repo_root, config)
    try:
        output_root.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        raise BpdCliError(errorCode="OUTPUT_NOT_WRITABLE", reason="output root not writable", hint="check permissions or use --output-rootdir", details={"outputRoot": str(output_root)}) from e
    except OSError as e:
        raise BpdCliError(errorCode="OUTPUT_IO_ERROR", reason=str(e), hint="check output path and permissions", details={"outputRoot": str(output_root)}) from e
    return output_root


def _profile_overrides(profile: str) -> dict[str, Any]:
    if profile == "claude-code":
        return {"integration": {"profile": "claude-code"}}
    if profile == "hermes":
        return {"integration": {"profile": "hermes"}}
    if profile == "opencode":
        return {"integration": {"profile": "opencode"}}
    return {"integration": {"profile": "generic"}}


def _render_json_config(config_obj: dict[str, Any]) -> str:
    return json.dumps(config_obj, ensure_ascii=False, indent=2) + "\n"


def _render_yaml_config_minimal(config_obj: dict[str, Any]) -> str:
    lines: list[str] = []
    for k, v in config_obj.items():
        if isinstance(v, dict):
            lines.append(f"{k}:")
            for kk, vv in v.items():
                if isinstance(vv, dict):
                    lines.append(f"  {kk}:")
                    for kkk, vvv in vv.items():
                        lines.append(f"    {kkk}: {json.dumps(vvv, ensure_ascii=False)}")
                else:
                    lines.append(f"  {kk}: {json.dumps(vv, ensure_ascii=False)}")
        else:
            lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


def _write_config(repo_root: Path, *, profile: str, fmt: str, force: bool) -> Path:
    target = repo_root / ("build-project-docs-new.yaml" if fmt == "yaml" else "build-project-docs-new.json")
    if target.exists() and not force:
        raise BpdCliError(errorCode="CONFIG_EXISTS", reason="config file already exists", hint="use --force to overwrite", details={"path": str(target)})
    cfg = _profile_overrides(profile)
    content = _render_yaml_config_minimal(cfg) if fmt == "yaml" else _render_json_config(cfg)
    try:
        target.write_text(content, encoding="utf-8")
    except PermissionError as e:
        raise BpdCliError(errorCode="CONFIG_NOT_WRITABLE", reason="cannot write config file", hint="check permissions", details={"path": str(target)}) from e
    return target


def _doctor(repo_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    output_root = _resolve_output_root(repo_root, config)
    has_pyyaml = False
    try:
        __import__("yaml")
        has_pyyaml = True
    except Exception:
        has_pyyaml = False

    git_binary = True
    git_repo = False
    git_reason = None
    try:
        run_git(repo_root, ["--version"])
        try:
            run_git(repo_root, ["rev-parse", "--is-inside-work-tree"])
            git_repo = True
        except GitError as e:
            git_repo = False
            git_reason = str(e)
    except GitError as e:
        git_binary = False
        git_reason = str(e)

    return {
        "ok": True,
        "repoRoot": str(repo_root),
        "outputRoot": str(output_root),
        "env": {
            "python": {"executable": sys.executable, "version": ".".join(map(str, sys.version_info[:3]))},
            "git": {"available": git_binary, "isRepo": git_repo, "reason": git_reason},
            "pyyaml": {"available": has_pyyaml},
        },
        "recommended": {"integrationProfile": str(config.get("integration", {}).get("profile", "generic"))},
    }


def run_docs_mode(
    repo_root: Path,
    config: dict[str, Any],
    *,
    stage: str | None = None,
    module: str | None = None,
    skip_verify: bool = False,
    agent_id: str | None = None,
) -> int:
    output_root = _preflight(repo_root, config)
    docs_root = output_root / "docs"
    identity = build_run_identity(agent_id=agent_id)

    if stage is not None:
        _assert_stage_id("docs", stage)

    lease_seconds = int(config.get("progress", {}).get("lockLeaseSeconds", 900))
    with progress_run_lock(docs_root, identity=identity, lease_seconds=lease_seconds):
        state = ProgressState.load_or_new(
            docs_root,
            mode="docs",
            output_root=config["output"]["rootDir"],
            index_file=config["output"]["indexFile"],
            run_identity=identity,
        )
        state = state.with_run_identity(identity).with_extension("lockStrategy", {"strategy": "file-lock", "lockFile": "_progress.lock"})
        write_progress(docs_root, state)

        modules = discover_modules(repo_root, config)
        selected = _select_modules(modules, module)

        if stage == "docs-8":
            project_ir = load_project_ir(output_root)
            if not project_ir:
                raise BpdCliError(errorCode="PROJECT_IR_MISSING", reason="project IR missing", hint="run docs-1 or full run first")
            selected_names = {str(m.get("displayName", "")) for m in selected if m.get("displayName")}
            report = verify_all(repo_root, output_root, project_ir, config, mode="docs", only_modules=selected_names or None)
            state = state.with_verification(report)
            state = state.with_stage_status("docs-8", "done" if report["blockingFailures"] == 0 else "blocked")
            state = _finalize_no_pending(state)
            write_progress(docs_root, state)
            _print_json(
                {
                    "ok": report["blockingFailures"] == 0,
                    "mode": "docs",
                    "scope": {"stage": "docs-8", "module": module},
                    "repoRoot": str(repo_root),
                    "outputRoot": str(output_root),
                    "progress": str((docs_root / "_progress.json")),
                    "summary": {"modulesTotal": len(selected), "modulesDone": 0, "modulesSkipped": 0},
                    "report": report,
                }
            )
            return 0 if report["blockingFailures"] == 0 else 2

        project_ir = write_project_ir(repo_root, output_root, modules, config)
        state = state.with_stage_status("docs-1", "done")
        write_progress(docs_root, state)

        if stage == "docs-1":
            state = _finalize_no_pending(state)
            write_progress(docs_root, state)
            _print_json(
                {
                    "ok": True,
                    "mode": "docs",
                    "scope": {"stage": "docs-1", "module": module},
                    "repoRoot": str(repo_root),
                    "outputRoot": str(output_root),
                    "progress": str((docs_root / "_progress.json")),
                    "summary": {"modulesTotal": len(selected), "modulesDone": 0, "modulesSkipped": 0},
                    "report": {"blockingFailures": 0, "warnings": 0, "blocking": [], "warning": []},
                }
            )
            return 0

        if stage == "docs-7":
            changelog_enabled = bool(config.get("changelog", {}).get("enabled", True))
            git_ok = False
            git_reason = None
            if changelog_enabled:
                try:
                    run_git(repo_root, ["rev-parse", "--is-inside-work-tree"])
                    git_ok = True
                except GitError as e:
                    git_ok = False
                    git_reason = str(e)

            if changelog_enabled and git_ok:
                state = state.with_stage_status("docs-7", "running")
                write_progress(docs_root, state)
                max_commits = int(config.get("changelog", {}).get("maxCommitsPerModule", 10))
                for m in selected:
                    module_dir = docs_root / str(m["displayName"])
                    changelog_path = module_dir / "CHANGELOG.md"
                    result = generate_and_update(
                        repo_root=repo_root,
                        module_name=str(m["displayName"]),
                        module_roots=list(m["roots"]),
                        changelog_path=changelog_path,
                        max_commits=max_commits,
                    )
                    status = "done" if result.get("newEntries", 0) > 0 else "skipped"
                    state = state.upsert_module_task(
                        "docs-7",
                        str(m["moduleId"]),
                        status=status,
                        artifacts=["CHANGELOG.md"],
                        lastEvidenceHash=str(result.get("latestCommit") or ""),
                    )
                    write_progress(docs_root, state)
                state = state.with_stage_status("docs-7", "done")
                write_progress(docs_root, state)
            else:
                state = state.with_stage_status("docs-7", "skipped")
                note = "changelog disabled by config" if not changelog_enabled else f"changelog skipped: {git_reason or 'not a git repo'}"
                state = state.with_stage_note("docs-7", note)
                write_progress(docs_root, state)
            state = _finalize_no_pending(state)
            write_progress(docs_root, state)
            _print_json(
                {
                    "ok": True,
                    "mode": "docs",
                    "scope": {"stage": "docs-7", "module": module},
                    "repoRoot": str(repo_root),
                    "outputRoot": str(output_root),
                    "progress": str((docs_root / "_progress.json")),
                    "summary": {"modulesTotal": len(selected), "modulesDone": 0, "modulesSkipped": 0},
                    "report": {"blockingFailures": 0, "warnings": 0, "blocking": [], "warning": []},
                }
            )
            return 0

        evidence_by_id: dict[str, str] = {}
        state = state.with_stage_status("docs-2", "running")
        write_progress(docs_root, state)
        for m in selected:
            module_id = str(m["moduleId"])
            evidence_hash = compute_evidence_hash(repo_root, list(m["roots"]), config)
            evidence_by_id[module_id] = evidence_hash
            state = state.upsert_module_task("docs-2", module_id, status="done", artifacts=[], lastEvidenceHash=evidence_hash)
            write_progress(docs_root, state)
        state = state.with_stage_status("docs-2", "done")
        write_progress(docs_root, state)

        if stage is None or stage == "docs-3":
            render_project(output_root, project_ir, config)
            state = state.with_stage_status("docs-3", "done")
            write_progress(docs_root, state)
        else:
            state = state.with_stage_status("docs-3", "skipped")
            write_progress(docs_root, state)

        if stage == "docs-3":
            state = _finalize_no_pending(state)
            write_progress(docs_root, state)
            _print_json(
                {
                    "ok": True,
                    "mode": "docs",
                    "scope": {"stage": "docs-3", "module": module},
                    "repoRoot": str(repo_root),
                    "outputRoot": str(output_root),
                    "progress": str((docs_root / "_progress.json")),
                    "summary": {"modulesTotal": len(selected), "modulesDone": 0, "modulesSkipped": 0},
                    "report": {"blockingFailures": 0, "warnings": 0, "blocking": [], "warning": []},
                }
            )
            return 0

        state = state.with_stage_status("docs-4", "running")
        write_progress(docs_root, state)
        for m in selected:
            module_id = str(m["moduleId"])
            evidence_hash = evidence_by_id.get(module_id) or compute_evidence_hash(repo_root, list(m["roots"]), config)
            prev = state.get_module_task("docs-4", module_id) or {}
            last = prev.get("lastEvidenceHash")
            module_ir_path = output_root / "docs" / "_ir" / "modules" / f"{module_id}.json"
            if (prev.get("status") in ["done", "skipped"]) and last == evidence_hash and module_ir_path.exists():
                state = state.upsert_module_task("docs-4", module_id, status="skipped", artifacts=[f"_ir/modules/{module_id}.json"], lastEvidenceHash=evidence_hash)
                write_progress(docs_root, state)
                continue
            module_ir = extract_module_ir(repo_root, m, config, evidence_hash=evidence_hash)
            write_module_ir(output_root, module_ir)
            state = state.upsert_module_task("docs-4", module_id, status="done", artifacts=[f"_ir/modules/{module_id}.json"], lastEvidenceHash=evidence_hash)
            write_progress(docs_root, state)
        state = state.with_stage_status("docs-4", "done")
        write_progress(docs_root, state)

        state = state.with_stage_status("docs-5", "running")
        write_progress(docs_root, state)
        for m in selected:
            module_id = str(m["moduleId"])
            evidence_hash = evidence_by_id.get(module_id) or compute_evidence_hash(repo_root, list(m["roots"]), config)
            prev = state.get_module_task("docs-5", module_id) or {}
            last = prev.get("lastEvidenceHash")
            module_ir = load_module_ir(output_root, module_id)
            if not module_ir:
                raise BpdCliError(errorCode="MODULE_IR_MISSING", reason="module IR missing", hint="run docs-4 or full run first", details={"moduleId": module_id})

            module_dir = docs_root / str(m["displayName"])
            expected = ["README.md", "CHANGELOG.md"]
            if bool(module_ir.get("api", {}).get("hasPublicApi") is True):
                expected.extend(["api-default.md", "data-model.md", "pitfalls.md"])
            if (prev.get("status") in ["done", "skipped"]) and last == evidence_hash and all((module_dir / f).exists() for f in expected):
                state = state.upsert_module_task("docs-5", module_id, status="skipped", artifacts=expected, lastEvidenceHash=evidence_hash)
                write_progress(docs_root, state)
                continue

            artifacts = render_module(output_root, m, module_ir)
            artifacts = ["README.md", *artifacts]
            state = state.upsert_module_task("docs-5", module_id, status="done", artifacts=artifacts, lastEvidenceHash=evidence_hash)
            write_progress(docs_root, state)
        state = state.with_stage_status("docs-5", "done")
        write_progress(docs_root, state)

        state = state.with_stage_status("docs-6", "skipped")
        write_progress(docs_root, state)

        if stage == "docs-5":
            report = {"blockingFailures": 0, "warnings": 0, "blocking": [], "warning": []}
            if not skip_verify:
                selected_names = {str(m.get("displayName", "")) for m in selected if m.get("displayName")}
                report = verify_all(repo_root, output_root, project_ir, config, mode="docs", only_modules=selected_names or None)
                state = state.with_verification(report)
                state = state.with_stage_status("docs-8", "done" if report["blockingFailures"] == 0 else "blocked")
                write_progress(docs_root, state)
            else:
                state = state.with_stage_status("docs-8", "skipped")
                write_progress(docs_root, state)
            state = _finalize_no_pending(state)
            write_progress(docs_root, state)
            stage5 = next((s for s in state.stages if s.get("stageId") == "docs-5"), {})
            tasks = [t for t in list(stage5.get("moduleTasks", []) or []) if str(t.get("moduleId")) in {str(m["moduleId"]) for m in selected}]
            done = len([t for t in tasks if t.get("status") == "done"])
            skipped = len([t for t in tasks if t.get("status") == "skipped"])
            _print_json(
                {
                    "ok": report.get("blockingFailures", 0) == 0,
                    "mode": "docs",
                    "scope": {"stage": "docs-5", "module": module, "skipVerify": skip_verify},
                    "repoRoot": str(repo_root),
                    "outputRoot": str(output_root),
                    "progress": str((docs_root / "_progress.json")),
                    "summary": {"modulesTotal": len(selected), "modulesDone": done, "modulesSkipped": skipped},
                    "report": report,
                }
            )
            return 0 if report.get("blockingFailures", 0) == 0 else 2

        changelog_enabled = bool(config.get("changelog", {}).get("enabled", True))
        git_ok = False
        git_reason = None
        if changelog_enabled:
            try:
                run_git(repo_root, ["rev-parse", "--is-inside-work-tree"])
                git_ok = True
            except GitError as e:
                git_ok = False
                git_reason = str(e)

        if changelog_enabled and git_ok:
            state = state.with_stage_status("docs-7", "running")
            write_progress(docs_root, state)
            max_commits = int(config.get("changelog", {}).get("maxCommitsPerModule", 10))
            for m in selected:
                module_dir = docs_root / str(m["displayName"])
                changelog_path = module_dir / "CHANGELOG.md"
                result = generate_and_update(
                    repo_root=repo_root,
                    module_name=str(m["displayName"]),
                    module_roots=list(m["roots"]),
                    changelog_path=changelog_path,
                    max_commits=max_commits,
                )
                status = "done" if result.get("newEntries", 0) > 0 else "skipped"
                state = state.upsert_module_task(
                    "docs-7",
                    str(m["moduleId"]),
                    status=status,
                    artifacts=["CHANGELOG.md"],
                    lastEvidenceHash=str(result.get("latestCommit") or ""),
                )
                write_progress(docs_root, state)
            state = state.with_stage_status("docs-7", "done")
            write_progress(docs_root, state)
        else:
            state = state.with_stage_status("docs-7", "skipped")
            note = "changelog disabled by config" if not changelog_enabled else f"changelog skipped: {git_reason or 'not a git repo'}"
            state = state.with_stage_note("docs-7", note)
            write_progress(docs_root, state)

        report = {"blockingFailures": 0, "warnings": 0, "blocking": [], "warning": []}
        if not skip_verify:
            selected_names = {str(m.get("displayName", "")) for m in selected if m.get("displayName")}
            report = verify_all(repo_root, output_root, project_ir, config, mode="docs", only_modules=selected_names or None)
            state = state.with_verification(report)
            state = state.with_stage_status("docs-8", "done" if report["blockingFailures"] == 0 else "blocked")
            write_progress(docs_root, state)
        else:
            state = state.with_stage_status("docs-8", "skipped")
            write_progress(docs_root, state)

        state = _finalize_no_pending(state)
        write_progress(docs_root, state)

        stage5 = next((s for s in state.stages if s.get("stageId") == "docs-5"), {})
        tasks = [t for t in list(stage5.get("moduleTasks", []) or []) if str(t.get("moduleId")) in {str(m["moduleId"]) for m in selected}]
        total = len(selected)
        done = len([t for t in tasks if t.get("status") == "done"])
        skipped = len([t for t in tasks if t.get("status") == "skipped"])

        out = {
            "ok": report.get("blockingFailures", 0) == 0,
            "mode": "docs",
            "scope": {"stage": stage, "module": module, "skipVerify": skip_verify},
            "repoRoot": str(repo_root),
            "outputRoot": str(output_root),
            "progress": str((docs_root / "_progress.json")),
            "summary": {"modulesTotal": total, "modulesDone": done, "modulesSkipped": skipped},
            "report": report,
        }
        _print_json(out)
        return 0 if out["ok"] else 2


def run_new_project_mode(
    repo_root: Path,
    config: dict[str, Any],
    prd_input: str,
    stack: str,
    *,
    stage: str | None = None,
    module: str | None = None,
    skip_verify: bool = False,
    agent_id: str | None = None,
) -> int:
    output_root = _preflight(repo_root, config)
    docs_root = output_root / "docs"
    identity = build_run_identity(agent_id=agent_id)

    if stage is not None:
        _assert_stage_id("new-project", stage)

    lease_seconds = int(config.get("progress", {}).get("lockLeaseSeconds", 900))
    with progress_run_lock(docs_root, identity=identity, lease_seconds=lease_seconds):
        state = ProgressState.load_or_new(
            docs_root,
            mode="new-project",
            output_root=config["output"]["rootDir"],
            index_file=config["output"]["indexFile"],
            run_identity=identity,
        )
        state = state.with_run_identity(identity).with_extension("lockStrategy", {"strategy": "file-lock", "lockFile": "_progress.lock"})
        write_progress(docs_root, state)

        state = state.with_stage_status("new-1", "running")
        write_progress(docs_root, state)
        prd_ir, _ = parse_prd_input(prd_input, repo_root)
        state = state.with_stage_status("new-1", "done")
        write_progress(docs_root, state)

        if stage == "new-1":
            state = _finalize_no_pending(state)
            write_progress(docs_root, state)
            _print_json(
                {
                    "ok": True,
                    "mode": "new-project",
                    "stack": stack,
                    "scope": {"stage": "new-1"},
                    "repoRoot": str(repo_root),
                    "outputRoot": str(output_root),
                    "progress": str((docs_root / "_progress.json")),
                    "summary": {"modulesTotal": 0, "modulesDone": 0, "modulesSkipped": 0},
                    "report": {"blockingFailures": 0, "warnings": 0, "blocking": [], "warning": []},
                    "missingInfo": prd_ir.get("missingInfo", []),
                }
            )
            return 0

        state = state.with_stage_status("new-2", "running")
        write_progress(docs_root, state)
        plan = build_plan_from_prd(prd_ir, stack=stack)
        project_ir = plan_to_project_ir(plan)
        state = state.with_stage_status("new-2", "done")
        write_progress(docs_root, state)

        if stage == "new-2":
            state = _finalize_no_pending(state)
            write_progress(docs_root, state)
            _print_json(
                {
                    "ok": True,
                    "mode": "new-project",
                    "stack": stack,
                    "scope": {"stage": "new-2"},
                    "repoRoot": str(repo_root),
                    "outputRoot": str(output_root),
                    "progress": str((docs_root / "_progress.json")),
                    "summary": {"modulesTotal": 0, "modulesDone": 0, "modulesSkipped": 0},
                    "report": {"blockingFailures": 0, "warnings": 0, "blocking": [], "warning": []},
                    "missingInfo": prd_ir.get("missingInfo", []),
                }
            )
            return 0

        state = state.with_stage_status("new-3", "running")
        write_progress(docs_root, state)
        write_project_ir_payload(output_root, project_ir)
        write_prd_and_plan_ir(output_root, prd_ir, plan)
        state = state.with_stage_status("new-3", "done")
        write_progress(docs_root, state)

        if stage == "new-3":
            state = _finalize_no_pending(state)
            write_progress(docs_root, state)
            _print_json(
                {
                    "ok": True,
                    "mode": "new-project",
                    "stack": stack,
                    "scope": {"stage": "new-3"},
                    "repoRoot": str(repo_root),
                    "outputRoot": str(output_root),
                    "progress": str((docs_root / "_progress.json")),
                    "summary": {"modulesTotal": 0, "modulesDone": 0, "modulesSkipped": 0},
                    "report": {"blockingFailures": 0, "warnings": 0, "blocking": [], "warning": []},
                    "missingInfo": prd_ir.get("missingInfo", []),
                }
            )
            return 0

        render_project(output_root, project_ir, config, mode="new-project")
        render_new_project_task_list(output_root, plan)
        state = state.with_stage_status("new-4", "done")
        write_progress(docs_root, state)

        if stage == "new-4":
            state = _finalize_no_pending(state)
            write_progress(docs_root, state)
            _print_json(
                {
                    "ok": True,
                    "mode": "new-project",
                    "stack": stack,
                    "scope": {"stage": "new-4"},
                    "repoRoot": str(repo_root),
                    "outputRoot": str(output_root),
                    "progress": str((docs_root / "_progress.json")),
                    "summary": {"modulesTotal": 0, "modulesDone": 0, "modulesSkipped": 0},
                    "report": {"blockingFailures": 0, "warnings": 0, "blocking": [], "warning": []},
                    "missingInfo": prd_ir.get("missingInfo", []),
                }
            )
            return 0

        module_plan = list(plan.get("moduleDocsPlan", []) or [])
        if module:
            module_plan = [mp for mp in module_plan if str(mp.get("moduleId", "")) == module or str(mp.get("displayName", "")) == module]
            if not module_plan:
                raise BpdCliError(errorCode="MODULE_NOT_FOUND", reason="module not found in plan", hint="use moduleId or displayName from plan", details={"module": module})

        state = state.with_stage_status("new-5", "running")
        write_progress(docs_root, state)
        for mp in module_plan:
            module_ir = plan_to_module_ir(plan, mp)
            write_module_ir(output_root, module_ir)
            artifacts = render_module(
                output_root,
                {"moduleId": mp["moduleId"], "displayName": mp["displayName"], "deps": []},
                module_ir,
                mode="new-project",
            )
            artifacts = ["README.md", *artifacts]
            state = state.upsert_module_task("new-5", str(mp["moduleId"]), status="done", artifacts=artifacts, lastEvidenceHash="planned")
            write_progress(docs_root, state)
        state = state.with_stage_status("new-5", "done")
        write_progress(docs_root, state)

        report = {"blockingFailures": 0, "warnings": 0, "blocking": [], "warning": []}
        if not skip_verify:
            selected_names = {str(mp.get("displayName", "")) for mp in module_plan if mp.get("displayName")}
            report = verify_all(repo_root, output_root, project_ir, config, mode="new-project", only_modules=selected_names or None)
            state = state.with_verification(report)
            state = state.with_stage_status("new-5", "done" if report["blockingFailures"] == 0 else "blocked")
            write_progress(docs_root, state)
        else:
            state = state.with_stage_status("new-5", "done")
            write_progress(docs_root, state)

        state = _finalize_no_pending(state)
        write_progress(docs_root, state)

        tasks = list((next((s for s in state.stages if s.get("stageId") == "new-5"), {}) or {}).get("moduleTasks", []) or [])
        tasks = [t for t in tasks if str(t.get("moduleId")) in {str(mp.get("moduleId")) for mp in module_plan}]
        out = {
            "ok": report.get("blockingFailures", 0) == 0,
            "mode": "new-project",
            "stack": stack,
            "scope": {"stage": stage, "module": module, "skipVerify": skip_verify},
            "repoRoot": str(repo_root),
            "outputRoot": str(output_root),
            "progress": str((docs_root / "_progress.json")),
            "summary": {"modulesTotal": len(module_plan), "modulesDone": len([t for t in tasks if t.get("status") == "done"]), "modulesSkipped": len([t for t in tasks if t.get("status") == "skipped"])},
            "report": report,
            "missingInfo": prd_ir.get("missingInfo", []),
        }
        _print_json(out)
        return 0 if out["ok"] else 2


def main() -> int:
    parser = argparse.ArgumentParser(prog="bpd_new")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run")
    run_p.add_argument("--repo-root", required=True)
    run_p.add_argument("--config", default=None)
    run_p.add_argument("--mode", default="auto", choices=["auto", "docs", "new-project"])
    run_p.add_argument("--prd", default=None, help="new-project 模式下的 PRD 文件路径或文本")
    run_p.add_argument("--stack", default="spring", choices=["spring", "fastapi", "nestjs", "go", "rust", "react", "vue"])
    run_p.add_argument("--stage", default=None)
    run_p.add_argument("--module", default=None)
    run_p.add_argument("--skip-verify", action="store_true")
    run_p.add_argument("--output-rootdir", default=None, dest="output_rootdir")
    run_p.add_argument("--output-indexfile", default=None, dest="output_indexfile")
    run_p.add_argument("--agent-id", default=None)

    verify_p = sub.add_parser("verify")
    verify_p.add_argument("--repo-root", required=True)
    verify_p.add_argument("--config", default=None)
    verify_p.add_argument("--mode", default="auto", choices=["auto", "docs", "new-project"])
    verify_p.add_argument("--module", default=None)
    verify_p.add_argument("--output-rootdir", default=None, dest="output_rootdir")
    verify_p.add_argument("--output-indexfile", default=None, dest="output_indexfile")
    verify_p.add_argument("--agent-id", default=None)

    init_p = sub.add_parser("init")
    init_p.add_argument("--repo-root", required=True)
    init_p.add_argument("--profile", default="generic", choices=["generic", "claude-code", "hermes", "opencode"])
    init_p.add_argument("--format", default="json", choices=["json", "yaml"])
    init_p.add_argument("--force", action="store_true")

    doctor_p = sub.add_parser("doctor")
    doctor_p.add_argument("--repo-root", required=True)
    doctor_p.add_argument("--config", default=None)
    doctor_p.add_argument("--mode", default="auto", choices=["auto", "docs", "new-project"])
    doctor_p.add_argument("--output-rootdir", default=None, dest="output_rootdir")
    doctor_p.add_argument("--output-indexfile", default=None, dest="output_indexfile")
    doctor_p.add_argument("--agent-id", default=None)

    args = parser.parse_args()
    repo_root = Path(args.repo_root).expanduser().resolve()
    try:
        if args.cmd == "init":
            if not repo_root.exists() or not repo_root.is_dir():
                raise BpdCliError(errorCode="REPO_ROOT_INVALID", reason="repo root is not a directory", hint="pass an existing local directory path", details={"repoRoot": str(repo_root)})
            p = _write_config(repo_root, profile=str(args.profile), fmt=str(args.format), force=bool(args.force))
            _print_json({"ok": True, "repoRoot": str(repo_root), "configPath": str(p), "profile": str(args.profile), "format": str(args.format)})
            return 0

        cfg = load_effective_config(repo_root, cli_args=vars(args), env=os.environ)

        if args.cmd == "doctor":
            mode = cfg["mode"]["defaultMode"]
            if args.mode != "auto":
                mode = args.mode
            _print_json(_doctor(repo_root, cfg | {"mode": {"defaultMode": mode}}))
            return 0

        if args.cmd == "verify":
            output_root = _resolve_output_root(repo_root, cfg)
            project_ir = load_project_ir(output_root)
            if not project_ir:
                raise BpdCliError(errorCode="PROJECT_IR_MISSING", reason="project IR missing", hint="run first to generate .claude/_ir")
            verify_mode = args.mode
            if verify_mode == "auto":
                p = output_root / "docs" / "_progress.json"
                if p.exists():
                    data = json.loads(p.read_text(encoding="utf-8"))
                    verify_mode = str(data.get("mode", "docs"))
                else:
                    verify_mode = "docs"

            only_modules = None
            if getattr(args, "module", None):
                selected = _select_modules(list(project_ir.get("modules", []) or []), str(args.module))
                only_modules = {str(m.get("displayName", "")) for m in selected if m.get("displayName")}

            report = verify_all(repo_root, output_root, project_ir, cfg, mode=verify_mode, only_modules=only_modules)
            _print_json({"ok": report["blockingFailures"] == 0, "mode": verify_mode, "scope": {"module": getattr(args, "module", None)}, "report": report})
            return 0 if report["blockingFailures"] == 0 else 2

        mode = cfg["mode"]["defaultMode"]
        if args.mode != "auto":
            mode = args.mode

        if mode == "new-project":
            if not args.prd:
                raise BpdCliError(errorCode="PRD_REQUIRED", reason="new-project mode requires --prd", hint="provide a PRD file path or PRD text")
            return run_new_project_mode(
                repo_root,
                cfg,
                prd_input=str(args.prd),
                stack=str(args.stack),
                stage=str(args.stage) if args.stage else None,
                module=str(args.module) if args.module else None,
                skip_verify=bool(args.skip_verify),
                agent_id=str(args.agent_id) if args.agent_id else None,
            )

        return run_docs_mode(
            repo_root,
            cfg,
            stage=str(args.stage) if args.stage else None,
            module=str(args.module) if args.module else None,
            skip_verify=bool(args.skip_verify),
            agent_id=str(args.agent_id) if args.agent_id else None,
        )
    except ProgressLockError as e:
        _print_json(
            {
                "ok": False,
                "errorCode": "PROGRESS_LOCKED",
                "reason": "another run is writing progress",
                "hint": "use a different outputRootDir or wait for the other run to finish",
                "details": {"lockPath": str(getattr(e, "lock_path", "")), "holder": getattr(e, "holder", None)},
            }
        )
        return 2
    except BpdCliError as e:
        _print_json({"ok": False, "errorCode": e.errorCode, "reason": e.reason, "hint": e.hint, "details": e.details})
        return 2
    except PermissionError as e:
        _print_json({"ok": False, "errorCode": "PERMISSION_DENIED", "reason": str(e), "hint": "check filesystem permissions or sandbox policy"})
        return 2
    except OSError as e:
        _print_json({"ok": False, "errorCode": "OS_ERROR", "reason": str(e), "hint": "check filesystem, paths, and sandbox policy"})
        return 2
    except RuntimeError as e:
        msg = str(e)
        if msg.startswith("YAML config requires PyYAML"):
            _print_json(
                {
                    "ok": False,
                    "errorCode": "CONFIG_YAML_REQUIRES_PYYAML",
                    "reason": msg,
                    "hint": "install PyYAML or use JSON config",
                }
            )
            return 2
        _print_json({"ok": False, "errorCode": "UNHANDLED_ERROR", "reason": msg})
        return 2
    except Exception as e:
        _print_json({"ok": False, "errorCode": "UNHANDLED_ERROR", "reason": str(e)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
