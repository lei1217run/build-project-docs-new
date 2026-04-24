from __future__ import annotations

import ast
import json
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
    ("rest", re.compile(r"\bMap(Get|Post|Put|Delete|Patch)\(\s*\"([^\"]+)\"")),
    ("rest", re.compile(r"\[(HttpGet|HttpPost|HttpPut|HttpDelete|HttpPatch)\s*(?:\(\s*\"([^\"]*)\"\s*\))?\s*\]")),
    ("grpc", re.compile(r"\brpc\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")),
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
            if p.name in ["pyproject.toml", "package.json", "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts"]:
                files.append(p)
            elif p.name in ["CMakeLists.txt", "Directory.Build.props", "Directory.Build.targets"]:
                files.append(p)
            elif p.suffix.lower() in [
                ".py",
                ".js",
                ".ts",
                ".tsx",
                ".java",
                ".go",
                ".rs",
                ".proto",
                ".cs",
                ".csproj",
                ".sln",
                ".props",
                ".targets",
                ".cpp",
                ".cc",
                ".cxx",
                ".h",
                ".hpp",
                ".hh",
                ".ipp",
                ".cmake",
            ]:
                files.append(p)
            if len(files) >= 300:
                return files
    return files


def _evidence(kind: str, rel: str, note: str | None = None, rng: tuple[int, int] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": kind, "path": rel}
    if note:
        out["note"] = note
    if rng:
        out["range"] = {"start": rng[0], "end": rng[1]}
    return out


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


def _python_public_surface(repo_root: Path, roots: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    exports: list[dict[str, Any]] = []
    entrypoints: list[dict[str, Any]] = []
    types: list[dict[str, Any]] = []

    for r in roots:
        base = (repo_root / r).resolve()
        if not base.exists() or not base.is_dir():
            continue

        pyproject = base / "pyproject.toml"
        if pyproject.exists():
            data = _read_toml(pyproject) or {}
            project = data.get("project") if isinstance(data.get("project"), dict) else {}
            scripts = project.get("scripts") if isinstance(project.get("scripts"), dict) else {}
            for name, target in scripts.items():
                entrypoints.append(
                    {
                        "kind": "cli",
                        "name": str(name),
                        "signature": str(target),
                        "evidence": [_evidence("config", pyproject.relative_to(repo_root).as_posix(), "project.scripts")],
                    }
                )

        cand_init: list[Path] = []
        for p in base.rglob("__init__.py"):
            rel = p.relative_to(base).as_posix()
            if rel.count("/") > 2:
                continue
            cand_init.append(p)
            if len(cand_init) >= 10:
                break

        for init_py in cand_init:
            rel = init_py.relative_to(repo_root).as_posix()
            try:
                src = init_py.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            try:
                tree = ast.parse(src)
            except Exception:
                continue

            explicit_all: list[str] = []
            for n in tree.body:
                if isinstance(n, ast.Assign):
                    for t in n.targets:
                        if isinstance(t, ast.Name) and t.id == "__all__":
                            if isinstance(n.value, (ast.List, ast.Tuple)):
                                for el in n.value.elts:
                                    if isinstance(el, ast.Constant) and isinstance(el.value, str):
                                        explicit_all.append(el.value)

            exported: set[str] = set()
            if explicit_all:
                exported.update(explicit_all)
            else:
                for n in tree.body:
                    if isinstance(n, ast.ImportFrom):
                        if n.level == 0:
                            for a in n.names:
                                if a.name and not a.name.startswith("_"):
                                    exported.add(a.asname or a.name)
                    if isinstance(n, ast.FunctionDef) and not n.name.startswith("_"):
                        exported.add(n.name)
                    if isinstance(n, ast.ClassDef) and not n.name.startswith("_"):
                        exported.add(n.name)

            for name in sorted(exported):
                exports.append(
                    {
                        "name": name,
                        "kind": "symbol",
                        "location": {"path": rel},
                        "evidence": [_evidence("export", rel, "__init__.py export")],
                    }
                )

        for f in base.rglob("*.py"):
            rel = f.relative_to(repo_root).as_posix()
            if rel.endswith("/__init__.py"):
                continue
            try:
                src = f.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(src)
            except Exception:
                continue
            for n in tree.body:
                if isinstance(n, ast.ClassDef):
                    decos = {getattr(d, "id", "") for d in n.decorator_list if isinstance(d, ast.Name)}
                    bases = {getattr(b, "id", "") for b in n.bases if isinstance(b, ast.Name)}
                    if "dataclass" in decos or "BaseModel" in bases or "TypedDict" in bases:
                        types.append(
                            {
                                "name": n.name,
                                "kind": "class",
                                "evidence": [_evidence("symbol", rel, "class model")],
                            }
                        )
            if len(types) >= 50:
                break

    uniq_exports: dict[str, dict[str, Any]] = {}
    for e in exports:
        uniq_exports[str(e.get("name"))] = e
    exports = list(sorted(uniq_exports.values(), key=lambda x: str(x.get("name"))))

    uniq_entry: dict[str, dict[str, Any]] = {}
    for e in entrypoints:
        uniq_entry[str(e.get("name"))] = e
    entrypoints = list(sorted(uniq_entry.values(), key=lambda x: str(x.get("name"))))

    uniq_types: dict[str, dict[str, Any]] = {}
    for t in types:
        uniq_types[str(t.get("name"))] = t
    types = list(sorted(uniq_types.values(), key=lambda x: str(x.get("name"))))

    return exports, entrypoints, types


def _js_public_surface(repo_root: Path, roots: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    exports: list[dict[str, Any]] = []
    entrypoints: list[dict[str, Any]] = []
    types: list[dict[str, Any]] = []

    for r in roots:
        base = (repo_root / r).resolve()
        if not base.exists() or not base.is_dir():
            continue
        pkg = base / "package.json"
        if pkg.exists():
            data = _read_json(pkg) or {}
            if isinstance(data.get("exports"), (dict, str)):
                exports.append(
                    {
                        "name": "package.exports",
                        "kind": "module",
                        "location": {"path": pkg.relative_to(repo_root).as_posix()},
                        "evidence": [_evidence("config", pkg.relative_to(repo_root).as_posix(), "package.json exports")],
                    }
                )
            if isinstance(data.get("main"), str):
                exports.append(
                    {
                        "name": "package.main",
                        "kind": "module",
                        "location": {"path": str(data.get("main"))},
                        "evidence": [_evidence("config", pkg.relative_to(repo_root).as_posix(), "package.json main")],
                    }
                )
            b = data.get("bin")
            if isinstance(b, str):
                entrypoints.append(
                    {
                        "kind": "cli",
                        "name": data.get("name") or "bin",
                        "signature": b,
                        "evidence": [_evidence("config", pkg.relative_to(repo_root).as_posix(), "package.json bin")],
                    }
                )
            if isinstance(b, dict):
                for k, v in b.items():
                    entrypoints.append(
                        {
                            "kind": "cli",
                            "name": str(k),
                            "signature": str(v),
                            "evidence": [_evidence("config", pkg.relative_to(repo_root).as_posix(), "package.json bin")],
                        }
                    )

        for f in base.rglob("*.ts"):
            rel = f.relative_to(repo_root).as_posix()
            try:
                src = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for m in re.finditer(r"\bexport\s+interface\s+([A-Za-z_][A-Za-z0-9_]*)", src):
                types.append({"name": m.group(1), "kind": "type", "evidence": [_evidence("symbol", rel, "export interface")]})
            for m in re.finditer(r"\bexport\s+type\s+([A-Za-z_][A-Za-z0-9_]*)\b", src):
                types.append({"name": m.group(1), "kind": "type", "evidence": [_evidence("symbol", rel, "export type")]})
            if len(types) >= 50:
                break
        if len(types) >= 50:
            break

    uniq_types: dict[str, dict[str, Any]] = {}
    for t in types:
        uniq_types[str(t.get("name"))] = t
    types = list(sorted(uniq_types.values(), key=lambda x: str(x.get("name"))))
    return exports, entrypoints, types


def _csharp_public_surface(repo_root: Path, roots: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    exports: list[dict[str, Any]] = []
    entrypoints: list[dict[str, Any]] = []
    types: list[dict[str, Any]] = []

    type_pat = re.compile(r"\bpublic\s+(?:partial\s+)?(class|struct|interface|enum|record)\s+([A-Za-z_][A-Za-z0-9_]*)\b")
    main_pat = re.compile(r"\b(static\s+)?(async\s+)?([A-Za-z0-9_<>]+)\s+Main\s*\(")

    for r in roots:
        base = (repo_root / r).resolve()
        if not base.exists() or not base.is_dir():
            continue

        cs_files: list[Path] = []
        for p in base.rglob("*.cs"):
            if p.is_file():
                cs_files.append(p)
            if len(cs_files) >= 200:
                break

        for f in cs_files:
            rel = f.relative_to(repo_root).as_posix()
            try:
                src = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if main_pat.search(src) or ("WebApplication.CreateBuilder" in src) or ("Host.CreateDefaultBuilder" in src):
                kind = "service" if ("WebApplication.CreateBuilder" in src or "Host.CreateDefaultBuilder" in src) else "cli"
                entrypoints.append(
                    {
                        "kind": kind,
                        "name": f.name,
                        "signature": "Main",
                        "evidence": [_evidence("symbol", rel, "entrypoint")],
                    }
                )
            if ("BackgroundService" in src) or ("IHostedService" in src):
                entrypoints.append(
                    {
                        "kind": "service",
                        "name": f.name,
                        "signature": "hosted-service",
                        "evidence": [_evidence("pattern", rel, "worker/hosted service")],
                    }
                )

            for m in type_pat.finditer(src):
                types.append({"name": m.group(2), "kind": m.group(1), "evidence": [_evidence("symbol", rel, "public type")]})
                if len(types) >= 80:
                    break
            if len(types) >= 80 and len(entrypoints) >= 10:
                break

    uniq_entry: dict[str, dict[str, Any]] = {}
    for e in entrypoints:
        k = f"{e.get('name')}::{e.get('signature')}"
        uniq_entry[str(k)] = e
    entrypoints = list(sorted(uniq_entry.values(), key=lambda x: str(x.get("name"))))

    uniq_types: dict[str, dict[str, Any]] = {}
    for t in types:
        uniq_types[str(t.get("name"))] = t
    types = list(sorted(uniq_types.values(), key=lambda x: str(x.get("name"))))

    return exports, entrypoints, types


def _csharp_config_items(repo_root: Path, roots: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    sensitive_re = re.compile(r"(?i)\b(password|passwd|token|secret|api[_-]?key|private[_-]?key|bearer)\b")

    def add_key(key: str, rel: str) -> None:
        k = key.strip()
        if not k or k in seen:
            return
        seen.add(k)
        out.append(
            {
                "key": k,
                "scope": "file",
                "sensitive": bool(sensitive_re.search(k)),
                "evidence": [_evidence("config", rel, "appsettings key")],
            }
        )

    for r in roots:
        base = (repo_root / r).resolve()
        if not base.exists():
            continue
        cand: list[Path] = []
        if base.is_file():
            cand = [base]
        else:
            for p in base.rglob("appsettings*.json"):
                if p.is_file():
                    cand.append(p)
                if len(cand) >= 10:
                    break
        for p in cand:
            rel = p.relative_to(repo_root).as_posix()
            data = _read_json(p) or {}
            if not isinstance(data, dict):
                continue
            for k, v in data.items():
                add_key(str(k), rel)
                if isinstance(v, dict):
                    for kk in v.keys():
                        add_key(f"{k}:{kk}", rel)
                if len(out) >= 80:
                    break
            if len(out) >= 80:
                break
        if len(out) >= 80:
            break
    return out


def _cpp_public_surface(repo_root: Path, roots: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    exports: list[dict[str, Any]] = []
    entrypoints: list[dict[str, Any]] = []
    types: list[dict[str, Any]] = []

    type_pat = re.compile(r"\b(class|struct|enum(?:\s+class)?)\s+([A-Za-z_][A-Za-z0-9_]*)\b")
    main_pat = re.compile(r"\b(int|auto)\s+main\s*\(")

    for r in roots:
        base = (repo_root / r).resolve()
        if not base.exists():
            continue
        include_dir = base / "include" if base.is_dir() else None
        headers: list[Path] = []
        if include_dir and include_dir.exists():
            for p in include_dir.rglob("*"):
                if p.is_file() and p.suffix.lower() in [".h", ".hpp", ".hh", ".ipp"]:
                    headers.append(p)
                if len(headers) >= 200:
                    break
        for h in headers:
            rel = h.relative_to(repo_root).as_posix()
            exports.append({"name": f"header:{rel}", "kind": "file", "location": {"path": rel}, "evidence": [_evidence("file", rel, "public header")]})
            try:
                src = h.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for m in type_pat.finditer(src):
                types.append({"name": m.group(2), "kind": m.group(1), "evidence": [_evidence("symbol", rel, "public header type")]})
                if len(types) >= 80:
                    break
            if len(types) >= 80:
                break

        if base.is_dir():
            cpp_files: list[Path] = []
            for p in base.rglob("*"):
                if p.is_file() and p.suffix.lower() in [".cpp", ".cc", ".cxx"]:
                    cpp_files.append(p)
                if len(cpp_files) >= 200:
                    break
            for f in cpp_files:
                rel = f.relative_to(repo_root).as_posix()
                try:
                    src = f.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                if main_pat.search(src):
                    entrypoints.append({"kind": "cli", "name": f.name, "signature": "main()", "evidence": [_evidence("symbol", rel, "main entrypoint")]})
                if len(entrypoints) >= 20:
                    break

    uniq_exports: dict[str, dict[str, Any]] = {}
    for e in exports:
        uniq_exports[str(e.get("name"))] = e
    exports = list(sorted(uniq_exports.values(), key=lambda x: str(x.get("name"))))

    uniq_entry: dict[str, dict[str, Any]] = {}
    for e in entrypoints:
        k = f"{e.get('name')}::{e.get('signature')}"
        uniq_entry[str(k)] = e
    entrypoints = list(sorted(uniq_entry.values(), key=lambda x: str(x.get("name"))))

    uniq_types: dict[str, dict[str, Any]] = {}
    for t in types:
        uniq_types[str(t.get("name"))] = t
    types = list(sorted(uniq_types.values(), key=lambda x: str(x.get("name"))))

    return exports, entrypoints, types


def _key_files(repo_root: Path, roots: list[str]) -> list[dict[str, Any]]:
    keywords = [
        "graph",
        "middleware",
        "backend",
        "backends",
        "profile",
        "profiles",
        "cli",
        "repl",
        "eval",
        "protocol",
        "adapter",
        "sandbox",
        "config",
        "settings",
        "bootstrap",
    ]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in roots:
        base = (repo_root / r).resolve()
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_dir():
                continue
            rel = p.relative_to(repo_root).as_posix()
            name = p.name.lower()
            rel_l = rel.lower()
            score = 0
            for k in keywords:
                if k in name:
                    score += 3
                if f"/{k}/" in rel_l:
                    score += 2
            if score <= 0:
                continue
            if rel in seen:
                continue
            seen.add(rel)
            out.append({"path": rel, "score": score, "evidence": [_evidence("file", rel, "key file signal")]})
            if len(out) >= 200:
                break
        if len(out) >= 200:
            break
    out.sort(key=lambda x: (-int(x.get("score") or 0), str(x.get("path") or "")))
    return out


def _layering(repo_root: Path, roots: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
    tags: set[str] = set()
    evidence: list[dict[str, Any]] = []
    patterns = [
        ("config", re.compile(r"(^|/)(config|configs|settings|env|environments|bootstrap|di)(/|$)", re.I)),
        ("config", re.compile(r"\b(application\.ya?ml|application\.properties|\.env|config\.(ya?ml|json|toml)|settings\.py|config\.py|appsettings(\.[a-z0-9_-]+)?\.json)\b", re.I)),
        ("foundation", re.compile(r"(^|/)(core|common|shared|utils|base)(/|$)", re.I)),
        ("foundation", re.compile(r"(?i)\b(adapter|protocol|middleware|sandbox|bootstrap|client|transport|codec|serializer|auth|logging)\b")),
        ("data", re.compile(r"(^|/)(db|database|migrations|migration|repository|repositories|dao|mapper|persistence|store|storage)(/|$)", re.I)),
        ("data", re.compile(r"(?i)\b(migrate|migration|seed)\b")),
        ("business", re.compile(r"(^|/)(domain|domains|usecase|usecases|service|services|application|workflow|workflows)(/|$)", re.I)),
        ("business", re.compile(r"(?i)\b(controller|handler|resolver|endpoint|orchestrator)\b")),
    ]
    for r in roots:
        base = (repo_root / r).resolve()
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_dir():
                continue
            rel = p.relative_to(repo_root).as_posix()
            for tag, pat in patterns:
                if pat.search(rel):
                    tags.add(tag)
                    evidence.append(_evidence("file", rel, f"layer signal: {tag}"))
                    if len(evidence) >= 30:
                        break
            if len(evidence) >= 30:
                break
        if len(evidence) >= 30:
            break
    if not tags:
        tags.add("unknown")
    return list(sorted(tags)), evidence


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
                    if method.lower().startswith("http") and len(method) > 4:
                        method = method[4:]
                    path = m.group(2) if m.group(2) else "unknown"
                    sig = f"{method.upper()} {path}"
                elif m.lastindex and m.lastindex == 1:
                    one = m.group(1) if m.group(1) else "unknown"
                    if kind == "grpc":
                        sig = f"RPC {one}"
                    else:
                        sig = f"{kind.upper()} {one}"
                if sig is None:
                    sig = f"{kind} detected"
                api_items.append(
                    {
                        "kind": kind,
                        "signature": sig,
                        "name": sig,
                        "evidence": [_evidence("pattern", rel, "pattern match")],
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

    layer_tags, layer_evidence = _layering(repo_root, roots)

    lang_counts: dict[str, int] = {}
    for f in src_files:
        suf = f.suffix.lower()
        if f.name == "pyproject.toml":
            lang_counts["python"] = lang_counts.get("python", 0) + 1
        elif suf == ".py":
            lang_counts["python"] = lang_counts.get("python", 0) + 1
        elif suf in [".js", ".ts", ".tsx"]:
            lang_counts["js-ts"] = lang_counts.get("js-ts", 0) + 1
        elif suf == ".go" or f.name == "go.mod":
            lang_counts["go"] = lang_counts.get("go", 0) + 1
        elif suf == ".rs" or f.name == "Cargo.toml":
            lang_counts["rust"] = lang_counts.get("rust", 0) + 1
        elif suf == ".java" or f.name in ["pom.xml", "build.gradle", "build.gradle.kts"]:
            lang_counts["java"] = lang_counts.get("java", 0) + 1
        elif suf in [".cs", ".csproj", ".sln", ".props", ".targets"]:
            lang_counts["csharp"] = lang_counts.get("csharp", 0) + 1
        elif suf in [".cpp", ".cc", ".cxx", ".h", ".hpp", ".hh", ".ipp"] or f.name == "CMakeLists.txt":
            lang_counts["cpp"] = lang_counts.get("cpp", 0) + 1

    py_exports: list[dict[str, Any]] = []
    py_entry: list[dict[str, Any]] = []
    py_types: list[dict[str, Any]] = []
    if lang_counts.get("python", 0) > 0:
        py_exports, py_entry, py_types = _python_public_surface(repo_root, roots)

    js_exports: list[dict[str, Any]] = []
    js_entry: list[dict[str, Any]] = []
    js_types: list[dict[str, Any]] = []
    if lang_counts.get("js-ts", 0) > 0:
        js_exports, js_entry, js_types = _js_public_surface(repo_root, roots)

    cs_exports: list[dict[str, Any]] = []
    cs_entry: list[dict[str, Any]] = []
    cs_types: list[dict[str, Any]] = []
    cs_config_items: list[dict[str, Any]] = []
    if lang_counts.get("csharp", 0) > 0:
        cs_exports, cs_entry, cs_types = _csharp_public_surface(repo_root, roots)
        cs_config_items = _csharp_config_items(repo_root, roots)

    cpp_exports: list[dict[str, Any]] = []
    cpp_entry: list[dict[str, Any]] = []
    cpp_types: list[dict[str, Any]] = []
    if lang_counts.get("cpp", 0) > 0:
        cpp_exports, cpp_entry, cpp_types = _cpp_public_surface(repo_root, roots)

    exports = [*py_exports, *js_exports, *cs_exports, *cpp_exports]
    entrypoints = [*py_entry, *js_entry, *cs_entry, *cpp_entry]
    types = [*py_types, *js_types, *cs_types, *cpp_types]

    key_files = _key_files(repo_root, roots)

    module_ir: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "generatorVersion": "0.1",
        "generatedAt": _now_iso(),
        "module": {
            "moduleId": module["moduleId"],
            "roots": roots,
            "deps": module.get("deps", []),
            "layerTags": layer_tags or module.get("layerTags", ["unknown"]),
            "extensions": {"layerEvidence": layer_evidence, "languageSignals": lang_counts},
        },
        "api": {"hasPublicApi": has_public_api if has_public_api else False, "domains": domains, "extensions": {}},
        "dataModel": {"types": [], "extensions": {}},
        "config": {"items": list(cs_config_items), "extensions": {}},
        "pitfalls": [],
        "publicSurface": {
            "exports": exports,
            "entrypoints": entrypoints,
            "types": types,
            "keyFiles": key_files,
            "extensions": {},
        },
        "extensions": {"evidenceHash": evidence_hash},
    }
    if has_public_api is False and not domains:
        module_ir["api"]["hasPublicApi"] = False
    if types:
        module_ir["dataModel"]["types"] = [{"name": str(t.get("name")), "kind": "unknown", "evidence": list(t.get("evidence", []))} for t in types[:50]]
    return module_ir


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


from lib.registry import register_extractor


register_extractor("default")(extract_module_ir)
