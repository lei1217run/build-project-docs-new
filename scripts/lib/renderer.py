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
    claude_md_lines.append("## 模块索引")
    claude_md_lines.append("| 模块 | 层级 | 文档 |")
    claude_md_lines.append("|------|------|------|")
    for m in modules:
        name = m["displayName"]
        layer = ",".join(m.get("layerTags", []))
        link = f"[README](docs/{name}/README.md)"
        claude_md_lines.append(f"| {name} | {layer} | {link} |")
    claude_md_lines.append("")
    claude_md_lines.append("## 文档索引")
    for m in modules:
        name = m["displayName"]
        claude_md_lines.append(f"- [docs/{name}/README.md](docs/{name}/README.md)")
    if mode == "new-project":
        claude_md_lines.append("- [开发任务清单](docs/_task-list.md)")

    claude_md = _line_limit("\n".join(claude_md_lines), 150)
    _write_if_changed(output_root / config["output"]["indexFile"], claude_md)


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
    files_to_link.append("CHANGELOG.md")

    readme_lines: list[str] = []
    readme_lines.append(f"# {name}")
    readme_lines.append("")
    readme_lines.append("## 概述")
    readme_lines.append(f"- 模块：{name}")
    readme_lines.append(f"- 依赖：{', '.join(module_summary.get('deps', [])) or '-'}")
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
