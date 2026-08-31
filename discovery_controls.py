from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

Item = tuple[str, str, str, str]


def apply_discovery_controls(items: Iterable[Item], discovery: dict) -> list[Item]:
    include_patterns = discovery.get("url_include_patterns") or []
    exclude_patterns = discovery.get("url_exclude_patterns") or []
    max_pages = int(discovery.get("max_pages") or 0)
    filtered = list(items)
    if include_patterns:
        filtered = [item for item in filtered if _matches_any(_item_text(item), include_patterns)]
    if exclude_patterns:
        filtered = [item for item in filtered if not _matches_any(_item_text(item), exclude_patterns)]
    if max_pages > 0:
        filtered = filtered[:max_pages]
    return filtered


def _item_text(item: Item) -> str:
    return " ".join(str(part) for part in item)


def _matches_any(text: str, patterns: Sequence[str]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)
