#!/usr/bin/env python3
import argparse
import json
import os
import re
from datetime import datetime

from resources_output import load_resource_summary
from specs_output import load_spec_summary

SKIP_NAMES = {
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
POLLUTION = [
    "产品资讯", "其他产品", "正在加载", "请稍候", "显示更多",
    "Beckhoff Live", "SPS 2025", "Day 1:", "技术演示", "AI technologies",
    "#media-popup", "data-config 中的 panels",
]


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def iter_md_files(output_root):
    for root, dirs, files in os.walk(output_root):
        dirs[:] = [d for d in dirs if d not in {"图片", "资料", "_archive"}]
        for name in files:
            if not name.endswith(".md") or name in SKIP_NAMES:
                continue
            yield os.path.join(root, name)


def image_issues(md_path, text):
    issues = []
    md_dir = os.path.dirname(md_path)
    refs = re.findall(r'!\[[^\]]*\]\(([^)]+)\)', text)
    local_refs = [r for r in refs if r.startswith("图片/")]
    missing = [r for r in set(local_refs) if not os.path.exists(os.path.join(md_dir, r))]
    if missing:
        issues.append(f"broken-image x{len(missing)}")
    if not refs:
        issues.append("no-image")
    return issues, len(refs)


def resource_issues(md_path, text):
    issues = []
    md_dir = os.path.dirname(md_path)
    refs = re.findall(r'(?<!!)\[[^\]]*\]\(([^)]+)\)', text)
    local_refs = [r for r in refs if r.startswith("资料/") or r.startswith("../资料/")]
    missing = [r for r in set(local_refs) if not os.path.exists(os.path.normpath(os.path.join(md_dir, r)))]
    if missing:
        issues.append(f"broken-resource x{len(missing)}")
    return issues, len(local_refs)


def empty_heading_issues(lines):
    issues = []
    for i, line in enumerate(lines):
        match = re.match(r'^(#{1,6})\s+(.+)', line.strip())
        if not match:
            continue
        level = len(match.group(1))
        # Walk forward: is there any non-heading content before the next
        # heading at a STRICTLY higher level (parent closes)? If yes, not empty.
        # Same-level and deeper headings are part of this section's subtree;
        # their body content counts as content for this heading too.
        j = i + 1
        has_content = False
        while j < len(lines):
            s = lines[j].strip()
            if not s:
                j += 1
                continue
            next_heading = re.match(r'^(#{1,6})\s+', s)
            if next_heading:
                if len(next_heading.group(1)) < level:
                    break
                # same level or deeper — keep scanning for content under it
                j += 1
                continue
            has_content = True
            break
        if not has_content:
            issues.append(f"empty-section:{match.group(2)[:30]}")
    return issues


def heading_issues(lines):
    issues = []
    prev = 0
    for i, line in enumerate(lines, 1):
        match = re.match(r'^(#{1,6})\s*(.*)', line.strip())
        if not match:
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        if not title:
            issues.append(f"empty-heading:L{i}")
        if prev and level > prev + 1:
            issues.append(f"heading-jump:L{i}:{prev}->{level}")
        prev = level
    return issues


def score_file(md_path, output_root):
    text = open(md_path, encoding="utf-8", errors="ignore").read()
    lines = text.splitlines()
    issues = []
    issues.extend(heading_issues(lines))
    issues.extend(empty_heading_issues(lines))
    img_issues, image_count = image_issues(md_path, text)
    issues.extend(img_issues)
    res_issues, resource_count = resource_issues(md_path, text)
    issues.extend(res_issues)
    pollution_hits = [kw for kw in POLLUTION if kw in text]
    issues.extend(f"pollution:{kw}" for kw in pollution_hits)
    if "<div" in text or "<span" in text:
        issues.append("raw-html")
    word_count = len(re.findall(r'[\w\u4e00-\u9fff]+', text))
    heading_count = len(re.findall(r'^#{1,6}\s+', text, re.MULTILINE))
    if word_count < 120:
        issues.append("too-short")
    score = 100
    score -= min(30, 10 * len([i for i in issues if i.startswith("heading") or i.startswith("empty-")]))
    score -= min(30, 30 if "too-short" in issues else 0)
    score -= min(20, 10 * len(img_issues))
    score -= min(20, 8 * len(pollution_hits) + (10 if "raw-html" in issues else 0))
    return {
        "path": os.path.relpath(md_path, output_root),
        "score": max(0, score),
        "issues": issues,
        "word_count": word_count,
        "heading_count": heading_count,
        "image_count": image_count,
        "resource_count": resource_count,
    }


def build_report(output_root, site="", expect_specs=False, expect_resources=False):
    files = list(iter_md_files(output_root))
    per_file = [score_file(path, output_root) for path in files]
    worst = sorted(per_file, key=lambda item: item["score"])[:20]
    site_score = round(sum(item["score"] for item in per_file) / len(per_file), 1) if per_file else 0
    spec_summary = load_spec_summary(output_root, len(files))
    resource_summary = load_resource_summary(output_root, len(files))
    report = {
        "schema": "quality-report-v1",
        "site": site,
        "run_at": now_iso(),
        "site_score": site_score,
        "files_total": len(files),
        "files_scored": len(per_file),
        "worst": worst,
        "per_file": per_file,
        "spec_summary": spec_summary,
        "resource_summary": resource_summary,
        "expectations": {"specs": expect_specs, "resources": expect_resources},
    }
    report["agent_verdict"] = agent_verdict(report)
    return report


def agent_verdict(report):
    reasons = []
    if report.get("files_scored", 0) <= 0:
        reasons.append("no-files-scored")
    if report.get("site_score", 0) < 70:
        reasons.append("site-score-below-70")
    issue_text = "\n".join(",".join(item.get("issues", [])) for item in report.get("per_file", []))
    if "broken-image" in issue_text:
        reasons.append("broken-image")
    if "broken-resource" in issue_text:
        reasons.append("broken-resource")
    expectations = report.get("expectations", {})
    if expectations.get("specs") and report.get("spec_summary", {}).get("pages_with_specs", 0) <= 0:
        reasons.append("missing-expected-specs")
    if expectations.get("resources") and report.get("resource_summary", {}).get("total_resources", 0) <= 0:
        reasons.append("missing-expected-resources")
    if not reasons:
        next_action = "approve_full_run"
    elif "no-files-scored" in reasons:
        next_action = "fix_discovery_or_selectors"
    else:
        next_action = "fix_config_and_rerun_sample"
    return {
        "ok": not reasons,
        "reasons": reasons,
        "next_action": next_action,
    }


def write_report(report, output_root):
    meta_dir = os.path.join(output_root, "_meta")
    os.makedirs(meta_dir, exist_ok=True)
    json_path = os.path.join(meta_dir, "_quality_report.json")
    md_path = os.path.join(output_root, "_quality_report.md")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    spec = report.get("spec_summary", {})
    resources = report.get("resource_summary", {})
    verdict = report.get("agent_verdict", {})
    lines = [
        "# 质量报告",
        "",
        f"总体分数：{report['site_score']}",
        f"文件数：{report['files_scored']}",
        f"结构化属性覆盖：{spec.get('pages_with_specs', 0)}/{report['files_scored']}，共 {spec.get('total_specs', 0)} 条",
        f"资料覆盖：{resources.get('pages_with_resources', 0)}/{report['files_scored']}，共 {resources.get('total_resources', 0)} 个，{resources.get('total_bytes', 0)} bytes",
        f"Agent 判定：{verdict.get('next_action', 'unknown')}",
        "",
        "## 最差样本",
    ]
    for item in report["worst"]:
        issues = ", ".join(item["issues"][:6]) or "无"
        lines.append(f"- {item['score']} · `{item['path']}` · {issues}")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return json_path, md_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Score extracted Markdown quality")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--site", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--expect-specs", action="store_true")
    parser.add_argument("--expect-resources", action="store_true")
    args = parser.parse_args(argv)
    report = build_report(args.output_root, site=args.site, expect_specs=args.expect_specs, expect_resources=args.expect_resources)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report
    paths = write_report(report, args.output_root)
    print(f"Quality report: {paths[0]} {paths[1]}")
    return report


if __name__ == "__main__":
    main()
