from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_PATTERN_WORDS = [
    "strategy",
    "factory",
    "adapter",
    "repository",
    "middleware",
    "pipeline",
    "observer",
    "builder",
    "command",
    "event",
    "plugin",
    "registry",
    "ioc",
    "di",
]


def generate_report(repo_root: Path, output_root: Path, project_ir: dict[str, Any], config: dict[str, Any], mode: str = "docs") -> None:
    docs_root = output_root / "docs"
    report_path = docs_root / "_report.md"
    report_depth = int(config.get("report", {}).get("depth", 0) or 0)

    sources = _collect_curated_sources(repo_root)
    text_by_path: dict[str, str] = {}
    for p in sources:
        txt = _read_text_file(p, max_bytes=200_000)
        if txt is None:
            continue
        rel = p.relative_to(repo_root).as_posix()
        text_by_path[rel] = txt

    excerpts_positioning = _extract_positioning(text_by_path)
    excerpts_capabilities = _extract_capabilities(text_by_path)
    patterns = _extract_patterns_from_docs(text_by_path)

    entrypoint_examples, scanned_repo_files = _extract_entrypoint_examples(repo_root, output_root, project_ir, depth=report_depth)
    scanned_repo_files = sorted(set(scanned_repo_files))

    facts = _summarize_facts(output_root, project_ir)

    lines: list[str] = []
    lines.append("# 全维度报告")
    lines.append("")
    lines.append("## 项目定位")
    if not excerpts_positioning:
        lines.append("- unknown（未在 README/ARCHITECTURE/docs 中找到明确表述）")
    else:
        for ex in excerpts_positioning[:5]:
            lines.append(f"- {ex['quote']} [evidence: {ex['path']}]")
    lines.append("")
    lines.append("## 能力说明")
    if not excerpts_capabilities:
        lines.append("- unknown（未在 README/ARCHITECTURE/docs 中找到明确条目）")
    else:
        for ex in excerpts_capabilities[:10]:
            lines.append(f"- {ex['quote']} [evidence: {ex['path']}]")
    lines.append("")
    lines.append("## 代码示例")
    if entrypoint_examples:
        for e in entrypoint_examples[:50]:
            nm = e.get("name") or "entry"
            sig = e.get("signature") or ""
            p = e.get("path") or ""
            if sig:
                lines.append(f"- entrypoint: {nm}: {sig} [evidence: {p}]")
            else:
                lines.append(f"- entrypoint: {nm} [evidence: {p}]")
    else:
        lines.append("- unknown（未在 IR 中定位到稳定入口/路由/CLI 子命令）")
    lines.append("")
    lines.append("## 设计模式")
    if patterns:
        for p in patterns[:20]:
            lines.append(f"- pattern: {p['name']} [evidence: {p['path']}]")
    else:
        lines.append("- unknown（未在 README/ARCHITECTURE/docs 中找到明确表述）")
    lines.append("")
    lines.append("## Facts/IR 汇总")
    lines.extend(facts)
    lines.append("")
    lines.append("## 附录")
    lines.append(f"- mode: {mode}")
    lines.append(f"- report.depth: {report_depth}")
    lines.append("- sources:")
    for p in sorted(text_by_path.keys()):
        lines.append(f"  - {p}")
    lines.append("- scanned (repo, ir-guided):")
    if scanned_repo_files:
        for p in scanned_repo_files[:200]:
            lines.append(f"  - {p}")
        if len(scanned_repo_files) > 200:
            lines.append("  - ...")
    else:
        lines.append("  - (none)")
    lines.append("- ir:")
    lines.append("  - docs/_ir/project.json")
    lines.append("")

    _write_if_changed(report_path, "\n".join(lines))


