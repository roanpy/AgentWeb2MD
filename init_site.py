#!/usr/bin/env python3
"""AgentWeb2MD Site Initialization Wizard — v2

Creates a validated config directory for a new site in 6 phases:
  1. PROBE:   Fetch homepage, detect sitemap/API/nav structure, CSS selectors
  2. PRESET:  Match a preset (product_catalog_sitemap/api/nav), overlay defaults
  3. CONFIG:  Generate common.json + page-type configs with schema defaults
  4. VALIDATE: Check against formal schema, report errors/warnings
  5. WRITE:   Output config files to config/{site}/
  6. GUIDE:   Print next steps

Usage:
  python init_site.py --site newcustomer --base-url https://example.com
  python init_site.py --site newcustomer --base-url https://example.com --preset product_catalog_sitemap
  python init_site.py --site newcustomer --auto   # probe only, print JSON, no files

For AI agents (non-interactive):
  python init_site.py --site newcustomer --base-url https://example.com --preset product_catalog_sitemap --non-interactive --output-root /path/to/output
"""

import argparse
import json
import os
import re
import sys
from urllib.parse import urlparse

WORKDIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORKDIR)

from config_schema import (
    CONFIG_SCHEMA_V1,
    PRESETS,
    _schema_keys_at,
    _list_keys_at,
    _required_keys_at,
    _field_schema,
    _check_type,
    SCHEMA_VERSION,
)

# ─── Lazy imports for probe phase (may not be needed in --non-interactive) ──

def _import_requests():
    import requests
    return requests

def _import_bs4():
    from bs4 import BeautifulSoup
    return BeautifulSoup


# ─── PROBE: detect site structure (kept from v1, enhanced) ─────────

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

_API_PROBES = [
    ("menu",                 "/api/menu"),
    ("products",             "/api/products"),
    ("product_detail",       "/api/v1/products"),
    ("categories",           "/api/categories"),
    ("nav",                  "/api/nav"),
    ("sitemap",              "/sitemap.xml"),
]


def _probe_site(base_url: str, timeout: int = 15) -> dict:
    """Fetch base_url, detect sitemap, API endpoints, nav structure, and CSS selectors."""
    requests = _import_requests()
    BeautifulSoup = _import_bs4()

    result = {
        "base_url": base_url.rstrip("/"),
        "has_sitemap": False,
        "sitemap_urls": 0,
        "has_api_menu": False,
        "api_menu_url": "",
        "api_hits": {},
        "nav_links": 0,
        "product_links": 0,
        "industry_links": 0,
        "page_title": "",
        "lang": "zh-CN",
        "lang_path": "",
        "content_selector": "main",
        "tabs": {},
        "carousel": "",
        "collapse": "",
        "suggested_mode": None,
        "sample_urls": {"product": [], "industry": [], "solution": []},
    }

    # 1. Check sitemap
    try:
        r = requests.get(base_url.rstrip("/") + "/sitemap.xml", timeout=timeout, headers=HEADERS)
        if r.status_code == 200 and "<url" in r.text[:2000].lower():
            result["has_sitemap"] = True
            urls = re.findall(r'https?://[^<>\s"\']+', r.text)
            result["sitemap_urls"] = len(urls)
            for u in urls:
                if "/product" in u.lower() and len(result["sample_urls"]["product"]) < 5:
                    result["sample_urls"]["product"].append(u)
                if "/industr" in u.lower() and len(result["sample_urls"]["industry"]) < 5:
                    result["sample_urls"]["industry"].append(u)
                if "/solution" in u.lower() and len(result["sample_urls"]["solution"]) < 5:
                    result["sample_urls"]["solution"].append(u)
    except Exception:
        pass

    # 2. Probe API endpoints
    for name, path in _API_PROBES:
        try:
            r = requests.get(base_url.rstrip("/") + path, headers=HEADERS, timeout=5)
            ct = r.headers.get("content-type", "")
            if r.status_code == 200 and ("json" in ct or "xml" in ct):
                result["api_hits"][name] = path
                if "menu" in name:
                    result["has_api_menu"] = True
                    result["api_menu_url"] = path
        except Exception:
            pass

    # 3. Fetch homepage for structure detection
    try:
        r = requests.get(base_url, timeout=timeout, headers=HEADERS)
        if r.status_code != 200:
            print(f"  ⚠ Homepage returned HTTP {r.status_code}")
            return result

        soup = BeautifulSoup(r.text, "html.parser")
        result["page_title"] = (soup.title.string or "").strip()[:100] if soup.title else ""

        # Detect language
        html_el = soup.find("html")
        if html_el and html_el.get("lang"):
            result["lang"] = html_el["lang"]

        # Detect lang path from URL
        href = re.search(r'/([a-z]{2}-[a-z]{2})/?', base_url)
        if href:
            result["lang_path"] = "/" + href.group(1)

        # Content selector detection
        result["content_selector"] = _detect_content_selector(soup)

        result["children_selector"] = _detect_children_selector(soup, base_url)

        # Tab detection
        result["tabs"] = _detect_tabs(soup)

        # Carousel detection
        result["carousel"] = _detect_carousel(soup)

        # Collapse detection
        result["collapse"] = _detect_collapse(soup)

        # Nav links
        all_links = soup.find_all("a", href=True)
        result["nav_links"] = len(all_links)
        for a in all_links:
            href = a.get("href", "")
            if "/product" in href.lower():
                result["product_links"] += 1
            if "/industr" in href.lower() or "/solution" in href.lower():
                result["industry_links"] += 1

    except Exception as e:
        print(f"  ⚠ Error probing site: {e}")

    # 4. Suggest discovery mode
    if result["has_api_menu"]:
        result["suggested_mode"] = "api"
    elif result["has_sitemap"] and result["sitemap_urls"] > 20:
        result["suggested_mode"] = "sitemap"
    else:
        result["suggested_mode"] = "nav"

    return result


