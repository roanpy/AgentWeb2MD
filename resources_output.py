from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ResourceRecord:
    page_id: str
    title: str
    rel_path: str
    source: str
    filename: str
    resource_rel_path: str
    kind: str
    bytes: int
    source_url: str


def record_resource(records: list[ResourceRecord], output_root: str, save_dir: str, page_id: str, title: str, source: str, filename: str, rel: str, kind: str, size: int, url: str) -> None:
    rel_path = os.path.relpath(save_dir, output_root)
    record = ResourceRecord(
        page_id=str(page_id),
        title=title,
        rel_path=rel_path,
        source=source,
        filename=filename,
        resource_rel_path=rel,
        kind=kind,
        bytes=size,
        source_url=url,
    )
    key = (record.page_id, record.resource_rel_path)
    if key not in {(item.page_id, item.resource_rel_path) for item in records}:
        records.append(record)


def write_resources_output(output_root: str, records: list[ResourceRecord]) -> str | None:
    if not records:
        return None
    meta_dir = Path(output_root) / "_meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    path = meta_dir / "_resources.json"
    payload = {
        "schema": "webextract-resources-v1",
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "pages_with_resources": len({record.rel_path for record in records}),
        "total_resources": len(records),
        "total_bytes": sum(record.bytes for record in records),
        "records": [asdict(record) for record in records],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def load_resource_summary(output_root: str, files_total: int) -> dict[str, int | float]:
    path = Path(output_root) / "_meta" / "_resources.json"
    if not path.exists():
        return {"pages_with_resources": 0, "total_resources": 0, "avg_resources_per_page": 0, "resource_coverage": 0, "total_bytes": 0}
    data = json.loads(path.read_text(encoding="utf-8"))
    pages = int(data.get("pages_with_resources") or 0)
    total = int(data.get("total_resources") or 0)
    return {
        "pages_with_resources": pages,
        "total_resources": total,
        "avg_resources_per_page": round(total / pages, 1) if pages else 0,
        "resource_coverage": round(pages / files_total, 3) if files_total else 0,
        "total_bytes": int(data.get("total_bytes") or 0),
    }
