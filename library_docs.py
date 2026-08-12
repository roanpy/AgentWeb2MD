#!/usr/bin/env python3
import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final


GENERATED_DOCS: Final = {
    "README.md",
    "全库索引.md",
    "全库摘要.md",
    "文档结构.md",
    "_文档结构.md",
    "产品及解决方案清单索引.md",
    "产品及解决方案总览.md",
    "总览.md",
    "清单索引.md",
    "_deep_discovery_audit.md",
    "_quality_report.md",
    "_config_validation.md",
}
SKIP_DIRS: Final = {"图片", "资料", "_archive", "_meta", "__pycache__"}


@dataclass(frozen=True, slots=True)
class LibraryDoc:
    rel_path: str
    title: str
    section: str
    category: str
    brief: str
    headings: int


def _structure(config: dict) -> tuple[str, str, str]:
    out = config.get("output_structure", {})
    return (
        out.get("products_dir", "产品"),
        out.get("solutions_dir", "解决方案"),
        out.get("industries_dir", "行业"),
    )


def _first_heading(text: str, fallback: str) -> str:
    match = re.search(r"^#{1,6}\s+(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else fallback


_BRIEF_NOISE_PREFIXES = (
    "image similar", "图片相似", "may contain optional",
    "image:", "图片：", "photo:", "照片：",
)

def _brief(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", "!", ">", "|", "-", "---", "*数据来源")):
            continue
        if len(s) < 15:
            continue
        if s.lower().startswith(_BRIEF_NOISE_PREFIXES):
            continue
        return s[:140]
    return ""


def _section(rel_path: str, products_dir: str, solutions_dir: str, industries_dir: str) -> str:
    first = rel_path.split(os.sep, 1)[0]
    if first == products_dir:
        return "product"
    if first == solutions_dir:
        return "solution"
    if first == industries_dir:
        return "industry"
    return "other"


def _category(rel_path: str) -> str:
    parts = rel_path.split(os.sep)
    return parts[1] if len(parts) > 2 else "未分类"


def collect_documents(output_root: str, config: dict) -> list[LibraryDoc]:
    products_dir, solutions_dir, industries_dir = _structure(config)
    docs: list[LibraryDoc] = []
    for root, dirs, files in os.walk(output_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in sorted(files):
            if not name.endswith(".md") or name in GENERATED_DOCS:
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, output_root)
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
            docs.append(LibraryDoc(
                rel_path=rel,
                title=_first_heading(text, Path(name).stem),
                section=_section(rel, products_dir, solutions_dir, industries_dir),
                category=_category(rel),
                brief=_brief(text),
                headings=len(re.findall(r"^#{1,6}\s+", text, re.MULTILINE)),
            ))
    return sorted(docs, key=lambda d: (d.section, d.category, d.title, d.rel_path))


def _md_link(doc: LibraryDoc) -> str:
    return f"[{doc.title}]({doc.rel_path.replace(os.sep, '/')})"


def _grouped_lines(docs: list[LibraryDoc], section: str) -> list[str]:
    lines: list[str] = []
    current = ""
    for doc in [d for d in docs if d.section == section]:
        if doc.category != current:
            current = doc.category
            lines.append(f"\n### {current}\n")
        brief = f" - {doc.brief}" if doc.brief else ""
        lines.append(f"- {_md_link(doc)}{brief}")
    return lines


def _quality_summary(output_root: str) -> str:
    path = Path(output_root) / "_meta" / "_quality_report.json"
    if not path.exists():
        # ponytail: fallback to old location for compat
        path = Path(output_root) / "_quality_report.json"
    if not path.exists():
        return "质量报告：未生成"
    data = json.loads(path.read_text(encoding="utf-8"))
    return f"质量分数：{data.get('site_score', 0)}；评分文件：{data.get('files_scored', 0)}"


def write_standard_docs(output_root: str, config: dict) -> dict[str, str]:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    docs = collect_documents(output_root, config)
    site_name = config.get("site_name") or config.get("site") or "资料库"
    base_url = config.get("base_url", "")
    site_id = config.get("site_id", config.get("site", "")).lower()
    today = date.today().isoformat()
    product_count = len([d for d in docs if d.section == "product"])
    solution_count = len([d for d in docs if d.section == "solution"])
    industry_count = len([d for d in docs if d.section == "industry"])

    readme = root / "README.md"
    index = root / "全库索引.md"
    summary = root / "全库摘要.md"

    readme_lines = [
        f"# {site_name} 资料库",
        "",
        f"> 自动生成 | {today}",
    ]
    if base_url:
        readme_lines.extend(["", f"**来源**：{base_url}"])
    readme_lines.extend([
        "",
        "## 文件说明",
        "",
        "- [全库索引](全库索引.md)：按产品/解决方案分类的入口目录",
        "- [全库摘要](全库摘要.md)：统计、质量和内容概览",
        "- `_quality_report.md`：可选质量报告，由 `quality_report.py` 生成",
        "",
        "## 使用建议",
        "",
        "优先从 `全库索引.md` 定位具体 Markdown，再按需把单篇文档入库或引用。",
        "",
        "## 更新方式",
        "",
        "```bash",
        f"agentweb2md --site {site_id or site_name.lower()} --page-type product",
        "```",
    ])
    readme.write_text("\n".join(readme_lines) + "\n", encoding="utf-8")

    index_lines = [f"# {site_name} 全库索引", "", f"> 自动生成 | {today}", "", "## 产品"]
    index_lines.extend(_grouped_lines(docs, "product") or ["", "暂无产品文档"])
    index_lines.extend(["", "## 解决方案"])
    index_lines.extend(_grouped_lines(docs, "solution") or ["", "暂无解决方案文档"])
    index_lines.extend(["", "## 行业"])
    index_lines.extend(_grouped_lines(docs, "industry") or ["", "暂无行业文档"])
    other = [d for d in docs if d.section == "other"]
    if other:
        index_lines.extend(["", "## 其他"])
        index_lines.extend(f"- {_md_link(doc)}" for doc in other)
    index.write_text("\n".join(index_lines) + "\n", encoding="utf-8")

    top_categories = sorted({d.category for d in docs if d.section == "product"})
    sol_categories = sorted({d.category for d in docs if d.section == "solution"})
    industry_categories = sorted({d.category for d in docs if d.section == "industry"})
    cat_counts = {}
    for d in docs:
        cat_counts[d.category] = cat_counts.get(d.category, 0) + 1
    base_url = config.get("base_url", "")
    summary_lines = [
        f"# {site_name} 全库摘要",
        "",
        f"> 自动生成 | {today}",
        "",
        "## 统计",
        "",
        f"- 文档总数：{len(docs)}",
        f"- 产品文档：{product_count}",
        f"- 解决方案文档：{solution_count}",
        f"- 行业文档：{industry_count}",
        f"- 产品分类：{len(top_categories)}",
        f"- 解决方案分类：{len(sol_categories)}",
        f"- 行业分类：{len(industry_categories)}",
        "",
        "## 质量",
        "",
        f"- {_quality_summary(output_root)}",
        "",
        "## 产品分类",
        "",
    ]
    for cat in top_categories:
        summary_lines.append(f"- {cat}：{cat_counts.get(cat, 0)} 个产品")
    if not top_categories:
        summary_lines.append("- 暂无产品分类")
    if sol_categories:
        summary_lines.extend(["", "## 解决方案分类", ""])
        for cat in sol_categories:
            summary_lines.append(f"- {cat}：{cat_counts.get(cat, 0)} 篇方案")
    if industry_categories:
        summary_lines.extend(["", "## 行业分类", ""])
        for cat in industry_categories:
            summary_lines.append(f"- {cat}：{cat_counts.get(cat, 0)} 篇行业文档")
    summary_lines.append("")
    summary.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    return {"readme": str(readme), "index": str(index), "summary": str(summary)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Write standard library docs")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--site-name", default="")
    args = parser.parse_args()
    paths = write_standard_docs(args.output_root, {"site_name": args.site_name})
    print("Library docs:", paths["readme"], paths["index"], paths["summary"])


if __name__ == "__main__":
    main()