_DENYLIST = {"nav", "footer", "header", "aside"}

def _detect_content_selector(soup) -> str:
    best, best_score = None, 0
    for el in soup.select("main, article, .content, #content, .main, .container, [role=main]"):
        if el.name in _DENYLIST or el.get("role") in ("banner", "navigation", "contentinfo"):
            continue
        text = el.get_text(strip=True)
        score = len(text)
        if score > best_score:
            best, best_score = el, score
    if not best:
        return "main"
    if best.get("id"):
        return f"#{best['id']}"
    cls = best.get("class") or []
    if cls:
        return "." + cls[0]
    return best.name


def _detect_tabs(soup) -> dict:
    for sel in ["[role=tablist]", ".tabs", "[class*=tab-list]", "[class*=tab]"]:
        if soup.select_one(sel):
            return {"tab_label": "[role=tab]", "tab_panel": "[role=tabpanel]"}
    return {}


def _detect_carousel(soup) -> str:
    for sel in [".carousel", ".swiper", "[data-carousel]"]:
        if soup.select_one(sel):
            return sel
    return ""


def _detect_children_selector(soup, base_url: str) -> str:
    """Auto-detect CSS selector for sub-product links on a product page."""
    _child_patterns = ["/items/", "/detail/", "/series/", "/models/", "/product/", "/p/"]
    for pattern in _child_patterns:
        links = [a for a in soup.select("main a, article a, [role=main] a")
                 if pattern in (a.get("href", "") or "")]
        if len(links) >= 2:
            return f"main a[href*='{pattern}']"
    return ""


def _detect_collapse(soup) -> str:
    if soup.find("details"):
        return "details"
    for sel in [".accordion", ".collapse", "[data-bs-toggle=collapse]"]:
        if soup.select_one(sel):
            return sel
    return ""


# ─── PRESET: select and overlay ────────────────────────────────────

def _select_preset(probe: dict, preset_override: str | None) -> str:
    if preset_override:
        if preset_override not in PRESETS:
            print(f"  ✗ Unknown preset '{preset_override}'. Available: {list(PRESETS.keys())}")
            sys.exit(1)
        return preset_override

    mode = probe.get("suggested_mode", "nav")
    if mode == "api":
        return "product_catalog_api"
    elif mode == "sitemap":
        return "product_catalog_sitemap"
    else:
        return "product_catalog_nav"


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# ─── CONFIG: generate from preset + probe ──────────────────────────

def _infer_site_id(base_url: str) -> str:
    host = urlparse(base_url).netloc.lower()
    host = re.sub(r'^www\.', '', host)
    return re.sub(r'[^a-z0-9]+', '_', host).strip('_') or "unknown_site"


