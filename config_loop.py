#!/usr/bin/env python3
import argparse
import json
import os
from datetime import datetime
from urllib.parse import urlparse

from agentweb2md_paths import validate_identifier, validate_site_id, writable_config_root
from extract_generic import load_config, validate_config
from init_site import _probe_site, _generate_common_config, _select_preset, _infer_site_id, _infer_site_name
from quality_report import build_report, write_report


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def default_site(url):
    host = urlparse(url).netloc or urlparse("https://" + url).netloc
    return host.split(".")[0] or "site"


def write_config(site, common, page_types):
    validate_site_id(site)
    for page_type in page_types:
        validate_identifier(page_type, "page type")
    out_dir = os.path.join(writable_config_root(), site)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "common.json"), "w", encoding="utf-8") as f:
        json.dump(common, f, ensure_ascii=False, indent=2)
    for page_type in page_types:
        path = os.path.join(out_dir, f"{page_type}.json")
        if os.path.exists(path):
            continue
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"page_type": page_type, "content_blocks": ["body_html"], "html_components": {}, "templates": {}}, f, ensure_ascii=False, indent=2)
    return out_dir


def suggestions_for(config_errors, config_warnings, quality=None):
    suggestions = []
    for msg in config_errors:
        suggestions.append({"rule": "config-error", "severity": "high", "message": msg, "suggested_config_patch": {}})
    for msg in config_warnings:
        severity = "medium" if "Unknown" in msg or "dead key" in msg else "low"
        suggestions.append({"rule": "config-warning", "severity": severity, "message": msg, "suggested_config_patch": {}})
    if quality:
        if quality.get("files_scored", 0) == 0:
            suggestions.append({
                "rule": "empty-sample",
                "severity": "high",
                "message": "No sample Markdown files were produced; check discovery rules and content_selector.",
                "suggested_config_patch": {"html_components.content_selector": "main"},
            })
        elif quality.get("site_score", 100) < 70:
            suggestions.append({
                "rule": "low-quality-sample",
                "severity": "medium",
                "message": f"Sample quality score is {quality.get('site_score')}; inspect worst files before full extraction.",
                "suggested_config_patch": {},
            })
    return suggestions


def build_validation_report(site, url, common, quality=None):
    errors, warnings = validate_config(common)
    return {
        "schema": "config-validation-v1",
        "site": site,
        "url": url,
        "run_at": now_iso(),
        "config_errors": errors,
        "config_warnings": warnings,
        "quality": quality or {},
        "suggestions": suggestions_for(errors, warnings, quality=quality),
    }


def write_validation_report(report, output_root):
    os.makedirs(output_root, exist_ok=True)
    json_path = os.path.join(output_root, "_config_validation.json")
    md_path = os.path.join(output_root, "_config_validation.md")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    lines = ["# 配置验证报告", "", f"站点：{report['site']}", f"建议数：{len(report['suggestions'])}", ""]
    for item in report["suggestions"]:
        lines.append(f"- {item['severity']} · {item['rule']} · {item['message']}")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return json_path, md_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Draft and validate a site config for agent review")
    parser.add_argument("url")
    parser.add_argument("--site", default="")
    parser.add_argument("--output-root", default="")
    parser.add_argument("--page-types", default="product,industry")
    parser.add_argument("--apply", action="store_true", help="write the reviewed draft config and validation report")
    parser.add_argument("--sample-root", default="", help="existing sample output root to score")
    args = parser.parse_args(argv)
    site = args.site or default_site(args.url)
    try:
        validate_site_id(site)
    except ValueError as error:
        parser.error(str(error))
    page_types = [p.strip() for p in args.page_types.split(",") if p.strip()]
    try:
        for page_type in page_types:
            validate_identifier(page_type, "page type")
    except ValueError as error:
        parser.error(str(error))
    probe = _probe_site(args.url)
    preset = _select_preset(probe, None)
    common = _generate_common_config(
        site_id=site,
        site_name=_infer_site_name(args.url, probe),
        base_url=args.url,
        output_root=args.output_root or f"./output/{site}",
        preset_name=preset,
        probe=probe,
    )
    common["output_root"] = common.get("output_root") or f"./output/{site}"
    quality = build_report(args.sample_root, site=site) if args.sample_root else None
    report = build_validation_report(site, args.url, common, quality=quality)
    if report["config_errors"]:
        print(json.dumps({"common": common, "validation": report}, ensure_ascii=False, indent=2))
        return 1
    if args.apply:
        write_config(site, common, page_types)
        if args.sample_root:
            write_report(quality, args.sample_root)
        write_validation_report(report, common["output_root"])
    else:
        print(json.dumps({"common": common, "validation": report}, ensure_ascii=False, indent=2))
    sample_failed = quality and not quality.get("agent_verdict", {}).get("ok", False)
    return int(bool(report["config_errors"]) or sample_failed)


if __name__ == "__main__":
    raise SystemExit(main())
