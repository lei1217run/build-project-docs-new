from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def compute_evidence_hash(repo_root: Path, roots: list[str], config: dict[str, Any]) -> str:
    h = hashlib.sha256()
    out_root = str(config["output"]["rootDir"])
    if out_root.startswith("./"):
        out_root = out_root[2:]
    exclude_prefixes = {".git", "node_modules", out_root}
    ex_globs = set(config.get("incremental", {}).get("excludeGlobs", []))

    for r in sorted(set(roots)):
        base = (repo_root / r).resolve()
        if not base.exists():
            continue
        if base.is_file():
            _add_file(h, repo_root, base, ex_globs, exclude_prefixes)
            continue
        for p in sorted(base.rglob("*")):
            if p.is_dir():
                continue
            _add_file(h, repo_root, p, ex_globs, exclude_prefixes)

    return h.hexdigest()


def _add_file(h: "hashlib._Hash", repo_root: Path, p: Path, ex_globs: set[str], exclude_prefixes: set[str]) -> None:
    rel = p.relative_to(repo_root).as_posix()
    first = rel.split("/", 1)[0]
    if first in exclude_prefixes:
        return
    if any(rel == g for g in ex_globs):
        return
    if p.suffix.lower() not in [".py", ".js", ".ts", ".tsx", ".java", ".go", ".rs", ".proto", ".json", ".yaml", ".yml"]:
        return
    st = p.stat()
    h.update(rel.encode("utf-8"))
    h.update(b"\n")
    h.update(str(st.st_size).encode("utf-8"))
    h.update(b"\n")
    h.update(str(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))).encode("utf-8"))
    h.update(b"\n")
