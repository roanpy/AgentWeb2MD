"""AgentWeb2MD Formal Config Schema — v1

Defines every config key the engine reads: type, required, default, description,
editor hint (for AI agents / init wizard), and which sites use it.

Design principles (from industry research):
  - Firecrawl: 3-layer config (discovery → extraction → output)
  - Apify: JSON Schema + editor hints for AI agents
  - Scrapit: fallback selectors as arrays
  - Crawl4AI: CSS-first, LLM-fallback
  - Jina Reader: named presets for common site archetypes
  - ScrapAI: validated JSON → generic runtime spider

This schema is the SINGLE SOURCE OF TRUTH for what the engine accepts.
extract_generic.py's old _CONFIG_SCHEMA / _CONFIG_LIST_KEYS are superseded.
"""

SCHEMA_VERSION = 1

# ─── Field definition ───────────────────────────────────────────────
# Each field: {
#   type:       JSON Schema type string or list of allowed types
#   required:   bool — engine will fail without it
#   default:    default value (None = no default, must be provided if required)
#   description: human-readable explanation (used by init_site wizard)
#   editor:     hint for AI agent / wizard UI
#               "text"      — free text
#               "url"       — URL string
#               "selector"  — CSS selector
#               "select"    — pick from enum
#               "multiselect"— pick multiple from enum
#               "key_value" — object mapping string→string
#               "key_any"   — object mapping string→any
#               "flag"      — boolean toggle
#               "number"    — numeric
#               "list_text" — list of strings
#               "list_selector" — list of CSS selectors
#               "template"  — string with {variable} placeholders
#               "json"      — arbitrary JSON
#   enum:       list of allowed values (for "select"/"multiselect")
#   items:      for list types, item schema (shorthand)
#   properties: for object types, nested field definitions
#   examples:   list of example values from real sites
# }
# ─────────────────────────────────────────────────────────────────────

