# 🔗 AgentWeb2MD

[English](README.md) | [简体中文](README.zh-CN.md)

**Agent-guided web content extraction to clean, reviewable Markdown.**

[![License: MIT](https://img.shields.io/badge/license-MIT-2ea44f.svg)](LICENSE)
[![CI](https://github.com/roanpy/AgentWeb2MD/actions/workflows/ci.yml/badge.svg)](https://github.com/roanpy/AgentWeb2MD/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-3776ab.svg)
![Workflow](https://img.shields.io/badge/workflow-agent--guided-8a63d2.svg)
![Output](https://img.shields.io/badge/output-Markdown-1f6feb.svg)

AgentWeb2MD pairs an agent's judgment with a deterministic extraction engine. The agent defines scope, reviews the site profile, checks a small sample, reads the quality report, and iterates before any full run.

[Why agent-guided?](#why-agent-guided) · [Capabilities](#capabilities) · [Install](#install) · [Agent workflow](#agent-workflow) · [Responsible use](#responsible-use)

> This is deliberately not a one-click autonomous crawler. Website scope, selectors, noise rules, and output quality require review.

## Why agent-guided?

Website structure and acceptable output are judgment calls. Automatically guessing selectors, crawl scope, noise rules, and quality thresholds can silently produce incomplete or polluted Markdown. AgentWeb2MD keeps those decisions with an agent or human reviewer while automating the repeatable mechanics.

```text
Agent inspects site and scope
        ↓
Draft and review config
        ↓
Run a small sample in /tmp
        ↓
Inspect Markdown + quality report
        ↓
Patch config and repeat
        ↓
Approve the full run
```

## Capabilities

- configuration-driven URL-list, sitemap, crawl, or JSON API discovery
- content isolation and configurable boilerplate removal
- tables, tabs, accordions, images, and downloadable-resource handling
- structured specification and resource sidecars
- deterministic quality reports and local regression baselines
- optional Playwright rendering for JavaScript-heavy pages

## Requirements

- Python 3.10+
- `requests`, `beautifulsoup4`, and `markdownify`
- a coding agent or human reviewer to drive the extraction loop
- optional: Playwright for JavaScript-rendered pages

## Install

From a source checkout:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Or install the wheel attached to a GitHub release:

```bash
python3 -m venv .venv
.venv/bin/pip install ./agentweb2md-0.1.0-py3-none-any.whl
```

For JavaScript-rendered sites:

```bash
.venv/bin/pip install playwright
.venv/bin/playwright install chromium
```

Run the local checks with:

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m pip_audit --progress-spinner off
.venv/bin/python -m bandit -q -lll -r . -x ./.git,./tests
.venv/bin/python -m ruff check --select E9,F63,F7,F82 .
.venv/bin/python -m build
```

Installation provides `agentweb2md`, `agentweb2md-init`, `agentweb2md-config`, and `agentweb2md-quality`. The original Python scripts remain supported when running from a checkout.

## Agent workflow

Give your coding agent the target URL, desired content scope, exclusions, attachment policy, and quality threshold. The repository's `AGENTS.md` defines the operating contract.

### 1. Probe without writing

```bash
.venv/bin/agentweb2md-init --site example --base-url https://example.com --auto
```

The probe is evidence, not a final configuration. The agent must review the proposed discovery mode and selectors.

### 2. Create a draft profile

```bash
.venv/bin/agentweb2md-init \
  --site example \
  --base-url https://example.com \
  --output-root /tmp/agentweb2md/example \
  --non-interactive
```

Alternatively, print a draft plus validation suggestions without writing files:

```bash
.venv/bin/agentweb2md-config https://example.com --site example
```

### 3. Review and validate

The agent should narrow discovery, set the smallest useful `content_selector`, and keep the first run capped. Then validate:

```bash
.venv/bin/python - <<'PY'
from extract_generic import load_config, validate_config

config = load_config("config/example/common.json")
errors, warnings = validate_config(config)
print({"errors": errors, "warnings": warnings})
raise SystemExit(bool(errors))
PY
```

### 4. Extract a sample

```bash
.venv/bin/agentweb2md --site example
```

### 5. Score and inspect

```bash
.venv/bin/agentweb2md-quality \
  --output-root /tmp/agentweb2md/example \
  --site example
```

The agent must inspect representative Markdown and `_quality_report.md`; a passing score is not a substitute for content review. Patch the profile and repeat until the sample is acceptable.

### 6. Approve the full run

Only remove discovery caps or change the output destination after the sample passes review. The tools never imply that generated output is approved for publication or reuse.

## Generic profile

`config/generic` is a safe explicit-URL-list starting point. Edit `discovery.urls`; it does not recursively crawl links.

```bash
.venv/bin/agentweb2md --site generic
```

Output defaults to `/tmp/generic_url_list_sample`.

The bundled `example.com` URL is intentionally tiny and may score below the default quality threshold. It verifies the pipeline; replace it with representative URLs before judging extraction quality.

Site profiles are read from `./config` by default; set `AGENTWEB2MD_CONFIG_DIR` to use another writable profile directory. Baselines and checkpoints are stored under `./.agentweb2md`; set `AGENTWEB2MD_STATE_DIR` to move that local state.

Extraction and quality commands return a non-zero exit status when discovery is empty, an item fails, or the quality gate fails, so an agent or CI job can stop reliably. HTTP responses are capped at 25 MiB by default; adjust the positive `rate_limit.max_response_bytes` value only for reviewed sites that need it.

## Responsible use

Only extract content you are authorized to access and reuse. Respect site terms, robots policies, rate limits, privacy, and copyright. This project does not bypass authentication or access controls.

## License

MIT

Security issues should be reported privately as described in [SECURITY.md](SECURITY.md).
