from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


_BUILD_MARKERS = [
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "package.json",
    "pyproject.toml",
    "go.mod",
    "Cargo.toml",
    "CMakeLists.txt",
    "Directory.Build.props",
    "Directory.Build.targets",
]


class ManifestDiscoveryError(RuntimeError):
    def __init__(self, *, errorCode: str, reason: str, hint: str | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.errorCode = errorCode
        self.reason = reason
        self.hint = hint
        self.details = details or {}


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
    if (module_dir / "CMakeLists.txt").exists():
        tools.append("cmake")
    if _has_dotnet_marker(module_dir):
        tools.append("dotnet")
    return tools or ["unknown"]

def _has_dotnet_marker(d: Path) -> bool:
    try:
        if any(d.glob("*.sln")):
            return True
        if any(d.glob("*.csproj")):
            return True
    except Exception:
        return False
    if (d / "global.json").exists():
        return True
    if (d / "NuGet.Config").exists():
        return True
    if (d / "Directory.Build.props").exists() or (d / "Directory.Build.targets").exists():
        return True
    return False


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
    if any((d / m).exists() for m in _BUILD_MARKERS):
        return True
    return _has_dotnet_marker(d)

def _repo_root_prefix(repo_root: Path) -> str:
    rr = repo_root.resolve().as_posix()
    return rr if rr.endswith("/") else (rr + "/")

def manifest_path(repo_root: Path, config: dict[str, Any]) -> Path:
    raw = config.get("discovery", {}).get("manifestPath")
    if raw is None or str(raw).strip() == "":
        raw = "build-project-docs-new.manifest.json"
    p = Path(str(raw))
    if p.is_absolute():
        raise ManifestDiscoveryError(
            errorCode="MANIFEST_PATH_INVALID",
            reason="manifestPath must be a relative path",
            hint="set discovery.manifestPath to a path relative to repoRoot",
            details={"manifestPath": str(raw)},
        )
    if any(part == ".." for part in p.parts):
        raise ManifestDiscoveryError(
            errorCode="MANIFEST_PATH_INVALID",
            reason="manifestPath must not contain '..'",
            hint="use a clean relative path under repoRoot",
            details={"manifestPath": str(raw)},
        )
    resolved = (repo_root / p).resolve()
    rr = _repo_root_prefix(repo_root)
    if not resolved.as_posix().startswith(rr) and resolved.as_posix() != repo_root.resolve().as_posix():
        raise ManifestDiscoveryError(
            errorCode="MANIFEST_PATH_INVALID",
            reason="manifestPath escapes repoRoot",
            hint="use a path under repoRoot",
            details={"manifestPath": str(raw)},
        )
    return resolved

def _normalize_root(repo_root: Path, raw_root: Any) -> str:
    if not isinstance(raw_root, str) or not raw_root.strip():
        raise ManifestDiscoveryError(
            errorCode="MANIFEST_ROOT_INVALID",
            reason="manifest root must be a non-empty string",
            hint="roots[] must be relative paths under repoRoot",
            details={"root": raw_root},
        )
    rp = Path(raw_root)
    if rp.is_absolute():
        raise ManifestDiscoveryError(
            errorCode="MANIFEST_ROOT_INVALID",
            reason="manifest root must be a relative path",
            hint="use paths relative to repoRoot",
            details={"root": raw_root},
        )
    if any(part == ".." for part in rp.parts):
        raise ManifestDiscoveryError(
            errorCode="MANIFEST_ROOT_INVALID",
            reason="manifest root must not contain '..'",
            hint="use a clean relative path under repoRoot",
            details={"root": raw_root},
        )
    resolved = (repo_root / rp).resolve()
    rr = _repo_root_prefix(repo_root)
    if not resolved.as_posix().startswith(rr) and resolved.as_posix() != repo_root.resolve().as_posix():
        raise ManifestDiscoveryError(
            errorCode="MANIFEST_ROOT_INVALID",
            reason="manifest root escapes repoRoot",
            hint="use a path under repoRoot",
            details={"root": raw_root},
        )
    if not resolved.exists():
        raise ManifestDiscoveryError(
            errorCode="MANIFEST_ROOT_INVALID",
            reason="manifest root does not exist",
            hint="fix the root path or update the manifest",
            details={"root": raw_root},
        )
    try:
        rel = resolved.relative_to(repo_root.resolve()).as_posix()
    except Exception:
        rel = raw_root
    return rel if rel else "."

def _load_manifest(repo_root: Path, mp: Path) -> dict[str, Any]:
    try:
        raw = mp.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise ManifestDiscoveryError(
            errorCode="MANIFEST_REQUIRED",
            reason="manifest file missing",
            hint="create the manifest file or use discovery.strategy=default",
            details={"manifestPath": str(mp)},
        ) from e
    except PermissionError as e:
        raise ManifestDiscoveryError(
            errorCode="MANIFEST_INVALID",
            reason="manifest not readable",
            hint="check file permissions",
            details={"manifestPath": str(mp)},
        ) from e
    except OSError as e:
        raise ManifestDiscoveryError(
            errorCode="MANIFEST_INVALID",
            reason=str(e),
            hint="check filesystem and manifestPath",
            details={"manifestPath": str(mp)},
        ) from e
    try:
        data = json.loads(raw)
    except Exception as e:
        raise ManifestDiscoveryError(
            errorCode="MANIFEST_INVALID",
            reason="manifest is not valid JSON",
            hint="ensure discovery.manifestPath points to a JSON file",
            details={"manifestPath": str(mp)},
        ) from e
    if not isinstance(data, dict):
        raise ManifestDiscoveryError(
            errorCode="MANIFEST_INVALID",
            reason="manifest must be a JSON object",
            hint="expected {\"modules\": [...]}",
            details={"manifestPath": str(mp)},
        )
    return data

def discover_modules_manifest(repo_root: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    mp = manifest_path(repo_root, config)
    data = _load_manifest(repo_root, mp)
    mods = data.get("modules")
    if not isinstance(mods, list):
        raise ManifestDiscoveryError(
            errorCode="MANIFEST_INVALID",
            reason="manifest.modules must be an array",
            hint="expected {\"modules\": [{\"displayName\": \"...\", \"roots\": [\"...\"]}]}",
            details={"manifestPath": str(mp)},
        )
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    seen_display: set[str] = set()
    for i, m in enumerate(mods):
        if not isinstance(m, dict):
            raise ManifestDiscoveryError(
                errorCode="MANIFEST_INVALID",
                reason="each manifest.modules[] entry must be an object",
                hint="expected {\"displayName\": \"...\", \"roots\": [\"...\"]}",
                details={"manifestPath": str(mp), "moduleIndex": i},
            )
        if "layerTags" in m:
            raise ManifestDiscoveryError(
                errorCode="MANIFEST_INVALID",
                reason="manifest must not provide layerTags",
                hint="layerTags must remain unknown and be derived by extractor evidence",
                details={"manifestPath": str(mp), "moduleIndex": i},
            )
        if "moduleId" in m:
            raise ManifestDiscoveryError(
                errorCode="MANIFEST_INVALID",
                reason="manifest must not provide moduleId",
                hint="moduleId is derived from normalized roots",
                details={"manifestPath": str(mp), "moduleIndex": i},
            )
        display = m.get("displayName")
        if not isinstance(display, str) or not display.strip():
            raise ManifestDiscoveryError(
                errorCode="MANIFEST_INVALID",
                reason="manifest module displayName must be a non-empty string",
                hint="set displayName for each module",
                details={"manifestPath": str(mp), "moduleIndex": i},
            )
        if display in seen_display:
            raise ManifestDiscoveryError(
                errorCode="MANIFEST_INVALID",
                reason="duplicate manifest module displayName",
                hint="displayName must be unique across modules for deps resolution",
                details={"manifestPath": str(mp), "moduleIndex": i, "displayName": display},
            )
        seen_display.add(display)
        roots = m.get("roots")
        if not isinstance(roots, list) or not roots:
            raise ManifestDiscoveryError(
                errorCode="MANIFEST_INVALID",
                reason="manifest module roots must be a non-empty array",
                hint="roots[] must contain at least one relative path under repoRoot",
                details={"manifestPath": str(mp), "moduleIndex": i},
            )
        norm_roots = [_normalize_root(repo_root, r) for r in roots]
        module_id = _module_id_from_roots(norm_roots)
        if module_id in seen:
            raise ManifestDiscoveryError(
                errorCode="MANIFEST_INVALID",
                reason="duplicate module after roots normalization",
                hint="ensure each module has unique roots",
                details={"manifestPath": str(mp), "moduleIndex": i, "moduleId": module_id},
            )
        seen.add(module_id)

        deps_raw = m.get("deps", [])
        if deps_raw is None:
            deps_raw = []
        if not isinstance(deps_raw, list) or any(not isinstance(x, str) for x in deps_raw):
            raise ManifestDiscoveryError(
                errorCode="MANIFEST_INVALID",
                reason="manifest module deps must be an array of strings",
                hint="omit deps or use [\"displayName\", ...]",
                details={"manifestPath": str(mp), "moduleIndex": i},
            )
        deps = [str(x) for x in deps_raw]

        signals_raw = m.get("signals", [])
        if signals_raw is None:
            signals_raw = []
        signals: list[dict[str, str]] = []
        if not isinstance(signals_raw, list):
            raise ManifestDiscoveryError(
                errorCode="MANIFEST_INVALID",
                reason="manifest module signals must be an array",
                hint="signals[] items must be {\"name\": \"...\", \"value\": \"...\"}",
                details={"manifestPath": str(mp), "moduleIndex": i},
            )
        for s in signals_raw:
            if not isinstance(s, dict):
                raise ManifestDiscoveryError(
                    errorCode="MANIFEST_INVALID",
                    reason="manifest module signals[] entries must be objects",
                    hint="signals[] items must be {\"name\": \"...\", \"value\": \"...\"}",
                    details={"manifestPath": str(mp), "moduleIndex": i},
                )
            name = s.get("name")
            value = s.get("value")
            if not isinstance(name, str) or not isinstance(value, str):
                raise ManifestDiscoveryError(
                    errorCode="MANIFEST_INVALID",
                    reason="manifest module signal must have string name/value",
                    hint="signals[] items must be {\"name\": \"...\", \"value\": \"...\"}",
                    details={"manifestPath": str(mp), "moduleIndex": i},
                )
            signals.append({"name": name, "value": value})

        exts = m.get("extensions", {})
        if exts is None:
            exts = {}
        if not isinstance(exts, dict):
            raise ManifestDiscoveryError(
                errorCode="MANIFEST_INVALID",
                reason="manifest module extensions must be an object",
                hint="extensions may contain {\"layerHints\": [\"...\"]}",
                details={"manifestPath": str(mp), "moduleIndex": i},
            )
        layer_hints_raw = exts.get("layerHints")
        if layer_hints_raw is None:
            layer_hints = _suggest_layer(display.split("/")[-1])
        else:
            if not isinstance(layer_hints_raw, list) or any(not isinstance(x, str) for x in layer_hints_raw):
                raise ManifestDiscoveryError(
                    errorCode="MANIFEST_INVALID",
                    reason="extensions.layerHints must be an array of strings",
                    hint="use {\"extensions\": {\"layerHints\": [\"...\"]}}",
                    details={"manifestPath": str(mp), "moduleIndex": i},
                )
            layer_hints = [str(x) for x in layer_hints_raw]

        allowed_keys = {"displayName", "roots", "deps", "signals", "extensions"}
        extra = sorted(set(m.keys()) - allowed_keys)
        if extra:
            raise ManifestDiscoveryError(
                errorCode="MANIFEST_INVALID",
                reason="manifest module contains unknown fields",
                hint=f"allowed fields: {', '.join(sorted(allowed_keys))}",
                details={"manifestPath": str(mp), "moduleIndex": i, "unknownFields": extra},
            )

        allowed_exts = {"layerHints"}
        extra_exts = sorted(set(exts.keys()) - allowed_exts)
        if extra_exts:
            raise ManifestDiscoveryError(
                errorCode="MANIFEST_INVALID",
                reason="manifest module extensions contains unknown fields",
                hint=f"allowed extensions fields: {', '.join(sorted(allowed_exts))}",
                details={"manifestPath": str(mp), "moduleIndex": i, "unknownFields": extra_exts},
            )

        out.append(
            {
                "moduleId": module_id,
                "displayName": display,
                "roots": norm_roots,
                "layerTags": ["unknown"],
                "deps": deps,
                "signals": signals,
                "extensions": {"layerHints": layer_hints},
            }
        )
    display_to_id = {str(m.get("displayName")): str(m.get("moduleId")) for m in out if m.get("displayName") and m.get("moduleId")}
    for m in out:
        d = str(m.get("displayName") or "")
        resolved: list[str] = []
        for dep_name in list(m.get("deps", []) or []):
            dep_display = str(dep_name)
            if dep_display == d:
                raise ManifestDiscoveryError(
                    errorCode="MANIFEST_INVALID",
                    reason="module cannot depend on itself",
                    hint="remove self reference from deps",
                    details={"manifestPath": str(mp), "module": d, "dep": dep_display},
                )
            dep_id = display_to_id.get(dep_display)
            if not dep_id:
                raise ManifestDiscoveryError(
                    errorCode="MANIFEST_DEP_NOT_FOUND",
                    reason="manifest dependency not found",
                    hint="deps[] must reference an existing module displayName",
                    details={"manifestPath": str(mp), "module": d, "dep": dep_display},
                )
            resolved.append(dep_id)
        m["deps"] = sorted(set(resolved))
    return out

def discover_modules_manifest_first(repo_root: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    mp = manifest_path(repo_root, config)
    if mp.exists():
        return discover_modules_manifest(repo_root, config)
    return discover_modules(repo_root, config)


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
    cands = list(sorted(uniq.values(), key=lambda x: x.as_posix()))
    kept: list[Path] = []
    kept_abs: list[str] = []
    for p in sorted(cands, key=lambda x: (len(x.resolve().as_posix()), x.as_posix())):
        abs_p = p.resolve().as_posix()
        if any(abs_p == k or abs_p.startswith(k + "/") for k in kept_abs):
            continue
        kept.append(p)
        kept_abs.append(abs_p)
    return list(sorted(kept, key=lambda x: x.as_posix()))


def _find_sln_files(repo_root: Path, ignore: set[str], max_depth: int) -> list[Path]:
    out: list[Path] = []

    def walk(base: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            for p in base.glob("*.sln"):
                if p.is_file():
                    out.append(p)
        except Exception:
            pass
        try:
            for child in base.iterdir():
                if not child.is_dir():
                    continue
                rel_first = child.relative_to(repo_root).parts[0]
                if rel_first in ignore:
                    continue
                walk(child, depth + 1)
        except Exception:
            return

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
    sln_files = _find_sln_files(repo_root, ignore, max_depth=max(1, scan_depth))
    sln_modules: list[dict[str, Any]] = []
    sln_dirs_abs: list[str] = []
    for sln in sln_files:
        d = sln.parent
        dir_rel = d.relative_to(repo_root).as_posix()
        if dir_rel == ".":
            dir_root = "."
        else:
            dir_root = dir_rel
        sln_rel = sln.relative_to(repo_root).as_posix()
        roots = [dir_root, sln_rel]
        module_id = _module_id_from_roots(roots)
        tools = _detect_tools(d)
        signals = [{"name": "build.tools", "value": ",".join(tools)}]
        fw = _frontend_framework(d)
        if fw:
            signals.append({"name": "frontend.framework", "value": fw})
        display = sln.stem if dir_rel == "." else f"{dir_rel}/{sln.stem}"
        sln_dirs_abs.append(d.resolve().as_posix())
        layer_hints = _suggest_layer(d.name)
        sln_modules.append(
            {
                "moduleId": module_id,
                "displayName": display,
                "roots": roots,
                "layerTags": ["unknown"],
                "deps": [],
                "signals": signals,
                "extensions": {"layerHints": layer_hints},
            }
        )

    candidates = _collect_candidates(repo_root, ignore, max_depth=scan_depth)
    if not candidates and scan_depth == 1:
        candidates = _collect_candidates(repo_root, ignore, max_depth=2)

    modules: list[dict[str, Any]] = []
    if sln_modules:
        modules.extend(sorted(sln_modules, key=lambda m: m.get("displayName", "")))
    if candidates:
        for d in sorted(candidates, key=lambda p: p.name):
            rel = d.relative_to(repo_root).as_posix()
            roots = [rel]
            module_id = _module_id_from_roots(roots)
            fw = _frontend_framework(d)
            tools = _detect_tools(d)
            if sln_dirs_abs and "dotnet" in tools:
                abs_d = d.resolve().as_posix()
                if any(abs_d == s or abs_d.startswith(s + "/") for s in sln_dirs_abs):
                    continue
            signals = [{"name": "build.tools", "value": ",".join(tools)}]
            if fw:
                signals.append({"name": "frontend.framework", "value": fw})
            layer_hints = _suggest_layer(d.name)
            modules.append(
                {
                    "moduleId": module_id,
                    "displayName": rel,
                    "roots": roots,
                    "layerTags": ["unknown"],
                    "deps": [],
                    "signals": signals,
                    "extensions": {"layerHints": layer_hints},
                }
            )
        return modules

    if modules:
        return modules

    roots = ["."]
    module_id = _module_id_from_roots(roots)
    return [
        {
            "moduleId": module_id,
            "displayName": repo_root.name,
            "roots": roots,
            "layerTags": ["unknown"],
            "deps": [],
            "signals": [{"name": "build.tools", "value": ",".join(_detect_tools(repo_root))}],
            "extensions": {"layerHints": _suggest_layer(repo_root.name)},
        }
    ]


from lib.registry import register_discovery


register_discovery("default")(discover_modules)
register_discovery("manifest")(discover_modules_manifest)
register_discovery("manifest-first")(discover_modules_manifest_first)
