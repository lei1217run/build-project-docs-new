from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if path.exists():
        old = path.read_text(encoding="utf-8")
        if old == content:
            return
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def ir_root(output_root: Path) -> Path:
    return output_root / "docs" / "_ir"

def load_project_ir(output_root: Path) -> dict[str, Any] | None:
    p = ir_root(output_root) / "project.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def load_module_ir(output_root: Path, module_id: str) -> dict[str, Any] | None:
    p = ir_root(output_root) / "modules" / f"{module_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def write_project_ir(repo_root: Path, output_root: Path, modules: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    tools: set[str] = set()
    for m in modules:
        sig = ",".join([s.get("value", "") for s in m.get("signals", []) if s.get("name") == "build.tools"])
        for t in sig.split(","):
            tt = t.strip()
            if tt:
                tools.add(tt)
    if not tools:
        tools.add("unknown")

    old = load_project_ir(output_root)
    core: dict[str, Any] = {
        "project": {
            "name": repo_root.name,
            "repoRoot": ".",
            "build": {"tools": sorted(tools)},
            "environments": {"configPriority": ["cli", "yaml", "env"], "secretsPolicy": "no_plaintext_secrets_in_docs"},
            "extensions": {},
        },
        "modules": [],
        "extensions": {},
    }

    for m in modules:
        core["modules"].append(
            {
                "moduleId": m["moduleId"],
                "displayName": m["displayName"],
                "roots": m["roots"],
                "layerTags": m.get("layerTags", ["unknown"]),
                "deps": m.get("deps", []),
                "signals": m.get("signals", []),
                "extensions": {},
            }
        )

    if old:
        old_core = {k: v for k, v in old.items() if k not in ["schemaVersion", "generatorVersion", "generatedAt"]}
        if old_core == core:
            return old

    project_ir: dict[str, Any] = {"schemaVersion": "1.0.0", "generatorVersion": "0.1", "generatedAt": _now_iso(), **core}
    _atomic_write_json(ir_root(output_root) / "project.json", project_ir)
    return project_ir


def write_module_ir(output_root: Path, module_ir: dict[str, Any]) -> None:
    module_id = module_ir["module"]["moduleId"]
    _atomic_write_json(ir_root(output_root) / "modules" / f"{module_id}.json", module_ir)


def write_project_ir_payload(output_root: Path, project_ir: dict[str, Any]) -> dict[str, Any]:
    _atomic_write_json(ir_root(output_root) / "project.json", project_ir)
    return project_ir
