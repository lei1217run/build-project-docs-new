from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_API_PATTERNS = [
    ("rest", re.compile(r"@app\.(get|post|put|delete|patch)\(\s*['\"]([^'\"]+)['\"]\s*\)")),
    ("rest", re.compile(r"APIRouter\(")),
    ("rest", re.compile(r"@RestController\b")),
    ("rest", re.compile(r"@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)\(\s*['\"]([^'\"]+)['\"]\s*\)")),
    ("rest", re.compile(r"\brouter\.(get|post|put|delete|patch)\(\s*['\"]([^'\"]+)['\"]\s*,")),
    ("rest", re.compile(r"\bapp\.(get|post|put|delete|patch)\(\s*['\"]([^'\"]+)['\"]\s*,")),
]


def _iter_source_files(repo_root: Path, roots: list[str], config: dict[str, Any]) -> list[Path]:
    excluded = set(config["incremental"].get("excludeGlobs", []))
    files: list[Path] = []
    for r in roots:
        base = (repo_root / r).resolve()
        if not base.exists():
            continue
        if base.is_file():
            files.append(base)
            continue
        for p in base.rglob("*"):
            if p.is_dir():
                continue
            rel = p.relative_to(repo_root).as_posix()
            if any(rel.startswith(x.split("/")[0]) for x in ["node_modules", ".git", ".claude"]):
                continue
            if any(rel == g for g in excluded):
                continue
            if p.suffix.lower() in [".py", ".js", ".ts", ".tsx", ".java", ".go", ".rs", ".proto"]:
                files.append(p)
            if len(files) >= 300:
                return files
    return files


def extract_module_ir(repo_root: Path, module: dict[str, Any], config: dict[str, Any], *, evidence_hash: str) -> dict[str, Any]:
    roots = list(module["roots"])
    src_files = _iter_source_files(repo_root, roots, config)

    api_items: list[dict[str, Any]] = []
    for f in src_files:
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        rel = f.relative_to(repo_root).as_posix()
        for kind, pat in _API_PATTERNS:
            for m in pat.finditer(content):
                sig = None
                if m.lastindex and m.lastindex >= 2:
                    method = m.group(1) if m.group(1) else "unknown"
                    path = m.group(2) if m.group(2) else "unknown"
                    if pat.pattern.startswith("@"):
                        sig = f"{method.upper()} {path}"
                    else:
                        sig = f"{method.upper()} {path}"
                if sig is None:
                    sig = f"{kind} detected"
                api_items.append(
                    {
                        "kind": kind,
                        "signature": sig,
                        "name": sig,
                        "evidence": [{"kind": "file", "path": rel, "note": "pattern match"}],
                    }
                )
                if len(api_items) >= 50:
                    break
            if len(api_items) >= 50:
                break
        if len(api_items) >= 50:
            break

    has_public_api: Any
    if api_items:
        has_public_api = True
    else:
        has_public_api = False

    domains: list[dict[str, Any]] = []
    if api_items:
        domains.append({"domainId": "default", "items": api_items})

    module_ir: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "generatorVersion": "0.1",
        "generatedAt": _now_iso(),
        "module": {
            "moduleId": module["moduleId"],
            "roots": roots,
            "deps": module.get("deps", []),
            "layerTags": module.get("layerTags", ["unknown"]),
            "extensions": {},
        },
        "api": {"hasPublicApi": has_public_api if has_public_api else False, "domains": domains, "extensions": {}},
        "dataModel": {"types": [], "extensions": {}},
        "config": {"items": [], "extensions": {}},
        "pitfalls": [],
        "extensions": {"evidenceHash": evidence_hash},
    }
    if has_public_api is False and not domains:
        module_ir["api"]["hasPublicApi"] = False
    return module_ir


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
