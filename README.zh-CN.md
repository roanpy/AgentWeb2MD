# 🔗 AgentWeb2MD

[English](README.md) | [简体中文](README.zh-CN.md)

**由 Agent 驱动，将网页内容提取为干净、可审查的 Markdown。**

[![License: MIT](https://img.shields.io/badge/license-MIT-2ea44f.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-3776ab.svg)
![Workflow](https://img.shields.io/badge/workflow-agent--guided-8a63d2.svg)
![Output](https://img.shields.io/badge/output-Markdown-1f6feb.svg)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-555.svg)

AgentWeb2MD 将 Agent 的判断能力与确定性的提取引擎结合起来。Agent 负责确定范围、审查站点配置、检查小样、阅读质量报告并持续迭代，然后才执行完整提取。

[为什么由 Agent 驱动？](#为什么由-agent-驱动) · [主要能力](#主要能力) · [安装](#安装) · [Agent 工作流](#agent-工作流) · [使用边界](#使用边界)

> 本项目刻意不做“一键式全自动爬虫”。网页范围、选择器、噪声规则和输出质量都需要 Agent 或人工审查。

## 为什么由 Agent 驱动？

网站结构和可接受的输出质量都需要判断。完全自动猜测选择器、抓取范围、噪声规则与质量阈值，可能在没有明显报错的情况下生成缺失或受污染的 Markdown。AgentWeb2MD 把这些决策交给 Agent 或人工审查者，同时自动完成可重复、可验证的机械步骤。

```text
Agent 检查网站与提取范围
        ↓
生成并审查配置草案
        ↓
在 /tmp 中运行小样
        ↓
检查 Markdown 与质量报告
        ↓
修正配置并重复验证
        ↓
批准完整提取
```

## 主要能力

- 通过 URL 列表、Sitemap、页面爬取或 JSON API 进行配置驱动的内容发现
- 主内容隔离与可配置的页面噪声清理
- 表格、标签页、折叠面板、图片和下载资源处理
- 结构化规格与资源 Sidecar 索引
- 确定性的质量报告与本地回归基线
- 可选使用 Playwright 渲染 JavaScript 页面

## 环境要求

- Python 3.10+
- `requests`、`beautifulsoup4` 和 `markdownify`
- 一个负责驱动提取循环的编码 Agent 或人工审查者
- 可选：用于 JavaScript 页面的 Playwright

## 安装

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

如需提取 JavaScript 渲染的网站：

```bash
.venv/bin/pip install playwright
.venv/bin/playwright install chromium
```

## Agent 工作流

向编码 Agent 提供目标 URL、需要提取的内容、必须排除的区域、附件策略和质量阈值。仓库中的 `AGENTS.md` 定义了 Agent 的操作契约。

### 1. 只探测，不写入配置

```bash
.venv/bin/python init_site.py --site example --base-url https://example.com --auto
```

探测结果只是证据，不是最终配置。Agent 必须审查建议的发现模式与选择器。

### 2. 创建配置草案

```bash
.venv/bin/python init_site.py \
  --site example \
  --base-url https://example.com \
  --output-root /tmp/agentweb2md/example \
  --non-interactive
```

也可以只输出配置草案与验证建议，不写入文件：

```bash
.venv/bin/python config_loop.py https://example.com --site example
```

### 3. 审查并验证配置

Agent 应缩小发现范围、设置最小且准确的 `content_selector`，并限制首次提取的页面数量。随后验证配置：

```bash
.venv/bin/python - <<'PY'
from extract_generic import load_config, validate_config

config = load_config("config/example/common.json")
errors, warnings = validate_config(config)
print({"errors": errors, "warnings": warnings})
raise SystemExit(bool(errors))
PY
```

### 4. 提取小样

```bash
.venv/bin/python extract_generic.py --site example
```

### 5. 评分并检查

```bash
.venv/bin/python quality_report.py \
  --output-root /tmp/agentweb2md/example \
  --site example
```

Agent 必须阅读代表性 Markdown 和 `_quality_report.md`。分数通过不能代替内容检查。持续修正配置并重复小样，直到结果可接受。

### 6. 批准完整提取

只有小样通过审查后，才能解除发现数量限制或修改输出目录。工具生成内容并不意味着内容已经获准发布或复用。

## 通用配置

`config/generic` 是安全的显式 URL 列表起点。编辑 `discovery.urls` 即可；该配置不会递归抓取链接。

```bash
.venv/bin/python extract_generic.py --site generic
```

默认输出目录为 `/tmp/generic_url_list_sample`。

自动生成的 `_baseline*.json` 和 `_quality_baseline.json` 属于本地状态文件，已被 Git 忽略。

## 使用边界

只提取你有权访问和复用的内容。请遵守目标网站的使用条款、robots 策略、速率限制、隐私要求与版权规则。本项目不会绕过身份认证或访问控制。

## 许可证

MIT
