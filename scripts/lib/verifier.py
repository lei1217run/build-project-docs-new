from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_MD_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
_PLACEHOLDER_RE = re.compile(r"(?i)\b(TBD|TO\s*DO|需查看|placeholder)\b")
_LOW_CONF_SECRET_WORD_RE = re.compile(r"(?i)\b(password|passwd|token|secret|api[_-]?key|private[_-]?key|bearer)\b")
_PEM_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z0-9 ]*PRIVATE KEY-----")
_JWT_RE = re.compile(r"\beyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\b")
_PASSWORD_ASSIGN_RE = re.compile(r"(?i)\b(password|passwd|pwd)\s*[:=]\s*([^\s;]{4,})")


def verify_all(
    repo_root: Path,
    output_root: Path,
    project_ir: dict[str, Any],
    config: dict[str, Any],
    mode: str = "docs",
    *,
    only_modules: set[str] | None = None,
) -> dict[str, Any]:
    blocking: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    sec_mode = str(config.get("security", {}).get("redactionMode", "block"))

    modules = list(project_ir.get("modules", []) or [])
    if only_modules is not None:
        only = set(only_modules)
        modules = [m for m in modules if str(m.get("displayName", "")) in only]

    claude = output_root / config["output"]["indexFile"]
    if not output_root.exists():
        blocking.append({"rule": "V1-STRUCT-001", "msg": "output root missing", "path": str(output_root)})

    if not claude.exists():
        blocking.append({"rule": "V1-STRUCT-002", "msg": "CLAUDE.md missing", "path": str(claude)})
    else:
        lines = claude.read_text(encoding="utf-8", errors="ignore").splitlines()
        if len(lines) > 150:
            blocking.append({"rule": "V1-STRUCT-002", "msg": "CLAUDE.md line limit exceeded", "path": str(claude)})
        _check_secrets_and_links(claude, output_root, blocking, warnings, sec_mode)
        claude_links = set(_extract_local_link_targets("\n".join(lines)))
        if "docs/_modules.md" not in claude_links:
            blocking.append({"rule": "V1-STRUCT-004", "msg": "CLAUDE.md missing modules index link", "path": str(claude), "missing": "docs/_modules.md"})
        if mode == "new-project":
            if "docs/_task-list.md" not in claude_links:
                blocking.append({"rule": "V1-NEW-001", "msg": "CLAUDE.md missing task-list link", "path": str(claude), "missing": "docs/_task-list.md"})
            if not (output_root / "docs" / "_task-list.md").exists():
                blocking.append({"rule": "V1-NEW-002", "msg": "task-list missing", "path": str(output_root / 'docs' / '_task-list.md')})

    docs_root = output_root / "docs"
    modules_index = docs_root / "_modules.md"
    if modules:
        if not modules_index.exists():
            blocking.append({"rule": "V1-STRUCT-006", "msg": "modules index missing", "path": str(modules_index)})
        else:
            _check_secrets_and_links(modules_index, output_root, blocking, warnings, sec_mode)
            text = modules_index.read_text(encoding="utf-8", errors="ignore")
            targets = set(_extract_local_link_targets(text))
            for m in modules:
                name = str(m.get("displayName") or "")
                if not name:
                    continue
                expected = f"{name}/README.md"
                if expected not in targets and f"./{expected}" not in targets and f"docs/{expected}" not in targets:
                    blocking.append({"rule": "V1-STRUCT-006", "msg": "modules index missing module link", "path": str(modules_index), "missing": expected})

    for m in modules:
        name = m.get("displayName")
        if not name:
            continue
        module_dir = docs_root / name
        readme = module_dir / "README.md"
        if not readme.exists():
            blocking.append({"rule": "V1-STRUCT-003", "msg": "module README missing", "path": str(readme)})
            continue
        readme_text = readme.read_text(encoding="utf-8", errors="ignore")
        readme_lines = readme_text.splitlines()
        if len(readme_lines) > 200:
            blocking.append({"rule": "V1-STRUCT-003", "msg": "module README line limit exceeded", "path": str(readme)})
        _check_secrets_and_links(readme, output_root, blocking, warnings, sec_mode)

        readme_links = set(_extract_local_link_targets(readme_text))
        for f in sorted(module_dir.glob("*.md")):
            if f.name in ["README.md", "CHANGELOG.md"]:
                continue
            if f.name not in readme_links:
                blocking.append({"rule": "V1-STRUCT-005", "msg": "README missing link to file", "path": str(readme), "missing": f.name})

        module_id = m.get("moduleId")
        module_ir_path = output_root / "docs" / "_ir" / "modules" / f"{module_id}.json"
        has_public_api = None
        module_ir = None
        if module_id and module_ir_path.exists():
            module_ir = __import__("json").loads(module_ir_path.read_text(encoding="utf-8"))
            has_public_api = module_ir.get("api", {}).get("hasPublicApi")

        if isinstance(module_ir, dict):
            layer_tags = list(module_ir.get("module", {}).get("layerTags", []) or [])
            exts = module_ir.get("module", {}).get("extensions", {}) if isinstance(module_ir.get("module", {}).get("extensions"), dict) else {}
            layer_ev = exts.get("layerEvidence") if isinstance(exts.get("layerEvidence"), list) else []
            if any(t != "unknown" for t in layer_tags):
                if not layer_ev:
                    blocking.append({"rule": "V1-LAYER-001", "msg": "layerTags has no evidence", "path": str(module_ir_path)})

        if has_public_api is True:
            api_files = list(module_dir.glob("api-*.md"))
            if not api_files:
                blocking.append({"rule": "V1-API-001", "msg": "api docs missing", "path": str(module_dir)})
            if not (module_dir / "data-model.md").exists():
                blocking.append({"rule": "V1-API-002", "msg": "data-model missing", "path": str(module_dir / "data-model.md")})
            if mode == "new-project":
                if not (module_dir / "dev-checklist.md").exists():
                    blocking.append({"rule": "V1-API-004", "msg": "dev-checklist missing", "path": str(module_dir / "dev-checklist.md")})
            else:
                if not (module_dir / "pitfalls.md").exists():
                    blocking.append({"rule": "V1-API-003", "msg": "pitfalls missing", "path": str(module_dir / "pitfalls.md")})

        for f in sorted(module_dir.glob("*.md")):
            _check_secrets_and_links(f, output_root, blocking, warnings, sec_mode)

        if (module_dir / "CHANGELOG.md").exists():
            cl = (module_dir / "CHANGELOG.md").read_text(encoding="utf-8", errors="ignore")
            if _PLACEHOLDER_RE.search(cl):
                blocking.append({"rule": "V1-CHANGELOG-002", "msg": "placeholder in CHANGELOG", "path": str(module_dir / "CHANGELOG.md")})
        else:
            warnings.append({"rule": "V1-CHANGELOG-001", "msg": "CHANGELOG missing", "path": str(module_dir / "CHANGELOG.md")})

    return {"blockingFailures": len(blocking), "warnings": len(warnings), "blocking": blocking, "warning": warnings}


