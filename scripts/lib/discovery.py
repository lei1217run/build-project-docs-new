from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


_BUILD_MARKERS = ["pom.xml", "build.gradle", "build.gradle.kts", "package.json", "pyproject.toml", "go.mod", "Cargo.toml"]


def _module_id_from_roots(roots: list[str]) -> str:
    h = hashlib.sha256()
    for r in sorted(set(roots)):
        h.update(r.encode("utf-8"))
        h.update(b"\n")
    return "m-" + h.hexdigest()[:12]


def _detect_tools(module_dir: Path) -> list[str]:
    tools: list[str] = []
    if (module_dir / "pom.xml").exists() or (module_dir / "build.gradle").exists() or (module_dir / "build.gradle.kts").exists():
        tools.append("maven")
    pkg = module_dir / "package.json"
    if pkg.exists():
        tools.append("npm")
    if (module_dir / "pyproject.toml").exists():
        tools.append("uv")
    if (module_dir / "go.mod").exists():
        tools.append("go")
    if (module_dir / "Cargo.toml").exists():
        tools.append("cargo")
    return tools or ["unknown"]


def _suggest_layer(name: str) -> list[str]:
    n = name.lower()
    if any(x in n for x in ["web", "ui", "frontend", "front", "client"]):
        return ["frontend"]
    if any(x in n for x in ["common", "shared", "utils", "core", "base"]):
        return ["foundation"]
    if any(x in n for x in ["dao", "db", "data", "repository", "mapper"]):
        return ["data"]
    if "config" in n:
        return ["config"]
    return ["business"]

def _frontend_framework(module_dir: Path) -> str | None:
    pkg = module_dir / "package.json"
    if not pkg.exists():
        return None
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except Exception:
        return None
    deps = {}
    for k in ["dependencies", "devDependencies", "peerDependencies"]:
        v = data.get(k)
        if isinstance(v, dict):
            deps.update(v)
    keys = {str(k).lower() for k in deps.keys()}
    if "react" in keys or "next" in keys:
        return "react"
    if "vue" in keys or "@vue/runtime-core" in keys or "nuxt" in keys:
        return "vue"
    return None

def _has_marker(d: Path) -> bool:
    return any((d / m).exists() for m in _BUILD_MARKERS)

def _collect_candidates(repo_root: Path, ignore: set[str], max_depth: int) -> list[Path]:
    out: list[Path] = []
    def walk(base: Path, depth: int) -> None:
        if depth > max_depth:
            return
        for child in base.iterdir():
            if not child.is_dir():
                continue
            rel_first = child.relative_to(repo_root).parts[0]
            if rel_first in ignore:
                continue
            if _has_marker(child):
                out.append(child)
                continue
            walk(child, depth + 1)
    walk(repo_root, 1)
    uniq: dict[str, Path] = {}
    for p in out:
        uniq[p.resolve().as_posix()] = p
    return list(sorted(uniq.values(), key=lambda x: x.as_posix()))


def discover_modules(repo_root: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    out_root = str(config["output"]["rootDir"])
    if out_root.startswith("./"):
        out_root = out_root[2:]
    ignore = {".git", out_root}
    scan_depth = int(config.get("discovery", {}).get("maxDepth", 1))
    candidates = _collect_candidates(repo_root, ignore, max_depth=scan_depth)
    if not candidates and scan_depth == 1:
        candidates = _collect_candidates(repo_root, ignore, max_depth=2)

    modules: list[dict[str, Any]] = []
    if candidates:
        for d in sorted(candidates, key=lambda p: p.name):
            roots = [d.name]
            module_id = _module_id_from_roots(roots)
            fw = _frontend_framework(d)
            signals = [{"name": "build.tools", "value": ",".join(_detect_tools(d))}]
            if fw:
                signals.append({"name": "frontend.framework", "value": fw})
            modules.append(
                {
                    "moduleId": module_id,
                    "displayName": d.name,
                    "roots": roots,
                    "layerTags": _suggest_layer(d.name),
                    "deps": [],
                    "signals": signals,
                }
            )
        return modules

    roots = ["."]
    module_id = _module_id_from_roots(roots)
    modules.append(
        {
            "moduleId": module_id,
            "displayName": repo_root.name,
            "roots": roots,
            "layerTags": ["unknown"],
            "deps": [],
            "signals": [{"name": "build.tools", "value": ",".join(_detect_tools(repo_root))}],
        }
    )
    return modules