def _infer_site_name(base_url: str, probe: dict) -> str:
    title = probe.get("page_title", "")
    if title and len(title) > 3:
        return title.split("|")[0].split("-")[0].strip()[:50]
    return urlparse(base_url).netloc


def _infer_url_patterns(probe: dict) -> dict:
    """Guess product/industry URL patterns from sample URLs.
    
    Fixed: for sites with lang paths like /zh-cn/, we find the segment
    containing 'product'/'industr'/'solution' and include everything up to it,
    producing '/zh-cn/products/' instead of just '/zh-cn/'.
    """
    patterns = {"product_pattern": "/products/", "industry_pattern": "/industries/"}
    for url_type in ("product", "industry", "solution"):
        for u in probe["sample_urls"].get(url_type, [])[:3]:
            rel = u.replace(probe["base_url"], "")
            parts = [p for p in rel.split("/") if p]
            # Find the segment matching the type keyword
            target_kw = url_type if url_type != "industry" else "industr"
            match_idx = None
            for i, seg in enumerate(parts):
                if target_kw in seg.lower():
                    match_idx = i
                    break
            if match_idx is not None:
                # Include all segments up to and including the matched one
                pattern = "/" + "/".join(parts[:match_idx + 1]) + "/"
                if url_type == "product":
                    patterns["product_pattern"] = pattern
                elif url_type == "industry":
                    patterns["industry_pattern"] = pattern
            elif len(parts) >= 2:
                # Fallback: use second segment
                segment = "/" + parts[1] + "/"
                if url_type == "product":
                    patterns["product_pattern"] = segment
                elif url_type == "industry":
                    patterns["industry_pattern"] = segment
            break
    return patterns


def _generate_common_config(
    site_id: str,
    site_name: str,
    base_url: str,
    output_root: str,
    preset_name: str,
    probe: dict,
    user_overrides: dict | None = None,
    category_filter: list[str] | None = None,
) -> dict:
    preset_cfg = PRESETS[preset_name]["config"]
    config = _deep_merge(preset_cfg, {})

    # Required identity
    config["site_id"] = site_id
    config["site_name"] = site_name
    config["base_url"] = base_url.rstrip("/")
    config["output_root"] = output_root

    # Language
    lang_path = probe.get("lang_path", "")
    if lang_path:
        config["lang_path"] = lang_path
    config.setdefault("locale", probe.get("lang", "zh-CN"))

    # Discovery
    disc = config.get("discovery", {})
    if disc.get("mode") == "sitemap" and probe.get("has_sitemap"):
        disc["sitemap_url"] = base_url.rstrip("/") + "/sitemap.xml"
    elif disc.get("mode") == "nav":
        disc["start_url"] = base_url
    elif probe.get("has_api_menu"):
        disc["mode"] = None  # API mode: discovery not needed

    url_patterns = _infer_url_patterns(probe)
    disc["product_pattern"] = url_patterns["product_pattern"]
    disc["industry_pattern"] = url_patterns["industry_pattern"]
    config["discovery"] = disc

    # API (for API preset)
    if preset_name == "product_catalog_api" and probe.get("has_api_menu"):
        config.setdefault("api", {})["menu"] = probe.get("api_menu_url", "")

    # HTML components from probe detection
    comp = config.get("html_components", {})
    if probe.get("content_selector") and not comp.get("content_selector"):
        comp["content_selector"] = probe["content_selector"]
    if probe.get("tabs"):
        comp.update(probe["tabs"])
    if probe.get("carousel") and not comp.get("carousel"):
        comp["carousel"] = probe["carousel"]
    if probe.get("collapse") and not comp.get("collapse"):
        comp["collapse"] = probe["collapse"]
    config["html_components"] = comp

    fb = config.get("web_fallback", {})
    if probe.get("children_selector") and not fb.get("children_selector"):
        fb["children_selector"] = probe["children_selector"]
    config["web_fallback"] = fb

    # Apply user overrides win
    if user_overrides:
        config = _deep_merge(config, user_overrides)

    # Apply category filter
    if category_filter:
        config.setdefault("extraction", {})["category_filter"] = category_filter

    return config