def sanitize_output(output_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    mode = str(config.get("security", {}).get("redactionMode", "block"))
    if mode != "redact":
        return {"ok": True, "mode": mode, "redactedFiles": 0}

    changed: list[str] = []
    for p in sorted(output_root.rglob("*.md")):
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        redacted = _redact_high_confidence_secrets(text)
        if redacted != text:
            try:
                p.write_text(redacted, encoding="utf-8")
                rel = p.relative_to(output_root).as_posix()
                changed.append(rel)
            except Exception:
                continue

    return {"ok": True, "mode": mode, "redactedFiles": len(changed), "files": changed[:50]}


def _redact_high_confidence_secrets(text: str) -> str:
    out = _PEM_PRIVATE_KEY_RE.sub("<REDACTED:PRIVATE_KEY>", text)
    out = _JWT_RE.sub("<REDACTED:JWT>", out)

    def repl_pw(m: re.Match[str]) -> str:
        k = m.group(1)
        return f"{k}=<REDACTED>"

    out = _PASSWORD_ASSIGN_RE.sub(repl_pw, out)
    return out


def _high_confidence_secret_types(text: str) -> list[str]:
    out: list[str] = []
    if _PEM_PRIVATE_KEY_RE.search(text):
        out.append("pem-private-key")
    if _JWT_RE.search(text):
        out.append("jwt")
    if _PASSWORD_ASSIGN_RE.search(text):
        out.append("password-assign")
    return out


def _check_secrets_and_links(path: Path, output_root: Path, blocking: list[dict[str, Any]], warnings: list[dict[str, Any]], sec_mode: str) -> None:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if sec_mode != "off":
        hi = _high_confidence_secret_types(text)
        if hi:
            blocking.append({"rule": "V1-SEC-002", "msg": "high confidence secret detected", "path": str(path), "types": hi})
        elif _LOW_CONF_SECRET_WORD_RE.search(text):
            warnings.append({"rule": "V1-SEC-001", "msg": "possible secret keyword", "path": str(path)})

    for m in _MD_LINK_RE.finditer(text):
        target = m.group(1).strip()
        if target.startswith("http://") or target.startswith("https://"):
            continue
        if target.startswith("/"):
            blocking.append({"rule": "V1-LINK-002", "msg": "absolute path link forbidden", "path": str(path), "link": target})
            continue
        if "://" in target:
            continue
        resolved = (path.parent / target).resolve()
        try:
            resolved.relative_to(output_root.resolve())
        except Exception:
            blocking.append({"rule": "V1-LINK-001", "msg": "link points outside output root", "path": str(path), "link": target})
            continue
        if not resolved.exists():
            blocking.append({"rule": "V1-LINK-001", "msg": "link target missing", "path": str(path), "link": target})


def _extract_local_link_targets(text: str) -> list[str]:
    out: list[str] = []
    for m in _MD_LINK_RE.finditer(text):
        target = m.group(1).strip()
        if target.startswith("http://") or target.startswith("https://"):
            continue
        if target.startswith("/"):
            continue
        if "://" in target:
            continue
        out.append(target)
    return out
