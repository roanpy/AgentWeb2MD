# WebExtractMd

Configuration-driven HTML-to-Markdown extraction with content cleanup, page discovery, image/resource handling, structured-spec sidecars, and quality reports.

This public distribution intentionally contains only generic examples. Private site profiles, production destinations, generated baselines, and vendor-specific operational scripts are not included.

## Requirements

- Python 3.10+
- `requests`, `beautifulsoup4`, and `markdownify`
- Optional: Playwright for JavaScript-rendered pages

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

For JavaScript-rendered sites:

```bash
.venv/bin/pip install playwright
.venv/bin/playwright install chromium
```

## Quick start

Edit `config/generic/common.json` and replace `discovery.urls`, then run:

```bash
.venv/bin/python extract_generic.py --site generic
```

Output defaults to `/tmp/generic_url_list_sample`. Keep first runs in a temporary directory and review the generated Markdown before using another destination.

To generate a starter profile for a site:

```bash
.venv/bin/python init_site.py --site example --base-url https://example.com --output-root /tmp/example_sample --non-interactive
```

Validate a profile before extraction:

```bash
.venv/bin/python - <<'PY'
from extract_generic import load_config, validate_config

config = load_config("config/generic/common.json")
errors, warnings = validate_config(config)
print({"errors": errors, "warnings": warnings})
raise SystemExit(bool(errors))
PY
```

Score an extraction:

```bash
.venv/bin/python quality_report.py --output-root /tmp/generic_url_list_sample --site generic
```

## Profiles

- `generic`: extracts an explicit URL list without recursive crawling.
- `python_docs`: small crawl-mode example using the public Python documentation.

Generated `_baseline*.json` and `_quality_baseline.json` files are local state and are ignored by Git.

## Responsible use

Only extract content you are authorized to access and reuse. Respect site terms, robots policies, rate limits, privacy, and copyright. This project does not bypass authentication or access controls.

## License

MIT
