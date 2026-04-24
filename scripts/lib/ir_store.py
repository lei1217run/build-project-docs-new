from __future__ import annotations

import json
import os
import re
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


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None


def _read_toml(path: Path) -> dict[str, Any] | None:
    try:
        import tomllib  # type: ignore

        return tomllib.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None


def _detect_workspace_type(repo_root: Path) -> str:
    pyproject = repo_root / "pyproject.toml"
    if pyproject.exists():
        data = _read_toml(pyproject) or {}
        tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
        if isinstance(tool.get("uv"), dict) or (repo_root / "uv.lock").exists():
            return "uv"
        if isinstance(tool.get("poetry"), dict):
            return "poetry"
    if (repo_root / "pnpm-workspace.yaml").exists():
        return "pnpm"
    if (repo_root / "package.json").exists():
        pkg = _read_json(repo_root / "package.json") or {}
        pm = str(pkg.get("packageManager") or "").lower()
        if pm.startswith("pnpm@"):
            return "pnpm"
        if pm.startswith("yarn@"):
            return "yarn"
        return "npm"
    if (repo_root / "Cargo.toml").exists():
        data = _read_toml(repo_root / "Cargo.toml") or {}
        if isinstance(data.get("workspace"), dict):
            return "cargo-workspace"
        return "cargo"
    if (repo_root / "go.work").exists():
        return "go-work"
    if (repo_root / "go.mod").exists():
        return "go"
    if (repo_root / "pom.xml").exists():
        return "maven"
    if (repo_root / "build.gradle").exists() or (repo_root / "build.gradle.kts").exists():
        return "gradle"
    try:
        if any(repo_root.glob("*.sln")):
            return "dotnet"
    except Exception:
        pass
    if (repo_root / "CMakeLists.txt").exists():
        return "cmake"
    return "unknown"


def _package_facts(repo_root: Path, module: dict[str, Any]) -> dict[str, Any]:
    roots = list(module.get("roots", []) or [])
    base = repo_root if roots == ["."] else (repo_root / roots[0]).resolve()
    out: dict[str, Any] = {"path": base.relative_to(repo_root).as_posix() if base.exists() else str(roots[0] if roots else "."), "entrypoints": [], "exports": [], "deps": []}

    pyproject = base / "pyproject.toml"
    if pyproject.exists():
        data = _read_toml(pyproject) or {}
        project = data.get("project") if isinstance(data.get("project"), dict) else {}
        out["name"] = str(project.get("name") or module.get("displayName") or "")
        if project.get("version"):
            out["version"] = str(project.get("version"))
        scripts = project.get("scripts") if isinstance(project.get("scripts"), dict) else {}
        for k, v in scripts.items():
            out["entrypoints"].append({"kind": "cli", "name": str(k), "signature": str(v), "evidence": [{"kind": "config", "path": pyproject.relative_to(repo_root).as_posix(), "note": "project.scripts"}]})
        out["language"] = "python"
        return out

    pkg = base / "package.json"
    if pkg.exists():
        data = _read_json(pkg) or {}
        out["name"] = str(data.get("name") or module.get("displayName") or "")
        if data.get("version"):
            out["version"] = str(data.get("version"))
        b = data.get("bin")
        if isinstance(b, str):
            out["entrypoints"].append({"kind": "cli", "name": out["name"] or "bin", "signature": b, "evidence": [{"kind": "config", "path": pkg.relative_to(repo_root).as_posix(), "note": "package.json bin"}]})
        if isinstance(b, dict):
            for k, v in b.items():
                out["entrypoints"].append({"kind": "cli", "name": str(k), "signature": str(v), "evidence": [{"kind": "config", "path": pkg.relative_to(repo_root).as_posix(), "note": "package.json bin"}]})
        out["language"] = "js-ts"
        return out

    cargo = base / "Cargo.toml"
    if cargo.exists():
        data = _read_toml(cargo) or {}
        pkgd = data.get("package") if isinstance(data.get("package"), dict) else {}
        out["name"] = str(pkgd.get("name") or module.get("displayName") or "")
        if pkgd.get("version"):
            out["version"] = str(pkgd.get("version"))
        out["language"] = "rust"
        return out

    gomod = base / "go.mod"
    if gomod.exists():
        try:
            m = re.search(r"^module\\s+(.+)$", gomod.read_text(encoding="utf-8", errors="ignore"), re.M)
            out["name"] = str(m.group(1).strip()) if m else str(module.get("displayName") or "")
        except Exception:
            out["name"] = str(module.get("displayName") or "")
        out["language"] = "go"
        return out

    pom = base / "pom.xml"
    if pom.exists():
        try:
            txt = pom.read_text(encoding="utf-8", errors="ignore")
            a = re.search(r"<artifactId>([^<]+)</artifactId>", txt)
            out["name"] = str(a.group(1).strip()) if a else str(module.get("displayName") or "")
        except Exception:
            out["name"] = str(module.get("displayName") or "")
        out["language"] = "java"
        return out

    try:
        if any(base.glob("*.sln")) or any(base.glob("*.csproj")):
            out["language"] = "csharp"
            sln = next(iter(base.glob("*.sln")), None)
            if sln is not None:
                out["name"] = str(sln.stem)
                return out
            csproj = next(iter(base.glob("*.csproj")), None)
            if csproj is not None and csproj.exists():
                try:
                    import xml.etree.ElementTree as ET

                    root = ET.fromstring(csproj.read_text(encoding="utf-8", errors="ignore"))
                    vals: dict[str, str] = {}
                    for el in root.iter():
                        tag = str(el.tag).split("}", 1)[-1]
                        if tag in ["AssemblyName", "PackageId", "Version", "TargetFramework", "TargetFrameworks", "OutputType"]:
                            if el.text and str(el.text).strip():
                                vals[tag] = str(el.text).strip()
                    out["name"] = vals.get("PackageId") or vals.get("AssemblyName") or str(module.get("displayName") or "")
                    if vals.get("Version"):
                        out["version"] = vals["Version"]
                    if vals.get("OutputType"):
                        out["extensions"] = {"dotnet": {"outputType": vals.get("OutputType")}}
                except Exception:
                    out["name"] = str(module.get("displayName") or "")
                return out
    except Exception:
        pass

    cmake = base / "CMakeLists.txt"
    if cmake.exists():
        out["language"] = "cpp"
        out["name"] = str(module.get("displayName") or "")
        try:
            txt = cmake.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r"\bproject\s*\(\s*([A-Za-z_][A-Za-z0-9_\-]*)", txt, re.I)
            if m:
                out["name"] = str(m.group(1))
        except Exception:
            pass
        return out

    out["name"] = str(module.get("displayName") or "")
    out["language"] = "unknown"
    return out


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
    if any(t in tools for t in ["dotnet"]):
        tools.discard("unknown")
    if any(t in tools for t in ["cmake"]):
        tools.discard("unknown")

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
        "workspace": {"type": _detect_workspace_type(repo_root), "packages": []},
        "entrypoints": [],
        "extensions": {},
    }

    for m in modules:
        pkg = _package_facts(repo_root, m)
        core["workspace"]["packages"].append(pkg)
        for ep in list(pkg.get("entrypoints", []) or []):
            core["entrypoints"].append(ep)
        core["modules"].append(
            {
                "moduleId": m["moduleId"],
                "displayName": m["displayName"],
                "roots": m["roots"],
                "layerTags": m.get("layerTags", ["unknown"]),
                "deps": m.get("deps", []),
                "signals": m.get("signals", []),
                "extensions": dict(m.get("extensions") or {}),
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