CONFIG_SCHEMA_V1 = {
    "$schema": "webextract-config/v1",
    "version": SCHEMA_VERSION,

    "properties": {
        # ── IDENTITY ──────────────────────────────────────────────
        "site_id": {
            "type": "string",
            "required": True,
            "default": None,
            "description": "Unique slug identifying this site (used as config dir name and baseline key)",
            "editor": "text",
            "examples": ["example", "docs", "catalog"],
        },
        "site_name": {
            "type": "string",
            "required": True,
            "default": None,
            "description": "Human-readable site name (used in output footers and 三件套)",
            "editor": "text",
            "examples": ["Example Site", "Documentation", "Product Catalog"],
        },
        "base_url": {
            "type": "string",
            "required": True,
            "default": None,
            "description": "Root URL of the website (used for URL resolution and web fallback)",
            "editor": "url",
            "examples": ["https://example.com", "https://docs.example.com"],
        },

        # ── DISCOVERY (how to find pages) ─────────────────────────
        "discovery": {
            "type": "object",
            "required": False,
            "default": {},
            "description": "How the engine discovers product/industry/solution URLs on the site",
            "editor": "key_any",
            "properties": {
                "mode": {
                    "type": "string",
                    "required": False,
                    "default": None,
                    "description": (
                        "Discovery strategy: "
                        "'sitemap' = parse XML sitemap for URLs, "
                        "'nav' = crawl rendered page and extract nav links, "
                        "unset = use the configured API menu endpoint"
                    ),
                    "editor": "select",
                    "enum": ["sitemap", "nav", "crawl", "url_list", None],
                    "examples": ["sitemap", "nav"],
                },
                "sitemap_url": {
                    "type": "string",
                    "required": False,
                    "default": None,
                    "description": "Explicit sitemap URL (default: {base_url}/sitemap.xml)",
                    "editor": "url",
                    "examples": ["https://example.com/sitemap.xml"],
                },
                "url": {
                    "type": "string",
                    "required": False,
                    "default": None,
                    "description": "Alias for sitemap_url (deprecated, use sitemap_url instead)",
                    "editor": "url",
                },
                "urls": {
                    "type": "array",
                    "required": False,
                    "default": [],
                    "description": "Static URL list for url_list mode; each item may include url, title/name, and category.",
                    "editor": "json",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "required": True, "editor": "url"},
                            "title": {"type": "string", "required": False, "editor": "text"},
                            "name": {"type": "string", "required": False, "editor": "text"},
                            "category": {"type": "string", "required": False, "editor": "text"},
                        },
                    },
                    "examples": [[{"url": "https://example.com/article", "title": "Example Article", "category": "Blog"}]],
                },
                "start_url": {
                    "type": "string",
                    "required": False,
                    "default": None,
                    "description": "Entry page for nav/crawl mode (default: base_url)",
                    "editor": "url",
                    "examples": ["https://example.com/products/"],
                },
                "product_pattern": {
                    "type": "string",
                    "required": False,
                    "default": "/products/",
                    "description": "URL substring that identifies product pages",
                    "editor": "text",
                    "examples": ["/zh-cn/products/", "/products/"],
                },
                "industry_pattern": {
                    "type": "string",
                    "required": False,
                    "default": "/industries/",
                    "description": "URL substring that identifies industry/solution pages",
                    "editor": "text",
                    "examples": ["/zh-cn/industries/", "/industries/"],
                },
                "exclude_pattern": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "URL substring to exclude from discovery",
                    "editor": "text",
                    "examples": ["product-news", "/terms/"],
                },
                "nav_selector": {
                    "type": "string",
                    "required": False,
                    "default": "a",
                    "description": "CSS selector for nav links in nav/crawl mode",
                    "editor": "selector",
                    "examples": ["a[href*='/products/']"],
                },
                "name_case": {
                    "type": "string",
                    "required": False,
                    "default": "title",
                    "description": "How to transform URL-derived names: 'title'|'preserve'|'upper'|'lower'",
                    "editor": "select",
                    "enum": ["title", "preserve", "upper", "lower"],
                },
                "name_from_url": {
                    "type": "boolean",
                    "required": False,
                    "default": False,
                    "description": "Derive item names from URL slug instead of link text",
                    "editor": "flag",
                },
                "require_html": {
                    "type": "boolean",
                    "required": False,
                    "default": True,
                    "description": "Only include URLs containing '.html' (sitemap mode)",
                    "editor": "flag",
                },
                "sitemap_pdp_pattern": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Only follow sitemap-index child URLs containing this substring",
                    "editor": "text",
                },
                "sitemap_max_sub": {
                    "type": "integer",
                    "required": False,
                    "default": 5,
                    "description": "Maximum child sitemaps to follow from a sitemap index",
                    "editor": "number",
                },
                "max_pages": {
                    "type": "integer",
                    "required": False,
                    "default": 0,
                    "description": "Maximum discovered pages to extract; 0 means no cap",
                    "editor": "number",
                },
                "url_include_patterns": {
                    "type": "array",
                    "required": False,
                    "default": [],
                    "description": "Regex patterns; keep only discovered items whose URL/name/category matches one",
                    "editor": "list_text",
                },
                "url_exclude_patterns": {
                    "type": "array",
                    "required": False,
                    "default": [],
                    "description": "Regex patterns; drop discovered items whose URL/name/category matches one",
                    "editor": "list_text",
                },
                "skip_list_pages": {
                    "type": "boolean",
                    "required": False,
                    "default": True,
                    "description": "Auto-skip URLs that look like list/index pages (e.g. /reports, /list, ?page=N); only keep detail pages in url_list mode",
                    "editor": "flag",
                },
                "category_name": {
                    "type": "string",
                    "required": False,
                    "default": "All",
                    "description": "Fallback category name when no url_category_rule matches (nav mode)",
                    "editor": "text",
                    "examples": ["Products"],
                },
                "url_category_rule": {
                    "type": "object",
                    "required": False,
                    "default": None,
                    "description": "Rule to group products by URL path segment into categories",
                    "editor": "json",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "required": True,
                            "description": "Regex with capture group for category slug",
                            "editor": "text",
                            "examples": ["/zh-cn/products/([^/]+)"],
                        },
                        "group": {
                            "type": "integer",
                            "required": False,
                            "default": 1,
                            "description": "Capture group index for category slug",
                            "editor": "number",
                        },
                        "name_map": {
                            "type": "object",
                            "required": False,
                            "default": {},
                            "description": "Map slug → display name",
                            "editor": "key_value",
                            "examples": [{"ipc": "工业PC", "i-o": "IO系统"}],
                        },
                    },
                },
            },
        },

        # ── API (backend endpoints) ───────────────────────────────
        "api": {
            "type": "object",
            "required": False,
            "default": {},
            "description": "Backend API endpoints for JSON-driven sites (empty string = use web fallback)",
            "editor": "key_any",
            "properties": {
                "menu": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "API endpoint returning site navigation/menu JSON",
                    "editor": "text",
                    "examples": ["/api/menu"],
                },
                "product_detail": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "API endpoint for product detail (with {id} placeholder)",
                    "editor": "text",
                    "examples": ["/api/products/{id}"],
                },
                "product_children": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "API endpoint for product sub-items",
                    "editor": "text",
                    "examples": ["/api/products/{id}/children"],
                },
                "industry_detail": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "API endpoint for industry/solution detail",
                    "editor": "text",
                    "examples": ["/api/industries/{id}"],
                },
                "industry_cases": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "API endpoint for industry case studies",
                    "editor": "text",
                    "examples": ["/api/industries/{id}/cases"],
                },
            },
        },

        # ── HTML COMPONENTS (DOM extraction rules) ────────────────
        "html_components": {
            "type": "object",
            "required": False,
            "default": {},
            "description": "CSS selectors and DOM processing rules for content extraction",
            "editor": "key_any",
            "properties": {
                "content_selector": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Main content container selector (engine extracts only this region)",
                    "editor": "selector",
                    "examples": ["main", "#content", ".product-detail"],
                },
                "tab_label": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Selector for tab labels (each tab becomes a section in output)",
                    "editor": "selector",
                    "examples": ["button.nav-link", "[role='tab']"],
                },
                "tab_panel": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Selector for tab content panels",
                    "editor": "selector",
                    "examples": ["div.tab-pane", "[role='tabpanel']"],
                },
                "tab_container": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Selector for the overall tab widget container",
                    "editor": "selector",
                    "examples": [".tabs", "[role='tablist']"],
                },
                "tab_header_to_decompose": {
                    "type": "array",
                    "required": False,
                    "default": [],
                    "description": "Tab header elements to remove from output (nav UI noise)",
                    "editor": "list_selector",
                    "examples": [["ul.nav-tabs", ".tabs-header"]],
                },
                "decompose_selectors": {
                    "type": "array",
                    "required": False,
                    "default": [],
                    "description": "Elements to remove from HTML before conversion (ads, popups, boilerplate)",
                    "editor": "list_selector",
                    "examples": [[".account-widget", ".cookie-banner"]],
                },
                "carousel": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Selector for image carousel/slider widget",
                    "editor": "selector",
                    "examples": [".carousel"],
                },
                "carousel_mode": {
                    "type": "string",
                    "required": False,
                    "default": "first",
                    "description": "Carousel extraction: 'first' = first slide only, 'all' = all slides",
                    "editor": "select",
                    "enum": ["first", "all"],
                },
                "carousel_image_selector": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Selector for images within carousel",
                    "editor": "selector",
                    "examples": [".carousel img"],
                },
                "collapse": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Selector for collapsible/accordion sections",
                    "editor": "selector",
                    "examples": [".accordion"],
                },
                "collapse_header": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Selector for collapse section headers (becomes headings in output)",
                    "editor": "selector",
                    "examples": [".accordion-header"],
                },
                "contact_block_selector": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Selector for contact form blocks to remove",
                    "editor": "selector",
                    "examples": [".contact-position-container"],
                },
                "boilerplate_disable": {
                    "type": "array",
                    "required": False,
                    "default": [],
                    "description": "Built-in boilerplate removal patterns to disable",
                    "editor": "list_text",
                },
                "boilerplate_class_exceptions": {
                    "type": "array",
                    "required": False,
                    "default": [],
                    "description": "CSS classes that should NOT be removed by boilerplate cleanup",
                    "editor": "list_selector",
                    "examples": [["media-popup"]],
                },
                "download_images": {
                    "type": "boolean",
                    "required": False,
                    "default": False,
                    "description": "Download and save images locally (vs. leaving as remote URLs)",
                    "editor": "flag",
                },
                "download_link_patterns": {
                    "type": "array",
                    "required": False,
                    "default": [],
                    "description": "URL substrings identifying downloadable resources (PDF, ZIP, etc.)",
                    "editor": "list_text",
                    "examples": [["downloads.example.com", ".pdf", ".zip"]],
                },
                "download_tab_labels": {
                    "type": "array",
                    "required": False,
                    "default": [],
                    "description": "Tab labels whose content should be treated as resource downloads",
                    "editor": "list_text",
                    "examples": [["Downloads", "资料下载"]],
                },
                "related_tab_labels": {
                    "type": "array",
                    "required": False,
                    "default": [],
                    "description": "Tab labels for related products (link list, not full content)",
                    "editor": "list_text",
                },
                "card_grid_selector": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Selector for card grid layouts to flatten",
                    "editor": "selector",
                },
                "base_heading_level": {
                    "type": "integer",
                    "required": False,
                    "default": 0,
                    "description": "Minimum heading level for output (0=auto, 2=start at ##)",
                    "editor": "number",
                    "examples": [0, 2, 3],
                },
                "empty_header_fill": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Text to insert for empty section headers",
                    "editor": "text",
                },
                "image_name_pattern": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Template for downloaded image filenames: {current}, {module} placeholders",
                    "editor": "template",
                    "examples": ["{current}_{module}_配图"],
                },
                "image_name_fallback": {
                    "type": "string",
                    "required": False,
                    "default": "image",
                    "description": "Fallback image filename prefix when no context available",
                    "editor": "text",
                },
                "visual_heading_pattern": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "HTML pattern identifying visual-only headings (styled bold text, not <hN>)",
                    "editor": "text",
                },
                "pair_table_header": {
                    "type": "boolean",
                    "required": False,
                    "default": False,
                    "description": "Pair header row with data rows in tables",
                    "editor": "flag",
                },
                "flattened_table_headers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "required": False,
                    "default": [],
                    "description": "Header labels for flattened metric grids (one cell per paragraph). Engine rebuilds the grid as a Markdown table when these labels match.",
                    "editor": "list",
                },
                "unescape_double_escaped_html": {
                    "type": "boolean",
                    "required": False,
                    "default": False,
                    "description": "Decode &lt; / &gt; / &quot; in source HTML before BeautifulSoup parses. Enable only when an API genuinely returns double-escaped nested HTML.",
                    "editor": "flag",
                },
                "unwrap_iframe_srcdoc": {
                    "type": "boolean",
                    "required": False,
                    "default": False,
                    "description": "Replace <iframe srcdoc=\"...\"> with the unescaped inner HTML before BeautifulSoup parses. Enable only when content is wrapped in iframe srcdoc.",
                    "editor": "flag",
                },
                "subheading_demote_patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "required": False,
                    "default": [],
                    "description": "Heading title substrings to demote when a component emits child headings at the same level as their parent.",
                    "editor": "list",
                },
                "tab_heading_level": {
                    "type": "integer",
                    "required": False,
                    "default": 0,
                    "description": "Heading level for tab section headers (0 = base+1)",
                    "editor": "number",
                },
                "collapse_heading_level": {
                    "type": "integer",
                    "required": False,
                    "default": 0,
                    "description": "Heading level for collapse section headers (0 = base+2)",
                    "editor": "number",
                },
                "layout_table_heading_level": {
                    "type": "integer",
                    "required": False,
                    "default": 0,
                    "description": "Heading level for layout table captions (0 = base+2)",
                    "editor": "number",
                },
                "related_product_wiki_template": {
                    "type": "string",
                    "required": False,
                    "default": "[[{name}]]：{desc}",
                    "description": "Template for related product wiki links: {name}, {desc} placeholders",
                    "editor": "template",
                },
                "visual_heading_sizes": {
                    "type": "array",
                    "required": False,
                    "default": [],
                    "description": "Font sizes (px) whose bold styled text should be promoted to H4 headings",
                    "editor": "list_number",
                    "examples": [[24]],
                },
                "site_selectors": {
                    "type": "object",
                    "required": False,
                    "default": {},
                    "description": "Map domain → CSS selector for per-site content extraction. Overrides content_selector for matching domains.",
                    "editor": "key_value",
                    "examples": [{"www.sohu.com": "article, .article, #mp-editor"}],
                },
            },
        },

        # ── FIELD MAPPING (JSON API response → engine fields) ─────
        "field_mapping": {
            "type": "object",
            "required": False,
            "default": {},
            "description": (
                "Maps engine field names → JSON API response field names. "
                "Only needed for API-driven sites. "
                "For web-scraped sites, use html_components selectors instead."
            ),
            "editor": "key_value",
            "properties": {
                "item_id": {"type": "string", "description": "API field for item ID", "examples": ["id"]},
                "item_name": {"type": "string", "description": "API field for item name", "examples": ["name"]},
                "item_features": {"type": "string", "description": "API field for short features", "examples": ["summary"]},
                "item_cover": {"type": "string", "description": "API field for cover image URL", "examples": ["cover_image"]},
                "product_html": {"type": "string", "description": "API field for product HTML body", "examples": ["html"]},
                "industry_html": {"type": "string", "description": "API field for industry HTML body", "examples": ["content"]},
                "category_name": {"type": "string", "description": "API field for category name", "examples": ["name"]},
                "category_children": {"type": "string", "description": "API field for child items", "examples": ["children"]},
                "menu_categories": {"type": "string", "description": "API field for menu product categories", "examples": ["categories"]},
                "menu_industries": {"type": "string", "description": "API field for menu industry list", "examples": ["industries"]},
                "case_title": {"type": "string", "description": "API field for case study title"},
                "case_summary": {"type": "string", "description": "API field for case study summary"},
                "case_html": {"type": "string", "description": "API field for case study HTML body"},
                "product_html_selector": {"type": "string", "description": "CSS selector for product HTML in web-fallback mode", "examples": ["main"]},
                "industry_html_selector": {"type": "string", "description": "CSS selector for industry HTML in web-fallback mode", "examples": ["main"]},
            },
        },

        # ── FILTERS (noise removal) ───────────────────────────────
        "filters": {
            "type": "object",
            "required": False,
            "default": {},
            "description": "Post-conversion noise removal and content filtering rules",
            "editor": "key_any",
            "properties": {
                "noise_text_keywords_exact": {
                    "type": "array",
                    "required": False,
                    "default": [],
                    "description": "Lines matching these strings exactly are removed (CTA buttons, nav labels)",
                    "editor": "list_text",
                    "examples": [["联系我们", "了解更多", "Request Quote"]],
                },
                "noise_prefix_chars": {
                    "type": "array",
                    "required": False,
                    "default": [],
                    "description": "Lines starting with these characters are removed (garbled encoding artifacts)",
                    "editor": "list_text",
                    "examples": [["建随", "问？"]],
                },
                "image_ignore_keywords": {
                    "type": "array",
                    "required": False,
                    "default": [],
                    "description": "Image URLs/filenames containing these keywords are skipped (logos, icons, banners)",
                    "editor": "list_text",
                    "examples": [["logo", "banner", "icon", "qrcode"]],
                },
                "banned_images_md5": {
                    "type": "array",
                    "required": False,
                    "default": [],
                    "description": "MD5 hashes of images to always skip (tracking pixels, repeated banners)",
                    "editor": "list_text",
                },
                "cross_industry_pollution_keywords": {
                    "type": "array",
                    "required": False,
                    "default": [],
                    "description": "Phrases indicating cross-industry content leaked into wrong category",
                    "editor": "list_text",
                },
                "cross_industry_exempt": {
                    "type": "array",
                    "required": False,
                    "default": [],
                    "description": "Industry names exempt from cross-industry pollution check",
                    "editor": "list_text",
                    "examples": [["智慧楼宇"]],
                },
                "dev_pollution_patterns": {
                    "type": "array",
                    "required": False,
                    "default": [],
                    "description": "Developer/debug text patterns to remove (internal notes, config dumps)",
                    "editor": "list_text",
                },
                "external_link_restore": {
                    "type": "object",
                    "required": False,
                    "default": {},
                    "description": "Map hostname → full URL prefix for restoring stripped external links",
                    "editor": "key_value",
                    "examples": [{"docs.example.com": "https://docs.example.com"}],
                },
                "resource_shared_keywords": {
                    "type": "array",
                    "required": False,
                    "default": [],
                    "description": "Keywords indicating a shared/generic resource (not product-specific)",
                    "editor": "list_text",
                    "examples": [["系列产品综合样本", "综合样本"]],
                },
                "strip_resource_card_sections": {
                    "type": "boolean",
                    "required": False,
                    "default": False,
                    "description": "Remove resource card sections (webinars, whitepapers, podcasts) — site-specific noise",
                    "editor": "flag",
                },
            },
        },

        # ── HEADING (heading processing) ──────────────────────────
        "heading": {
            "type": "object",
            "required": False,
            "default": {},
            "description": "Rules for processing and cleaning Markdown headings",
            "editor": "key_any",
            "properties": {
                "max_level": {
                    "type": "integer",
                    "required": False,
                    "default": 4,
                    "description": "Maximum heading level to generate (# = 1, #### = 4)",
                    "editor": "number",
                },
                "long_heading_threshold": {
                    "type": "integer",
                    "required": False,
                    "default": 200,
                    "description": "Headings longer than this (chars) are flagged as suspicious",
                    "editor": "number",
                },
                "promote_strong_paragraph": {
                    "type": "boolean",
                    "required": False,
                    "default": False,
                    "description": "Convert bold-only paragraphs to headings",
                    "editor": "flag",
                },
                "strong_min_len": {
                    "type": "integer",
                    "required": False,
                    "default": 6,
                    "description": "Minimum bold text length to promote to heading",
                    "editor": "number",
                },
                "strong_max_len": {
                    "type": "integer",
                    "required": False,
                    "default": 80,
                    "description": "Maximum bold text length to promote to heading",
                    "editor": "number",
                },
                "url_heading_pattern": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Regex: headings matching this are converted to links, not kept as headings",
                    "editor": "text",
                    "examples": ["^https?://|^www\\."],
                },
                "strip_trailing_product_code": {
                    "type": "boolean",
                    "required": False,
                    "default": False,
                    "description": "Remove product codes (e.g. '6ES7...') from end of headings",
                    "editor": "flag",
                },
                "empty_heading_min_level": {
                    "type": "integer",
                    "required": False,
                    "default": 3,
                    "description": "Minimum level for empty heading fill behavior",
                    "editor": "number",
                },
                "body_max_level": {
                    "type": "integer",
                    "required": False,
                    "default": 0,
                    "description": "Max heading level in body text (0 = no limit); headings deeper are converted to bold",
                    "editor": "number",
                },
            },
        },

        # ── TEMPLATES (output formatting) ─────────────────────────
        "templates": {
            "type": "object",
            "required": False,
            "default": {},
            "description": "Mustache-style templates for output formatting ({variable} placeholders)",
            "editor": "key_any",
            "properties": {
                "block_separator": {
                    "type": "string",
                    "required": False,
                    "default": "\\n\\n---\\n\\n",
                    "description": "Separator between content blocks",
                    "editor": "template",
                    "examples": ["\\n\\n---\\n\\n", "\\n\\n---\\n"],
                },
                "extract_time_footer": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Footer appended to each document: {date}, {url} placeholders",
                    "editor": "template",
                    "examples": ["\\n\\n> 提取时间: {date} | 来源: {url}"],
                },
                "product_heading": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Template for product heading: {name}, {prefix} (# markers) placeholders",
                    "editor": "template",
                },
                "product_cover_image": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Template for cover image: {name}, {rel} placeholders",
                    "editor": "template",
                },
                "product_features": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Template for features block: {features} placeholder",
                    "editor": "template",
                },
                "product_data_source": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Template for product data source footer: {site_name}, {base_url}, {id}, {date}",
                    "editor": "template",
                },
                "industry_heading": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Template for industry heading: {name} placeholder",
                    "editor": "template",
                },
                "industry_cases_heading": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Template for cases section heading",
                    "editor": "template",
                },
                "industry_data_source": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Template for industry data source footer",
                    "editor": "template",
                },
                "case_heading": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Template for case heading: {n}, {title} placeholders",
                    "editor": "template",
                },
                "case_summary": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Template for case summary: {summary} placeholder",
                    "editor": "template",
                },
                "image_name_pattern": {
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "Template for image naming: {current}, {module} placeholders",
                    "editor": "template",
                },
                "image_name_fallback": {
                    "type": "string",
                    "required": False,
                    "default": "image",
                    "description": "Fallback image name prefix",
                    "editor": "text",
                },
            },
        },

        # ── OUTPUT STRUCTURE ──────────────────────────────────────
        "output_root": {
            "type": "string",
            "required": True,
            "default": None,
            "description": "Absolute path to the output directory (where 产品/, 解决方案/, etc. are created)",
            "editor": "text",
            "examples": ["/path/to/output"],
        },
        "destinations": {
            "type": "object",
            "required": False,
            "default": {},
            "description": "Separate final library path from temporary full/update extraction paths",
            "editor": "key_any",
            "properties": {
                "production_root": {
                    "type": "string", "required": False, "default": "",
                    "description": "Final curated library destination",
                    "editor": "text",
                },
                "staging_full_root": {
                    "type": "string", "required": False, "default": "",
                    "description": "Temporary base directory for full extraction runs",
                    "editor": "text",
                },
                "staging_update_root": {
                    "type": "string", "required": False, "default": "",
                    "description": "Temporary base directory for update/check runs",
                    "editor": "text",
                },
            },
        },
        "output_structure": {
            "type": "object",
            "required": False,
            "default": {
                "products_dir": "产品",
                "solutions_dir": "解决方案",
                "image_subdir": "图片",
                "resource_subdir": "资料",
                "subproduct_mode": "merge",
            },
            "description": "Directory names and subproduct handling within output_root",
            "editor": "key_any",
            "properties": {
                "products_dir": {
                    "type": "string", "required": False, "default": "产品",
                    "description": "Directory name for products",
                    "editor": "text",
                },
                "solutions_dir": {
                    "type": "string", "required": False, "default": "解决方案",
                    "description": "Directory name for solutions/industries",
                    "editor": "text",
                },
                "industries_dir": {
                    "type": "string", "required": False, "default": "",
                    "description": "Separate industries dir (if different from solutions)",
                    "editor": "text",
                    "examples": ["行业"],
                },
                "image_subdir": {
                    "type": "string", "required": False, "default": "图片",
                    "description": "Subdirectory for downloaded images",
                    "editor": "text",
                },
                "resource_subdir": {
                    "type": "string", "required": False, "default": "资料",
                    "description": "Subdirectory for downloaded resources (PDF, ZIP)",
                    "editor": "text",
                },
                "subproduct_mode": {
                    "type": "string", "required": False, "default": "merge",
                    "description": "'merge' = subproducts inline in parent MD, 'split' = one MD per subproduct",
                    "editor": "select",
                    "enum": ["merge", "split"],
                },
            },
        },

        # ── DOCUMENT (三件套 generation) ──────────────────────────
        "document": {
            "type": "object",
            "required": False,
            "default": {"generate_index": True, "generate_summary": True, "generate_toc": False},
            "description": "Controls generation of standard library docs (三件套: README, 全库索引, 全库摘要)",
            "editor": "key_any",
            "properties": {
                "generate_index": {
                    "type": "boolean", "required": False, "default": True,
                    "description": "Generate 全库索引.md", "editor": "flag",
                },
                "generate_summary": {
                    "type": "boolean", "required": False, "default": True,
                    "description": "Generate 全库摘要.md", "editor": "flag",
                },
                "generate_toc": {
                    "type": "boolean", "required": False, "default": False,
                    "description": "Generate table of contents", "editor": "flag",
                },
            },
        },

        # ── DISCOVERY QUALITY (deep audit thresholds) ─────────────
        "discovery_quality": {
            "type": "object",
            "required": False,
            "default": {},
            "description": "Thresholds for the deep discovery audit (empty/shell page detection)",
            "editor": "key_any",
            "properties": {
                "archive_chars": {
                    "type": "integer", "required": False, "default": 20,
                    "description": "Pages with ≤ this many chars are auto-archived as empty",
                    "editor": "number",
                },
                "review_chars": {
                    "type": "integer", "required": False, "default": 200,
                    "description": "Pages with ≤ this many chars are flagged for review",
                    "editor": "number",
                },
                "slug_title_max_chars": {
                    "type": "integer", "required": False, "default": 500,
                    "description": "Max chars for slug-derived titles",
                    "editor": "number",
                },
                "slug_title_pattern": {
                    "type": "string", "required": False, "default": "",
                    "description": "Regex pattern for valid slug titles",
                    "editor": "text",
                },
            },
        },

        # ── UPDATE CHECK (website change detection) ──────────────
        "update_check": {
            "type": "object",
            "required": False,
            "default": {},
            "description": "Configuration for detecting whether the source website has changed since last baseline",
            "editor": "key_any",
            "properties": {
                "enabled": {
                    "type": "boolean", "required": False, "default": False,
                    "description": "Enable update detection for this site",
                    "editor": "flag",
                },
                "mode": {
                    "type": "string", "required": False, "default": "auto",
                    "description": "Detection mode: 'api' (fetch menu + detail fingerprints), 'sitemap' (parse sitemap URLs + page hashes), 'auto' (pick based on discovery.mode)",
                    "editor": "select",
                    "enum": ["auto", "api", "sitemap"],
                },
                "check_content": {
                    "type": "boolean", "required": False, "default": True,
                    "description": "Also check page content fingerprints (not just structure)",
                    "editor": "flag",
                },
                "baseline_file": {
                    "type": "string", "required": False, "default": "",
                    "description": "Path to baseline JSON (default: config/<site>/_baseline_menu.json)",
                    "editor": "text",
                },
                "max_items": {
                    "type": "integer", "required": False, "default": 0,
                    "description": "Max items to check (0 = all)",
                    "editor": "number",
                },
            },
        },

        # ── RENDER MODE ───────────────────────────────────────────
        "render_mode": {
            "type": "string",
            "required": False,
            "default": "requests",
            "description": "Page rendering backend: 'requests' (fast, static HTML), 'playwright' (full JS rendering), or 'auto' (try requests, fallback to playwright if JS shell page detected)",
            "editor": "select",
            "enum": ["requests", "playwright", "auto"],
        },
        "playwright_args": {
            "type": "object",
            "required": False,
            "default": {},
            "description": "Additional Playwright launch arguments (when render_mode=playwright)",
            "editor": "json",
        },

        # ── RATE LIMIT ────────────────────────────────────────────
        "rate_limit": {
            "type": "object",
            "required": False,
            "default": {},
            "description": "Request throttling and retry configuration",
            "editor": "key_any",
            "properties": {
                "delay_between_requests": {
                    "type": "number", "required": False, "default": 0,
                    "description": "Seconds to wait between HTTP requests", "editor": "number",
                },
                "max_retries": {
                    "type": "integer", "required": False, "default": 3,
                    "description": "Max retry attempts per failed request", "editor": "number",
                },
                "retry_backoff_base": {
                    "type": "number", "required": False, "default": 2,
                    "description": "Exponential backoff base (delay = base ^ attempt)", "editor": "number",
                },
                "scan_concurrency": {
                    "type": "integer", "required": False, "default": 1,
                    "description": "Max concurrent scan requests", "editor": "number",
                },
                "timeout": {
                    "type": "integer", "required": False, "default": 30,
                    "description": "Per-request timeout in seconds", "editor": "number",
                },
            },
        },

        # ── WEB FALLBACK ──────────────────────────────────────────
        "web_fallback": {
            "type": "object",
            "required": False,
            "default": {},
            "description": "Fallback to web scraping when API endpoints fail or are empty",
            "editor": "key_any",
            "properties": {
                "enabled": {
                    "type": "boolean", "required": False, "default": False,
                    "description": "Enable web scraping fallback", "editor": "flag",
                },
                "menu_url": {
                    "type": "string", "required": False, "default": "",
                    "description": "URL path for menu discovery fallback", "editor": "text",
                },
                "product_url": {
                    "type": "string", "required": False, "default": "",
                    "description": "URL template for product pages: {id} placeholder", "editor": "template",
                },
                "industry_url": {
                    "type": "string", "required": False, "default": "",
                    "description": "URL template for industry pages: {id} placeholder", "editor": "template",
                },
                "children_selector": {
                    "type": "string", "required": False, "default": "",
                    "description": "CSS selector for sub-product links on product pages (web fallback children discovery)",
                    "editor": "text",
                    "examples": ["main a[href*='/items/']", ".product-list a"],
                },
                "selectors": {
                    "type": "object", "required": False, "default": {},
                    "description": "CSS selectors for web fallback extraction",
                    "editor": "key_any",
                },
            },
        },

        # ── SPEC EXTRACTION (5-layer cascade) ──────────────────────
        "spec_extraction": {
            "type": "object",
            "required": False,
            "default": {},
            "description": (
                "Technical specification extraction via a 5-layer cascade. "
                "Layers 1-3 are zero-config (auto-detect); layers 4-5 are per-site config. "
                "Inspired by Apify (multi-layer merge), Crawl4AI (CSS schema), DEXTER (structural features)."
            ),
            "editor": "key_any",
            "properties": {
                "enabled": {
                    "type": "boolean", "required": False, "default": False,
                    "description": "Enable spec extraction (adds 'spec_tables' content block)",
                    "editor": "flag",
                },
                "cascade": {
                    "type": "array", "required": False,
                    "default": ["json_ld", "microdata", "spec_table", "css_heuristic", "linked_page"],
                    "description": (
                        "Ordered extraction layers to try. Each layer fills gaps from previous. "
                        "json_ld: schema.org/Product in <script type='application/ld+json'>. "
                        "microdata: itemscope itemtype attributes. "
                        "spec_table: auto-detect spec tables/dls by structural features (zero-config). "
                        "css_heuristic: per-site CSS selectors from config. "
                        "linked_page: follow URL template to separate spec page."
                    ),
                    "editor": "multiselect",
                    "enum": ["json_ld", "microdata", "spec_table", "css_heuristic", "linked_page"],
                },
                "min_spec_pairs": {
                    "type": "integer", "required": False, "default": 3,
                    "description": "Minimum key-value pairs to consider a spec block valid (filters noise)",
                    "editor": "number",
                },
                "noise_values": {
                    "type": "array", "required": False,
                    "default": ["See Details", "Submit Inquiry", "View Guidance", "Download", "Publication", "—", "--"],
                    "description": "Spec values to filter out (noise like 'See Details', placeholder dashes)",
                    "editor": "list_text",
                },
                "noise_keys": {
                    "type": "array", "required": False,
                    "default": ["Drawings", "Environmental Compliance", "SVHC Information", "EU Importer", "EU Authorized"],
                    "description": "Spec keys to filter out (non-technical metadata)",
                    "editor": "list_text",
                },
                "css_heuristics": {
                    "type": "object", "required": False, "default": {},
                    "description": "Per-site CSS selectors for spec sections (Layer 4). Auto-detect tries first; this is fallback.",
                    "editor": "key_any",
                    "properties": {
                        "spec_table": {
                            "type": "string", "required": False, "default": "",
                            "description": "CSS selector for spec tables",
                            "editor": "selector",
                            "examples": ["table.tech-specs", "table.product-specifications"],
                        },
                        "spec_section": {
                            "type": "string", "required": False, "default": "",
                            "description": "CSS selector for the spec section container",
                            "editor": "selector",
                            "examples": ["#specifications", ".specs", ".technical-data"],
                        },
                        "spec_dl": {
                            "type": "string", "required": False, "default": "",
                            "description": "CSS selector for spec definition lists",
                            "editor": "selector",
                            "examples": ["dl.product-specs"],
                        },
                    },
                },
                "linked_page": {
                    "type": "object", "required": False, "default": {},
                    "description": "Config for Layer 5: follow links to separate spec detail pages",
                    "editor": "key_any",
                    "properties": {
                        "url_template": {
                            "type": "string", "required": False, "default": "",
                            "description": "URL template for spec detail pages: {catalog} placeholder",
                            "editor": "template",
                            "examples": ["/en-us/products/details.{catalog}.html", "/products/{catalog}/specifications"],
                        },
                        "catalog_pattern": {
                            "type": "string", "required": False, "default": "",
                            "description": "Regex to extract catalog/part numbers from product HTML (1st capture group)",
                            "editor": "text",
                            "examples": [r"\\b(1756-[A-Z0-9]+)\\b", r"\\b(6ES7[0-9A-Z-]+)\\b"],
                        },
                        "max_catalogs": {
                            "type": "integer", "required": False, "default": 5,
                            "description": "Max catalog numbers to try per product (prevents runaway)",
                            "editor": "number",
                        },
                    },
                },
            },
        },

        # ── CONTENT BLOCKS (extraction pipeline) ──────────────────
        "content_blocks": {
            "type": ["array", "object"],
            "required": False,
            "default": None,
            "description": (
                "Ordered list of content block types to extract per page. "
                "Product default: ['cover_image', 'features', 'body_html', 'subproducts']. "
                "Industry default: ['body_html', 'cases']. "
                "Can also be an object: {'product': [...], 'industry': [...]}"
            ),
            "editor": "json",
            "examples": [
                ["cover_image", "features", "body_html", "subproducts"],
                {"product": ["cover_image", "features", "body_html", "subproducts"], "industry": ["body_html", "cases"]},
            ],
        },

        # ── EXTRACTION (entity types and filters) ─────────────────
        "extraction": {
            "type": "object",
            "required": False,
            "default": {},
            "description": "Controls which entity types to extract and optional category filtering",
            "editor": "key_any",
            "properties": {
                "entity_types": {
                    "type": "array", "required": False, "default": ["product", "industry"],
                    "description": "Entity types to extract from this site", "editor": "multiselect",
                    "enum": ["product", "industry", "solution"],
                },
                "category_filter": {
                    "type": "array", "required": False, "default": [],
                    "description": "Only extract items in these named categories (empty = all)", "editor": "list_text",
                },
            },
        },

        # ── PAGE TYPE (split config) ──────────────────────────────
        "page_type": {
            "type": "string",
            "required": False,
            "default": None,
            "description": "Page type for split config files (product.json, industry.json, etc.)",
            "editor": "select",
            "enum": ["product", "industry", "solution", None],
        },
        "page_types": {
            "type": "object",
            "required": False,
            "default": {},
            "description": "Per-page-type overrides for html_components (merged at runtime)",
            "editor": "json",
        },

        # ── CATEGORY NAME MAP ─────────────────────────────────────
        "category_name_map": {
            "type": "object",
            "required": False,
            "default": {},
            "description": "Map of category slug/abbreviation → display name",
            "editor": "key_value",
            "examples": [{"I/O": "IO系统"}],
        },

        # ── LOCALE / LANG ─────────────────────────────────────────
        "locale": {
            "type": "string",
            "required": False,
            "default": "zh-CN",
            "description": "Browser locale for page rendering",
            "editor": "text",
        },
        "lang_path": {
            "type": "string",
            "required": False,
            "default": "",
            "description": "Language path prefix in URLs (e.g. '/zh-cn')",
            "editor": "text",
            "examples": ["/zh-cn"],
        },

        # ── RESOURCES (download keyword hints) ────────────────────
        "resources": {
            "type": "object",
            "required": False,
            "default": {},
            "description": "Hints for identifying and categorizing downloadable resources",
            "editor": "key_any",
            "properties": {
                "general_keywords": {
                    "type": "array", "required": False, "default": [],
                    "description": "Keywords indicating a generic/shared resource document", "editor": "list_text",
                },
            },
        },

        # ── LLM REFINE ────────────────────────────────────────────
        "llm_refine": {
            "type": "object",
            "required": False,
            "default": {},
            "description": "LLM post-processing. Store only an API key environment-variable name in api_key_env; never put a key in config.",
            "editor": "json",
        },

        "adaptive_hooks": {
            "type": "object",
            "required": False,
            "default": {},
            "description": "Optional named cleanup hooks by stage, e.g. post_markdown",
            "editor": "json",
        },

        # ── DOWNLOAD LINK PATTERNS (top-level shortcut) ───────────
        "download_link_patterns": {
            "type": "array",
            "required": False,
            "default": ["download"],
            "description": "Top-level shortcut: URL substrings for downloadable resources (promoted to html_components)",
            "editor": "list_text",
        },

        # ── EXTRACT DATE ──────────────────────────────────────────
        "extract_date": {
            "type": "string",
            "required": False,
            "default": "",
            "description": "Override date for extract_time_footer (ISO format, default: today)",
            "editor": "text",
        },

        # ── CAROUSEL MODE (top-level shortcut) ────────────────────
        "carousel_mode": {
            "type": "string",
            "required": False,
            "default": "first",
            "description": "Top-level shortcut for carousel extraction mode (promoted to html_components)",
            "editor": "select",
            "enum": ["first", "all"],
        },

        # ── INTERNAL ENGINE KEY (not for user config) ─────────────
        "__engine_page_type": {
            "type": "string",
            "required": False,
            "default": "product",
            "description": "INTERNAL: set by engine from page_type, do not set in config files",
            "editor": "text",
        },
    },
}


