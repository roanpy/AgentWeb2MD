from __future__ import annotations

import os
import re
import sysconfig
from pathlib import Path


_SITE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def validate_identifier(value: str, label: str) -> str:
    if not _SITE_ID.fullmatch(value):
        raise ValueError(f"{label} must use 1-64 letters, numbers, '-' or '_'")
    return value


def validate_site_id(site_id: str) -> str:
    return validate_identifier(site_id, "site ID")


def site_config_dir(site_id: str) -> Path:
    """Find an existing site profile, including the profile bundled in wheels."""
    validate_site_id(site_id)
    for root in _config_roots():
        candidate = root / site_id
        if (candidate / "common.json").is_file():
            return candidate
    return writable_config_root() / site_id


def writable_config_root() -> Path:
    configured = os.environ.get("AGENTWEB2MD_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.cwd() / "config"


def state_file(site_id: str, filename: str) -> Path:
    validate_site_id(site_id)
    configured = os.environ.get("AGENTWEB2MD_STATE_DIR")
    root = Path(configured).expanduser() if configured else Path.cwd() / ".agentweb2md"
    return root / site_id / filename


def _config_roots() -> list[Path]:
    roots = [writable_config_root(), Path(__file__).resolve().parent / "config"]
    data_root = Path(sysconfig.get_path("data")) / "share" / "agentweb2md" / "config"
    if data_root not in roots:
        roots.append(data_root)
    return roots
