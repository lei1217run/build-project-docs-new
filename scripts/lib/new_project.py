from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _module_id(name: str) -> str:
    h = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    return f"m-{h}"


def parse_prd_text(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    project_name = "new-project"
    summary = ""
    modules: list[str] = []
    missing: list[str] = []

    for ln in lines:
        s = ln.strip()
        if s.startswith("# ") and project_name == "new-project":
            project_name = s[2:].strip() or project_name
            continue
        if s.startswith("项目名:") or s.startswith("项目名称:"):
            project_name = s.split(":", 1)[1].strip() or project_name
            continue
        if not summary and s and not s.startswith("#"):
            summary = s
        if s.startswith("- "):
            item = s[2:].strip()
            if any(k in item.lower() for k in ["模块", "服务", "管理", "中心", "引擎", "api", "module", "service"]):
                name = re.split(r"[:：,， ]", item)[0].strip()
                if name:
                    modules.append(name)

    if not modules:
        modules = ["core"]
        missing.append("未识别明确模块，已使用默认模块 core")
    if not summary:
        summary = "来自 PRD 的新项目规划"

    return {
        "schemaVersion": "1.0.0",
        "generatedAt": _now_iso(),
        "projectName": project_name,
        "summary": summary,
        "modules": sorted(list(dict.fromkeys(modules))),
        "missingInfo": missing,
    }


def parse_prd_input(prd_input: str, repo_root: Path) -> tuple[dict[str, Any], str]:
    p = Path(prd_input).expanduser()
    if not p.is_absolute():
        p = (repo_root / p).resolve()
    if p.exists() and p.is_file():
        text = p.read_text(encoding="utf-8", errors="ignore")
        return parse_prd_text(text), text
    return parse_prd_text(prd_input), prd_input


def _stack_tools(stack: str) -> list[str]:
    if stack == "spring":
        return ["maven"]
    if stack == "fastapi":
        return ["uv"]
    if stack == "nestjs":
        return ["npm"]
    if stack == "go":
        return ["go"]
    if stack == "rust":
        return ["cargo"]
    if stack in ["react", "vue"]:
        return ["npm"]
    return ["unknown"]


def build_plan_from_prd(prd_ir: dict[str, Any], stack: str) -> dict[str, Any]:
    modules = list(prd_ir["modules"])
    architecture = {
        "projectType": "multi" if len(modules) > 3 else "single",
        "modulePlan": [],
    }
    tasks: list[dict[str, Any]] = []
    module_docs_plan: list[dict[str, Any]] = []

    stack_item = _stack_check_item(stack)
    for i, m in enumerate(modules, start=1):
        architecture["modulePlan"].append({"moduleId": _module_id(m), "displayName": m, "roles": ["business"], "deps": []})
        tasks.extend(
            [
                {"taskId": f"{i}.1", "title": f"{m}: API 设计", "priority": "P0", "artifacts": [f"docs/{m}/api-default.md"], "deps": []},
                {"taskId": f"{i}.2", "title": f"{m}: 数据模型", "priority": "P0", "artifacts": [f"docs/{m}/data-model.md"], "deps": [f"{i}.1"]},
                {"taskId": f"{i}.3", "title": f"{m}: 开发清单", "priority": "P0", "artifacts": [f"docs/{m}/dev-checklist.md"], "deps": [f"{i}.1", f"{i}.2"]},
            ]
        )
        module_docs_plan.append(
            {
                "moduleId": _module_id(m),
                "displayName": m,
                "apiDomains": ["default"],
                "dataModelTypes": ["MainModel"],
                "devChecklistItems": [
                    f"[ ] {m} API 路由定义",
                    f"[ ] {m} 请求/响应模型定义",
                    f"[ ] {m} Service 与错误处理",
                    f"[ ] {stack_item}",
                ],
            }
        )

    return {
        "schemaVersion": "1.0.0",
        "generatedAt": _now_iso(),
        "stack": stack,
        "projectName": prd_ir["projectName"],
        "summary": prd_ir["summary"],
        "architecture": architecture,
        "taskList": {"tasks": tasks},
        "moduleDocsPlan": module_docs_plan,
        "tools": _stack_tools(stack),
    }


def _stack_check_item(stack: str) -> str:
    if stack == "spring":
        return "Spring: Controller + Service + ExceptionHandler 规范"
    if stack == "fastapi":
        return "FastAPI: APIRouter + Pydantic Model + Depends 规范"
    if stack == "nestjs":
        return "NestJS: Module + Controller + Provider + DTO 规范"
    if stack == "go":
        return "Go: 标准库 http/chi/gin 路由与错误处理规范（择一）"
    if stack == "rust":
        return "Rust: axum/actix 路由与错误处理规范（择一）"
    if stack == "react":
        return "React: Router + 状态管理 + API Client 规范"
    if stack == "vue":
        return "Vue: Router + Pinia + API Client 规范"
    return "通用模板规范"


def plan_to_project_ir(plan: dict[str, Any]) -> dict[str, Any]:
    modules = []
    is_frontend = plan["stack"] in ["react", "vue"]
    layer_hints = ["frontend"] if is_frontend else ["business"]
    for m in plan["moduleDocsPlan"]:
        name = m["displayName"]
        modules.append(
            {
                "moduleId": m["moduleId"],
                "displayName": name,
                "roots": [name],
                "layerTags": ["unknown"],
                "deps": [],
                "signals": [{"name": "template.stack", "value": plan["stack"]}],
                "extensions": {"plannedOnly": True, "layerHints": layer_hints},
            }
        )

    return {
        "schemaVersion": "1.0.0",
        "generatorVersion": "0.1",
        "generatedAt": _now_iso(),
        "project": {
            "name": plan["projectName"],
            "repoRoot": ".",
            "build": {"tools": plan["tools"]},
            "environments": {"configPriority": ["cli", "yaml", "env"], "secretsPolicy": "no_plaintext_secrets_in_docs"},
            "extensions": {"stack": plan["stack"], "summary": plan["summary"]},
        },
        "modules": modules,
        "extensions": {"projectType": plan["architecture"]["projectType"]},
    }


def plan_to_module_ir(plan: dict[str, Any], module_plan: dict[str, Any]) -> dict[str, Any]:
    is_frontend = plan["stack"] in ["react", "vue"]
    layer_hints = ["frontend"] if is_frontend else ["business"]
    return {
        "schemaVersion": "1.0.0",
        "generatorVersion": "0.1",
        "generatedAt": _now_iso(),
        "module": {
            "moduleId": module_plan["moduleId"],
            "roots": [module_plan["displayName"]],
            "deps": [],
            "layerTags": ["unknown"],
            "extensions": {"plannedOnly": True, "layerHints": layer_hints},
        },
        "api": {
            "hasPublicApi": True,
            "domains": [
                {
                    "domainId": "default",
                    "items": [
                        {
                            "kind": "rest",
                            "signature": "GET /api/v1/health",
                            "name": "health",
                            "evidence": [{"kind": "pattern", "path": "PRD", "note": "planned endpoint"}],
                        }
                    ],
                }
            ],
            "extensions": {"stack": plan["stack"]},
        },
        "dataModel": {
            "types": [{"name": "MainModel", "kind": "dto", "evidence": [{"kind": "pattern", "path": "PRD", "note": "planned model"}]}],
            "extensions": {},
        },
        "config": {"items": [], "extensions": {}},
        "pitfalls": [],
        "extensions": {"plannedOnly": True, "devChecklistItems": list(module_plan.get("devChecklistItems", []))},
    }


def write_prd_and_plan_ir(output_root: Path, prd_ir: dict[str, Any], plan: dict[str, Any]) -> None:
    ir_dir = output_root / "docs" / "_ir"
    ir_dir.mkdir(parents=True, exist_ok=True)
    (ir_dir / "prd.json").write_text(json.dumps(prd_ir, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ir_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_new_project_task_list(output_root: Path, plan: dict[str, Any]) -> None:
    p = output_root / "docs" / "_task-list.md"
    lines: list[str] = ["# 开发任务清单", ""]
    for t in plan["taskList"]["tasks"]:
        deps = ",".join(t.get("deps", [])) or "-"
        lines.append(f"- {t['taskId']} | {t['title']} | {t['priority']} | deps: {deps}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