# ─── PRESET LIBRARY ────────────────────────────────────────────────
# Named bundles of config defaults for common site archetypes.
# An AI agent picks a preset, then overrides site-specific selectors.
# (Inspired by Jina Reader's preset system)

PRESETS = {
    "product_catalog_sitemap": {
        "description": (
            "Product catalog site with XML sitemap. "
            "Best for sites with a structured sitemap and product or category URL patterns."
        ),
        "config": {
            "discovery": {"mode": "sitemap"},
            "render_mode": "requests",
            "html_components": {
                "content_selector": "main",
                "download_images": True,
            },
            "output_structure": {
                "products_dir": "产品",
                "solutions_dir": "解决方案",
                "image_subdir": "图片",
                "resource_subdir": "资料",
                "subproduct_mode": "merge",
            },
            "document": {"generate_index": True, "generate_summary": True, "generate_toc": False},
            "templates": {
                "block_separator": "\n\n---\n\n",
                "extract_time_footer": "\n\n> 提取时间: {date} | 来源: {url}",
            },
            "web_fallback": {"enabled": True},
            "rate_limit": {"delay_between_requests": 0, "max_retries": 3, "retry_backoff_base": 2},
        },
        "required_overrides": [
            "site_id", "site_name", "base_url", "output_root",
            "discovery.sitemap_url", "discovery.product_pattern", "discovery.industry_pattern",
        ],
    },
    "product_catalog_api": {
        "description": (
            "Product catalog site with JSON API backend. "
            "Best for sites with API endpoints returning structured page data."
        ),
        "config": {
            "render_mode": "requests",
            "html_components": {
                "download_images": True,
            },
            "field_mapping": {},  # must be filled per-site
            "output_structure": {
                "products_dir": "产品",
                "solutions_dir": "解决方案",
                "image_subdir": "图片",
                "resource_subdir": "资料",
                "subproduct_mode": "merge",
            },
            "document": {"generate_index": True, "generate_summary": True, "generate_toc": False},
            "templates": {
                "block_separator": "\n\n---\n",
                "extract_time_footer": "\n\n---\n*提取时间：{date}*",
            },
            "web_fallback": {"enabled": True},
            "rate_limit": {"delay_between_requests": 0.3, "max_retries": 3, "retry_backoff_base": 2},
        },
        "required_overrides": [
            "site_id", "site_name", "base_url", "output_root",
            "api.menu", "api.product_detail",
            "field_mapping.item_id", "field_mapping.item_name", "field_mapping.product_html",
        ],
    },
    "product_catalog_nav": {
        "description": (
            "Product catalog discovered by crawling nav links. "
            "Best for: sites without sitemap or API, where products are found via navigation links."
        ),
        "config": {
            "discovery": {"mode": "nav"},
            "render_mode": "requests",
            "html_components": {
                "content_selector": "main",
                "download_images": True,
            },
            "output_structure": {
                "products_dir": "产品",
                "solutions_dir": "解决方案",
                "image_subdir": "图片",
                "resource_subdir": "资料",
                "subproduct_mode": "merge",
            },
            "document": {"generate_index": True, "generate_summary": True, "generate_toc": False},
            "templates": {
                "block_separator": "\n\n---\n\n",
                "extract_time_footer": "\n\n> 提取时间: {date} | 来源: {url}",
            },
            "web_fallback": {"enabled": True},
            "rate_limit": {"delay_between_requests": 0.5, "max_retries": 3, "retry_backoff_base": 2},
        },
        "required_overrides": [
            "site_id", "site_name", "base_url", "output_root",
            "discovery.start_url", "discovery.nav_selector",
        ],
    },
    "docs_site_sitemap": {
        "description": (
            "Documentation / knowledge-base site with XML sitemap. "
            "Best for: docs sites, developer portals, wiki-style content. "
            "No spec extraction, body_html only."
        ),
        "config": {
            "discovery": {"mode": "sitemap"},
            "render_mode": "requests",
            "extraction": {"entity_types": ["product"]},
            "html_components": {
                "content_selector": "main",
                "download_images": False,
            },
            "content_blocks": ["body_html"],
            "filters": {
                "image_ignore_keywords": ["logo", "icon", "banner", "sprite", "placeholder", "footer", "header", "btn"],
            },
            "output_structure": {
                "products_dir": "文档",
                "solutions_dir": "解决方案",
                "image_subdir": "图片",
                "resource_subdir": "资料",
                "subproduct_mode": "merge",
            },
            "document": {"generate_index": True, "generate_summary": True, "generate_toc": False},
            "templates": {
                "block_separator": "\n\n---\n\n",
                "extract_time_footer": "\n\n> 提取时间: {date} | 来源: {url}",
            },
            "web_fallback": {"enabled": True},
            "rate_limit": {"delay_between_requests": 0, "max_retries": 3, "retry_backoff_base": 2},
        },
        "required_overrides": [
            "site_id", "site_name", "base_url", "output_root",
            "discovery.sitemap_url", "discovery.product_pattern",
        ],
    },
    "generic_url_list": {
        "description": (
            "Generic static URL list. Best for: blogs, docs, news pages, marketing pages, "
            "or any small curated set of pages where recursive crawling is unnecessary."
        ),
        "config": {
            "discovery": {"mode": "url_list", "urls": [], "max_pages": 50},
            "render_mode": "requests",
            "extraction": {"entity_types": ["product"]},
            "html_components": {
                "content_selector": "main, article, [role='main'], body",
                "download_images": False,
            },
            "content_blocks": ["body_html"],
            "output_structure": {
                "products_dir": "pages",
                "solutions_dir": "solutions",
                "image_subdir": "images",
                "resource_subdir": "resources",
                "subproduct_mode": "merge",
            },
            "document": {"generate_index": True, "generate_summary": True, "generate_toc": False},
            "templates": {
                "block_separator": "\n\n---\n\n",
                "extract_time_footer": "\n\n> Extracted: {date} | Source: {url}",
            },
            "web_fallback": {"enabled": True},
            "rate_limit": {"delay_between_requests": 0, "max_retries": 3, "retry_backoff_base": 2},
        },
        "required_overrides": ["site_id", "site_name", "base_url", "output_root", "discovery.urls"],
    },
    "report_consulting_url_list": {
        "description": (
            "Curated reports, whitepapers, research, insights, and consulting pages. "
            "Skips news, press releases, blogs, events, careers, and legal pages by URL/title/category patterns."
        ),
        "config": {
            "discovery": {
                "mode": "url_list",
                "urls": [],
                "max_pages": 50,
                "url_include_patterns": [
                    "(?i)(report|research|whitepaper|insight|consulting|advisory|survey|case-study|case_study|resources?)",
                    "(?i)(报告|研究|白皮书|洞察|咨询|调研|案例|资源)"
                ],
                "url_exclude_patterns": [
                    "(?i)(news|press|media|blog|event|webinar|career|jobs|legal|privacy|terms)",
                    "(?i)(新闻|动态|博客|活动|招聘|隐私|条款)"
                ],
            },
            "render_mode": "requests",
            "extraction": {"entity_types": ["product"]},
            "html_components": {
                "content_selector": "main, article, [role='main'], body",
                "download_images": False,
                "download_link_patterns": [".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"]
            },
            "content_blocks": ["body_html"],
            "filters": {
                "image_ignore_keywords": ["logo", "icon", "banner", "sprite", "placeholder", "footer", "header", "btn"]
            },
            "output_structure": {
                "products_dir": "reports",
                "solutions_dir": "solutions",
                "image_subdir": "images",
                "resource_subdir": "resources",
                "subproduct_mode": "merge",
            },
            "document": {"generate_index": True, "generate_summary": True, "generate_toc": False},
            "templates": {
                "block_separator": "\n\n---\n\n",
                "extract_time_footer": "\n\n> Extracted: {date} | Source: {url}",
            },
            "web_fallback": {"enabled": True},
            "rate_limit": {"delay_between_requests": 0, "max_retries": 3, "retry_backoff_base": 2},
        },
        "required_overrides": ["site_id", "site_name", "base_url", "output_root", "discovery.urls"],
    },
    "resource_center": {
        "description": (
            "Download center / resource library. "
            "Best for: whitepaper libraries, certification portals, standards repositories. "
            "Prioritizes resource downloading and metadata extraction."
        ),
        "config": {
            "discovery": {"mode": "sitemap"},
            "render_mode": "requests",
            "extraction": {"entity_types": ["product"]},
            "html_components": {
                "content_selector": "main",
                "download_images": False,
                "download_link_patterns": [".pdf", ".zip", ".doc", ".xls"],
            },
            "content_blocks": ["body_html"],
            "output_structure": {
                "products_dir": "资料库",
                "solutions_dir": "解决方案",
                "image_subdir": "图片",
                "resource_subdir": "资料",
                "subproduct_mode": "merge",
            },
            "document": {"generate_index": True, "generate_summary": True, "generate_toc": False},
            "templates": {
                "block_separator": "\n\n---\n\n",
                "extract_time_footer": "\n\n> 提取时间: {date} | 来源: {url}",
            },
            "web_fallback": {"enabled": True},
            "rate_limit": {"delay_between_requests": 0.5, "max_retries": 3, "retry_backoff_base": 2},
        },
        "required_overrides": [
            "site_id", "site_name", "base_url", "output_root",
            "discovery.sitemap_url", "discovery.product_pattern",
        ],
    },
    "support_faq": {
        "description": (
            "Support / FAQ / knowledge-base discovered by crawling. "
            "Best for: help centers, FAQ pages, community forums. "
            "Many short pages, nav-mode discovery."
        ),
        "config": {
            "discovery": {"mode": "nav"},
            "render_mode": "requests",
            "extraction": {"entity_types": ["product"]},
            "html_components": {
                "content_selector": "main",
                "download_images": False,
            },
            "content_blocks": ["body_html"],
            "output_structure": {
                "products_dir": "常见问题",
                "solutions_dir": "解决方案",
                "image_subdir": "图片",
                "resource_subdir": "资料",
                "subproduct_mode": "merge",
            },
            "document": {"generate_index": True, "generate_summary": True, "generate_toc": False},
            "templates": {
                "block_separator": "\n\n---\n\n",
                "extract_time_footer": "\n\n> 提取时间: {date} | 来源: {url}",
            },
            "web_fallback": {"enabled": True},
            "rate_limit": {"delay_between_requests": 0.3, "max_retries": 3, "retry_backoff_base": 2},
        },
        "required_overrides": [
            "site_id", "site_name", "base_url", "output_root",
            "discovery.start_url", "discovery.nav_selector",
        ],
    },
    "product_specs_sitemap": {
        "description": (
            "Product detail pages with structured specifications. "
            "Best for product detail pages that contain specification tables. "
            "Enables spec extraction cascade and spec_tables content block."
        ),
        "config": {
            "discovery": {"mode": "sitemap"},
            "render_mode": "requests",
            "extraction": {"entity_types": ["product"]},
            "html_components": {
                "content_selector": "main",
                "download_images": True,
            },
            "content_blocks": ["body_html", "spec_tables"],
            "spec_extraction": {
                "enabled": True,
                "cascade": ["json_ld", "microdata", "spec_table", "css_heuristic", "linked_page"],
                "min_spec_pairs": 3,
            },
            "output_structure": {
                "products_dir": "产品",
                "solutions_dir": "解决方案",
                "image_subdir": "图片",
                "resource_subdir": "资料",
                "subproduct_mode": "merge",
            },
            "document": {"generate_index": True, "generate_summary": True, "generate_toc": False},
            "templates": {
                "block_separator": "\n\n---\n\n",
                "extract_time_footer": "\n\n> 提取时间: {date} | 来源: {url}",
            },
            "web_fallback": {"enabled": True},
            "rate_limit": {"delay_between_requests": 0, "max_retries": 3, "retry_backoff_base": 2},
        },
        "required_overrides": [
            "site_id", "site_name", "base_url", "output_root",
            "discovery.sitemap_url", "discovery.product_pattern",
        ],
    },
}