def _write_if_changed(path: Path, content: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = content.rstrip() + "\n"
    if path.exists():
        old = path.read_text(encoding="utf-8", errors="ignore")
        if old == new:
            return False
    path.write_text(new, encoding="utf-8")
    return True


def _read_text_file(path: Path, *, max_bytes: int) -> str | None:
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > max_bytes:
            return None
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None


def _collect_curated_sources(repo_root: Path) -> list[Path]:
    out: list[Path] = []
    for p in sorted(repo_root.glob("README*")):
        if p.is_file():
            out.append(p)
    for p in sorted(repo_root.glob("ARCHITECTURE*")):
        if p.is_file():
            out.append(p)
    docs = repo_root / "docs"
    if docs.exists() and docs.is_dir():
        for p in sorted(docs.rglob("*.md")):
            if p.is_file():
                out.append(p)
                if len(out) >= 80:
                    break
    return out


def _first_paragraph(text: str) -> str | None:
    lines = [ln.rstrip() for ln in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return None
    if lines[0].lstrip().startswith("#"):
        lines.pop(0)
    while lines and not lines[0].strip():
        lines.pop(0)
    buf: list[str] = []
    for ln in lines:
        if not ln.strip():
            break
        if ln.lstrip().startswith("```"):
            break
        buf.append(ln.strip())
        if sum(len(x) for x in buf) > 280:
            break
    s = " ".join(buf).strip()
    return s or None


def _extract_positioning(text_by_path: dict[str, str]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for p in ["README.md", "README.MD", "README"]:
        if p in text_by_path:
            para = _first_paragraph(text_by_path[p])
            if para:
                out.append({"path": p, "quote": para})
            break
    if out:
        return out
    for path, txt in text_by_path.items():
        para = _first_paragraph(txt)
        if para:
            out.append({"path": path, "quote": para})
            break
    return out


def _extract_capabilities(text_by_path: dict[str, str]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    keys = ["功能", "特性", "能力", "features", "capabilities"]
    for path, txt in text_by_path.items():
        lines = txt.splitlines()
        idx = -1
        for i, ln in enumerate(lines):
            m = _HEADING_RE.match(ln.strip())
            if not m:
                continue
            title = m.group(2).strip().lower()
            if any(k in title for k in keys):
                idx = i + 1
                break
        if idx < 0:
            continue
        for ln in lines[idx : idx + 60]:
            s = ln.strip()
            if not s:
                break
            if s.startswith("#"):
                break
            if s.startswith("- "):
                out.append({"path": path, "quote": s[2:].strip()})
            elif len(s) > 0 and len(out) < 3:
                out.append({"path": path, "quote": s})
        if out:
            break
    return out


def _extract_patterns_from_docs(text_by_path: dict[str, str]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for path, txt in text_by_path.items():
        lo = txt.lower()
        if "设计模式" not in txt and "pattern" not in lo:
            continue
        found: set[str] = set()
        for w in _PATTERN_WORDS:
            if w in lo:
                found.add(w)
        for nm in sorted(found):
            out.append({"path": path, "name": nm})
    return out


def _safe_rel_path(root: Path, p: str) -> str | None:
    if not p or p.startswith("/"):
        return None
    if "://" in p:
        return None
    try:
        pp = Path(p)
    except Exception:
        return None
    if any(part in ["..", "."] for part in pp.parts):
        return None
    resolved = (root / pp).resolve()
    try:
        resolved.relative_to(root.resolve())
    except Exception:
        return None
    if not resolved.exists() or not resolved.is_file():
        return None
    return resolved.relative_to(root).as_posix()


def _extract_entrypoint_examples(repo_root: Path, output_root: Path, project_ir: dict[str, Any], *, depth: int) -> tuple[list[dict[str, str]], list[str]]:
    examples: list[dict[str, str]] = []
    scanned: list[str] = []
    modules = list(project_ir.get("modules", []) or [])
    for m in modules:
        module_id = str(m.get("moduleId") or "")
        if not module_id:
            continue
        mp = output_root / "docs" / "_ir" / "modules" / f"{module_id}.json"
        if not mp.exists():
            continue
        try:
            mi = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            continue
        ps = mi.get("publicSurface", {}) if isinstance(mi.get("publicSurface"), dict) else {}
        entrypoints = ps.get("entrypoints") if isinstance(ps.get("entrypoints"), list) else []
        for e in entrypoints:
            if not isinstance(e, dict):
                continue
            loc = e.get("location") if isinstance(e.get("location"), dict) else {}
            rel = _safe_rel_path(repo_root, str(loc.get("path") or ""))
            if not rel:
                continue
            examples.append({"name": str(e.get("name") or ""), "signature": str(e.get("signature") or ""), "path": rel})
            scanned.append(rel)
    if depth > 0:
        key_files = _ir_guided_files(repo_root, output_root, project_ir)
        scanned.extend(key_files)
    return examples, scanned


def _ir_guided_files(repo_root: Path, output_root: Path, project_ir: dict[str, Any]) -> list[str]:
    out: list[str] = []
    modules = list(project_ir.get("modules", []) or [])
    for m in modules:
        module_id = str(m.get("moduleId") or "")
        if not module_id:
            continue
        mp = output_root / "docs" / "_ir" / "modules" / f"{module_id}.json"
        if not mp.exists():
            continue
        try:
            mi = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            continue
        ps = mi.get("publicSurface", {}) if isinstance(mi.get("publicSurface"), dict) else {}
        kfs = ps.get("keyFiles") if isinstance(ps.get("keyFiles"), list) else []
        for kf in kfs:
            if not isinstance(kf, dict):
                continue
            rel = _safe_rel_path(repo_root, str(kf.get("path") or ""))
            if rel:
                out.append(rel)
                if len(out) >= 60:
                    return out
    return out


def _summarize_facts(output_root: Path, project_ir: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    proj = project_ir.get("project", {}) if isinstance(project_ir.get("project"), dict) else {}
    name = str(proj.get("name") or "")
    tools = proj.get("build", {}).get("tools") if isinstance(proj.get("build"), dict) else []
    if not isinstance(tools, list):
        tools = []
    lines.append(f"- 项目：{name or 'unknown'} [evidence: docs/_ir/project.json]")
    lines.append(f"- 构建工具：{', '.join([str(x) for x in tools]) or 'unknown'} [evidence: docs/_ir/project.json]")
    modules = list(project_ir.get("modules", []) or [])
    lines.append(f"- 模块数：{len(modules)} [evidence: docs/_ir/project.json]")
    return lines

