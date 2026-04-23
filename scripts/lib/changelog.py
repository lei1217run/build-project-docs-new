from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.git_tools import GitError, run_git


_COMMIT_LINE_RE = re.compile(r"^([0-9a-f]{40})\x1f([0-9]{4}-[0-9]{2}-[0-9]{2})\x1f(.*)$")
_HASH_IN_CHANGELOG_RE = re.compile(r"(?i)\*\*提交\*\*:\s*([0-9a-f]{7,40})")


@dataclass(frozen=True)
class ChangeFile:
    path: str
    additions: int | None
    deletions: int | None


@dataclass(frozen=True)
class ChangeEntry:
    date: str
    subject: str
    commit: str
    kind: str
    risk: str
    riskReasons: list[str]
    files: list[ChangeFile]
    impacts: dict[str, bool]


def collect_recorded_commits(changelog_path: Path) -> set[str]:
    if not changelog_path.exists():
        return set()
    text = changelog_path.read_text(encoding="utf-8", errors="ignore")
    out: set[str] = set()
    for m in _HASH_IN_CHANGELOG_RE.finditer(text):
        out.add(m.group(1))
    return out


def list_commits(repo_root: Path, paths: list[str], max_commits: int) -> list[tuple[str, str, str]]:
    args = ["log", f"-n{max_commits}", "--date=short", "--pretty=format:%H%x1f%ad%x1f%s", "--", *paths]
    out = run_git(repo_root, args)
    commits: list[tuple[str, str, str]] = []
    for line in out.splitlines():
        m = _COMMIT_LINE_RE.match(line.strip())
        if not m:
            continue
        commits.append((m.group(1), m.group(2), m.group(3)))
    return commits


def show_numstat(repo_root: Path, commit: str, paths: list[str]) -> list[ChangeFile]:
    out = run_git(repo_root, ["show", "--numstat", "--format=", commit, "--", *paths])
    files: list[ChangeFile] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        a, d, p = parts
        additions = None if a == "-" else int(a)
        deletions = None if d == "-" else int(d)
        files.append(ChangeFile(path=p, additions=additions, deletions=deletions))
    return files


def show_all_changed_paths(repo_root: Path, commit: str) -> list[str]:
    out = run_git(repo_root, ["show", "--name-only", "--format=", commit])
    return [x.strip() for x in out.splitlines() if x.strip()]


def classify_kind(subject: str) -> str:
    s = subject.lower()
    if any(k in subject for k in ["修复", "修正", "fix"]):
        return "fix"
    if any(k in subject for k in ["新增", "增加", "支持", "feat", "feature"]):
        return "feat"
    if any(k in subject for k in ["优化", "perf"]):
        return "perf"
    if any(k in subject for k in ["重构", "refactor"]):
        return "refactor"
    return "chore"


def classify_impacts(paths: list[str]) -> dict[str, bool]:
    joined = "\n".join(paths).lower()
    api = any(x in joined for x in ["controller", "router", "handler", "resolver", "grpc", "proto"])
    data = any(x in joined for x in ["model", "entity", "dto", "vo", "schema", "migration", "migrate", ".sql"])
    cfg = any(x in joined for x in ["application.", ".yml", ".yaml", ".properties", "config", ".env"])
    return {"api": api, "dataModel": data, "config": cfg}


