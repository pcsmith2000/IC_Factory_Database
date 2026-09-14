"""Layer 0: load the fixed inputs — source registry, config, control checksum."""
from __future__ import annotations
import hashlib, subprocess
from pathlib import Path
import yaml


def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def active_sources(registry: dict) -> list[dict]:
    return [s for s in registry["sources"] if s.get("status") == "active" and s.get("class") in {"A", "B", "C", "D"}]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def registry_version(repo: Path) -> str:
    """Git commit of the registry file — the version a release is tagged with."""
    try:
        return subprocess.check_output(
            ["git", "log", "-1", "--format=%h", "--", "registry/sources.yaml"], cwd=repo, text=True).strip() or "uncommitted"
    except Exception:
        return "unknown"
