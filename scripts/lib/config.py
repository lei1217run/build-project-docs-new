from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _default_config() -> dict[str, Any]:
    return {
        "output": {"rootDir": ".claude", "indexFile": "CLAUDE.md"},
        "mode": {"defaultMode": "auto"},
        "incremental": {
            "enabled": True,
            "excludeGlobs": [".git/**", "node_modules/**", "dist/**", "build/**"],
        },
        "changelog": {"enabled": True, "maxCommitsPerModule": 10},
        "verification": {"failOnWarnings": False},
        "security": {"redactionMode": "block"},
        "progress": {"lockLeaseSeconds": 900},
        "integration": {"profile": "generic"},
        "configPriority": ["cli", "yaml", "env"],
    }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_yaml_if_available(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except Exception as e:
        raise RuntimeError(f"YAML config requires PyYAML: {e}") from e
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data or {}


def _load_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if path.suffix.lower() in [".yaml", ".yml"]:
        return _load_yaml_if_available(path)
    return _load_json(path)


def _env_overrides(env: os._Environ[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    root_dir = env.get("BPD_NEW_OUTPUT_ROOTDIR")
    index_file = env.get("BPD_NEW_OUTPUT_INDEXFILE")
    redaction_mode = env.get("BPD_NEW_SECURITY_REDACTIONMODE")
    fail_on_warnings = env.get("BPD_NEW_VERIFICATION_FAILONWARNINGS")
    if root_dir or index_file:
        out.setdefault("output", {})
        if root_dir:
            out["output"]["rootDir"] = root_dir
        if index_file:
            out["output"]["indexFile"] = index_file
    if redaction_mode:
        out.setdefault("security", {})
        out["security"]["redactionMode"] = redaction_mode
    if fail_on_warnings is not None:
        out.setdefault("verification", {})
        out["verification"]["failOnWarnings"] = fail_on_warnings.lower() in ["1", "true", "yes"]
    return out


def _cli_overrides(cli_args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    root_dir = cli_args.get("output_rootdir")
    index_file = cli_args.get("output_indexfile")
    if root_dir or index_file:
        out.setdefault("output", {})
        if root_dir:
            out["output"]["rootDir"] = root_dir
        if index_file:
            out["output"]["indexFile"] = index_file
    return out


def load_effective_config(repo_root: Path, cli_args: dict[str, Any], env: os._Environ[str]) -> dict[str, Any]:
    base = _default_config()

    cfg_path = None
    if cli_args.get("config"):
        cfg_path = Path(str(cli_args["config"])).expanduser()
        if not cfg_path.is_absolute():
            cfg_path = (repo_root / cfg_path).resolve()
    else:
        y = (repo_root / "build-project-docs-new.yaml")
        j = (repo_root / "build-project-docs-new.json")
        cfg_path = y if y.exists() else j

    file_cfg = _load_config_file(cfg_path)
    env_cfg = _env_overrides(env)
    cli_cfg = _cli_overrides(cli_args)

    merged = _deep_merge(base, file_cfg)
    merged = _deep_merge(merged, env_cfg)
    merged = _deep_merge(merged, cli_cfg)

    merged["configPriority"] = ["cli", "yaml", "env"]
    return merged
