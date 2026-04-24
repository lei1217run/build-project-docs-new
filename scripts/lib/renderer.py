from __future__ import annotations

from pathlib import Path
from typing import Any


def _write_if_changed(path: Path, content: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = content.rstrip() + "\n"
    if path.exists():
        old = path.read_text(encoding="utf-8", errors="ignore")
        if old == new:
            return False
    path.write_text(new, encoding="utf-8")
    return True


def _line_limit(content: str, limit: int) -> str:
    lines = content.splitlines()
    if len(lines) <= limit:
        return content
    return "\n".join(lines[: max(0, limit - 1)] + ["..."])


def render_project(output_root: Path, project_ir: dict[str, Any], config: dict[str, Any], mode: str = "docs") -> None:
    docs_root = output_root / "docs"
    docs_root.mkdir(parents=True, exist_ok=True)

    modules = list(project_ir.get("modules", []))
    modules = sorted(modules, key=lambda m: m.get("displayName", ""))

    module_irs: dict[str, dict[str, Any]] = {}
    for m in modules:
        module_id = str(m.get("moduleId") or "")
        if not module_id:
            continue
        p = output_root / "docs" / "_ir" / "modules" / f"{module_id}.json"
        if not p.exists():
            continue
        try:
            module_irs[module_id] = __import__("json").loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue

    modules_lines: list[str] = []
    modules_lines.append("# 模块索引")
    modules_lines.append("")
    modules_lines.append("| 模块 | 层级 | 文档 |")
    modules_lines.append("|------|------|------|")
    for m in modules:
        name = str(m.get("displayName") or "")
        if not name:
            continue
        module_id = str(m.get("moduleId") or "")
        mi = module_irs.get(module_id) if module_id else None
        if isinstance(mi, dict):
            layer = ",".join(list(mi.get("module", {}).get("layerTags", []) or [])) or "unknown"
        else:
            layer = ",".join(list(m.get("layerTags", []) or [])) or "unknown"
        link = f"[README]({name}/README.md)"
        modules_lines.append(f"| {name} | {layer} | {link} |")
    _write_if_changed(docs_root / "_modules.md", "\n".join(modules_lines))

    claude_md_lines: list[str] = []
    claude_md_lines.append(f"# {project_ir['project']['name']}")
    claude_md_lines.append("")
    claude_md_lines.append("## 概述")
    claude_md_lines.append("该文档由 build-project-docs-new 生成，用于分层加载项目知识。")
    claude_md_lines.append("")
    claude_md_lines.append("## 构建")
    tools = ", ".join(project_ir["project"]["build"]["tools"])
    claude_md_lines.append(f"- 构建工具：{tools}")
    claude_md_lines.append("")
    claude_md_lines.append("## 索引")
    claude_md_lines.append(f"- 模块索引：[{len(modules)} 个模块](docs/_modules.md)")
    claude_md_lines.append("- [全维度报告](docs/_report.md)")
    if module_irs:
        claude_md_lines.append("- [facts 汇总](docs/_facts.md)")
    if mode == "new-project":
        claude_md_lines.append("- [开发任务清单](docs/_task-list.md)")
    claude_md_lines.append("")

    claude_md = _line_limit("\n".join(claude_md_lines), 150)
    _write_if_changed(output_root / config["output"]["indexFile"], claude_md)

    if module_irs:
        facts_lines: list[str] = []
        facts_lines.append("# facts 汇总")
        facts_lines.append("")
        facts_lines.append("## 核心模块候选")
        scored: list[tuple[int, str, dict[str, Any]]] = []
        for m in modules:
            module_id = str(m.get("moduleId") or "")
            mi = module_irs.get(module_id)
            if not mi:
                continue
            ps = mi.get("publicSurface", {}) if isinstance(mi.get("publicSurface"), dict) else {}
            exports = ps.get("exports") if isinstance(ps.get("exports"), list) else []
            entrypoints = ps.get("entrypoints") if isinstance(ps.get("entrypoints"), list) else []
            key_files = ps.get("keyFiles") if isinstance(ps.get("keyFiles"), list) else []
            score = len(exports) * 3 + len(entrypoints) * 5 + min(len(key_files), 20)
            scored.append((score, str(m.get("displayName") or ""), mi))
        scored.sort(key=lambda x: (-x[0], x[1]))
        for score, name, mi in scored[:10]:
            module_id = str(mi.get("module", {}).get("moduleId") or "")
            ps = mi.get("publicSurface", {}) if isinstance(mi.get("publicSurface"), dict) else {}
            exports_n = len(ps.get("exports") or [])
            entry_n = len(ps.get("entrypoints") or [])
            key_n = len(ps.get("keyFiles") or [])
            layer = ",".join(list(mi.get("module", {}).get("layerTags", []) or [])) or "unknown"
            facts_lines.append(f"- {name}（layer={layer}，exports={exports_n}，entrypoints={entry_n}，keyFiles={key_n}）")
            facts_lines.append(f"  - [模块 README](./{name}/README.md)")
            if module_id:
                facts_lines.append(f"  - IR: `_ir/modules/{module_id}.json`")
        facts_lines.append("")
        facts_lines.append("## 模块 facts 索引")
        for m in modules:
            name = str(m.get("displayName") or "")
            module_id = str(m.get("moduleId") or "")
            mi = module_irs.get(module_id)
            if not mi:
                continue
            facts_lines.append(f"- {name}")
            base = f"./{name}"
            for fn in ["facts-overview.md", "facts-exports.md", "facts-entrypoints.md", "facts-keyfiles.md", "facts-types.md", "facts-config.md", "facts-extensibility.md"]:
                p = docs_root / name / fn
                if p.exists():
                    facts_lines.append(f"  - [{fn}]({base}/{fn})")
        _write_if_changed(docs_root / "_facts.md", _line_limit("\n".join(facts_lines), 400))


def render_module(output_root: Path, module_summary: dict[str, Any], module_ir: dict[str, Any] | None, mode: str = "docs") -> list[str]:
    docs_root = output_root / "docs"
    name = module_summary["displayName"]
    module_dir = docs_root / name
    module_dir.mkdir(parents=True, exist_ok=True)

    has_api = False
    if module_ir is not None:
        has_api_val = module_ir.get("api", {}).get("hasPublicApi")
        has_api = bool(has_api_val is True)

    files_to_link: list[str] = []
    if has_api:
        if mode == "new-project":
            files_to_link.extend(["api-default.md", "data-model.md", "dev-checklist.md"])
        else:
            files_to_link.extend(["api-default.md", "data-model.md", "pitfalls.md"])
    facts_files: list[str] = []
    if module_ir is not None:
        ps = module_ir.get("publicSurface", {}) if isinstance(module_ir.get("publicSurface"), dict) else {}
        exports = ps.get("exports") if isinstance(ps.get("exports"), list) else []
        entrypoints = ps.get("entrypoints") if isinstance(ps.get("entrypoints"), list) else []
        key_files = ps.get("keyFiles") if isinstance(ps.get("keyFiles"), list) else []
        types = ps.get("types") if isinstance(ps.get("types"), list) else []

        if exports or entrypoints or key_files or types:
            facts_files.append("facts-overview.md")
        if exports:
            facts_files.append("facts-exports.md")
        if entrypoints:
            facts_files.append("facts-entrypoints.md")
        if key_files:
            facts_files.append("facts-keyfiles.md")
        if types:
            facts_files.append("facts-types.md")

        exts = module_ir.get("module", {}).get("extensions", {}) if isinstance(module_ir.get("module", {}).get("extensions"), dict) else {}
        layer_evidence = exts.get("layerEvidence") if isinstance(exts.get("layerEvidence"), list) else []
        if layer_evidence:
            facts_files.append("facts-config.md")

    files_to_link.extend(facts_files)
    files_to_link.append("CHANGELOG.md")

    readme_lines: list[str] = []
    readme_lines.append(f"# {name}")
    readme_lines.append("")
    readme_lines.append("## 概述")
    readme_lines.append(f"- 模块：{name}")
    readme_lines.append(f"- 依赖：{', '.join(module_summary.get('deps', [])) or '-'}")
    layer = ",".join(module_summary.get("layerTags", []) or []) or "unknown"
    readme_lines.append(f"- 分层：{layer}")
    readme_lines.append("")
    if module_ir is not None:
        ps = module_ir.get("publicSurface", {}) if isinstance(module_ir.get("publicSurface"), dict) else {}
        exports_n = len(ps.get("exports") or [])
        entry_n = len(ps.get("entrypoints") or [])
        key_n = len(ps.get("keyFiles") or [])
        types_n = len(ps.get("types") or [])
        readme_lines.append("## 核心能力（facts 概览）")
        readme_lines.append(f"- exports：{exports_n}")
        readme_lines.append(f"- entrypoints：{entry_n}")
        readme_lines.append(f"- key files：{key_n}")
        readme_lines.append(f"- types：{types_n}")
        readme_lines.append("")
    readme_lines.append("## 详细文档")
    for fn in files_to_link:
        readme_lines.append(f"- [{fn}]({fn})")

    readme = _line_limit("\n".join(readme_lines), 200)
    _write_if_changed(module_dir / "README.md", readme)

    if has_api:
        _write_if_changed(module_dir / "api-default.md", f"# {name} API\n")
        _write_if_changed(module_dir / "data-model.md", f"# {name} 数据模型\n")
        if mode == "new-project":
            checklist = []
            if module_ir is not None:
                checklist = list(module_ir.get("extensions", {}).get("devChecklistItems", []) or [])
            if not checklist:
                checklist = ["[ ] API 实现", "[ ] 数据模型实现", "[ ] 单元测试"]
            body = "# {name} 开发清单\n\n".format(name=name) + "\n".join(f"- {x}" for x in checklist) + "\n"
            _write_if_changed(module_dir / "dev-checklist.md", body)
        else:
            _write_if_changed(module_dir / "pitfalls.md", f"# {name} 坑点\n")

    changelog_path = module_dir / "CHANGELOG.md"
    if not changelog_path.exists():
        _write_if_changed(changelog_path, f"# Changelog - {name}\n\n> 模块变更历史。最新变更在最上方。\n\n---\n")

    if module_ir is not None and facts_files:
        ps = module_ir.get("publicSurface", {}) if isinstance(module_ir.get("publicSurface"), dict) else {}
        exports = ps.get("exports") if isinstance(ps.get("exports"), list) else []
        entrypoints = ps.get("entrypoints") if isinstance(ps.get("entrypoints"), list) else []
        key_files = ps.get("keyFiles") if isinstance(ps.get("keyFiles"), list) else []
        types = ps.get("types") if isinstance(ps.get("types"), list) else []
        exts = module_ir.get("module", {}).get("extensions", {}) if isinstance(module_ir.get("module", {}).get("extensions"), dict) else {}
        layer_evidence = exts.get("layerEvidence") if isinstance(exts.get("layerEvidence"), list) else []
        lang_signals = exts.get("languageSignals") if isinstance(exts.get("languageSignals"), dict) else {}

        if "facts-overview.md" in facts_files:
            lines: list[str] = []
            lines.append(f"# {name} facts overview")
            lines.append("")
            lines.append("## facets")
            lines.append(f"- exports: {len(exports)}")
            lines.append(f"- entrypoints: {len(entrypoints)}")
            lines.append(f"- key files: {len(key_files)}")
            lines.append(f"- types: {len(types)}")
            if lang_signals:
                lines.append("")
                lines.append("## language signals")
                for k in sorted(lang_signals.keys()):
                    lines.append(f"- {k}: {lang_signals.get(k)}")
            _write_if_changed(module_dir / "facts-overview.md", _line_limit("\n".join(lines), 250))

        if "facts-exports.md" in facts_files:
            lines = [f"# {name} exports", "", "## exports"]
            for e in exports:
                nm = str(e.get("name") or "")
                loc = e.get("location") if isinstance(e.get("location"), dict) else {}
                p = str(loc.get("path") or "")
                lines.append(f"- {nm} ({p})")
            _write_if_changed(module_dir / "facts-exports.md", _line_limit("\n".join(lines), 800))

        if "facts-entrypoints.md" in facts_files:
            lines = [f"# {name} entrypoints", "", "## entrypoints"]
            for e in entrypoints:
                nm = str(e.get("name") or "")
                sig = str(e.get("signature") or "")
                lines.append(f"- {nm}: {sig}")
            _write_if_changed(module_dir / "facts-entrypoints.md", _line_limit("\n".join(lines), 600))

        if "facts-keyfiles.md" in facts_files:
            lines = [f"# {name} key files", "", "## key files"]
            for kf in key_files:
                p = str(kf.get("path") or "")
                sc = str(kf.get("score") or "")
                lines.append(f"- {p} (score={sc})")
            _write_if_changed(module_dir / "facts-keyfiles.md", _line_limit("\n".join(lines), 800))

        if "facts-types.md" in facts_files:
            lines = [f"# {name} types", "", "## types"]
            for t in types:
                nm = str(t.get("name") or "")
                kd = str(t.get("kind") or "")
                lines.append(f"- {nm} ({kd})")
            _write_if_changed(module_dir / "facts-types.md", _line_limit("\n".join(lines), 600))

        if "facts-config.md" in facts_files:
            lines = [f"# {name} layering evidence", "", "## layer evidence"]
            for ev in layer_evidence:
                p = str(ev.get("path") or "")
                note = str(ev.get("note") or "")
                lines.append(f"- {p} {note}".rstrip())
            _write_if_changed(module_dir / "facts-config.md", _line_limit("\n".join(lines), 600))

    return files_to_link


def render_all(output_root: Path, project_ir: dict[str, Any], config: dict[str, Any], mode: str = "docs") -> None:
    render_project(output_root, project_ir, config, mode=mode)
    modules = list(project_ir.get("modules", []))
    modules = sorted(modules, key=lambda m: m.get("displayName", ""))
    for m in modules:
        module_id = m["moduleId"]
        module_ir_path = output_root / "docs" / "_ir" / "modules" / f"{module_id}.json"
        module_ir = None
        if module_ir_path.exists():
            module_ir = __import__("json").loads(module_ir_path.read_text(encoding="utf-8"))
        render_module(output_root, m, module_ir, mode=mode)