def _generate_page_type_config(page_type: str, extra_overrides: dict | None = None) -> dict:
    cfg = {"page_type": page_type}
    if extra_overrides:
        cfg = _deep_merge(cfg, extra_overrides)
    return cfg


# ─── VALIDATE: check against CONFIG_SCHEMA_V1 ──────────────────────

def _validate_config(config: dict) -> tuple[list, list]:
    """Validate config dict against CONFIG_SCHEMA_V1. Returns (errors, warnings)."""
    errors = []
    warnings = []
    schema_props = CONFIG_SCHEMA_V1["properties"]

    # Top-level required
    for key in _required_keys_at(""):
        val = config.get(key)
        if val is None or val == "":
            errors.append(f"'{key}' is required but missing or empty")

    # Unknown top-level keys
    valid_top = _schema_keys_at("")
    for key in config:
        if key.startswith("__"):
            continue
        if key not in valid_top:
            warnings.append(f"Unknown key '{key}' (not in schema v{SCHEMA_VERSION})")

    # Type checks and nested validation
    for key, value in config.items():
        if key.startswith("__") or not isinstance(value, dict):
            continue
        field = schema_props.get(key, {})
        if "properties" in field:
            sub_e, sub_w = _validate_nested(value, field["properties"], f"{key}.")
            errors.extend(sub_e)
            warnings.extend(sub_w)

    # List keys
    for key in _list_keys_at(""):
        val = config.get(key)
        if val is not None and not isinstance(val, list):
            errors.append(f"'{key}' must be a list, got {type(val).__name__}")

    return errors, warnings


def _validate_nested(obj: dict, props: dict, prefix: str) -> tuple[list, list]:
    errors = []
    warnings = []

    # Required in this section
    for key, field in props.items():
        if field.get("required") and key not in obj:
            errors.append(f"'{prefix}{key}' is required but missing")

    # Unknown keys
    for key in obj:
        if key not in props:
            warnings.append(f"Unknown key '{prefix}{key}'")
            continue
        field = props[key]
        val = obj[key]
        expected = field.get("type")
        if expected and val is not None:
            if not _check_type(val, expected):
                errors.append(f"'{prefix}{key}' expected type {expected}, got {type(val).__name__}")

        # Recurse
        if isinstance(val, dict) and "properties" in field:
            sub_e, sub_w = _validate_nested(val, field["properties"], f"{prefix}{key}.")
            errors.extend(sub_e)
            warnings.extend(sub_w)

    return errors, warnings


# ─── WRITE: output config files ────────────────────────────────────

def _write_config_dir(site_id: str, common: dict, page_types: dict[str, dict]) -> str:
    config_dir = os.path.join(WORKDIR, "config", site_id)
    os.makedirs(config_dir, exist_ok=True)

    # Add $schema marker
    common_out = dict(common)
    common_out["$schema"] = f"webextract-config/v{SCHEMA_VERSION}"

    _write_json(os.path.join(config_dir, "common.json"), common_out)
    for pt, cfg in page_types.items():
        _write_json(os.path.join(config_dir, f"{pt}.json"), cfg)

    return config_dir


