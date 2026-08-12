from __future__ import annotations

import re
from collections.abc import Callable

Hook = Callable[[str, dict], str]


def apply_hooks(stage: str, text: str, config: dict, context: dict) -> str:
    hook_cfg = config.get("adaptive_hooks", {})
    names = hook_cfg.get(stage, [])
    if not names:
        return text
    result = text
    for name in names:
        hook = HOOKS.get(name)
        if hook is None:
            print(f"  ⚠ Unknown hook '{name}' ignored")
            continue
        result = hook(result, context)
    return result


def strip_blank_runs(text: str, context: dict) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


HOOKS: dict[str, Hook] = {
    "strip_blank_runs": strip_blank_runs,
}
