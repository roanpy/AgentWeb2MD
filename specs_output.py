from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True, slots=True)
class SpecRecord:
    page_id: str
    title: str
    rel_path: str
    source: str
    specs: dict[str, str]


def record_specs(records: list[SpecRecord], output_root: str, save_dir: str, page_id: str, title: str, source: str, specs: Mapping[str, str]) -> None:
    rel_path = os.path.relpath(save_dir, output_root)
    records.append(SpecRecord(
        page_id=str(page_id),
        title=title,
        rel_path=rel_path,
        source=source,
        specs={str(key): str(value) for key, value in specs.items()},
    ))


def write_specs_output(output_root: str, records: list[SpecRecord]) -> str | None:
    if not records:
        return None
    meta_dir = Path(output_root) / "_meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    path = meta_dir / "_specs.json"
    payload = {
        "schema": "webextract-specs-v1",
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "pages_with_specs": len({record.rel_path for record in records}),
        "total_specs": sum(len(record.specs) for record in records),
        "records": [asdict(record) for record in records],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def load_spec_summary(output_root: str, files_total: int) -> dict[str, int | float]:
    path = Path(output_root) / "_meta" / "_specs.json"
    if not path.exists():
        return {"pages_with_specs": 0, "total_specs": 0, "avg_specs_per_page": 0, "spec_coverage": 0}
    data = json.loads(path.read_text(encoding="utf-8"))
    pages = int(data.get("pages_with_specs") or 0)
    total = int(data.get("total_specs") or 0)
    return {
        "pages_with_specs": pages,
        "total_specs": total,
        "avg_specs_per_page": round(total / pages, 1) if pages else 0,
        "spec_coverage": round(pages / files_total, 3) if files_total else 0,
    }