def _write_json(path: str, obj: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print(f"  ✓ Written {path}")


# ─── INTERACTIVE: ask which product/industry lines to extract ──────

def _ask_categories(probe: dict, args) -> list[str] | None:
    """Ask user which product lines to extract, or use --categories flag.

    Returns list of category display names to filter, or None (= extract all).
    """
    # Non-interactive with explicit --categories
    if args.categories:
        return [c.strip() for c in args.categories.split(",") if c.strip()]

    # Discover categories from probe data
    categories = _discover_categories(probe)

    if not categories:
        return None

    # Non-interactive without --categories → extract all
    if args.non_interactive or args.auto:
        print(f"\n  📂 Detected {len(categories)} product categories (extracting all)")
        return None

    # Interactive: show categories and let user pick
    print(f"\n  📂 Detected {len(categories)} product categories:")
    for i, cat in enumerate(categories, 1):
        print(f"    {i}. {cat}")
    print(f"    0. All categories (default)")

    choice = input(f"  Select categories (e.g. 1,3,5 or 0 for all): ").strip()
    if not choice or choice == "0":
        return None

    selected = []
    for part in choice.split(","):
        idx = int(part.strip()) - 1
        if 0 <= idx < len(categories):
            selected.append(categories[idx])
    if not selected:
        return None

    print(f"  → Selected: {', '.join(selected)}")
    return selected


def _discover_categories(probe: dict) -> list[str]:
    """Discover product category names from sitemap URLs or nav links."""
    categories = []

    # From sitemap sample URLs: extract category segments
    cat_slugs = set()
    for url_type in ("product", "industry", "solution"):
        for u in probe.get("sample_urls", {}).get(url_type, []):
            base = probe.get("base_url", "")
            rel = u.replace(base, "").strip("/")
            parts = [p for p in rel.split("/") if p]
            # Find segment after "products" or similar
            for i, seg in enumerate(parts):
                if seg.lower() in ("products", "industries", "solutions") and i + 1 < len(parts):
                    cat_slugs.add(parts[i + 1])

    # If we have url_category_rule name_map from probe, use those names
    # Otherwise, slug → title case
    for slug in sorted(cat_slugs):
        name = slug.replace("-", " ").replace("_", " ").title()
        categories.append(name)

    return categories


# ─── INTERACTIVE: gather missing fields ────────────────────────────

def _ask_required(preset_name: str, probe: dict) -> dict:
    preset = PRESETS[preset_name]
    required = preset.get("required_overrides", [])
    answers = {}

    skip_auto = {"site_id", "site_name", "base_url", "output_root"}
    for key_path in required:
        if key_path in skip_auto:
            continue

        field = _field_schema(key_path)
        desc = field.get("description", key_path)
        examples = field.get("examples", [])
        example_str = f" (e.g. {examples[0]})" if examples else ""

        val = input(f"  {desc}{example_str}: ").strip()
        if not val:
            continue

        # Set nested value
        parts = key_path.split(".")
        d = answers
        for part in parts[:-1]:
            d.setdefault(part, {})
            d = d[part]
        d[parts[-1]] = val

    return answers


# ─── MAIN ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Probe a site and draft a profile for agent review")
    parser.add_argument("--site", required=True, help="Site ID (slug, used as config dir name)")
    parser.add_argument("--base-url", default="", help="Base URL of the site")
    parser.add_argument("--output-root", default="", help="Output directory path")
    parser.add_argument("--preset", default="", choices=list(PRESETS.keys()), help="Preset to use (auto-detected if omitted)")
    parser.add_argument("--site-name", default="", help="Display name for the site")
    parser.add_argument("--non-interactive", action="store_true", help="Skip interactive prompts")
    parser.add_argument("--auto", action="store_true", help="Probe only, print config JSON, don't write files")
    parser.add_argument("--force", action="store_true", help="Overwrite existing config dir")
    parser.add_argument("--categories", default="", help="Comma-separated category names to extract (default: all)")
    args = parser.parse_args()

    # Resolve base_url
    base_url = args.base_url
    if not base_url:
        if args.non_interactive:
            print("✗ --base-url is required in non-interactive mode")
            sys.exit(1)
        base_url = input("  Website base URL: ").strip()
        if not base_url:
            print("✗ base_url is required")
            sys.exit(1)
    if not base_url.startswith("http"):
        base_url = "https://" + base_url

    # Phase 1: Probe
    print(f"\n🔍 Phase 1: Probing {base_url}...")
    probe = _probe_site(base_url)
    print(f"  Sitemap: {'✓' if probe['has_sitemap'] else '✗'} ({probe['sitemap_urls']} URLs)")
    print(f"  API menu: {'✓' if probe['has_api_menu'] else '✗'}")
    print(f"  API hits: {list(probe['api_hits'].keys()) or 'none'}")
    print(f"  Nav links: {probe['nav_links']} ({probe['product_links']} product, {probe['industry_links']} industry)")
    print(f"  Content selector: {probe['content_selector']}")
    print(f"  Tabs: {probe['tabs'] or 'none'}")
    print(f"  Carousel: {probe['carousel'] or 'none'}")
    print(f"  Collapse: {probe['collapse'] or 'none'}")
    print(f"  Page title: {probe.get('page_title', '(none)')}")
    print(f"  Suggested mode: {probe['suggested_mode']}")

    # Phase 2: Preset
    preset_name = _select_preset(probe, args.preset or None)
    print(f"\n📋 Phase 2: Using preset '{preset_name}'")
    print(f"  {PRESETS[preset_name]['description']}")

    # Identity
    site_id = args.site
    site_name = args.site_name or _infer_site_name(base_url, probe)
    output_root = args.output_root
    if not output_root and not args.non_interactive:
        output_root = input(f"  Output directory [{site_name}]: ").strip() or site_name
    if not output_root:
        output_root = os.path.join("/tmp", site_id)
        print(f"  ⚠ No output_root, using {output_root}")

    # Phase 3: Collect overrides (interactive only)
    user_overrides = {}
    if not args.non_interactive and not args.auto:
        print(f"\n✏️  Phase 3: Configuration")
        print(f"  Preset requires: {', '.join(PRESETS[preset_name]['required_overrides'])}")
        user_overrides = _ask_required(preset_name, probe)

    # Phase 3.5: Ask which product/industry lines to extract
    category_filter = _ask_categories(probe, args)

    # Phase 4: Generate
    print(f"\n⚙️  Phase 4: Generating config...")
    common = _generate_common_config(
        site_id=site_id,
        site_name=site_name,
        base_url=base_url,
        output_root=output_root,
        preset_name=preset_name,
        probe=probe,
        user_overrides=user_overrides,
        category_filter=category_filter,
    )

    entity_types = common.get("extraction", {}).get("entity_types", ["product", "industry"])
    if probe["sample_urls"]["solution"] and "solution" not in entity_types:
        entity_types.append("solution")

    page_type_configs = {}
    for pt in entity_types:
        page_type_configs[pt] = _generate_page_type_config(pt)

    # --auto: just print and exit
    if args.auto:
        print(json.dumps(common, ensure_ascii=False, indent=2))
        return

    # Check preset required_overrides even in non-interactive mode
    preset_required = PRESETS[preset_name].get("required_overrides", [])
    auto_filled = {"site_id", "site_name", "base_url", "output_root"}
    missing_overrides = []
    for key_path in preset_required:
        if key_path in auto_filled:
            continue
        parts = key_path.split(".")
        val = common
        for p in parts:
            if isinstance(val, dict):
                val = val.get(p)
            else:
                val = None
                break
        if not val:
            missing_overrides.append(key_path)
    if missing_overrides:
        print(f"\n  ⚠ Preset '{preset_name}' requires these keys but they're empty:")
        for k in missing_overrides:
            field = _field_schema(k)
            print(f"    - {k}: {field.get('description', k)}")
        print(f"  The config will be structurally valid but may fail at runtime.")
        print(f"  Edit config/{site_id}/common.json to fill them before extraction.")

    # Phase 5: Validate
    print(f"\n✔ Phase 5: Validating against schema v{SCHEMA_VERSION}...")
    errors, warnings = _validate_config(common)
    if warnings:
        for w in warnings:
            print(f"  ⚠ {w}")
    if errors:
        for e in errors:
            print(f"  ✗ {e}")
        if not args.non_interactive:
            fix = input("\n  Fix errors and continue? [y/N]: ").strip().lower()
            if fix != "y":
                print("  Aborted. Fix and re-run.")
                sys.exit(1)
    else:
        print(f"  ✓ No errors, {len(warnings)} warning(s)")

    # Phase 6: Write
    config_dir = os.path.join(WORKDIR, "config", site_id)
    if os.path.exists(config_dir) and not args.force:
        print(f"\n  ✗ {config_dir} already exists. Use --force to overwrite.")
        sys.exit(1)

    print(f"\n💾 Phase 6: Writing config files...")
    config_dir = _write_config_dir(site_id, common, page_type_configs)

    # Summary
    print(f"\n{'='*60}")
    print(f"✅ Draft profile created: {site_id}")
    print(f"  Config dir:   {config_dir}")
    print(f"  Preset:       {preset_name}")
    print(f"  Discovery:    {common.get('discovery', {}).get('mode', 'api')}")
    print(f"  Page types:   {', '.join(entity_types)}")
    print(f"  Output:       {output_root}")
    print(f"")
    print(f"  Agent review required:")
    print(f"    1. Confirm scope and review config/{site_id}/common.json")
    print(f"    2. Cap discovery and keep the sample output under /tmp")
    print(f"    3. Run a sample: python extract_generic.py --site {site_id}")
    print(f"    4. Inspect Markdown and run quality_report.py before expanding scope")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