def classify_risk(module_paths: list[str], all_changed_paths: list[str], impacts: dict[str, bool]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    cross = any(not _is_under_any(p, module_paths) for p in all_changed_paths)
    if cross:
        reasons.append("跨模块变更")
    if impacts.get("dataModel"):
        reasons.append("数据模型变更")
    if impacts.get("api"):
        reasons.append("API/入口变更")
    if impacts.get("config"):
        reasons.append("配置变更")

    if cross or impacts.get("dataModel"):
        return "HIGH", reasons
    if impacts.get("api") or impacts.get("config"):
        return "MEDIUM", reasons
    return "LOW", reasons


def _is_under_any(path: str, roots: list[str]) -> bool:
    for r in roots:
        rr = r.rstrip("/")
        if rr == ".":
            return True
        if path == rr or path.startswith(rr + "/"):
            return True
    return False


def generate_new_entries(
    repo_root: Path,
    module_roots: list[str],
    max_commits: int,
    recorded: set[str],
) -> list[ChangeEntry]:
    commits = list_commits(repo_root, module_roots, max_commits=max_commits)
    entries: list[ChangeEntry] = []
    for full_hash, date, subject in commits:
        short = full_hash[:7]
        if short in recorded or full_hash in recorded:
            continue
        files = show_numstat(repo_root, full_hash, module_roots)
        all_paths = show_all_changed_paths(repo_root, full_hash)
        impacts = classify_impacts(all_paths)
        kind = classify_kind(subject)
        risk, reasons = classify_risk(module_roots, all_paths, impacts)
        entries.append(
            ChangeEntry(
                date=date,
                subject=subject,
                commit=short,
                kind=kind,
                risk=risk,
                riskReasons=reasons,
                files=files,
                impacts={"api": impacts["api"], "dataModel": impacts["dataModel"], "config": impacts["config"], "crossModule": any(not _is_under_any(p, module_roots) for p in all_paths)},
            )
        )
    return entries


def render_entries(entries: list[ChangeEntry]) -> str:
    out: list[str] = []
    for e in entries:
        out.append(f"## [{e.date}] {e.subject}")
        out.append("")
        out.append(f"**类型**: {e.kind}")
        out.append(f"**提交**: {e.commit}")
        out.append(f"**风险**: {e.risk}")
        if e.riskReasons:
            out.append(f"**原因**: {', '.join(e.riskReasons)}")
        out.append("")
        out.append("### 变更文件")
        out.append("| 文件 | 变更 | 说明 |")
        out.append("|------|------|------|")
        if e.files:
            for f in e.files:
                a = "-" if f.additions is None else str(f.additions)
                d = "-" if f.deletions is None else str(f.deletions)
                out.append(f"| {f.path} | +{a}/-{d} | |")
        else:
            out.append("| - | - | |")
        out.append("")
        out.append("### 影响范围")
        out.append(f"- **API**: {'是' if e.impacts.get('api') else '否'}")
        out.append(f"- **跨模块**: {'是' if e.impacts.get('crossModule') else '否'}")
        out.append(f"- **数据模型**: {'是' if e.impacts.get('dataModel') else '否'}")
        out.append(f"- **配置**: {'是' if e.impacts.get('config') else '否'}")
        out.append("")
        if e.risk == "HIGH":
            out.append("### 回滚指南")
            out.append(f"- 回滚: `git revert {e.commit}`")
            out.append("- 检查文件: 上表所列文件")
            out.append("")
        out.append("---")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def ensure_header(module_name: str) -> str:
    return "\n".join(
        [
            f"# Changelog - {module_name}",
            "",
            "> 模块变更历史。最新变更在最上方。",
            "> 排查问题时优先阅读本文件。",
            "",
            "---",
            "",
        ]
    )


def update_changelog_file(changelog_path: Path, module_name: str, new_entries_md: str) -> None:
    header = ensure_header(module_name)
    if not changelog_path.exists():
        changelog_path.parent.mkdir(parents=True, exist_ok=True)
        changelog_path.write_text(header + new_entries_md, encoding="utf-8")
        return

    text = changelog_path.read_text(encoding="utf-8", errors="ignore")
    if not text.lstrip().startswith("# Changelog -"):
        text = header + text

    parts = text.split("\n---\n", 1)
    if len(parts) == 2:
        prefix = parts[0] + "\n---\n"
        rest = parts[1].lstrip("\n")
        changelog_path.write_text(prefix + "\n\n" + new_entries_md + rest, encoding="utf-8")
    else:
        changelog_path.write_text(header + new_entries_md + text, encoding="utf-8")


def generate_and_update(
    repo_root: Path,
    module_name: str,
    module_roots: list[str],
    changelog_path: Path,
    max_commits: int,
) -> dict[str, Any]:
    recorded = collect_recorded_commits(changelog_path)
    try:
        entries = generate_new_entries(repo_root, module_roots, max_commits=max_commits, recorded=recorded)
    except GitError as e:
        return {"ok": False, "error": str(e), "newEntries": 0, "latestCommit": None}

    if not entries:
        if not changelog_path.exists():
            update_changelog_file(changelog_path, module_name, "")
        return {"ok": True, "newEntries": 0, "latestCommit": None}

    md = render_entries(entries)
    update_changelog_file(changelog_path, module_name, md)
    return {"ok": True, "newEntries": len(entries), "latestCommit": entries[0].commit}