# ─── HELPER: get all valid keys at a given path ────────────────────

def _schema_keys_at(path: str) -> set:
    """Return the set of valid config keys at the given dot-path.

    >>> _schema_keys_at("")  # top-level keys
    {'site_id', 'site_name', 'base_url', ...}
    >>> _schema_keys_at("filters")
    {'noise_text_keywords_exact', 'image_ignore_keywords', ...}
    """
    props = CONFIG_SCHEMA_V1["properties"]
    if not path:
        return set(props.keys())
    parts = path.split(".")
    current = props
    for part in parts:
        if not isinstance(current, dict):
            return set()
        field = current.get(part, {})
        if "properties" in field:
            current = field["properties"]
        else:
            return set()
    return set(current.keys())


def _list_keys_at(path: str) -> set:
    """Return keys at path that should be lists (array type)."""
    props = CONFIG_SCHEMA_V1["properties"]
    if not path:
        return set()
    parts = path.split(".")
    current = props
    for part in parts:
        field = current.get(part, {})
        if "properties" in field:
            current = field["properties"]
        else:
            return set()
    result = set()
    for key, schema in current.items():
        t = schema.get("type")
        if t == "array" or (isinstance(t, list) and "array" in t):
            result.add(key)
    return result


def _required_keys_at(path: str) -> set:
    """Return keys at path that are required."""
    props = CONFIG_SCHEMA_V1["properties"]
    if not path:
        return {k for k, v in props.items() if v.get("required")}
    parts = path.split(".")
    current = props
    for part in parts:
        field = current.get(part, {})
        if "properties" in field:
            current = field["properties"]
        else:
            return set()
    return {k for k, v in current.items() if v.get("required")}


def _field_schema(dotpath: str) -> dict:
    """Get the schema definition for a dot-path like 'filters.noise_text_keywords_exact'."""
    props = CONFIG_SCHEMA_V1["properties"]
    parts = dotpath.split(".")
    current = props
    for i, part in enumerate(parts):
        if part not in current:
            return {}
        field = current[part]
        if i == len(parts) - 1:
            return field
        if "properties" in field:
            current = field["properties"]
        else:
            return {}
    return {}


def _check_type(value, expected) -> bool:
    """Check if value matches expected JSON Schema type.

    Used by both init_site.py and extract_generic.py validate_config.
    """
    type_map = {
        "string": str, "integer": int, "number": (int, float),
        "boolean": bool, "array": list, "object": dict,
    }
    if isinstance(expected, list):
        return any(_check_type(value, t) for t in expected)
    py_type = type_map.get(expected)
    if py_type is None:
        return True
    if expected == "number" and isinstance(value, int):
        return True
    return isinstance(value, py_type)
