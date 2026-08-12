"""通用网站内容提取引擎 — 配置驱动，markdownify 转换，完整实现。
配置结构：config/{site}/common.json + {page_type}.json，引擎自动合并。
新建站点只需在 config/{site}/ 下创建 common.json 和分类 json 即可。

支持：产品树递归遍历、行业方案+案例、图片下载、资料下载、增量提取。

用法：
  python extract_generic.py                       # 全量提取
  python extract_generic.py --config other.json   # 指定配置
  python extract_generic.py --incremental         # 增量(只重跑变更)
  python extract_generic.py --check               # 仅检测变更
  python extract_generic.py --init                # 初始化基线指纹(分批+断点续传)
"""
import os, re, sys, json, hashlib, argparse, time
from urllib.parse import urlparse, parse_qs, urljoin as _urljoin_std


def _url_join(base: str, href: str) -> str:
    """Resolve relative href against base URL using standard URL resolution."""
    return _urljoin_std(base, href)

try:
    import requests
    from bs4 import BeautifulSoup, Comment
    from markdownify import markdownify
except ImportError as e:
    print(f"缺少依赖: {e}\n请: pip install requests beautifulsoup4 markdownify"); sys.exit(1)

from resource_utils import (
    decide_save_dir,
    build_category_root_res,
    sanitize_filename,
    parse_download_table,
)
from discovery_controls import apply_discovery_controls
from hooks import apply_hooks
from resources_output import record_resource, write_resources_output
from specs_output import record_specs, write_specs_output

DEFAULT_SITE = os.environ.get("WEM_SITE", "generic")
DEFAULT_CONFIG = os.path.join(os.path.dirname(__file__), "config", DEFAULT_SITE, "common.json")
DEFAULT_BASELINE = os.path.join(os.path.dirname(__file__), "config", DEFAULT_SITE, "_baseline.json")
INIT_CHECKPOINT = os.path.join(os.path.dirname(__file__), "_init_checkpoint.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

DOWNLOADED_IMG_MD5 = {}
DOWNLOADED_RES_MD5 = {}

_last_request_time = 0.0


def _throttle(config):
    global _last_request_time
    delay = config.get("rate_limit", {}).get("delay_between_requests", 0)
    if delay > 0:
        elapsed = time.time() - _last_request_time
        if elapsed < delay:
            time.sleep(delay - elapsed)
    _last_request_time = time.time()


def _retry_get(url, config, timeout=15):
    max_retries = config.get("rate_limit", {}).get("max_retries", 3)
    backoff = config.get("rate_limit", {}).get("retry_backoff_base", 2)
    for attempt in range(max_retries):
        _throttle(config)
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                if not r.encoding or r.encoding.lower() in ("iso-8859-1",):
                    r.encoding = r.apparent_encoding or "utf-8"
                return r
            if r.status_code in (429, 503) and attempt < max_retries - 1:
                time.sleep(backoff ** attempt)
                continue
            r.raise_for_status()
        except requests.RequestException:
            if attempt < max_retries - 1:
                time.sleep(backoff ** attempt)
            else:
                raise
    return r


def _web_fetch(url, config):
    r = _retry_get(url, config, timeout=20)
    return BeautifulSoup(r.text, "html.parser")


def fetch_page(url, config):
    """Fetch a web page and return its HTML, using requests or Playwright.

    Uses ``render_mode`` from config to decide:
    - ``"requests"`` (default): simple HTTP GET via requests
    - ``"playwright"``: headless Chromium with stealth anti-detection
    - ``"auto"``: try requests first, fallback to playwright if JS-heavy shell page detected

    Playwright args can be customized via ``config["playwright_args"]``:
    - ``stealth`` (bool, default True): remove webdriver flag, set user-agent
    - ``wait_ms`` (int, default 2000): ms to wait after networkidle
    - ``headless`` (bool, default True): headless mode
    - ``viewport`` (dict): viewport size, default 1920x1080
    """
    render_mode = config.get("render_mode", "requests")
    if render_mode == "auto":
        html = _retry_get(url, config).text
        if _looks_like_js_shell(html):
            return _fetch_with_playwright(url, config)
        return html
    if render_mode != "playwright":
        return _retry_get(url, config).text
    return _fetch_with_playwright(url, config)


def _looks_like_js_shell(html):
    """True if HTML looks like a JS shell page with minimal real content."""
    if not html or len(html) < 500:
        return True
    soup = BeautifulSoup(html, "html.parser")
    body = soup.find("body")
    if not body:
        return True
    text = body.get_text(strip=True)
    if len(text) < 500:
        return True
    scripts = soup.find_all("script")
    if len(scripts) > 10 and len(text) < 2000:
        return True
    return False


def _fetch_with_playwright(url, config):
    pw_args = config.get("playwright_args", {})
    stealth = pw_args.get("stealth", True)
    wait_ms = pw_args.get("wait_ms", 2000)
    headless = pw_args.get("headless", True)
    viewport = pw_args.get("viewport", {"width": 1920, "height": 1080})

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ImportError(
            "playwright is required for render_mode='playwright' or 'auto' fallback. "
            "Install with: pip install playwright && playwright install chromium"
        )

    with sync_playwright() as p:
        launch_args = ["--disable-blink-features=AutomationControlled"] if stealth else []
        browser = p.chromium.launch(headless=headless, args=launch_args)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale=config.get("locale", "zh-CN"),
            viewport=viewport,
        )
        page = context.new_page()
        if stealth:
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
        page.goto(url, timeout=30000, wait_until="networkidle")
        page.wait_for_timeout(wait_ms)
        html = page.content()
        browser.close()

    return html


def discover_page_types(config_dir):
    """Discover available page types by globbing config dir for *.json (excluding common.json and _*.json)."""
    import glob as _glob
    types = []
    for f in sorted(os.listdir(config_dir)):
        if f.endswith(".json") and f != "common.json" and not f.startswith("_"):
            types.append(f[:-5])  # strip .json
    return types


def _reject_duplicate_json_keys(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError(f"Duplicate config key '{key}'")
        data[key] = value
    return data


def _load_json_config(path):
    with open(path, encoding="utf-8") as f:
        return json.loads(f.read(), object_pairs_hook=_reject_duplicate_json_keys)


def load_config(path, page_type=None):
    """Load config from common.json + {page_type}.json, merged via deep_merge.

    If page_type is given, look for common.json + {page_type}.json in the same dir.
    If page_type is None, load path as-is (for standalone configs).
    """
    config_dir = os.path.dirname(path)
    base_name = os.path.basename(path)

    # New split config: common.json + {page_type}.json
    if page_type:
        common_path = os.path.join(config_dir, "common.json")
        type_path = os.path.join(config_dir, f"{page_type}.json")
        if not os.path.exists(common_path):
            raise FileNotFoundError(f"通用配置不存在: {common_path}")
        if not os.path.exists(type_path):
            raise FileNotFoundError(f"分类配置不存在: {type_path}，可用分类: {discover_page_types(config_dir)}")
        config = _load_json_config(common_path)
        type_cfg = _load_json_config(type_path)
        # Merge: type_cfg overrides config (replace semantics for lists)
        _deep_merge(config, type_cfg)
        # Store page_type in config for downstream use (namespaced to avoid collision)
        config["__engine_page_type"] = page_type
        errors, warnings = validate_config(config)
        if errors:
            print("❌ 配置错误:")
            for e in errors: print(f"  {e}")
            raise ValueError(f"配置验证失败: {errors[0]}")
        if warnings:
            for w in warnings: print(f"  ⚠ {w}")
        return config

    # Fallback: load single json file (no page_type specified)
    config = _load_json_config(path)
    errors, warnings = validate_config(config)
    if errors:
        print("❌ 配置错误:")
        for e in errors: print(f"  {e}")
        raise ValueError(f"配置验证失败: {errors[0]}")
    if warnings:
        for w in warnings: print(f"  ⚠ {w}")
    return config


def _deep_merge(base, override):
    """Merge override into base dict recursively. Lists are replaced, not appended."""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def fm(config, key, default=""):
    return config.get("field_mapping", {}).get(key, default)


def fm_get(data, config, key, default=""):
    """Look up data[field] where field = config.field_mapping[key] or key itself."""
    field = config.get("field_mapping", {}).get(key) or key
    return data.get(field, default) if isinstance(data, dict) else default


class _SafeFormatDict(dict):
    """Dict that returns '{key}' for missing keys, so .format_map() never raises KeyError."""
    def __missing__(self, key):
        return "{" + key + "}"


def tpl(config, key, **kwargs):
    """Render a template string from config['templates'][key] with kwargs.
    Missing placeholders are left as-is instead of raising KeyError."""
    tmpl = config.get("templates", {}).get(key, "")
    if not tmpl: return ""
    try:
        return tmpl.format_map(_SafeFormatDict(kwargs))
    except (IndexError, ValueError):
        return tmpl


def kind_key(kind):
    return "industries" if kind == "industry" else kind + "s"


def _escape_table_pipes(md: str, empty_header_fill: str = " 描述 ") -> str:
    """Fix unescaped | inside markdown table cells.

    markdownify does not escape literal pipe characters that appear in cell
    content (e.g. "产品发布 | 预计").  This breaks every downstream table
    parser which splits on |.  We detect the column count from the separator
    row and reassemble any data rows that have too many columns.
    """
    lines = md.split('\n')
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Detect table start: pipe-delimited line with at least 2 columns
        if stripped.startswith('|') and stripped.endswith('|') and stripped.count('|') >= 3:
            table_start = i
            # Look ahead for separator row to determine column count
            sep_idx = None
            ncols = None
            if i + 1 < len(lines):
                next_s = lines[i + 1].strip()
                if next_s.startswith('|') and '---' in next_s:
                    sep_idx = i + 1
                    ncols = next_s.count('|') - 1  # | a | b | → 2 pipes interior = 2 cols

            if ncols is not None:
                # Collect all table rows (header + sep + data)
                table_lines = [line]
                i += 1
                while i < len(lines):
                    ts = lines[i].strip()
                    if ts.startswith('|') and ts.endswith('|'):
                        table_lines.append(lines[i])
                        i += 1
                    else:
                        break

                # Now fix rows that have too many columns
                fixed = []
                for tl in table_lines:
                    parts = tl.split('|')
                    # parts: ['', col1, col2, ..., ''] for a well-formed row
                    # Expected len = ncols + 2 (leading + trailing empty)
                    expected_len = ncols + 2
                    if len(parts) <= expected_len:
                        fixed.append(tl)
                    else:
                        # Too many splits — merge extras into the last real cell
                        # Keep leading '' + first (ncols-1) cells intact
                        merged = parts[:ncols]  # '' + (ncols-1) cells
                        # The last real cell = parts[ncols:-1] joined with | (skip trailing empty)
                        last_cell_content = '|'.join(parts[ncols:-1])
                        # Escape the internal pipes in the last cell
                        last_cell_content = last_cell_content.replace('|', '\\|')
                        merged.append(last_cell_content)
                        merged.append('')  # trailing empty for closing |
                        fixed.append('|'.join(merged))

                # Also fix the header row if its second cell is empty (e.g. | 附件 |  |)
                # by replacing with meaningful column names
                if len(fixed) >= 2:
                    hdr_parts = fixed[0].split('|')
                    if ncols == 2 and hdr_parts[1].strip() and not hdr_parts[2].strip():
                        # Header like | 附件 | | → | 型号 | 描述 |
                        hdr_parts[2] = ' 描述 '
                        fixed[0] = '|'.join(hdr_parts)

                result.extend(fixed)
            else:
                # No separator found — leave as-is
                result.append(line)
                i += 1
        else:
            result.append(line)
            i += 1

    return '\n'.join(result)


def _strip_table_text_echo(md: str) -> str:
    """Remove bare-text echo that markdownify appends after MD tables.

    markdownify renders <table> as a MD pipe-table then repeats each cell
    as a standalone text line.  This function detects that pattern and
    strips the echo, keeping only the proper MD table.
    """
    lines = md.split('\n')
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Detect start of a MD table: line starts with | and has |
        if stripped.startswith('|') and stripped.endswith('|') and stripped.count('|') >= 3:
            # Collect the entire table block (header, separator, rows)
            table_lines = [line]
            table_cells = set()
            for cell in stripped.split('|'):
                c = cell.strip()
                if c and c != '---' and not all(ch == '-' for ch in c):
                    table_cells.add(c)
            i += 1
            while i < len(lines):
                tl = lines[i]
                ts = tl.strip()
                if ts.startswith('|') and ts.endswith('|') and ts.count('|') >= 2:
                    table_lines.append(tl)
                    for cell in ts.split('|'):
                        c = cell.strip()
                        if c and c != '---' and not all(ch == '-' for ch in c):
                            table_cells.add(c)
                    i += 1
                else:
                    break

            # Emit the table
            result.extend(table_lines)

            # Now scan ahead: skip bare-text echo lines that match table cells
            # Pattern: alternating blank lines and single-word/cell lines
            while i < len(lines):
                ls = lines[i].strip()
                if ls == '':
                    # Blank line — might be between echo lines
                    # Look ahead: if next non-blank line is a table cell echo, skip both
                    j = i + 1
                    while j < len(lines) and lines[j].strip() == '':
                        j += 1
                    if j < len(lines):
                        candidate = lines[j].strip()
                        # Don't skip if it's a heading, list, or new content
                        if candidate.startswith('#') or candidate.startswith('-') or candidate.startswith('*') or candidate.startswith('>') or candidate.startswith('!') or candidate.startswith('|'):
                            break
                        # Check if candidate matches a table cell
                        if candidate in table_cells:
                            i = j + 1  # skip the echo line
                            continue
                        # Check if candidate is a number/unit that appeared in table
                        is_echo = False
                        for cell in table_cells:
                            # Loose match: candidate is substring of a cell or vice versa
                            if len(candidate) <= len(cell) and candidate in cell and len(candidate) >= 2:
                                is_echo = True
                                break
                        if is_echo:
                            i = j + 1
                            continue
                    # Not an echo — stop scanning
                    break
                elif ls in table_cells:
                    # Direct echo line
                    i += 1
                else:
                    # Check if it's a partial echo
                    is_echo = False
                    for cell in table_cells:
                        if len(ls) <= len(cell) and ls in cell and len(ls) >= 2:
                            is_echo = True
                            break
                    if is_echo:
                        i += 1
                    else:
                        break
        else:
            result.append(line)
            i += 1

    return '\n'.join(result)


def _flatten_table_with_headings(md: str) -> str:
    """Convert tables that contain headings into standalone headings + content.
    
    Pattern: | #### Title   long paragraph | ![img](...) |  becomes:
             #### Title
             long paragraph
             ![img](...)
    Also removes orphaned header + separator rows left after flattening.
    """
    lines = md.split('\n')
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        # Detect a table row cell that starts with a heading
        if s.startswith('|') and '#' in s:
            cells = [c.strip() for c in s.strip('|').split('|')]
            if len(cells) >= 1:
                cell0 = cells[0]
                # If first cell starts with heading markers, flatten
                m = re.match(r'^(#{1,6})\s+(.*)', cell0)
                if m:
                    heading = m.group(0)
                    rest = cell0[m.end():].strip()
                    # Remove preceding orphaned header row + separator if present
                    if len(result) >= 2:
                        prev1 = result[-1].strip()
                        prev2 = result[-2].strip() if len(result) >= 2 else ""
                        if prev1.startswith('|') and '---' in prev1 and prev2.startswith('|'):
                            result.pop()   # separator
                            result.pop()   # header row
                    result.append(heading)
                    if rest:
                        result.append('')
                        result.append(rest)
                    # Other cells (images, etc.)
                    for c in cells[1:]:
                        if c:
                            result.append('')
                            result.append(c)
                    # Skip next line if it's the table separator (| --- |)
                    if i + 1 < len(lines) and lines[i + 1].strip().startswith('|') and '---' in lines[i + 1]:
                        i += 2
                    else:
                        i += 1
                    continue
        result.append(line)
        i += 1
    return '\n'.join(result)


def _fix_table_separators(md: str) -> str:
    """Insert missing header separator rows in markdown tables.

    markdownify often drops the |---| separator between header and data rows,
    causing tables to render as plain text instead of tables.  This detects any
    block of consecutive |...| lines that lacks a separator on line two and
    injects one.
    """
    lines = md.splitlines()
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("|") and stripped.count("|") >= 2:
            j = i
            while j < len(lines) and lines[j].strip().startswith("|") and lines[j].strip().count("|") >= 2:
                j += 1
            block = lines[i:j]
            if len(block) == 1:
                cells = [c.strip() for c in block[0].strip().strip("|").split("|")]
                out.append(" ".join(c for c in cells if c))
                i = j
                continue
            if len(block) >= 2:
                if any("\\| --- \\| --- \\|" in row for row in block):
                    i = j
                    continue
                first_cells = [c.strip() for c in block[0].strip().strip("|").split("|")]
                second = block[1].strip()
                if not any(first_cells) and re.search(r'^\|[\s\-:]+\|', second):
                    for row in block[2:]:
                        cells = [c.strip() for c in row.strip().strip("|").split("|")]
                        text = " ".join(c for c in cells if c)
                        if text:
                            out.append(text)
                    i = j
                    continue
                if not re.search(r'^\|[\s\-:]+\|', second):
                    cols = block[0].strip().count("|") - 1
                    if cols >= 1:
                        sep = "| " + " | ".join(["---"] * cols) + " |"
                        indent = block[0][:len(block[0]) - len(block[0].lstrip())]
                        block.insert(1, indent + sep)
                target_pipes = block[0].strip().count("|")
                for n in range(2, len(block)):
                    row = block[n]
                    missing = target_pipes - row.strip().count("|")
                    if missing > 0:
                        indent = row[:len(row) - len(row.lstrip())]
                        cells = [c.strip() for c in row.strip().strip("|").split("|")]
                        block[n] = indent + "| " + " | ".join([""] * missing + cells) + " |"
            out.extend(block)
            i = j
        else:
            out.append(line)
            i += 1
    return "\n".join(out)


def _recover_fragmented_tables(md: str) -> str:
    """Detect sequences of short standalone lines separated by blank lines that
    fit the 4-column metric comparison table pattern (header row + N data rows
    interleaved by blank lines) and merge each column-separated run into a single
    Markdown table.

    Pattern (each cell on its own line, blank line between cells):
        <col0>
        <blank>
        <col1>
        <blank>
        <col2>
        <blank>
        <col3>
    Becomes one row: | col0 | col1 | col2 | col3 |
    Repeats for as many rows as detected (typically 4).
    """
    lines = md.split('\n')
    out = []
    i = 0
    while i < len(lines):
        # Try to detect a run of cell-blank repeats at position i
        cells = []
        j = i
        while j < len(lines):
            s = lines[j].strip()
            if s and not s.startswith(('#', '!', '|', '-', '*', '>', '<')) and len(s) < 40:
                cells.append(s)
                j += 1
                if j < len(lines) and not lines[j].strip():
                    j += 1
                else:
                    break
            else:
                break
        # If we got exactly 4 cells in interleaved blank-line pattern, rebuild as table
        if len(cells) == 4:
            # Find how many rows: each row has 4 cells + 3 blank lines in between
            # We already consumed one row (4 cells). Now look for next row.
            all_rows = [cells]
            while j < len(lines):
                next_cells = []
                k = j
                while k < len(lines) and len(next_cells) < 4:
                    s = lines[k].strip()
                    if s and not s.startswith(('#', '!', '|', '-', '*', '>', '<')) and len(s) < 40:
                        next_cells.append(s)
                        k += 1
                        if k < len(lines) and not lines[k].strip():
                            k += 1
                    elif not s and len(next_cells) > 0 and len(next_cells) < 4:
                        # unexpected blank mid-row, abort
                        break
                    else:
                        break
                if len(next_cells) == 4:
                    all_rows.append(next_cells)
                    j = k
                else:
                    break
            if len(all_rows) >= 2:  # header + at least 1 data row
                # Build table
                header = all_rows[0]
                out.append('| ' + ' | '.join(header) + ' |')
                # 4-column separator
                sep_header_count = sum(1 for c in header if c)
                out.append('| ' + ' | '.join(['---'] * sep_header_count) + ' |')
                for row in all_rows[1:]:
                    out.append('| ' + ' | '.join(row) + ' |')
                out.append('')  # blank line after table
                i = j
                continue
        if i < len(lines):
            out.append(lines[i])
            i += 1
    return '\n'.join(out)


def _fix_orphan_links(md: str) -> str:
    """Fix orphaned markdown link closers split across lines by markdownify.

    When <a href> wraps block elements, markdownify produces:
        [![](image.jpg)

        ### Title

        desc](/link)
    This merges them into: [![](image.jpg) ### Title desc](/link)
    """
    lines = md.split('\n')
    # Orphan closer: a line ending with ](url), where there's no [
    # on the same line (meaning the [ opener is on a different line).
    orphan_pat = re.compile(r'^([^\[\]]*)\]\(([^)]+)\)\s*$')
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        m = orphan_pat.match(s)
        if not m:
            i += 1
            continue
        before_close = m.group(1)  # text before ]( on this line
        link_url = m.group(2)
        # If [ is in before_close, it has its own opener — normal link
        if '[' in before_close:
            i += 1
            continue

        # Scan backwards for the [ opener — the nearest preceding non-blank
        # line that starts with [ and has unbalanced brackets (more [ than ])
        # indicating the markdown link anchor is still open.
        opener_idx = None
        for j in range(i - 1, max(i - 20, -1), -1):
            prev = lines[j].strip()
            if not prev:
                continue
            # Check if line starts with [ or ends with [ or [!
            is_opener = prev.startswith('[') or prev.endswith('[') or prev.endswith('[!')
            if is_opener:
                open_b = prev.count('[')
                close_b = prev.count(']')
                # If open brackets > close brackets, link anchor is open
                if open_b > close_b:
                    opener_idx = j
                    break

        if opener_idx is None:
            # No opener found — drop the orphan line
            lines.pop(i)
            continue

        # Collect mid content
        opener = lines[opener_idx].rstrip()
        mid_parts = [opener]
        for k in range(opener_idx + 1, i):
            t = lines[k].strip()
            if t:
                mid_parts.append(t)
        if before_close:
            mid_parts.append(before_close)

        merged = ' '.join(mid_parts) + f"]({link_url})"
        lines[opener_idx] = merged
        del lines[opener_idx + 1:i + 1]
        i = opener_idx + 1

    return '\n'.join(lines)


def _merge_alternating_pairs(md: str, pair_table_header: str = "| 型号 | 描述 |") -> str:
    """Detect repeating key→empty→value→empty patterns and merge into a table.

    Catches product spec blocks (e.g. "CX2900-0132\\n\\n16 GB MicroSD 卡")
    that markdownify renders as flat lines from <div>/<span> structures
    instead of proper <table> elements.

    Detection: 3+ consecutive groups of (short line → blank → longer line → blank)
    where "short" looks like a product code (alphanumeric with dashes, short),
    not descriptive prose (long Chinese text, bold markers, colons).
    """
    # Heuristic: a "product code" key is short (<40 chars), starts with
    # uppercase letter or digit, and contains a dash or is all-caps+digits.
    # Descriptive text keys (with **bold**, ：, Chinese chars) are rejected.
    def _looks_like_product_code(text):
        t = text.strip().lstrip('*').rstrip('*').strip()
        if len(t) > 50:
            return False
        # Must start with uppercase letter or digit
        if not t or (not t[0].isupper() and not t[0].isdigit()):
            return False
        # Must contain a dash (like CX2900-0132, S7-1200) OR be short all-caps+digits
        if '-' in t:
            return True
        if t.replace(' ', '').isalnum() and len(t) <= 20:
            return True
        return False

    lines = md.split('\n')
    result = []
    i = 0
    while i < len(lines):
        run_start = i
        pairs = []
        j = i
        while j < len(lines):
            key_idx = j
            if (not lines[key_idx].strip()
                or lines[key_idx].startswith('#')
                or lines[key_idx].startswith('|')):
                break
            key_text = lines[key_idx].strip()
            if len(key_text) > 60:
                break
            # Key must look like a product code, not descriptive text
            if not _looks_like_product_code(key_text):
                break
            if j + 1 >= len(lines) or lines[j + 1].strip():
                break
            if j + 2 >= len(lines):
                break
            val_idx = j + 2
            if (not lines[val_idx].strip()
                or lines[val_idx].startswith('#')
                or lines[val_idx].startswith('|')):
                break
            val_text = lines[val_idx].strip()
            pairs.append((key_text, val_text))
            j = val_idx + 1
            if j < len(lines) and not lines[j].strip():
                j += 1
        if len(pairs) >= 3:
            result.append(pair_table_header)
            result.append("| --- | --- |")
            for key, val in pairs:
                safe_key = key.replace('|', '\\|')
                safe_val = val.replace('|', '\\|')
                result.append(f"| {safe_key} | {safe_val} |")
            i = j
        else:
            result.append(lines[i])
            i += 1
    return '\n'.join(result)


def _merge_flattened_small_tables(md: str, expected_headers: list[str] | None = None) -> str:
    if not expected_headers:
        return md
    headers = expected_headers
    cols = len(headers)
    lines = md.split('\n')
    result = []
    seen_tables = set()
    i = 0
    while i < len(lines):
        if lines[i].strip() != headers[0]:
            result.append(lines[i])
            i += 1
            continue

        cursor = i
        header: list[str] = []
        for _ in range(cols):
            while cursor < len(lines) and not lines[cursor].strip():
                cursor += 1
            if cursor >= len(lines):
                break
            header.append(lines[cursor].strip())
            cursor += 1
        if header != headers:
            result.append(lines[i])
            i += 1
            continue

        cells = []
        while cursor < len(lines):
            while cursor < len(lines) and not lines[cursor].strip():
                cursor += 1
            if cursor >= len(lines):
                break
            text = lines[cursor].strip()
            if text == headers[0] and cells and len(cells) % cols == 0:
                break
            if text.startswith(("#", "|", "!")) or len(text) > 80:
                break
            cells.append(text)
            cursor += 1

        min_rows = max(8, cols * 2)
        if len(cells) < min_rows or len(cells) % cols:
            result.append(lines[i])
            i += 1
            continue

        table_key = tuple(cells)
        if table_key in seen_tables:
            i = cursor
            continue
        seen_tables.add(table_key)

        result.append("| " + " | ".join(headers) + " |")
        result.append("| " + " | ".join(["---"] * cols) + " |")
        for row_start in range(0, len(cells), cols):
            row = [str(cell).replace('|', '\\|') for cell in cells[row_start:row_start + cols]]
            result.append("| " + " | ".join(row) + " |")
        i = cursor
    return '\n'.join(result)


def _clean_empty_links(md: str) -> str:
    """Normalize empty/self-link markdown anchors: [text]() → text, [url](url) → url."""
    lines = md.split('\n')
    cleaned = []
    for line in lines:
        s = line
        # [text]() / [text](#) / [text](#anchor) / [text](javascript:)
        s = re.sub(r'\[([^\]]+)\]\(\s*(?:#|javascript:)[^)]*\)', r'\1', s)
        # [text]( ) with nothing meaningful in href
        s = re.sub(r'\[([^\]]+)\]\(\s*\)', r'\1', s)
        # [url](url) where text is exactly the URL
        s = re.sub(r'\[(https?://[^\]]+)\]\(\1\)', r'\1', s)
        cleaned.append(s)
    return '\n'.join(cleaned)


def _split_image_caption(md: str) -> str:
    """Separate image markdown from immediately following italic caption.
    
    ![alt](file)*caption* → ![alt](file)\n\n*caption*
    Some renderers merge the *caption* into the image alt without a separator."""
    return re.sub(r'(!\[[^\]]*\]\([^)]*\))\*([^*]+)\*', r'\1\n\n*\2*', md)


def _normalize_unicode_whitespace(md: str) -> str:
    """Normalize Unicode whitespace characters to plain spaces.

    Handles NBSP, thin spaces, zero-width chars, ideographic space.
    Preserves code block content between ``` delimiters."""
    lines = md.split('\n')
    in_code = False
    out = []
    for line in lines:
        if line.strip().startswith('```'):
            in_code = not in_code
        elif not in_code:
            line = (line.replace('\u00a0', ' ')      # NBSP
                        .replace('\u200b', '')        # ZWSP
                        .replace('\u200c', '')        # ZWNJ
                        .replace('\u200d', '')        # ZWJ
                        .replace('\ufeff', '')        # BOM
                        .replace('\u202f', ' ')       # narrow NBSP
                        .replace('\u2009', ' ')       # thin space
                        .replace('\u2007', ' ')       # figure space
                        .replace('\u3000', ' '))      # ideographic space
        out.append(line)
    return '\n'.join(out)


def _fix_split_colon_labels(md: str) -> str:
    """Merge short label lines followed by a colon line into a bullet."""
    lines = md.split('\n')
    out = []
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if (
            s
            and j < len(lines)
            and len(s) <= 20
            and not s.startswith(('#', '-', '|', '!', '['))
            and lines[j].strip().startswith(("：", ":"))
        ):
            out.append(f"- {s}{lines[j].strip()}")
            i = j + 1
            continue
        if (
            s.startswith('- ')
            and j < len(lines)
            and 2 < len(s) <= 24
            and lines[j].strip().startswith(("：", ":"))
        ):
            out.append(f"{s}{lines[j].strip()}")
            i = j + 1
            continue
        out.append(lines[i])
        i += 1
    result = '\n'.join(out)
    result = re.sub(r'^\s*-\s+-\s+', '- ', result, flags=re.MULTILINE)
    result = re.sub(r'^\s*-\s+#{1,6}\s+', '- ', result, flags=re.MULTILINE)

    def clean_heading(match):
        content = re.sub(r'\s+', ' ', match.group(2)).strip()
        return f"{match.group(1)} {content}"

    return re.sub(r'^(#{1,6})\s+(.+?)\s*$', clean_heading, result, flags=re.MULTILINE)


def _unwrap_iframe_srcdoc(html_str: str) -> str:
    """Replace ``<iframe srcdoc="...">`` with its unescaped inner HTML.

    Unescaping only the attribute value avoids exposing unrelated escaped
    attributes as visible text.
    """
    import html as _html
    def repl(m):
        inner = _html.unescape(m.group(1)).replace('\\"', '"')
        return inner
    return re.sub(
        r'<iframe[^>]*\bsrcdoc="([^"]*)"[^>]*>\s*</iframe>',
        repl, html_str, flags=re.DOTALL,
    )


def _strip_mirrored_escaped_html_blocks(html: str) -> str:
    if "&lt;" not in html or html.count("<") < 20:
        return html

    def norm_text(value: str) -> str:
        soup = BeautifulSoup(value, "html.parser")
        return re.sub(r'\s+', '', soup.get_text(" "))

    escaped_block_re = re.compile(r'&lt;(?:p|ul|ol|table)\b.*?&lt;/(?:p|ul|ol|table)&gt;', re.DOTALL)
    real_only = escaped_block_re.sub('', html)
    real_text = norm_text(real_only)

    def replace_if_mirror(match):
        block = match.group(0)
        import html as _html
        text = norm_text(_html.unescape(block).replace('\\"', '"'))
        if len(text) >= 20 and text in real_text:
            return ''
        return block

    return escaped_block_re.sub(replace_if_mirror, html)


def _normalize_blank_lines(md: str) -> str:
    lines = [line.rstrip() if line.strip() else "" for line in md.splitlines()]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines))


def _strip_empty_value_labels(md: str) -> str:
    labels = r'(产品状态|Product status|Status)'
    return re.sub(rf'^\*\*{labels}\s*[:：]\*\*\s*$', '', md, flags=re.MULTILINE | re.IGNORECASE)


def _strip_image_alt_echoes(md: str) -> str:
    lines = md.split('\n')
    out = []
    last_alt = ""
    for line in lines:
        match = re.match(r'^!\[([^\]]+)\]\([^)]+\)\s*$', line.strip())
        if match:
            last_alt = match.group(1).strip()
            out.append(line)
            continue
        if last_alt and line.strip() == last_alt:
            last_alt = ""
            continue
        if line.strip():
            last_alt = ""
        out.append(line)
    return '\n'.join(out)


def _strip_resource_card_sections(md: str) -> str:
    resource_headings = {"观看", "收听", "阅读", "Watch", "Listen", "Read"}
    resource_section_re = re.compile(r"^#{1,6}\s+.*资源\s*$")
    resource_card_re = re.compile(
        r"^\[!\[.*\]\(.+\).*?(?:###\s*(?:观看|收听|阅读|Watch|Listen|Read)\b|\*\*(?:点播式网络研讨会|点播网络研讨会|Podcast|White paper)\*\*)",
        re.IGNORECASE,
    )
    ui_noise_re = re.compile(r"按服务探索|按子行业探索|按数字主线探索|Explore by \w+(?:\s+\w+)*", re.IGNORECASE)
    ui_cell_re = re.compile(r"\|\s*Select\.\.\.\s*\|")
    lines = md.split('\n')
    out = []
    skipping_level = 0
    for line in lines:
        match = re.match(r'^(#{1,6})\s+(.+?)\s*$', line.strip())
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            if title in resource_headings or resource_section_re.match(line.strip()):
                skipping_level = level
                continue
            if skipping_level and level <= skipping_level:
                skipping_level = 0
        if skipping_level:
            continue
        # Strip inline UI noise (keep line, remove noise text)
        cleaned_line = ui_noise_re.sub('', line)
        cleaned_line = ui_cell_re.sub('| |', cleaned_line)
        # Drop standalone Select... lines
        if cleaned_line.strip() == 'Select...':
            continue
        out.append(cleaned_line)
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(out)).strip()


def sanitize(name):
    """Sanitize a name for filesystem use; truncates to 200 chars to stay under OS limits."""
    s = re.sub(r'[\\/*?:"<>|]', '_', name).strip().replace(' ', '_')
    # ponytail: truncate at 200 chars — OS limit is 255, leave room for parent path + extension
    return s[:200] if len(s) > 200 else s


def text_fp(html):
    if not html: return ""
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style"]): t.decompose()
    return hashlib.md5(re.sub(r"\s+", "", soup.get_text(separator=" ")).encode()).hexdigest()


def is_meaningless_image(url, filters):
    kws = filters.get("image_ignore_keywords", [])
    try:
        parsed = urlparse(url)
        fn = os.path.basename(parsed.path).lower()
        qs = parse_qs(parsed.query)
        if "fileName" in qs: fn = os.path.basename(qs["fileName"][0]).lower()
        # ponytail: separator-based match — keyword must be a distinct token
        # (delimited by -_./ or string boundaries) to avoid false positives
        # like "up" matching "duptva" or "bg" matching "vcjbgnzr".
        return any(re.search(rf'(?:^|[-_./]){re.escape(kw)}(?:$|[-_./])', fn) for kw in kws)
    except Exception:
        return False


_IMG_COUNTER = {}  # (save_dir, prefix) → next sequence number

def download_image(url, save_dir, current_name, module_name, config):
    if not url: return ""
    base = config["base_url"]
    if url.startswith("/"): url = base + url
    elif not url.startswith("http"): url = base + "/" + url
    # Parse fileName query param for real extension (OSS download URLs)
    import urllib.parse
    qs = urlparse(url).query
    _qp = {}
    if qs:
        for _kv in qs.split("&"):
            if "=" in _kv:
                _k, _v = _kv.split("=", 1)
                _qp[_k] = _v
    orig_fn = _qp.get("fileName", "")
    if orig_fn:
        orig_fn = urllib.parse.unquote(orig_fn)
        m = re.search(r'\.(png|jpg|jpeg|webp|gif|mp4)', os.path.basename(orig_fn), re.IGNORECASE)
    else:
        m = None
    if not m:
        m = re.search(r'\.(png|jpg|jpeg|webp|gif|mp4)', os.path.basename(urlparse(url).path), re.IGNORECASE)
    ext = m.group(0).lower() if m else ".png"
    if current_name:
        # Main cover image (module="主图"): no "_配图" suffix, no sequence number
        if module_name == "主图":
            pref = f"{sanitize(current_name)}_主图"
        else:
            pref = tpl(config, "image_name_pattern", current=sanitize(current_name), module=module_name) or f"{sanitize(current_name)}_{module_name}_配图"
    else:
        pref = tpl(config, "image_name_fallback") or "image"
    # Auto-increment sequence number per (save_dir, prefix) to avoid overwriting
    # Exception: 主图 (cover) has no sequence number
    counter_key = (save_dir, pref)
    if module_name == "主图":
        filename = f"{pref}{ext}"
    else:
        seq = _IMG_COUNTER.get(counter_key, 1)
        filename = f"{pref}_{seq}{ext}"
    local = os.path.join(save_dir, filename)
    rel = f"{config['output_structure']['image_subdir']}/{filename}"
    try:
        r = _retry_get(url, config, timeout=20)
        if r.status_code != 200: return ""
        content = r.content
        if len(content) < 5 * 1024: return ""
        if content.startswith(b'<!doc') or content.startswith(b'<html'): return ""
        md5 = hashlib.md5(content).hexdigest()
        if md5 in set(config.get("filters", {}).get("banned_images_md5", [])): return ""
        # Always write file with its own name per module (same image, different tab = different file)
        # But skip re-downloading if content was already fetched
        os.makedirs(save_dir, exist_ok=True)
        with open(local, "wb") as f: f.write(content)
        if module_name != "主图":
            _IMG_COUNTER[counter_key] = _IMG_COUNTER.get(counter_key, 1) + 1
        return rel
    except Exception:
        if module_name != "主图":
            _IMG_COUNTER[counter_key] = _IMG_COUNTER.get(counter_key, 1) + 1
        return ""


def _normalize_config(config):
    """Normalize config dict to support both flat and nested formats.

    Allows `content_selector` at top level OR under `html_components`.
    Also allows top-level `filters`, `heading`, `templates` keys.
    Returns a normalized config with everything under the expected nested keys.
    """
    comp = config.get("html_components", {})
    # Promote flat keys into html_components if they're not already there
    flat_keys = ["content_selector", "tab_label", "tab_panel",
                 "tab_header_to_decompose", "decompose_selectors",
                 "download_tab_labels", "download_link_patterns",
                 "carousel", "carousel_mode",
                 "carousel_image_selector", "contact_block_selector",
                 "boilerplate_disable", "boilerplate_class_exceptions"]
    for key in flat_keys:
        if key in config and key not in comp:
            comp[key] = config[key]
    # Promote flat filters/heading/templates into nested if missing
    normalized = dict(config)  # shallow copy
    normalized["html_components"] = comp
    if "filters" not in normalized:
        normalized["filters"] = {}
    if "heading" not in normalized:
        normalized["heading"] = {}
    if "templates" not in normalized:
        normalized["templates"] = {}
    if "discovery_quality" not in normalized:
        normalized["discovery_quality"] = {}
    return normalized


# Legacy _CONFIG_SCHEMA / _CONFIG_LIST_KEYS / _DEAD_CONFIG_KEYS kept for backward compat.
# The formal schema is now in config_schema.py — validate_config uses it.
# These sets are kept so that any code referencing them directly still works.
try:
    from config_schema import _schema_keys_at, _list_keys_at, SCHEMA_VERSION as _SCHEMA_V
    _CONFIG_SCHEMA = {
        "": _schema_keys_at(""),
        "filters": _schema_keys_at("filters"),
        "html_components": _schema_keys_at("html_components"),
        "discovery_quality": _schema_keys_at("discovery_quality"),
    }
    _CONFIG_LIST_KEYS = {
        "filters": _list_keys_at("filters"),
        "html_components": _list_keys_at("html_components"),
        "discovery_quality": _list_keys_at("discovery_quality"),
    }
except ImportError:
    # Fallback: keep old static sets if config_schema not available
    _CONFIG_SCHEMA = {
        "": {
            "__engine_page_type", "api", "base_url", "carousel_mode", "content_blocks",
            "discovery", "discovery_quality", "document", "download_link_patterns", "extract_date", "field_mapping",
            "filters", "heading", "html_components", "lang", "lang_path",
            "output_root", "output_structure", "page_type", "page_types", "playwright_args", "rate_limit",
            "render_mode", "resources", "category_name_map", "site_id", "site_name", "templates", "web_fallback",
            "locale", "llm_refine", "extraction",
        },
        "filters": {
            "banned_images_md5", "cross_industry_exempt", "cross_industry_pollution_keywords",
            "dev_pollution_patterns", "external_link_restore", "image_ignore_keywords",
            "noise_prefix_chars", "noise_text_keywords_exact", "resource_shared_keywords",
            "strip_resource_card_sections",
        },
        "html_components": {
            "base_heading_level", "boilerplate_class_exceptions", "boilerplate_disable",
            "carousel", "carousel_image_selector", "carousel_mode", "collapse", "collapse_header",
            "contact_block_selector", "content_selector",
            "decompose_selectors", "download_images", "download_link_patterns",
            "download_tab_labels", "empty_header_fill", "image_name_fallback", "image_name_pattern",
            "pair_table_header", "related_tab_labels", "card_grid_selector",
            "tab_container", "tab_header_to_decompose", "tab_label", "tab_panel",
            "visual_heading_pattern", "tab_heading_level", "collapse_heading_level",
            "layout_table_heading_level", "related_product_wiki_template",
        },
        "discovery_quality": {
            "archive_chars", "review_chars", "slug_title_max_chars", "slug_title_pattern",
        },
    }
    _CONFIG_LIST_KEYS = {
        "filters": {
            "banned_images_md5", "cross_industry_exempt", "cross_industry_pollution_keywords",
            "dev_pollution_patterns", "image_ignore_keywords", "noise_prefix_chars",
            "noise_text_keywords_exact", "resource_shared_keywords",
        },
        "html_components": {
            "boilerplate_class_exceptions", "decompose_selectors", "download_link_patterns",
            "download_tab_labels", "related_tab_labels", "tab_header_to_decompose",
        },
        "discovery_quality": set(),
    }

_DEAD_CONFIG_KEYS = {
    "filters": {"news_image_keywords", "noise_text_patterns"},
    "": {"related_products"},
    "output_structure": {"summary_file"},
    "resources": {"cross_category_rules", "model_pattern"},
    "incremental": {"baseline_state_file", "rerun_on_change"},
}


def validate_config(config):
    """Validate site config dict and raise ValueError on known issues.

    Uses the formal CONFIG_SCHEMA_V1 from config_schema.py for deep validation.
    Returns (errors, warnings) — errors block execution, warnings are informational.
    """
    config = _normalize_config(config)
    errors = []
    warnings = []

    # ── Required keys (from formal schema) ─────────────────────
    if "base_url" not in config:
        errors.append("Missing 'base_url' — needed for image/resource URL resolution")
    if "output_root" not in config:
        errors.append("Missing 'output_root' — needed to know where to write output")

    # ── Common misspellings ─────────────────────────────────────
    misspellings = {
        "content_selectror": "content_selector",
        "content_seletor": "content_selector",
        "decompose_selectros": "decompose_selectors",
        "fiter": "filters",
        "headig": "heading",
        "templat": "templates",
    }
    for wrong, right in misspellings.items():
        if wrong in config:
            errors.append(f"Misspelled key '{wrong}' — should be '{right}'")

    # ── render_mode validation ──────────────────────────────────
    render_mode = config.get("render_mode", "requests")
    if render_mode not in ("requests", "playwright"):
        errors.append(f"Invalid render_mode '{render_mode}' — must be 'requests' or 'playwright'")

    # ── content_selector type check ─────────────────────────────
    comp = config.get("html_components", {})
    sel = comp.get("content_selector", "")
    if sel and not isinstance(sel, str):
        errors.append(f"content_selector must be a string, got {type(sel).__name__}")

    # ── Schema-based unknown key detection ──────────────────────
    for key in config:
        if key.startswith("$") or key.startswith("__"):
            continue  # $schema, __engine_page_type — internal/optional
        if key not in _CONFIG_SCHEMA.get("", set()):
            warnings.append(f"Unknown top-level config key '{key}' (not in schema)")

    for section in ("filters", "html_components", "discovery_quality"):
        section_value = config.get(section, {})
        if not isinstance(section_value, dict):
            errors.append(f"{section} must be an object, got {type(section_value).__name__}")
            continue
        for key, value in section_value.items():
            if key not in _CONFIG_SCHEMA.get(section, set()):
                warnings.append(f"Unknown config key '{section}.{key}' (not in schema)")
            if key in _CONFIG_LIST_KEYS.get(section, set()) and not isinstance(value, list):
                errors.append(f"{section}.{key} must be a list, got {type(value).__name__}")

    # ── Deep type validation using formal schema (if available) ─
    try:
        from config_schema import CONFIG_SCHEMA_V1, _check_type
        schema_props = CONFIG_SCHEMA_V1["properties"]
        # Check discovery sub-keys
        disc = config.get("discovery", {})
        disc_schema = schema_props.get("discovery", {}).get("properties", {})
        for k, v in disc.items():
            if k in disc_schema:
                expected = disc_schema[k].get("type")
                if expected and v is not None and not _check_type(v, expected):
                    errors.append(f"discovery.{k} expected type {expected}, got {type(v).__name__}")
            elif k not in ("url_category_rule",):
                warnings.append(f"Unknown discovery key '{k}'")
        # Check output_structure sub-keys
        out = config.get("output_structure", {})
        out_schema = schema_props.get("output_structure", {}).get("properties", {})
        for k, v in out.items():
            if k in out_schema:
                expected = out_schema[k].get("type")
                if expected and v is not None and not _check_type(v, expected):
                    errors.append(f"output_structure.{k} expected type {expected}, got {type(v).__name__}")
    except ImportError:
        pass  # config_schema not available, skip deep validation

    # ── Dead key detection ──────────────────────────────────────
    for section, keys in _DEAD_CONFIG_KEYS.items():
        parent = config.get(section, {}) if section else config
        for k in keys:
            if parent.get(k):
                warnings.append(f"Config key '{section}.{k}' is defined but not yet consumed by the engine (dead key)")

    return errors, warnings


def convert_html_to_md(html, config, image_dir=None, current_name="", module_name="", resource_dir=None, page_type=""):
    """Convert HTML to clean Markdown via 5-phase pipeline.

    page_type: selects per-page-type overrides from config['page_types'][page_type].
    Merged as: html_components (shared) + page_types.{page_type} (overrides).
    """
    if not html: return ""
    config = _normalize_config(config)

    # Merge page-type-specific overrides into html_components
    # page_types.{type} overrides shared html_components
    if page_type:
        _overrides = config.get("page_types", {}).get(page_type, {})
        if _overrides:
            import copy
            comp_base = copy.deepcopy(config.get("html_components", {}))
            comp_base.update(_overrides)
            # Temporarily patch config so downstream code sees merged comp
            config = copy.deepcopy(config)
            config["html_components"] = comp_base

    # ── Phase -1: Decode double-escaped HTML if the site opt-in flag is set.
    # Some APIs return nested HTML stringified inside attributes or text nodes.
    if config.get("html_components", {}).get("unescape_double_escaped_html"):
        import html as _html
        html = _strip_mirrored_escaped_html_blocks(html)
        if "&lt;" in html or "&gt;" in html:
            html = _html.unescape(html)
        # Strip JSON-style escaping that would otherwise break tag parsing.
        if '\\"' in html:
            html = html.replace('\\"', '"')

    # Unescape only the srcdoc payload, not the surrounding page.
    if config.get("html_components", {}).get("unwrap_iframe_srcdoc"):
        html = _unwrap_iframe_srcdoc(html)

    soup = BeautifulSoup(html, "html.parser")
    for c in soup.find_all(string=lambda x: isinstance(x, Comment)):
        c.extract()
    comp = config.get("html_components", {})

    # ── Phase 0a: Normalize custom HTML components ──
    _base = comp.get("base_heading_level", 1)
    # Convert configured styled bold spans to headings.
    # 36px = section title (H{_base}), 24px = subsection (H{_base+2})
    for size in (comp.get("visual_heading_sizes") or []):
        for span in soup.select("span"):
            style = (span.get("style") or "").replace(" ", "")
            if f"font-size:{size}px" not in style: continue
            strong = span.find("strong", recursive=False)
            if not strong: continue
            text = strong.get_text(strip=True)
            if text:
                h_level = _base if size >= 36 else min(_base + 2, 6)
                h = soup.new_tag(f"h{h_level}")
                h.string = text
                span.replace_with(h)

    filters = config.get("filters", {})
    heading_cfg = config.get("heading", {})
    _dl_links = []  # stash for download links extracted from decomposed download tabs
    
    # Unified heading level: base_level + offset for tabs/collapse/layout
    # If explicitly set in config (tab_heading_level=3), use as-is for backward compat.
    # Otherwise compute: tab = base+1, collapse/layout = base+2
    _base = comp.get("base_heading_level", 1)
    tab_level = comp.get("tab_heading_level", _base + 1)
    collapse_heading_level = comp.get("collapse_heading_level", _base + 2)
    layout_table_level = comp.get("layout_table_heading_level", _base + 2)

    # ── Phase 0: Content isolation & boilerplate removal ──

    # 0a. Content selector isolation: extract only the main content area
    #     If content_selector is set, everything outside is discarded.
    #     This is the single most effective noise removal rule.
    content_sel = comp.get("content_selector", "")
    if content_sel:
        main_el = soup.select_one(content_sel)
        if main_el:
            # Replace entire soup with just the content area
            new_soup = BeautifulSoup("", "html.parser")
            new_soup.append(main_el.extract())
            soup = new_soup

    # 0b. Universal boilerplate removal: decompose common noise blocks
    #     These patterns work across most websites regardless of framework.
    #     AI agent can disable any of these via boilerplate_disable list.
    disabled_bp = set(comp.get("boilerplate_disable", []))
    
    # HTML tags that are almost always boilerplate
    bp_tags = {"nav", "footer", "header", "aside"}
    for tag in bp_tags:
        if tag in disabled_bp:
            continue
        for el in soup.find_all(tag):
            el.decompose()

    # Role attributes that indicate non-content
    bp_roles = {"navigation", "banner", "contentinfo", "complementary"}
    if "role" not in disabled_bp:
        for role in bp_roles:
            for el in soup.find_all(attrs={"role": role}):
                el.decompose()

    # Class name substrings commonly used for boilerplate across CMS/frameworks
    bp_class_patterns = [
        "sidebar", "widget", "footer", "header-top", "navbar",
        "breadcrumb", "pagination", "cookie", "consent",
        "modal", "overlay", "flyout", "offcanvas", "carousel-indicators",
        "social-share", "share-buttons", "related-posts", "comments",
        "disclaimer", "newsletter", "subscription", "ad-banner",
        "sponsored", "promo", "banner-ad", "sticky-header",
    ]
    if "class_patterns" not in disabled_bp:
        # class_exceptions: substrings that OVERRIDE a boilerplate match.
        # E.g. "media-popup" contains "popup" (boilerplate) but is product image gallery.
        bp_class_exceptions = set(comp.get("boilerplate_class_exceptions", []))
        to_decompose = []
        for el in soup.find_all(attrs={"class": True}):
            cls = el.get("class")
            if not cls:
                continue
            cls_str = " ".join(cls)
            cls_lower = cls_str.lower()
            if any(p in cls_lower for p in bp_class_patterns):
                # Check if any exception pattern matches → skip decompose
                if any(ex in cls_lower for ex in bp_class_exceptions):
                    continue
                to_decompose.append(el)
        for el in to_decompose:
            el.decompose()

    # 0c. Contact info block detection: remove blocks containing
    #     phone/email/address patterns that are site-wide boilerplate.
    #     Configurable via contact_block_selector or auto-detected.
    contact_sel = comp.get("contact_block_selector", "")
    if contact_sel:
        for el in soup.select(contact_sel):
            el.decompose()
    elif "contact_detect" not in disabled_bp:
        # Auto-detect: find divs containing 2+ contact patterns (phone+email, etc.)
        contact_pats = [
            re.compile(r'\+\d{1,3}\s+\d'),      # phone: +86 21
            re.compile(r'[\w.-]+@[\w.-]+\.\w+'), # email
            re.compile(r'tel:'),                  # tel: links
            re.compile(r'mailto:'),               # mailto: links
        ]
        for el in soup.find_all(["div", "section", "aside"]):
            text = el.get_text()
            hits = sum(1 for p in contact_pats if p.search(text))
            if hits >= 2:
                # Verify it's a small boilerplate block, not main content
                if len(text.strip()) < 800:
                    el.decompose()

    # ── Phase 1: HTML 预处理 (config-driven, before markdownify) ──

    # 1a-0. Demote native <h1> → <h2>: page titles are injected by callers
    #     (extract_product_recursive / extract_industry) as the single H1.
    #     Native <h1> in body HTML (e.g. industry solution banners) would
    #     create duplicate H1s. Demote to h2 so exactly one H1 survives.
    #     Callers inject the page title separately.
    for h1 in soup.find_all("h1"):
        h1.name = "h2"

    # 1a-0b. Promote configured visual headings represented by styled text.
    for p in soup.find_all("p"):
        strong = p.find("strong", recursive=False)
        if not strong: continue
        span = strong.find("span", recursive=False)
        if not span or "font-size" not in (span.get("style") or ""): continue
        text = span.get_text(strip=True)
        style = (span.get("style") or "").replace(" ", "")
        if "font-size:36px" in style and p.get_text(strip=True) == text:
            p.name = f"h{_base}"
            p.clear()
            p.string = text
            continue
        if re.match(r'^[一二三四五六七八九十]+、', text) and p.get_text(strip=True) == text:
            p.name = "h4"
            p.clear()
            p.string = text

    # 1a. Remove script/style/SVG-sprite/base64-micro-icons/video-players
    for t in soup(["script", "style"]): t.decompose()
    # Remove SVG icon sprites: <svg> containing 3+ <symbol> elements are icon
    # definition blocks, not visible content. Decompose to prevent symbol IDs
    # leaking as text through markdownify.
    for svg in soup.find_all("svg"):
        if len(svg.find_all("symbol")) >= 3:
            svg.decompose()
    # Remove tiny base64 inline images (logos/icons <2 KB) — they produce
    # useless markdown image links with kilobytes of encoded data.
    for img in soup.find_all("img", src=lambda s: s and s.startswith("data:image")):
        if len(img.get("src", "")) < 3000:
            img.decompose()
    # Remove video player containers — their control labels (PausePlay,
    # UnmuteMute, Quality, Speed…) leak as text. Decompose the whole wrapper.
    for el in soup.select(".plyr, [class*='video-player'], [class*='video-wrapper']"):
        el.decompose()
    for sel in comp.get("decompose_selectors", []):
        for el in soup.select(sel):
            # Before decomposing, extract downloadable resource links from this element.
            if resource_dir:
                _dl_patterns = comp.get("download_link_patterns", config.get("download_link_patterns", ["download"]))
                for a in el.find_all("a", href=True):
                    href = a.get("href", "")
                    if not any(p in href for p in _dl_patterns): continue
                    # Derive resource name: prefer link text, then URL title param, then URL path
                    link_text = a.get_text(strip=True)
                    res_name = link_text
                    _title_m = re.search(r'[?&]title=([^&]+)', href)
                    if _title_m:
                        from urllib.parse import unquote
                        res_name = res_name or unquote(_title_m.group(1))
                    if not res_name:
                        # URL-derived fallback: extract filename from URL path
                        _url_name = re.search(r'/([^/?]+\.(?:pdf|zip|docx?|xlsx?|rar))', href, re.IGNORECASE)
                        if _url_name:
                            res_name = re.sub(r'\.\w+$', '', _url_name.group(1))
                        else:
                            continue  # no usable name, skip
                    rel = download_resource(href, resource_dir, res_name, config, current_name=current_name)
                    if rel:
                        _dl_links.append(f"- 📎 [{res_name}]({rel})")
            el.decompose()

    # 1a-2. Icon font glyph removal: FA/BI/Glyphicon/Material icons are not text content
    # ponytail: decompose material-icons unconditionally (text = icon name),
    #           other icon fonts only if empty/PUA (fallback text reliable)
    for el in soup.select('i[class*="fa-"], i[class*="fas-"], i[class*="far-"], i[class*="fab-"], i[class*="bi-"], i[class*="glyphicon-"], span.material-icons, span.glyphicon'):
        if el.name == 'span' and 'material-icons' in (el.get('class') or []):
            el.decompose()
        else:
            t = el.get_text(strip=True)
            if not t or (len(t) == 1 and '\ue000' <= t <= '\uf8ff'):
                el.decompose()

    # 1a-3. <details>/<summary> disclosure expansion: summary→h3, unwrap details
    for details in soup.find_all("details"):
        summary = details.find("summary", recursive=False)
        if summary:
            stxt = summary.get_text(strip=True)
            if stxt:
                h = soup.new_tag("h3")
                h.string = stxt
                details.insert_before(h)
            summary.decompose()
        details.unwrap()

    # 1b. Tab label injection: inject tab labels as headings before each panel
    #     Then remove the original tab navigation elements so they don't
    #     produce duplicate content after markdownify conversion.
    tab_label_sel = comp.get("tab_label", "")
    tab_panel_sel = comp.get("tab_panel", "")
    _related_labels = comp.get("related_tab_labels", ["相关产品", "推荐产品"])
    _wiki_tpl = comp.get("related_product_wiki_template", "[[{name}]]：{desc}")
    if tab_label_sel and tab_panel_sel:
        labels = soup.select(tab_label_sel)
        panels = soup.select(tab_panel_sel)
        dl_tab_labels = comp.get("download_tab_labels", [])
        for i, label in enumerate(labels):
            lbl_text = label.get_text(strip=True)
            if any(kw in lbl_text for kw in dl_tab_labels):
                # Extract downloadable resources from download tab before removing it
                # (resource links are processed in Phase 2, but we decompose the panel
                # here; so we must collect and stash them for Phase 2 to pick up)
                if i < len(panels) and resource_dir:
                    _dl_patterns = comp.get("download_link_patterns", config.get("download_link_patterns", ["download"]))
                    for a in panels[i].find_all("a", href=True):
                        href = a.get("href", "")
                        if not any(p in href for p in _dl_patterns): continue
                        link_text = a.get_text(strip=True)
                        _has_title = bool(re.search(r'[?&]title=', href))
                        if not link_text and not _has_title: continue
                        res_name = link_text or "资料"
                        _title_m = re.search(r'[?&]title=([^&]+)', href)
                        if _title_m:
                            from urllib.parse import unquote
                            res_name = unquote(_title_m.group(1))
                        rel = download_resource(href, resource_dir, res_name, config, current_name=current_name)
                        if rel:
                            a["href"] = rel
                            # Stash as a hidden marker that Phase 2 won't touch but
                            # will survive into the final markdown
                            _dl_links.append(f"- 📎 [{res_name}]({rel})")
                        else:
                            a.decompose()
                if i < len(panels): panels[i].decompose()
                label.decompose()
            elif any(kw in lbl_text for kw in _related_labels):
                # Related products tab: convert to wiki link list
                if i < len(panels):
                    # tab_level computed from base_heading_level above
                    heading = soup.new_tag(f"h{tab_level}")
                    heading.string = lbl_text
                    panels[i].insert_before(heading)
                    _wiki_items = []
                    # Find card elements: try direct children first, then one level deeper
                    cards = panels[i].find_all(recursive=False)
                    if len(cards) <= 1:
                        # Single wrapper div — look one level deeper
                        inner = panels[i].find(recursive=False)
                        if inner:
                            cards = inner.find_all(recursive=False)
                    for card in cards:
                        texts = [t.strip() for t in card.get_text(separator="\n").split("\n") if t.strip()]
                        title = texts[0] if texts else ""
                        # Skip JS loading placeholders and other noise (not real product names)
                        _noise_re = re.compile(r'正在加载|请稍候|Loading|显示更多|更多信息', re.IGNORECASE)
                        title = _noise_re.sub('', title).strip()
                        title = re.sub(r'更多信息\s*>?', '', title).strip()
                        if not title or _noise_re.match(title):
                            continue
                        desc = "，".join(texts[1:]) if len(texts) > 1 else ""
                        desc = re.sub(r'更多信息\s*>?', '', desc).strip()
                        desc = re.sub(r'^[，,]+', '', desc).strip().rstrip('，').rstrip(',').strip()
                        if title:
                            if desc:
                                _wiki_items.append(_wiki_tpl.format(name=title, desc=desc))
                            else:
                                _wiki_items.append(f"[[{title}]]")
                    if _wiki_items:
                        wiki_text = "\n".join(f"- {item}" for item in _wiki_items)
                        panels[i].replace_with(BeautifulSoup(f"<p>{wiki_text}</p>", "html.parser"))
                    else:
                        panels[i].decompose()
                label.decompose()
            elif lbl_text:
                # Inject tab label as heading before panel content
                if i < len(panels):
                    # tab_level computed from base_heading_level above
                    heading = soup.new_tag(f"h{tab_level}")
                    heading.string = lbl_text
                    panels[i].insert_before(heading)
                    # Mark panel with tab label so Phase 2 uses it as module_name for images
                    panels[i]["data-module-name"] = lbl_text
                    label.decompose()  # Remove original label element
        # Remove remaining tab navigation containers (ul/select/nav)
        for sel in comp.get("tab_header_to_decompose", []):
            for el in soup.select(sel): el.decompose()
        # Unhide all tab panels: remove CSS-hidden class/style so markdownify sees all content
        for panel in soup.select(tab_panel_sel):
            # Remove style="display:none" if present
            sty = panel.get("style", "")
            if "display:none" in sty.replace(" ", "").replace("display:none", "display: block"):
                panel["style"] = sty.replace("display:none", "").replace("display :none", "")
            elif "display:none" in sty.replace(" ", ""):
                del panel["style"]
            # Remove inactive class that hides panel (e.g. removing 'active' toggles visibility)
            for cls_name in panel.get("class", []):
                if cls_name in ("", "hidden", "is-hidden"):
                    panel["class"].remove(cls_name)
            for h in panel.find_all(["h1", "h2", "h3", "h4", "h5"]):
                if int(h.name[1]) <= tab_level:
                    h.name = f"h{min(6, tab_level + 1)}"

    # 1c. Carousel handling
    carousel_sel = comp.get("carousel", "")
    carousel_mode = comp.get("carousel_mode", "all")
    if carousel_sel:
        for carousel in soup.select(carousel_sel):
            imgs = carousel.find_all("img")
            if carousel_mode == "none":
                carousel.decompose()
            elif carousel_mode == "first" and len(imgs) > 1:
                for img in imgs[1:]:
                    img.decompose()

    # 1d. Visual heading promotion: <p><strong>long text</strong></p> → heading
    if heading_cfg.get("promote_strong_paragraph", False):
        min_len = heading_cfg.get("strong_min_len", 6)
        max_len = heading_cfg.get("strong_max_len", 80)
        for p in soup.find_all("p"):
            full_text = p.get_text().strip()
            if not (min_len <= len(full_text) <= max_len * 3):  # allow longer text for partial-bold
                continue
            if p.find("img") or p.find("table"):
                continue
            strong_tags = p.find_all(["strong", "b"])
            if not strong_tags:
                continue
            strong_text = "".join(s.get_text() for s in strong_tags).strip()
            # Case 1: Entire paragraph is bold → full promotion (existing)
            if strong_text == full_text:
                if not (min_len <= len(full_text) <= max_len):
                    continue
                _p_level = _base + 1
                _inside_tab_or_collapse = False
                for _p_parent in p.parents:
                    _p_cls = str(_p_parent.get("class", []))
                    if "tab__panel" in _p_cls or "tab-content" in _p_cls:
                        _p_level = _base + 2
                        _inside_tab_or_collapse = True
                        break
                    if "collapse" in _p_cls:
                        _p_level = collapse_heading_level + 1
                        _inside_tab_or_collapse = True
                        break
                next_el = p.find_next_sibling()
                if _inside_tab_or_collapse and next_el and next_el.name == "p":
                    rest = next_el.get_text(strip=True)
                    if rest.startswith(("：", ":")):
                        li = soup.new_tag("li")
                        li.string = f"{full_text}{rest}"
                        ul = soup.new_tag("ul")
                        ul.append(li)
                        next_el.decompose()
                        p.replace_with(ul)
                        continue
                heading = soup.new_tag(f"h{_p_level}")
                heading.string = full_text
                p.replace_with(heading)
            # Case 2: Paragraph starts with bold title (partial) e.g. "**Title** description..."
            elif full_text.startswith(strong_text) and len(strong_text) >= min_len and len(strong_text) <= max_len:
                _p_level = _base + 1
                _inside_tab_or_collapse = False
                for _p_parent in p.parents:
                    _p_cls = str(_p_parent.get("class", []))
                    if "tab__panel" in _p_cls or "tab-content" in _p_cls:
                        _p_level = _base + 2
                        _inside_tab_or_collapse = True
                        break
                    if "collapse" in _p_cls:
                        _p_level = collapse_heading_level + 1
                        _inside_tab_or_collapse = True
                        break
                rest = full_text[len(strong_text):].strip()
                if _inside_tab_or_collapse:
                    li = soup.new_tag("li")
                    if strong_text.endswith(("：", ":")) and rest.startswith(("：", ":")):
                        rest = rest[1:].strip()
                    if rest and not strong_text.endswith(("：", ":")) and not rest.startswith(("：", ":", "，", ",", "。")):
                        rest = "：" + rest
                    li.string = f"{strong_text}{rest}"
                    ul = soup.new_tag("ul")
                    ul.append(li)
                    p.replace_with(ul)
                    continue
                heading = soup.new_tag(f"h{_p_level}")
                heading.string = strong_text
                # Keep remaining text as paragraph
                p.replace_with(heading)
                if rest:
                    new_p = soup.new_tag("p")
                    new_p.string = rest
                    heading.insert_after(new_p)

    # 1d-1. <figure>/<figcaption> handling: <img> + <em>caption, dedup alt
    for fig in soup.find_all("figure"):
        img = fig.find("img")
        cap = fig.find("figcaption")
        if img and cap:
            ct = cap.get_text(strip=True)
            if ct:
                em = soup.new_tag("em")
                em.string = ct
                img.insert_after(em)
            # Dedup alt if it matches caption
            alt = img.get("alt", "").strip().lower()
            if alt and alt == ct.strip().lower():
                img["alt"] = ""
            cap.decompose()
        fig.unwrap()

    # 1d-2. Image alt fallback: fill empty alt from title/aria-label/figcaption/parent link
    for img in soup.find_all("img"):
        alt = (img.get("alt") or "").strip()
        if alt:
            continue
        # Fallback chain: title → aria-label → figcaption → parent <a> title → parent <a> text
        candidates = [
            (img.get("title") or "").strip(),
            (img.get("aria-label") or "").strip(),
        ]
        # Check sibling figcaption
        fig = img.find_parent("figure")
        if fig:
            cap = fig.find("figcaption")
            if cap:
                candidates.append(cap.get_text(strip=True))
        # Check parent <a> title or text
        parent_a = img.find_parent("a")
        if parent_a:
            candidates.append((parent_a.get("title") or "").strip())
            candidates.append(parent_a.get_text(strip=True))
        # Use first non-empty candidate
        for c in candidates:
            if c and len(c) <= 120:
                img["alt"] = c
                break

    # 1d-3. Card grid collapsing: detect repeated card containers → markdown list
    # ponytail: simple heuristic — container with ≥2 children each having a link+text
    _card_selectors = comp.get("card_grid_selector", "")
    if _card_selectors:
        _wiki_tpl = comp.get("related_product_wiki_template", "[[{name}]]：{desc}")
        for container in soup.select(_card_selectors):
            cards = container.find_all(recursive=False)
            if len(cards) < 2:
                continue
            items = []
            for card in cards:
                a = card.find("a")
                texts = [t.strip() for t in card.get_text(separator="\n").split("\n") if t.strip()]
                title = texts[0] if texts else ""
                # Clean noise like "更多信息 >"
                title = re.sub(r'更多信息\s*>?', '', title).strip()
                desc = "，".join(texts[1:]) if len(texts) > 1 else ""
                desc = re.sub(r'更多信息\s*>?', '', desc).strip()
                desc = re.sub(r'^[，,]+', '', desc).strip().rstrip('，').rstrip(',').strip()
                if title:
                    # Use wiki link template: - [[name]]：desc
                    if desc:
                        items.append(_wiki_tpl.format(name=title, desc=desc))
                    else:
                        items.append(f"[[{title}]]")
            if items:
                text = "\n".join(f"- {i}" for i in items)
                container.replace_with(BeautifulSoup(f"<p>{text}</p>", "html.parser"))

    # 1f. Bootstrap collapse/accordion: expand hidden content by removing
    #     display:none class markers so markdownify can see the content
    #     Handles both standard and escaped-quote class names
    #     Also: convert collapse headers to headings for RAG navigation
    def _strip_class(el, *bad):
        """Remove specified class tokens from element, handling escaped quotes."""
        cls = el.get("class", [])
        if not cls:
            return
        el["class"] = [c for c in cls if c not in bad and c.strip('"\\') not in bad]

    for el in soup.find_all("div", attrs={"class": True}):
        raw_cls = " ".join(el.get("class", []))
        # Normalize: strip literal \" for matching
        norm = raw_cls.replace('\\"', '').replace('"', '')
        if "accordion-collapse" in norm:
            _strip_class(el, "collapse", "fade")
        elif "section-collapse" in norm:
            _strip_class(el, "section-collapse")
        elif "tab-pane" in norm:
            _strip_class(el, "fade", "active")

    # 1f-1b. Collapse header → heading: convert collapse/accordion headers to headings
    #         Configured via html_components.collapse + collapse_header_selector
    collapse_sel = comp.get("collapse", "")
    collapse_header_sel = comp.get("collapse_header", "")
    # collapse_heading_level computed from base_heading_level above
    if collapse_sel and not collapse_header_sel:
        # Auto-detect: common patterns for collapse headers
        collapse_header_sel = f"{collapse_sel}__header"
    if collapse_header_sel:
        for header_el in soup.select(collapse_header_sel):
            text = header_el.get_text(strip=True)
            if text and len(text) <= 80:
                heading = soup.new_tag(f"h{collapse_heading_level}")
                heading.string = text
                heading["data-collapse-title"] = "1"
                header_el.replace_with(heading)
    if collapse_sel:
        for collapse_el in soup.select(collapse_sel):
            for h in collapse_el.find_all(["h1", "h2", "h3", "h4", "h5"]):
                if h.get("data-collapse-title"):
                    continue
                if int(h.name[1]) <= collapse_heading_level:
                    h.name = f"h{min(6, collapse_heading_level + 1)}"

    # 1f-2. Remove inline display:none so hidden content becomes visible
    for el in soup.find_all(attrs={"style": True}):
        if "display:none" in el.get("style", "").replace(" ", ""):
            del el["style"]

    # 1e. Layout table flattening: tables with cells >150 chars → flatten to paragraphs
    #     Skip tables with <thead> (real data tables) or product-table class
    #     Special case: 2-row tables where row0 is short (title) → make row0 a heading
    for table in soup.find_all("table"):
        # Skip data tables that have proper headers
        if table.find("thead"):
            continue
        table_classes = ' '.join(table.get("class", []))
        if 'product-table' in table_classes:
            continue
        is_layout = False
        for row in table.find_all("tr"):
            for cell in row.find_all(["td", "th"]):
                if len(cell.get_text().strip()) > 150:
                    is_layout = True
                    break
            if is_layout:
                break
        if is_layout:
            # Flatten: each cell becomes content, short cells become headings
            rows = table.find_all("tr")
            # Detect 2-row pattern: row0=short title, row1=long content
            is_title_content = (len(rows) == 2 and
                max(len(c.get_text(strip=True)) for c in rows[0].find_all(["td","th"])) <= 30 and
                max(len(c.get_text(strip=True)) for c in rows[1].find_all(["td","th"])) > 100)
            cell_parts = []
            heading_level = layout_table_level
            for row_i, row in enumerate(rows):
                for cell in row.find_all(["td", "th"]):
                    # Flatten nested containers so headings become direct
                    # children (preserves headings nested in <div> within <td>)
                    for el in cell.find_all(["article", "div", "span", "a", "strong", "b", "em", "i", "ul", "ol", "li"]):
                        el.unwrap()
                    # Preserve elements in DOCUMENT ORDER (headings, images,
                    # text interleaved). Layout tables in industry HTML wrap
                    # native <h3> section titles and <p><strong> (promoted to
                    # h3 by Phase 1d) inside <td>; outputting all headings then
                    # all text breaks the heading→content association and
                    # triggers the empty-heading filter (Phase 4f), losing ~20
                    # headings per solution.
                    from bs4 import NavigableString
                    cell_parts_for_cell = []
                    for child in cell.children:
                        if isinstance(child, NavigableString):
                            t = str(child).strip()
                            if t:
                                cell_parts_for_cell.append(f"<p>{t}</p>")
                        elif child.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                            ht = child.get_text(strip=True)
                            if ht:
                                # Strip attributes (style/class) — markdownify
                                # demotes styled headings inside flattened tables
                                cell_parts_for_cell.append(f"<{child.name}>{ht}</{child.name}>")
                        elif child.name == "img":
                            cell_parts_for_cell.append(str(child))
                        elif child.name in ("article", "p", "div", "span", "a", "strong", "b", "em", "i", "ul", "ol", "li"):
                            t = child.get_text(strip=True)
                            if t:
                                cell_parts_for_cell.append(f"<p>{t}</p>")
                            for img in child.find_all("img"):
                                cell_parts_for_cell.append(str(img))
                    if not cell_parts_for_cell:
                        continue
                    cell_parts.extend(cell_parts_for_cell)
            table.replace_with(BeautifulSoup("\n".join(cell_parts), "html.parser"))

    # 1g. Gallery counter removal: remove elements like "图片12", "视频1"
    for el in soup.find_all(class_=re.compile(r"gallery|counter|count")):
        text = el.get_text(strip=True)
        norm_text = text.replace('\\"', '').replace('"', '')
        if re.match(r'^(图片|视频|图|影像)\d+$', norm_text):
            el.decompose()

    # ── Phase 2: Image + resource processing ──
    
    # Demote native H1 tags to H2 (H1 is reserved for the document title)
    for h1 in soup.find_all("h1"):
        h1.name = "h2"

    if image_dir and comp.get("download_images", True):
        for img in soup.find_all("img"):
            src = img.get("src")
            if not src or is_meaningless_image(src, filters): img.decompose(); continue
            # Use tab label as module_name if img is inside a panel with data-module-name
            img_module = module_name
            parent = img.parent
            while parent:
                if parent.get("data-module-name"):
                    img_module = parent["data-module-name"]
                    break
                parent = parent.parent
            rel = download_image(src, image_dir, current_name, img_module, config)
            if rel:
                img["src"] = rel
                # Fill empty alt with semantic name derived from local filename
                if not img.get("alt", "").strip():
                    # e.g. "图片/JIC_PLC_8010_正文_配图_1.png" → "JIC PLC 8010 正文 配图 1"
                    fname = os.path.splitext(os.path.basename(rel))[0]
                    alt_text = fname.replace("_", " ")
                    img["alt"] = alt_text
            else:
                img.decompose()
    if resource_dir:
        patterns = comp.get("download_link_patterns", config.get("download_link_patterns", ["download"]))
        for a in soup.find_all("a"):
            href = a.get("href", "")
            if not any(p in href for p in patterns): continue
            # Skip empty links (no text, no title param, no URL-derived name → likely icon/button)
            link_text = a.get_text(strip=True)
            _has_title = bool(re.search(r'[?&]title=', href))
            _url_name = re.search(r'/([^/?]+\.(?:pdf|zip|docx?|xlsx?|rar))', href, re.IGNORECASE)
            if not link_text and not _has_title and not _url_name:
                a.decompose(); continue
            # Extract meaningful name: prefer URL title param, then link text, then URL path, then fallback
            res_name = link_text or ""
            _title_m = re.search(r'[?&]title=([^&]+)', href)
            if _title_m:
                from urllib.parse import unquote
                res_name = res_name or unquote(_title_m.group(1))
            if not res_name and _url_name:
                res_name = re.sub(r'\.\w+$', '', _url_name.group(1))
            if not res_name:
                res_name = "资料"
            rel = download_resource(href, resource_dir, res_name, config, current_name=current_name)
            if rel:
                a["href"] = rel
            else:
                a.decompose()

    # ── Phase 2b: Extract images from tables ──
    # markdownify drops <img> inside <td>, converting to plain alt text.
    # Move each table image out as a standalone <p><img></p> before the table.
    for table in soup.find_all("table"):
        for img in list(table.find_all("img")):
            img.extract()
            p = soup.new_tag("p")
            p.append(img)
            table.insert_before(p)

    # ── Phase 3: markdownify conversion ──

    md = markdownify(str(soup), heading_style="ATX", bullets="-", strip=["script", "style", "video", "source"])
    md = re.sub(r"\n{3,}", "\n\n", md)

    # ── Phase 4: MD 后处理 ──

    # 4a-0. Escape unescaped | inside table cells (must run before any table parsing)
    _empty_fill = comp.get("empty_header_fill", " 描述 ")
    md = _escape_table_pipes(md, _empty_fill)    # 4a-0b. Strip empty table headers: |  |  |\n| --- | --- |\n| actual | → remove first two lines
    _eh = r'\|(\s*\|)+\s*'      # empty header row: |  |  |  |
    _sep = r'\|(\s*[-:]+\s*\|)+\s*'  # separator row: | --- | --- |
    md = re.sub(r'\n' + _eh + r'\n' + _sep + r'\n', '\n', md)
    # 4a. Clean markdownify table double-rendering
    md = _strip_table_text_echo(md)
    # 4b. Flatten tables with embedded headings
    md = _flatten_table_with_headings(md)
    # 4c. Noise filtering
    noise_exact = set(filters.get("noise_text_keywords_exact", []))
    dev_patterns = filters.get("dev_pollution_patterns", [])
    noise_prefix = filters.get("noise_prefix_chars", [])
    lines = []
    for line in md.split("\n"):
        s = line.strip()
        if not s: lines.append(""); continue
        if any(p in s for p in dev_patterns): continue
        if s in noise_exact: continue
        # ponytail: substring noise match only fires on nav/CTA-style lines.
        # Body prose (full sentences with Chinese punctuation) containing
        # noise keywords like "解决方案" as a substring must survive — the
        # keyword is meant to catch standalone nav links like
        # "[获取解决方案](url)", not paragraphs that happen to use the word.
        _is_prose = any(p in s for p in '。，；！？、')
        if (
            '![' not in s
            and len(s.replace('*', '')) < 100
            and not _is_prose
            and any(kw in s for kw in noise_exact)
        ):
            continue
        # Also check heading-stripped version for noise match
        # (e.g. "#### Add page to favorites" → "Add page to favorites")
        s_no_heading = re.sub(r'^#{1,6}\s+', '', s)
        # Strip bold markers for noise matching (bold doesn't change semantic content)
        s_no_heading = s_no_heading.strip('*')
        # Strip markdown anchor links for noise matching: [text](#anchor) → text
        s_no_heading = re.sub(r'^\[([^\]]+)\]\(#[^)]*\)\s*$', r'\1', s_no_heading)
        # Strip markdown links for noise matching: [text](url) → text
        s_no_heading = re.sub(r'^\[([^\]]+)\]\([^)]*\)$', r'\1', s_no_heading)
        if s_no_heading in noise_exact: continue
        # Gallery counter filter: skip lines like "图片12", "视频1"
        if re.match(r'^(图片|视频|图|影像)\s?\d+$', s_no_heading): continue
        # Universal boilerplate text patterns (Readability + trafilatura inspired)
        # Loading placeholders: "正在加载…请稍候", "Loading…"
        if re.match(r'^(正在加载|Loading|Chargement|Cargando).{0,20}(请稍候|please wait)?$', s_no_heading, re.IGNORECASE):
            continue
        # Copyright lines: "© Company Name", "© 2026"
        if re.match(r'^©\s', s_no_heading): continue
        # "Show more" / "显示更多" buttons
        if re.match(r'^(Show more|显示更多|Read more|阅读更多|展开)$', s_no_heading, re.IGNORECASE): continue
        if s_no_heading in ('获取方案', '下载方案', '获取资料', '获取解决方案', '咨询方案'):
            continue
        # "More info >" style links: [更多信息 >](url) or [Learn more](url)
        if re.match(r'^\[(更多信息|Learn more|More info|了解更多)[^\]]*\]\(', s_no_heading, re.IGNORECASE): continue
        if "更多信息" in s_no_heading or "More info" in s_no_heading:
            continue
        # "Show product details" type navigation labels
        if re.match(r'^Show\s+\w+\s+details?:', s_no_heading, re.IGNORECASE): continue
        # Link density noise: lines that are only a markdown link to contact/newsletter
        if re.match(r'^\[联系我们\]', s_no_heading): continue
        # Back-to-top / scroll-to-top links
        if s_no_heading in ('回到顶部', '返回顶部', 'Back to top', 'Scroll to top', 'To top', 'Top'): continue
        # List-view / filter UI labels (CMS filtering components)
        if re.match(r'^前\s*\d+\s*条$', s_no_heading): continue  # "前 12 条"
        if re.match(r'^每页显示的条目数$', s_no_heading): continue  # "每页显示的条目数"
        if re.match(r'^\d+\s*items$', s_no_heading, re.IGNORECASE): continue  # "6 items"
        if s_no_heading in ('全部选中', '宣传册', 'Filter'): continue
        # Video player control leaks: PausePlay, UnmuteMute, Quality, Speed, etc.
        if re.match(r'^(PausePlay|UnmuteMute|Disable captions|Enable captions|CaptionsDisabled|CaptionsGo back|QualityGo back|SpeedGo back|PIPExit fullscreen|Enter fullscreen)$', s_no_heading):
            continue
        # Video player status leaks: "% buffered", timestamps, quality labels
        if re.match(r'^\d*%\s*buffered', s_no_heading): continue
        if re.match(r'^\d{1,2}:\d{2}$', s_no_heading): continue
        if re.match(r'^(480p|720p|1080p|360p)(SD|HD)?$', s_no_heading): continue
        # Screen reader / aria artifact: lone "all" or similar single words
        if s_no_heading in ('all', 'on', 'off', 'yes', 'no', 'true', 'false'):
            continue
        if "页面内容" in s_no_heading:
            continue
        # Strip noise prefix from start of line
        # noise_prefix_chars entries are multi-char strings (e.g. "建随");
        # only strip the first char when the full prefix matches, avoiding
        # false positives like stripping "建" from "建筑".
        if noise_prefix and s:
            for pf in noise_prefix:
                if s.startswith(pf):
                    s = s[len(pf[0]):]   # strip only the noise char (first char)
                    break
            line = s
        lines.append(line)

    # Strip empty markdown links `[](...)` and failed-image `![](...)` — leftover from
    # failed image/resource downloads. `!?` also catches the `!` prefix of `![](url)`
    # when the image download returned 500 and the `!` survived earlier filtering.
    lines = [re.sub(r'!?\[\]\([^)]*\)', '', l) for l in lines]

    # 4c-0. Filter image lines by image_ignore_keywords (logo/icon/banner etc.)
    # Matches ![alt](url) lines where the URL filename contains an ignore keyword.
    _img_ignore_kws = filters.get("image_ignore_keywords", [])
    if _img_ignore_kws:
        _img_pat = re.compile(r'!\[[^\]]*\]\(([^)]+)\)')
        filtered_lines = []
        for l in lines:
            m = _img_pat.match(l.strip())
            if m and is_meaningless_image(m.group(1), filters):
                continue
            filtered_lines.append(l)
        lines = filtered_lines

    # Cross-industry pollution removal
    _pollution_kws = filters.get("cross_industry_pollution_keywords", [])
    if _pollution_kws and not any(ex in (current_name or "") for ex in filters.get("cross_industry_exempt", [])):
        cleaned = []
        for line in lines:
            s = line.strip()
            if s and any(kw in s for kw in _pollution_kws):
                continue
            cleaned.append(line)
        lines = cleaned

    # 4c-1. Fix orphaned markdown link closers (markdownify splits <a> across blocks)
    md = _fix_orphan_links('\n'.join(lines))

    # 4c-1b. Detect fragmented tables: short standalone lines separated by blank
    # lines that look like a 4-column metric comparison table, and merge them.
    md = _recover_fragmented_tables(md)
    # 4c-2. Fix tables missing header separator (markdownify drops |---|)
    md = _fix_table_separators(md)
    # 4c-3. Clean empty/self-referencing markdown links: [text]() → text, [url](url) → url
    md = _clean_empty_links(md)
    lines = md.split('\n')

    # 4d. Heading normalization: normalize heading levels to prevent jumps
    max_level = heading_cfg.get("max_level", 5)
    # ponytail: body_max_level caps the highest heading allowed in body content.
    # Product title is H2 (added by extract_product_recursive), so body H1 → H2
    # to avoid multiple H1s in one file. Set in page_type config (e.g. product.json).
    body_max_level = heading_cfg.get("body_max_level", 0)  # 0 = no cap
    long_threshold = heading_cfg.get("long_heading_threshold", 60)
    url_pattern = heading_cfg.get("url_heading_pattern", r"^https?://")
    norm_lines = []
    prev_level = 0
    for line in lines:
        m = re.match(r'^(#{1,6})\s+(.*)', line)
        if m:
            level = len(m.group(1))
            # Cap body headings: demote any heading above body_max_level
            if body_max_level and level < body_max_level:
                level = body_max_level
            # Clean heading content: strip literal \n, collapse whitespace
            content = re.sub(r'\\n', ' ', m.group(2))
            content = re.sub(r'\s+', ' ', content).strip()
            if page_type == "product" and current_name and content == current_name:
                prev_level = 0
                continue
            # Strip trailing duplicate product code from heading
            # e.g. "为什么 SIMATIC S7-1200 G2？S7-1200" → "为什么 SIMATIC S7-1200 G2？"
            _codes = re.findall(r'[A-Z0-9]{2,}[-][A-Z0-9]+(?:[-][A-Z0-9]+)*', content)
            if len(_codes) >= 2:
                _last = _codes[-1]
                if any(_last in _ec for _ec in _codes[:-1]):
                    content = re.sub(
                        r'([？?—–]?\s*)' + re.escape(_last) + r'\s*$',
                        r'\1', content
                    ).rstrip()
            if not content:
                continue
            # URL as heading → demote to plain text
            if re.match(url_pattern, content):
                norm_lines.append(content)
                prev_level = 0
                continue
            # Long heading → bold instead
            if len(content) > long_threshold:
                norm_lines.append(f"**{content}**")
                prev_level = 0
                continue
            # Prevent heading level jumps
            if prev_level > 0 and level > prev_level + 1:
                level = prev_level + 1
            level = min(level, max_level)
            norm_lines.append(f"{'#' * level} {content}")
            prev_level = level
        else:
            if line.strip() and not line.strip().startswith('#'):
                prev_level = 0
            norm_lines.append(line)

    if page_type == "product":
        levels = [len(m.group(1)) for line in norm_lines if (m := re.match(r'^(#{1,6})\s+', line))]
        if levels and min(levels) > 2:
            shift = min(levels) - 2
            norm_lines = [
                re.sub(r'^(#{%d,6})\s+' % (shift + 1), lambda m: f"{m.group(1)[shift:]} ", line)
                if line.startswith('#') else line
                for line in norm_lines
            ]

    # 4e. Clean nested bold **** and literal \n in content
    result = '\n'.join(norm_lines)
    result = re.sub(r'\*\*\*\*', '', result)
    # Remove literal \n that appears as standalone text (markdownify artifact)
    result = re.sub(r'(?<!\\)\\n', ' ', result)
    result = re.sub(r'^\s*-\s+#{1,6}\s+', '- ', result, flags=re.MULTILINE)
    result = re.sub(r'^\s*-\s+-\s+', '- ', result, flags=re.MULTILINE)
    result = _strip_empty_value_labels(result)
    result = _strip_image_alt_echoes(result)
    result = _fix_split_colon_labels(result)
    if filters.get("strip_resource_card_sections"):
        result = _strip_resource_card_sections(result)

    # 4f. Empty heading removal: remove headings with no content below them
    #     (e.g. "### 其他产品" followed immediately by another heading or end of doc)
    #     Skip h1/h2 (section titles that may contain sub-headings)
    result_lines = result.split('\n')
    cleaned = []
    i = 0
    while i < len(result_lines):
        line = result_lines[i]
        # Remove completely empty headings: "###" with no text
        if re.match(r'^#{1,6}\s*$', line):
            i += 1; continue
        # Check if current line is a heading with content
        h_match = re.match(r'^(#{1,6})\s+(.+)', line)
        if h_match:
            heading_level = len(h_match.group(1))
            _empty_min = heading_cfg.get("empty_heading_min_level", 3)
            _empty_title = h_match.group(2).strip()
            if heading_level >= _empty_min:
                # Look ahead: is next non-empty line also a heading or end of doc?
                j = i + 1
                while j < len(result_lines) and not result_lines[j].strip():
                    j += 1
                next_heading = re.match(r'^(#{1,6})\s+', result_lines[j]) if j < len(result_lines) else None
                if j >= len(result_lines) or (next_heading and len(next_heading.group(1)) <= heading_level):
                    i += 1
                    continue
        cleaned.append(result_lines[i])
        i += 1
    result = '\n'.join(cleaned)

    # 4g. Alternating key-value line merging: detect "key→empty→value→empty"
    #     repeating 3+ times and merge into a markdown table.
    #     Table header is configurable via pair_table_header in html_components.
    _pair_hdr = comp.get("pair_table_header", "| 型号 | 描述 |")
    result = _merge_alternating_pairs(result, pair_table_header=_pair_hdr)
    result = _merge_flattened_small_tables(result, expected_headers=comp.get("flattened_table_headers"))

    result = re.sub(r'\n{3,}', '\n\n', result)
    # 4h. Clean bold markers from headings: ## **text** → ## text
    result = re.sub(r'^(#{1,6}\s+)\*{1,2}(.+?)\*{1,2}\s*$', r'\1\2', result, flags=re.MULTILINE)
    # 4h-1. Normalize heading whitespace: ##  text  → ## text
    result = re.sub(r'^(#{1,6})\s{2,}(.+?)\s*$', lambda m: f"{m.group(1)} {m.group(2).strip()}", result, flags=re.MULTILINE)
    # 4h-2. Split image-caption: ![alt](file)*caption* → separate lines
    result = _split_image_caption(result)
    # 4i. Unicode whitespace normalization: NBSP/zero-width/thin→space
    result = _normalize_unicode_whitespace(result)
    # 4j. external_link_restore: fix domain-relative URLs that lost their scheme
    _elr = filters.get("external_link_restore", {})
    if _elr:
        for domain, full_url in _elr.items():
            result = re.sub(rf'([("\'\s]){re.escape(domain)}', rf'\1{full_url}', result)
    # 4k. Deduplicate link text echo (must run last): [下载下载](url) → [下载](url)
    result = re.sub(r'\[([^\]]{1,6})\1\]', lambda m: f'[{m.group(1)}]', result)
    # 4l. Deduplicate consecutive identical blocks (any content, all page types)
    result = _dedupe_consecutive_blocks(result)
    if page_type == "product":
        result = _dedupe_repeated_download_sections(result)
    # Append stashed download links from decomposed download tabs
    if _dl_links:
        result += "\n\n## 资料下载\n\n" + "\n".join(_dl_links) + "\n"
    return _llm_refine(_normalize_blank_lines(result.strip()), config, {"page_type": page_type, "module_name": module_name, "current_name": current_name})


def _dedupe_consecutive_blocks(md):
    """Remove repeated consecutive paragraph sequences from final markdown.
    
    Detects when N paragraphs appear, then the exact same N paragraphs repeat
    immediately after. Keeps only the first occurrence. Works by scanning
    for repeated lines/paragraphs at any scale.
    """
    # Split into lines, find duplicate runs of consecutive non-empty lines
    lines = md.split('\n')
    out = []
    i = 0
    seen_runs = {}  # fingerprint -> first end position
    while i < len(lines):
        # Find end of current non-empty run
        j = i
        while j < len(lines) and lines[j].strip():
            j += 1
        if j == i:
            out.append(lines[i])
            i += 1
            continue
        run = [l.strip() for l in lines[i:j]]
        key = '\n'.join(run)
        if key in seen_runs:
            # Duplicate run — skip it
            i = j
            continue
        seen_runs[key] = j
        out.extend(lines[i:j])
        i = j
    return '\n'.join(out)


def _normalize_heading_whitespace(md):
    """Fix heading whitespace: ##  text  → ## text; ## text  → ## text"""
    def _fix(m):
        hashes = m.group(1)
        after = m.group(2).strip()
        return f"{hashes} {after}"
    return re.sub(r'^(#{1,6})\s+(.+?)\s*$', _fix, md, flags=re.MULTILINE)


def _normalize_heading_jumps(md):
    """Cap heading-level jumps: if a heading is more than 1 level deeper than
    the previous heading, demote it to prev_level+1.

    Catches visual-heading promotion artifacts where a 24px styled <p> becomes
    h5 but its parent context is h2/h3, producing a 'heading-jump' quality issue.
    """
    lines = md.splitlines()
    prev_level = 0
    out = []
    for line in lines:
        m = re.match(r'^(#{1,6})\s+(.+)$', line.strip())
        if not m:
            out.append(line)
            continue
        level = len(m.group(1))
        title = m.group(2)
        if prev_level and level > prev_level + 1:
            level = min(prev_level + 1, 6)
        out.append(f'{"#" * level} {title}')
        prev_level = level
    return '\n'.join(out)


def _demote_subheading_patterns(md, patterns):
    """Demote headings whose titles match known subheading patterns.

    Rules:
    - Track last_real_heading_level (non-pattern headings only) so pattern
      headings don't cascade-demote each other.
    - If a pattern heading sits at a shallower level than last_real_heading_level
      but its title text implies it belongs under last_real_heading_level, demote
      it to be one level deeper than last_real_heading_level.
    - If a pattern heading is already deeper than its real parent (e.g. h4 after
      a real h3), leave it alone — it's a legitimate child.
    """
    if not patterns:
        return md

    def is_demotable_pattern_title(title):
        if len(title) > 24:
            return False
        return any(p in title for p in patterns)

    lines = md.splitlines()
    out = []
    last_real_level = 0
    for line in lines:
        m = re.match(r'^(#{1,6})\s+(.+)$', line.strip())
        if not m:
            out.append(line)
            continue
        level = len(m.group(1))
        title = m.group(2).strip()
        is_pattern = is_demotable_pattern_title(title)
        if not is_pattern:
            out.append(f'{"#" * level} {title}')
            last_real_level = level
            continue
        if last_real_level and level <= last_real_level:
            new_level = min(last_real_level + 1, 6)
            out.append(f'{"#" * new_level} {title}')
        else:
            out.append(f'{"#" * level} {title}')
    return '\n'.join(out)


def _dedupe_adjacent_same_title_headings(md):
    """Drop the deeper heading when two adjacent headings share the same title.

    Keep the shallower heading as the canonical structural heading.
    """
    lines = md.splitlines()
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m1 = re.match(r'^(#{1,6})\s+(.+)$', line.strip())
        if m1 and i + 1 < len(lines):
            m2 = re.match(r'^(#{1,6})\s+(.+)$', lines[i + 1].strip())
            if m2 and m1.group(2).strip() == m2.group(2).strip():
                lvl1 = len(m1.group(1))
                lvl2 = len(m2.group(1))
                # keep the shallower heading — structural/canonical source
                kept = lines[i] if lvl1 <= lvl2 else lines[i + 1]
                out.append(kept)
                i += 2
                continue
        out.append(line)
        i += 1
    return '\n'.join(out)


def _dedupe_repeated_download_sections(md):
    lines = md.splitlines()
    out = []
    seen = set()
    i = 0
    while i < len(lines):
        m = re.match(r'^(#{2,5})\s+资料下载\s*$', lines[i].strip())
        if not m:
            out.append(lines[i])
            i += 1
            continue
        level = len(m.group(1))
        j = i + 1
        while j < len(lines):
            if lines[j].strip() == '---':
                break
            hm = re.match(r'^(#{1,6})\s+', lines[j].strip())
            if hm and len(hm.group(1)) <= level:
                break
            j += 1
        block = lines[i:j]
        key_lines = [re.sub(r'^#{2,5}\s+资料下载\s*$', '资料下载', line.strip()) for line in block]
        key = re.sub(r'\s+', ' ', '\n'.join(key_lines)).strip()
        if key not in seen:
            seen.add(key)
            out.extend(block)
        i = j
    return '\n'.join(out)


def fetch_menu(config):
    """Discover site content: returns dict with productTypeList and industryList.
    
    Supports multiple discovery strategies via config["discovery"]["mode"]:
    - "api" (default): use api.menu endpoint, fallback to web scraping
    - "sitemap": parse sitemap.xml for product/industry URLs
    - "url_list": use static URL lists from config
    - "crawl": scrape navigation links from a rendered entry page
    """
    disc = config.get("discovery", {})
    mode = disc.get("mode", "api")
    
    if mode == "sitemap":
        return _fetch_sitemap(config, disc)
    elif mode in ("crawl", "nav"):
        return _fetch_crawl(config, disc)
    elif mode == "url_list":
        return _fetch_url_list(config, disc)
    else:
        return _fetch_api_menu(config)


def _fetch_api_menu(config):
    """API-based menu discovery with web fallback."""
    try:
        r = _retry_get(config["base_url"] + config["api"]["menu"], config)
        return r.json().get("data", {})
    except Exception:
        return _web_fetch_menu(config)


def _fetch_sitemap(config, disc):
    """Sitemap-based URL discovery.
    
    Supports url_category_rule in discovery config to group products by URL path
    segments into separate categories (directories) instead of a flat "All" bucket.
    
    url_category_rule:
      pattern: regex with a capture group for the category slug
      group: capture group index (default 1)
      name_map: optional slug→display name mapping
    """
    import re
    url = disc.get("sitemap_url") or disc.get("url") or config["base_url"] + "/sitemap.xml"
    prod_pat = disc.get("product_pattern", "/products/")
    ind_pat = disc.get("industry_pattern", "/industries/")
    exclude = disc.get("exclude_pattern", "")
    # name_case: how to transform URL-derived names. "title"(default)|"preserve"|"upper"|"lower"
    name_case = disc.get("name_case", "title")
    def _apply_name_case(s):
        if name_case == "preserve": return s
        if name_case == "upper": return s.upper()
        if name_case == "lower": return s.lower()
        return s.title()  # default
    cat_rule = disc.get("url_category_rule")
    cat_name_map = cat_rule.get("name_map", {}) if cat_rule else {}
    cat_pat = re.compile(cat_rule["pattern"]) if cat_rule and cat_rule.get("pattern") else None
    cat_group = cat_rule.get("group", 1) if cat_rule else 1
    
    try:
        r = _retry_get(url, config)
        r.encoding = 'utf-8'
        raw_urls = re.findall(r'https?://[^<>\s"\']+', r.text)
        # Sitemap index: if the response contains sub-sitemap URLs, fetch them too
        if any("<sitemap>" in r.text or "sitemapindex" in r.text for _ in [1]):
            sub_urls = re.findall(r'<loc>\s*(https?://[^<>\s"\']+)\s*</loc>', r.text)
            pdp_pat = disc.get("sitemap_pdp_pattern", "")
            max_sub = disc.get("sitemap_max_sub", 5)
            followed = 0
            for sub_url in sub_urls:
                if followed >= max_sub:
                    break
                # Follow PDP sitemaps if pattern set, otherwise follow all non-docs
                should_follow = (pdp_pat and pdp_pat in sub_url) or (not pdp_pat and "docs" not in sub_url and "blog" not in sub_url)
                if not should_follow:
                    continue
                try:
                    r2 = _retry_get(sub_url, config)
                    r2.encoding = 'utf-8'
                    raw_urls.extend(re.findall(r'https?://[^<>\s"\']+', r2.text))
                    followed += 1
                except Exception:
                    pass
        urls = raw_urls
    except:
        return {}
    
    # Collect products keyed by category slug
    cat_buckets = {}   # slug -> list of product dicts
    industries = []
    seen = set()
    for u in urls:
        if exclude and exclude in u:
            continue
        if prod_pat in u and (not disc.get("require_html", True) or '.html' in u):
            path = u.replace(config["base_url"], "").strip("/")
            if path not in seen:
                seen.add(path)
                name = _apply_name_case(u.removesuffix('.html').rstrip('/').split('/')[-1].replace('-', ' '))
                # Determine category from URL
                cat_slug = "All"
                if cat_pat:
                    m = cat_pat.search(u)
                    if m:
                        cat_slug = m.group(cat_group)
                cat_display = cat_name_map.get(cat_slug, _apply_name_case(cat_slug.replace('-', ' ')))
                cat_buckets.setdefault(cat_slug, {"display": cat_display, "items": []})
                cat_buckets[cat_slug]["items"].append({"id": u, "name": name, "name_zhCN": name, "url": u})
        elif ind_pat in u and u.rstrip('/') not in seen:
            seen.add(u.rstrip('/'))
            slug = _apply_name_case(u.rstrip('/').split('/')[-1].replace('-', ' '))
            # Determine industry category from URL
            ind_cat_slug = ""
            if cat_pat:
                m = cat_pat.search(u)
                if m:
                    ind_cat_slug = m.group(cat_group)
            ind_cat_display = cat_name_map.get(ind_cat_slug, _apply_name_case(ind_cat_slug.replace('-', ' '))) if ind_cat_slug else ""
            industries.append({"id": u, "name": slug, "name_zhCN": slug, "url": u, "cat_slug": ind_cat_slug, "cat_display": ind_cat_display})
    
    # Build productTypeList from buckets
    product_type_list = []
    for slug in sorted(cat_buckets.keys()):
        bucket = cat_buckets[slug]
        product_type_list.append({"name": bucket["display"], "name_zhCN": bucket["display"], "children": bucket["items"]})
    
    return {"productTypeList": product_type_list, "industryList": industries}


def _fetch_crawl(config, disc):
    """Crawl nav links from a rendered entry page using fetch_page (supports playwright).
    
    Supports url_category_rule from discovery config to group products by URL path
    segments into separate categories (same as _fetch_sitemap).
    """
    start_url = disc.get("start_url", config["base_url"])
    html = fetch_page(start_url, config)
    soup = BeautifulSoup(html, "html.parser")
    prod_pat = disc.get("product_pattern", "/products/")
    ind_pat = disc.get("industry_pattern", "/industries/")
    nav_sel = disc.get("nav_selector", "a")
    exclude = disc.get("exclude_pattern", "")
    name_from_url = disc.get("name_from_url", False)
    name_case = disc.get("name_case", "title")
    def _apply_name_case(s):
        if name_case == "preserve": return s
        if name_case == "upper": return s.upper()
        if name_case == "lower": return s.lower()
        return s.title()

    cat_rule = disc.get("url_category_rule")
    cat_name_map = cat_rule.get("name_map", {}) if cat_rule else {}
    cat_pat = re.compile(cat_rule["pattern"]) if cat_rule and cat_rule.get("pattern") else None
    cat_group = cat_rule.get("group", 1) if cat_rule else 1

    cat_buckets = {}   # slug -> {"display": ..., "items": [...]}
    industries, seen = [], set()

    for a in soup.select(nav_sel):
        href = a.get("href", "")
        if not href or href.startswith(("#", "javascript:")):
            continue
        full = href if href.startswith("http") else _url_join(start_url, href)
        if "#" in full and full.split("#", 1)[0].rstrip("/") == start_url.rstrip("/"):
            continue
        if exclude and exclude in full:
            continue
        if full in seen:
            continue
        url_name = full.rstrip('/').split('/')[-1].replace('-', ' ')
        url_name = re.sub(r'\.(html?|htm|aspx?|php|jsp)$', '', url_name)
        name = url_name if name_from_url else (a.get_text(strip=True) or url_name)
        name = _apply_name_case(name)
        if prod_pat in full:
            seen.add(full)
            cat_slug = "All"
            if cat_pat:
                m = cat_pat.search(full)
                if m:
                    cat_slug = m.group(cat_group)
            cat_display = cat_name_map.get(cat_slug, _apply_name_case(cat_slug.replace('-', ' ')))
            cat_buckets.setdefault(cat_slug, {"display": cat_display, "items": []})
            cat_buckets[cat_slug]["items"].append({"id": full, "name": name, "name_zhCN": name, "url": full})
        elif ind_pat in full:
            seen.add(full)
            industries.append({"id": full, "name": name, "name_zhCN": name, "url": full})

    product_type_list = []
    for slug, bucket in cat_buckets.items():
        product_type_list.append({"name": bucket["display"], "name_zhCN": bucket["display"], "children": bucket["items"]})
    return {"productTypeList": product_type_list, "industryList": industries}


def _fetch_url_list(config, disc):
    raw_items = disc.get("urls", [])
    max_pages = int(disc.get("max_pages") or 0)
    include_patterns = disc.get("url_include_patterns") or []
    exclude_patterns = disc.get("url_exclude_patterns") or []
    skip_list_pages = disc.get("skip_list_pages", True)
    buckets = {}
    seen = set()
    for item in raw_items:
        entry = {"url": item} if isinstance(item, str) else dict(item)
        url = entry.get("url", "")
        if not url:
            continue
        full = url if str(url).startswith("http") else _url_join(config["base_url"], str(url))
        if full in seen:
            continue
        seen.add(full)
        if skip_list_pages and _is_list_page_url(full):
            continue
        slug = urlparse(full).path.rstrip("/").split("/")[-1] or urlparse(full).netloc or "page"
        name = entry.get("title") or entry.get("name") or re.sub(r"\.(html?|php|aspx?)$", "", slug).replace("-", " ").replace("_", " ").strip().title()
        category = entry.get("category") or disc.get("category_name") or "Pages"
        item_text = " ".join(str(part) for part in (full, name, category))
        if include_patterns and not any(re.search(pattern, item_text) for pattern in include_patterns):
            continue
        if exclude_patterns and any(re.search(pattern, item_text) for pattern in exclude_patterns):
            continue
        buckets.setdefault(category, [])
        buckets[category].append({"id": full, "name": name, "name_zhCN": name, "url": full})
        if max_pages and len(seen) >= max_pages:
            break
    return {
        "productTypeList": [
            {"name": cat, "name_zhCN": cat, "children": items}
            for cat, items in buckets.items()
        ],
        "industryList": [],
    }


_LIST_PAGE_PATTERNS = [
    r"(?i)/reports?/?$",
    r"(?i)/research/?$",
    r"(?i)/insights?/?$",
    r"(?i)/resources?/?$",
    r"(?i)/list/?$",
    r"(?i)/index\.html?$",
    r"(?i)/whitepapers?/?$",
    r"(?i)\?page=\d+",
]


def _is_list_page_url(url):
    path = urlparse(url).path.rstrip("/")
    if not path or path == "/":
        return False
    for pattern in _LIST_PAGE_PATTERNS:
        if re.search(pattern, url):
            return True
    return False


def _web_fetch_menu(config):
    fb = config.get("web_fallback", {})
    if not fb.get("enabled"): return {}
    sel = fb.get("selectors", {})
    soup = _web_fetch(config["base_url"] + fb.get("menu_url", "/products"), config)
    cats = []
    id_attr = sel.get("item_id_attr", "data-id")
    for cat_el in soup.select(sel.get("menu_categories", "nav .category")):
        cat_name = cat_el.get_text(strip=True)
        items = []
        for a in cat_el.select(sel.get("menu_items", "a.product-link")):
            pid = a.get(id_attr) or a.get("href", "").rstrip("/").split("/")[-1]
            items.append({"id": pid, "name": a.get_text(strip=True), "name_zhCN": a.get_text(strip=True)})
        cats.append({"name": cat_name, "name_zhCN": cat_name, "children": items})
    return {"productTypeList": cats}


def fetch_product_detail(pid, config):
    # ponytail: skip API call entirely if endpoint is empty (crawl-mode sites
    # When pid is already a full URL, avoid a wasted homepage fetch.
    if config.get("api", {}).get("product_detail"):
        try:
            url = config["base_url"] + config["api"]["product_detail"].format(id=pid)
            data = _retry_get(url, config).json().get("data", {})
            if fm_get(data, config, "product_html", ""):
                return data
        except Exception:
            pass
    return _web_fetch_product_detail(pid, config)


def _web_fetch_product_detail(pid, config):
    fb = config.get("web_fallback", {})
    if not fb.get("enabled"): return {}
    sel = fb.get("selectors", {})
    # ponytail: if pid is already a full URL (crawl-mode discovery), use it directly
    # instead of concatenating base_url + product_url template.
    if str(pid).startswith("http"):
        page_url = str(pid)
    else:
        page_url = config["base_url"] + fb.get("product_url", "/products/{id}").format(id=pid)
    soup = _web_fetch(page_url, config)
    _content_sel = config.get("html_components", {}).get("content_selector", "")
    site_selectors = config.get("html_components", {}).get("site_selectors", {})
    domain = urlparse(page_url).netloc
    site_sel = site_selectors.get(domain, "") if site_selectors else ""
    name_el = soup.select_one(sel.get("item_name", "h1.product-title"))
    html_sel = sel.get("product_html", "") or site_sel or _content_sel or "div.product-detail-content"
    html_el = soup.select_one(html_sel)
    if html_el and site_sel and len(html_el.get_text(strip=True)) < 200 and site_sel != _content_sel:
        html_el = soup.select_one(_content_sel) if _content_sel else None
    if not html_el:
        html_el = soup.select_one("body")
    name_val = name_el.get_text(strip=True) if name_el else ""
    desc_val = str(html_el) if html_el else ""
    return {
        "name": name_val, "name_zhCN": name_val,
        "description": desc_val, "description_zhCN": desc_val,
        "product_html": desc_val,
    }


def fetch_product_children(pid, config):
    if config.get("api", {}).get("product_children"):
        try:
            url = config["base_url"] + config["api"]["product_children"].format(id=pid)
            return _retry_get(url, config, timeout=10).json().get("data", [])
        except Exception:
            pass
    return _web_fetch_product_children(pid, config)


def _web_fetch_product_children(pid, config):
    """Discover sub-product links from a product page via web scraping.
    Uses web_fallback.children_selector to find child links on the product page.
    Returns list of dicts with id (full URL) and name_zhCN (link text)."""
    fb = config.get("web_fallback", {})
    if not fb.get("enabled"): return []
    children_sel = fb.get("children_selector", "")
    if not children_sel: return []
    page_url = str(pid) if str(pid).startswith("http") else config["base_url"] + fb.get("product_url", "/products/{id}").format(id=pid)
    soup = _web_fetch(page_url, config)
    children = []
    seen = set()
    for a in soup.select(children_sel):
        href = a.get("href", "")
        if not href or href.startswith(("#", "javascript:")): continue
        full = href if href.startswith("http") else _url_join(start_url, href)
        if full in seen: continue
        seen.add(full)
        name = a.get_text(strip=True)
        # Strip image alt text from link text (e.g. "![img] FR-A series" → "FR-A series")
        name = re.sub(r'!\[[^\]]*\]\s*', '', name).strip()
        if name:
            children.append({"id": full, "name": name, "name_zhCN": name, "url": full})
    return children


# ── Spec Extraction: 5-Layer Cascade ─────────────────────────────────
# Inspired by: Apify (multi-layer merge), Crawl4AI (CSS schema),
# DEXTER (structural features), Diffbot (auto-detect).
# Layers 1-3 are zero-config; layers 4-5 are per-site config.

_SPEC_NOISE_VALUES = frozenset([
    "See Details", "Submit Inquiry", "View Guidance",
    "Download", "Publication", "—", "--",
])
_SPEC_NOISE_KEYS = frozenset([
    "Drawings", "Environmental Compliance", "SVHC Information",
    "EU Importer Address", "EU Authorized Representative Address",
])


def _spec_filter(specs, noise_vals, noise_keys):
    """Filter noise from spec key-value pairs."""
    nv = set(noise_vals) | _SPEC_NOISE_VALUES
    nk = set(noise_keys) | _SPEC_NOISE_KEYS
    filtered = {}
    for k, v in specs.items():
        if not k or not v:
            continue
        if isinstance(v, dict):
            v = v.get("name", v.get("value", str(v)))
        v = str(v)
        if any(n in v for n in nv) or any(n in k for n in nk):
            continue
        v = re.sub(r'([a-z])(See details|See Details)', r'\1', v).strip()
        filtered[k] = v
    return filtered


def _extract_specs_jsonld(soup):
    """Layer 1: Extract specs from schema.org/Product JSON-LD blocks."""
    specs = {}
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            # Handle @graph wrapping
            graph = item.get("@graph", [])
            if graph:
                items.extend(graph)
                continue
            if item.get("@type") in ("Product", "ProductModel"):
                for prop in item.get("additionalProperty", []):
                    name = prop.get("name", "")
                    value = prop.get("value", "")
                    if name and value:
                        specs[name] = str(value)
                for field in ("weight", "depth", "width", "height", "color", "material", "brand"):
                    if field in item and item[field]:
                        val = item[field]
                        if isinstance(val, dict):
                            val = val.get("name", val.get("value", str(val)))
                        if isinstance(val, list):
                            val = val[0] if val else ""
                        specs[field.title()] = str(val).strip()
    return specs


def _extract_specs_microdata(soup):
    """Layer 2: Extract specs from itemscope/itemtype microdata attributes."""
    specs = {}
    for el in soup.select("[itemtype*='schema.org/Product'], [itemtype*='schema.org/ProductModel']"):
        for prop_el in el.select("[itemprop]"):
            prop_name = prop_el.get("itemprop", "")
            if not prop_name or prop_name in ("name", "url", "image", "description"):
                continue
            content = prop_el.get("content") or prop_el.get_text(strip=True)
            if content:
                specs[prop_name.replace("-", " ").title()] = content
    return specs


def _score_spec_block(block):
    """Score an HTML block on how likely it contains spec data (0-1).

    Based on DEXTER structural features:
    - Low link density (spec tables have few links; nav tables have many)
    - Consistent key-value pattern (short labels + values)
    - Short text cells (not long paragraphs)
    - Regular DOM structure (consistent row shapes)
    """
    links = block.find_all("a")
    text = block.get_text(strip=True)
    if not text:
        return 0.0

    link_density = len(links) / max(len(text), 1)
    if link_density > 0.02:
        return 0.0  # Too many links = nav, not specs

    rows = block.find_all("tr") or block.find_all("dt")
    if len(rows) < 2:
        return 0.0

    kv_count = 0
    total_len = 0
    for row in rows:
        if row.name == "dt":
            dd = row.find_next_sibling("dd")
            if dd:
                kv_count += 1
                total_len += len(row.get_text(strip=True)) + len(dd.get_text(strip=True))
        else:
            cells = row.find_all(["td", "th"])
            if len(cells) == 2:
                k = cells[0].get_text(strip=True)
                v = cells[1].get_text(strip=True)
                if k and v and len(k) < 80 and len(v) < 200:
                    kv_count += 1
                    total_len += len(k) + len(v)

    if kv_count < 2:
        return 0.0

    avg_len = total_len / max(kv_count, 1)
    length_score = max(0, 1.0 - avg_len / 200)  # Shorter = more spec-like
    density_score = kv_count / max(len(rows), 1)   # Higher kv ratio = better
    return length_score * 0.4 + density_score * 0.6


def _extract_specs_auto(soup):
    """Layer 3: Auto-detect spec blocks by structural features (zero-config).

    Scans all <table>, <dl>, and common spec div patterns.
    Merges ALL high-scoring blocks (not just the best one).
    """
    candidates = []

    for table in soup.find_all("table"):
        score = _score_spec_block(table)
        if score >= 0.3:
            specs = {}
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) >= 2:
                    k = cells[0].get_text(strip=True)
                    v = cells[1].get_text(strip=True)
                    if k and v:
                        specs[k] = v
            if len(specs) >= 2:
                candidates.append((score, specs))

    for dl in soup.find_all("dl"):
        specs = {}
        dts = dl.find_all("dt")
        for dt in dts:
            dd = dt.find_next_sibling("dd")
            if dd:
                k = dt.get_text(strip=True)
                v = dd.get_text(strip=True)
                if k and v:
                    specs[k] = v
        if len(specs) >= 2:
            score = _score_spec_block(dl)
            if score >= 0.2:
                candidates.append((score, specs))

    if not candidates:
        return {}

    # Merge all high-scoring blocks (dedup by key, prefer higher-scored source)
    merged = {}
    seen_keys = {}
    candidates.sort(key=lambda x: x[0], reverse=True)
    for score, specs in candidates:
        for k, v in specs.items():
            if k not in seen_keys:
                seen_keys[k] = score
                merged[k] = v
    return merged


def _extract_specs_css(soup, config):
    """Layer 4: Extract specs using per-site CSS selectors from config."""
    css = config.get("spec_extraction", {}).get("css_heuristics", {})
    if not css:
        return {}

    specs = {}
    for selector_key in ("spec_table", "spec_section", "spec_dl"):
        sel = css.get(selector_key, "")
        if not sel:
            continue
        for el in soup.select(sel):
            for table in el.find_all("table") if el.name != "table" else [el]:
                for row in table.find_all("tr"):
                    cells = row.find_all(["td", "th"])
                    if len(cells) >= 2:
                        k = cells[0].get_text(strip=True)
                        v = cells[1].get_text(strip=True)
                        if k and v:
                            specs[k] = v
            for dl in (el.find_all("dl") if el.name != "dl" else [el]):
                dts = dl.find_all("dt")
                for dt in dts:
                    dd = dt.find_next_sibling("dd")
                    if dd:
                        k = dt.get_text(strip=True)
                        v = dd.get_text(strip=True)
                        if k and v:
                            specs[k] = v
    return specs


def _extract_specs_linked(pid, config):
    """Layer 5: Follow URL template to separate spec detail pages.

    Discovers catalog numbers in the product HTML, constructs spec page URLs,
    fetches each, and extracts table key-value pairs.
    """
    lp = config.get("spec_extraction", {}).get("linked_page", {})
    url_tpl = lp.get("url_template", "")
    catalog_pat = lp.get("catalog_pattern", "")
    if not url_tpl or not catalog_pat:
        return []

    fb = config.get("web_fallback", {})
    page_url = str(pid) if str(pid).startswith("http") else config["base_url"] + fb.get("product_url", "/products/{id}").format(id=pid)
    soup = _web_fetch(page_url, config)
    page_text = soup.get_text()

    catalogs = list(dict.fromkeys(re.findall(catalog_pat, page_text)))
    if not catalogs:
        return []

    noise_vals = config.get("spec_extraction", {}).get("noise_values", list(_SPEC_NOISE_VALUES))
    noise_keys = config.get("spec_extraction", {}).get("noise_keys", list(_SPEC_NOISE_KEYS))
    max_catalogs = lp.get("max_catalogs", 5)
    results = []

    for catalog in catalogs[:max_catalogs]:
        spec_url = url_tpl.format(catalog=catalog)
        if not spec_url.startswith("http"):
            spec_url = config["base_url"] + spec_url
        try:
            spec_soup = _web_fetch(spec_url, config)
            specs = _extract_specs_auto(spec_soup)
            specs = _spec_filter(specs, noise_vals, noise_keys)
            if specs:
                results.append((catalog, specs))
        except Exception:
            continue

    return results


def extract_specs(pid, config):
    """Orchestrate spec extraction through the 5-layer cascade.

    Each layer fills gaps from previous layers (Apify merge pattern).
    Returns list of (source_label, specs_dict) tuples, deduped across layers.
    """
    se = config.get("spec_extraction", {})
    if not se.get("enabled"):
        return []

    cascade = se.get("cascade", ["json_ld", "microdata", "spec_table", "css_heuristic", "linked_page"])
    min_pairs = se.get("min_spec_pairs", 3)
    noise_vals = se.get("noise_values", list(_SPEC_NOISE_VALUES))
    noise_keys = se.get("noise_keys", list(_SPEC_NOISE_KEYS))

    fb = config.get("web_fallback", {})
    page_url = str(pid) if str(pid).startswith("http") else config["base_url"] + fb.get("product_url", "/products/{id}").format(id=pid)
    soup = _web_fetch(page_url, config)

    # Collect specs from each layer
    layer_specs = {}
    for layer in cascade:
        if layer == "json_ld":
            specs = _spec_filter(_extract_specs_jsonld(soup), noise_vals, noise_keys)
            if len(specs) >= min_pairs:
                layer_specs["JSON-LD"] = specs
        elif layer == "microdata":
            specs = _spec_filter(_extract_specs_microdata(soup), noise_vals, noise_keys)
            if len(specs) >= min_pairs:
                layer_specs["Microdata"] = specs
        elif layer == "spec_table":
            specs = _spec_filter(_extract_specs_auto(soup), noise_vals, noise_keys)
            if len(specs) >= min_pairs:
                layer_specs["Auto"] = specs
        elif layer == "css_heuristic":
            specs = _spec_filter(_extract_specs_css(soup, config), noise_vals, noise_keys)
            if len(specs) >= min_pairs:
                layer_specs["CSS"] = specs
        elif layer == "linked_page":
            for catalog, specs in _extract_specs_linked(pid, config):
                if len(specs) >= min_pairs:
                    layer_specs[catalog] = specs

    if not layer_specs:
        return []

    # Merge: first layer wins per key, later layers fill gaps
    merged = {}
    sources = {}
    for source, specs in layer_specs.items():
        for k, v in specs.items():
            if k not in merged:
                merged[k] = v
                sources[k] = source

    # Return as single merged result with best source label
    best_source = max(layer_specs.keys(), key=lambda s: len(layer_specs[s]))
    return [(best_source, merged)]


def fetch_industry_detail(iid, config):
    if config.get("api", {}).get("industry_detail"):
        try:
            url = config["base_url"] + config["api"]["industry_detail"].format(id=iid)
            return _retry_get(url, config).json().get("data", {})
        except Exception:
            pass
    return _web_fetch_industry_detail(iid, config)


def _web_fetch_industry_detail(iid, config):
    fb = config.get("web_fallback", {})
    if not fb.get("enabled"): return {}
    sel = fb.get("selectors", {})
    # ponytail: if iid is already a full URL (crawl-mode discovery), use it directly
    if str(iid).startswith("http"):
        page_url = str(iid)
    else:
        page_url = config["base_url"] + fb.get("industry_url", "/solutions/{id}").format(id=iid)
    soup = _web_fetch(page_url, config)
    _content_sel = config.get("html_components", {}).get("content_selector", "")
    name_el = soup.select_one(sel.get("item_name", "h1.solution-title"))
    html_sel = sel.get("industry_html", "") or _content_sel or "div.solution-content"
    html_el = soup.select_one(html_sel)
    html_field = fm(config, "industry_html", "content")
    return {
        "name": name_el.get_text(strip=True) if name_el else "",
        "name_zhCN": name_el.get_text(strip=True) if name_el else "",
        html_field: str(html_el) if html_el else ""
    }


def fetch_industry_cases(iid, config):
    try:
        url = config["base_url"] + config["api"]["industry_cases"].format(id=iid)
        return _retry_get(url, config, timeout=10).json().get("data", [])
    except Exception:
        return []


def extract_product_recursive(pid, name, features, cover, save_dir, config, level=1, index="", _depth=0):
    # ponytail: max recursion depth 3 to prevent infinite loops on deep product trees
    if _depth > 3:
        return []
    img_dir = os.path.join(save_dir, config["output_structure"]["image_subdir"])
    res_dir = os.path.join(save_dir, config["output_structure"]["resource_subdir"])
    sep = tpl(config, "block_separator") or "\n\n---\n"
    title = tpl(config, "product_heading", name=name, prefix="#"*level) or f"{'#'*level} {index + ' ' if index else ''}{name}"
    blocks = [title + "\n"]
    # If features or cover not provided, try fetching them from product detail
    _detail = None
    if not features or not cover:
        _detail = fetch_product_detail(pid, config)
        if not features:
            features = fm_get(_detail, config, "item_features", "")
        if not cover:
            cover = fm_get(_detail, config, "item_cover", "")
    # Content blocks: list (split config) or dict (monolithic config)
    _cb = config.get("content_blocks", ["cover_image", "features", "body_html", "subproducts"])
    cb = _cb if isinstance(_cb, list) else _cb.get("product", ["cover_image", "features", "body_html", "subproducts"])
    # page_type: from split config (__engine_page_type) or fallback to "product" for monolithic config
    page_type = config.get("__engine_page_type", "product")
    for block_type in cb:
        if block_type == "cover_image" and cover:
            rel = download_image(cover, img_dir, name, "主图", config)
            if rel:
                cover_line = tpl(config, "product_cover_image", name=name, rel=rel)
                if cover_line: blocks.append(cover_line + "\n")
        elif block_type == "features" and features:
            feat_line = tpl(config, "product_features", features=features)
            if feat_line: blocks.append(feat_line + "\n")
        elif block_type == "body_html":
            data = fetch_product_detail(pid, config)
            html = fm_get(data, config, "product_html", "")
            if html:
                md = convert_html_to_md(html, config, img_dir, name, "正文", resource_dir=res_dir, page_type=page_type)
                if md: blocks.append(md)
        elif block_type == "spec_tables":
            spec_results = extract_specs(pid, config)
            if spec_results:
                for source_label, specs in spec_results:
                    if "__spec_records" in config:
                        record_specs(config["__spec_records"], config["output_root"], save_dir, pid, name, source_label, specs)
                    spec_heading = f"{'#'*(level+1)} 技术参数 ({source_label})"
                    blocks.append(sep + spec_heading)
                    blocks.append("| 参数 | 值 |")
                    blocks.append("|------|-----|")
                    for k, v in specs.items():
                        ek = k.replace("|", "\\|")
                        ev = v.replace("|", "\\|")
                        blocks.append(f"| {ek} | {ev} |")
        elif block_type == "subproducts":
            sub_mode = config.get("output_structure", {}).get("subproduct_mode", "merge")
            children = fetch_product_children(pid, config) or []
            for i, sub in enumerate(children):
                sub_idx = f"{index}.{i+1}" if index else str(i+1)
                sub_name = fm_get(sub, config, "item_name", "")
                if not sub_name:
                    continue
                if sub_mode == "split":
                    sub_dir = os.path.join(save_dir, sanitize(sub_name))
                    sub_blocks = extract_product_recursive(
                        sub.get(fm(config, "item_id", "id")),
                        sub_name,
                        fm_get(sub, config, "item_features", ""),
                        fm_get(sub, config, "item_cover", ""),
                        sub_dir, config, level=2, index="", _depth=_depth+1)
                    if sub_blocks:
                        sub_md_path = os.path.join(sub_dir, f"{sanitize(sub_name)}.md")
                        os.makedirs(sub_dir, exist_ok=True)
                        with open(sub_md_path, "w", encoding="utf-8") as f:
                            f.write("\n".join(sub_blocks))
                else:
                    sub_blocks = extract_product_recursive(
                        sub.get(fm(config, "item_id", "id")),
                        sub_name,
                        fm_get(sub, config, "item_features", ""),
                        fm_get(sub, config, "item_cover", ""),
                        save_dir, config, level=level+1, index=sub_idx, _depth=_depth+1)
                    if sub_blocks:
                        blocks.append(sep); blocks.extend(sub_blocks)
    # Data source footer (top-level only, not subproducts)
    if not index:
        from datetime import date as _date
        _today = _date.today().isoformat()
        _site_name = config.get("site_name", config.get("base_url", ""))
        _base = config.get("base_url", "")
        footer = tpl(config, "product_data_source",
                     site_name=_site_name, base_url=_base, id=pid, date=_today)
        if footer:
            blocks.append(sep + footer)
        final = _normalize_heading_whitespace(_dedupe_repeated_download_sections("\n".join(blocks)))
        final = _normalize_heading_jumps(final)
        final = _demote_subheading_patterns(final, config.get("html_components", {}).get("subheading_demote_patterns", []))
        final = _dedupe_adjacent_same_title_headings(final)
        final = _normalize_blank_lines(final)
        blocks = [final]
    return blocks


def extract_industry(iid, name, config, save_dir=None):
    out = config["output_root"]
    sol_dir = save_dir or os.path.join(out, config["output_structure"]["solutions_dir"], sanitize(name))
    img_dir = os.path.join(sol_dir, config["output_structure"]["image_subdir"])
    res_dir = os.path.join(sol_dir, config["output_structure"]["resource_subdir"])
    sep = tpl(config, "block_separator") or "\n\n---\n"
    title = tpl(config, "industry_heading", name=name) or f"# {name} 解决方案"
    blocks = [title + "\n"]
    _cb = config.get("content_blocks", ["body_html", "cases"])
    cb = _cb if isinstance(_cb, list) else _cb.get("industry", ["body_html", "cases"])
    page_type = config.get("__engine_page_type", "industry")
    for block_type in cb:
        if block_type == "body_html":
            data = fetch_industry_detail(iid, config)
            html = fm_get(data, config, "industry_html", "")
            if html:
                md = convert_html_to_md(html, config, img_dir, name, "解决方案正文", resource_dir=res_dir, page_type=page_type)
                if md: blocks.append(md)
        elif block_type == "cases":
            cases = fetch_industry_cases(iid, config)
            if cases:
                cases_heading = tpl(config, "industry_cases_heading") or "## 💡 应用案例"
                blocks.append(sep + cases_heading)
                for i, case in enumerate(cases):
                    title = fm_get(case, config, "case_title", "")
                    case_title_line = tpl(config, "case_heading", n=i+1, title=title)
                    if case_title_line: blocks.append(case_title_line)
                    summary = fm_get(case, config, "case_summary", "")
                    if summary:
                        summary_line = tpl(config, "case_summary", summary=summary)
                        if summary_line: blocks.append(summary_line + "\n")
                    chtml = fm_get(case, config, "case_html", "")
                    if chtml:
                        cmd = convert_html_to_md(chtml, config, img_dir, title, "案例正文", resource_dir=res_dir, page_type=page_type)
                        if cmd: blocks.append(cmd)
    # Data source footer
    from datetime import date as _date
    _today = _date.today().isoformat()
    _site_name = config.get("site_name", config.get("base_url", ""))
    _base = config.get("base_url", "")
    footer = tpl(config, "industry_data_source",
                 site_name=_site_name, base_url=_base, id=iid, date=_today)
    if footer:
        blocks.append(sep + footer)
    result = "\n".join(blocks)
    result = _normalize_heading_whitespace(result)
    result = _normalize_heading_jumps(result)
    result = _demote_subheading_patterns(result, config.get("html_components", {}).get("subheading_demote_patterns", []))
    result = _dedupe_adjacent_same_title_headings(result)
    result = _normalize_blank_lines(result)
    return result



# 使用 resource_utils.decide_save_dir


def download_resource(url, save_dir, name, config, current_name=""):
    if not url: return ""
    base = config["base_url"]
    if url.startswith("/"): url = base + url
    elif not url.startswith("http"): url = base + "/" + url
    try:
        r = _retry_get(url, config, timeout=30)
        if r.status_code != 200: return ""
        content = r.content
        if len(content) < 1024: return ""
        from urllib.parse import unquote
        cd = r.headers.get("content-disposition", "")
        filename = ""
        m = re.search(r"filename\*=UTF-8''([^;]+)", cd, re.IGNORECASE)
        if m:
            filename = unquote(m.group(1).strip().strip('"'))
        if not filename:
            m = re.search(r'filename="?([^";]+)"?', cd, re.IGNORECASE)
            if m:
                filename = unquote(m.group(1).strip())
        if not filename:
            m = re.search(r'/([^/?]+\.(?:pdf|doc|docx|xls|xlsx|zip|rar))', r.url, re.IGNORECASE)
            if m:
                filename = unquote(m.group(1))
        if not filename:
            ext_m = re.search(r'\.(pdf|doc|docx|xls|xlsx|zip|rar)', r.url + name, re.IGNORECASE)
            ext = ext_m.group(0).lower() if ext_m else ".pdf"
            clean_name = re.sub(r'\.(pdf|doc|docx|xls|xlsx|zip|rar)$', '', name, flags=re.IGNORECASE).strip()
            filename = f"{sanitize(clean_name)}{ext}"
        else:
            filename = sanitize(filename)
        clean_name = re.sub(r'\.(pdf|doc|docx|xls|xlsx|zip|rar)$', '', filename, flags=re.IGNORECASE).strip()
        ext_m = re.search(r'\.(pdf|doc|docx|xls|xlsx|zip|rar)$', filename, re.IGNORECASE)
        ext = ext_m.group(0).lower() if ext_m else ".pdf"

        _shared_kw = config.get("filters", {}).get("resource_shared_keywords", [])
        is_shared = False
        if current_name:
            name_sanitized = sanitize(clean_name).replace("_", "")
            if sanitize(current_name).replace("_", "") in name_sanitized:
                is_shared = False
            if any(kw in clean_name for kw in _shared_kw):
                is_shared = True
        if is_shared:
            parent_dir = os.path.dirname(os.path.dirname(save_dir))
            if parent_dir:
                save_dir = os.path.join(parent_dir, config['output_structure']['resource_subdir'])

        local = os.path.join(save_dir, filename)
        rel = f"{config['output_structure']['resource_subdir']}/{filename}"
        if is_shared:
            rel = f"../{config['output_structure']['resource_subdir']}/{filename}"
        md5 = hashlib.md5(content).hexdigest()
        key = (save_dir, md5)
        if key in DOWNLOADED_RES_MD5: return DOWNLOADED_RES_MD5[key]
        DOWNLOADED_RES_MD5[key] = rel
        os.makedirs(save_dir, exist_ok=True)
        with open(local, "wb") as f: f.write(content)
        if "__resource_records" in config:
            record_resource(config["__resource_records"], config["output_root"], save_dir, current_name or name, current_name or name, "download", filename, rel, ext, len(content), url)
        return rel
    except Exception:
        return ""


def load_baseline(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f: return json.load(f)
    return {"products": {}, "industries": {}}


def save_baseline(state, path):
    with open(path, "w", encoding="utf-8") as f: json.dump(state, f, ensure_ascii=False, indent=2)


def merge_baseline_item(baseline, kind, pid, updates):
    kkey = kind_key(kind)
    existing = baseline.get(kkey, {}).get(pid, {})
    existing.update({k: v for k, v in updates.items() if v is not None})
    baseline.setdefault(kkey, {})[pid] = existing
    return existing


def detect_changes(state, config):
    menu = fetch_menu(config)
    changes = {"new": [], "changed": [], "errors": []}
    cat_key = fm(config, "menu_categories", "productTypeList")
    ind_key = fm(config, "menu_industries", "industryList")
    child_key = fm(config, "category_children", "children")
    for cat in menu.get(cat_key, []):
        for p in cat.get(child_key, []):
            try:
                pid = p.get(fm(config, "item_id", "id"))
                fp = text_fp(fm_get(fetch_product_detail(pid, config), config, "product_html", ""))
                base = state["products"].get(pid, {})
                name = fm_get(p, config, "item_name", "")
                if not base: changes["new"].append(("product", pid, name, ""))
                elif base.get("fp") != fp: changes["changed"].append(("product", pid, name, ""))
            except Exception as e:
                changes["errors"].append(("product", p.get(fm(config, "item_id", "id")), str(e)))
    for ind in menu.get(ind_key, []):
        try:
            iid = ind.get(fm(config, "item_id", "id"))
            fp = text_fp(fm_get(fetch_industry_detail(iid, config), config, "industry_html", ""))
            base = state["industries"].get(iid, {})
            name = fm_get(ind, config, "item_name", "")
            if not base: changes["new"].append(("industry", iid, name, ""))
            elif base.get("fp") != fp: changes["changed"].append(("industry", iid, name, ""))
        except Exception as e:
            changes["errors"].append(("industry", ind.get(fm(config, "item_id", "id")), str(e)))
    return changes


def refresh_baseline(items, config, path):
    state = load_baseline(path)
    errors = []
    for kind, pid, name, cat, _ in items:
        try:
            if kind == "product":
                html = fm_get(fetch_product_detail(pid, config), config, "product_html", "")
            else:
                html = fm_get(fetch_industry_detail(pid, config), config, "industry_html", "")
            state[kind_key(kind)][pid] = {"name": name, "category": cat, "fp": text_fp(html)}
        except Exception as e:
            errors.append((kind, pid, name, str(e)))
    save_baseline(state, path)
    return errors


def load_init_checkpoint():
    if os.path.exists(INIT_CHECKPOINT):
        with open(INIT_CHECKPOINT, encoding="utf-8") as f:
            return json.load(f)
    return {"done_ids": [], "items": []}


def save_init_checkpoint(state):
    with open(INIT_CHECKPOINT, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def collect_all_items(config):
    menu = fetch_menu(config)
    items = []
    cat_key = fm(config, "menu_categories", "productTypeList")
    ind_key = fm(config, "menu_industries", "industryList")
    children_key = fm(config, "category_children", "children")
    cat_name_key = fm(config, "category_name", "name")
    id_key = fm(config, "item_id", "id")
    name_key = fm(config, "item_name", "name")
    for cat in menu.get(cat_key, []):
        cat_name = cat.get(cat_name_key, "")
        for p in cat.get(children_key, []):
            pid = p.get(id_key)
            if pid: items.append(("product", pid, p.get(name_key, ""), cat_name, p))
            try:
                for sub in fetch_product_children(pid, config):
                    sid = sub.get(id_key)
                    if sid: items.append(("product", sid, sub.get(name_key, ""), cat_name, sub))
            except Exception:
                pass
    for ind in menu.get(ind_key, []):
        iid = ind.get(id_key)
        if iid: items.append(("industry", iid, ind.get(name_key, ""), ind.get("cat_display", ""), ind))
    return apply_discovery_controls(items, config.get("discovery", {}))


# ── Quality baseline ──

def _extract_md_metrics(md_text):
    """Extract structural metrics from a markdown string for quality comparison."""
    lines = md_text.split("\n") if md_text else []
    headings = {"h1": 0, "h2": 0, "h3": 0, "h4": 0, "h5": 0, "h6": 0}
    for line in lines:
        for lvl in range(1, 7):
            if line.startswith("#" * lvl + " "):
                headings[f"h{lvl}"] += 1
                break
    # Tables: count rows with pipe separators
    table_rows = [l for l in lines if l.strip().startswith("|") and l.strip().endswith("|")]
    empty_headers = 0
    for row in table_rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        if all(not c for c in cells):
            empty_headers += 1
    # Images
    import re
    local_imgs = len(re.findall(r"!\[.*?\]\([^h][^t].*?\)", md_text or ""))
    remote_imgs = len(re.findall(r"!\[.*?\]\(https?://", md_text or ""))
    # Wiki links [[name]]
    wiki_links = len(re.findall(r"\[\[.+?\]\]", md_text or ""))
    # Noise keywords (common noise patterns)
    noise_patterns = ["更多信息", "联系我们", "在线留言", "申请试用", "视频 0"]
    noise_count = sum(1 for p in noise_patterns if p in (md_text or ""))
    return {
        "headings": headings,
        "table_rows": len(table_rows),
        "empty_table_headers": empty_headers,
        "images_local": local_imgs,
        "images_remote": remote_imgs,
        "wiki_links": wiki_links,
        "link_count": len(re.findall(r"\[.*?\]\(.*?\)", md_text or "")),
        "noise_count": noise_count,
        "word_count": len((md_text or "").replace("\n", "").replace(" ", "")),
        "char_count": len(md_text or ""),
        "lines": len(lines),
    }


def generate_quality_baseline(output_root, config):
    """Scan extracted MD files and generate quality baseline json."""
    baseline = {"generated_at": "", "items": {}}
    from datetime import datetime
    baseline["generated_at"] = datetime.now().isoformat()

    out_struct = config.get("output_structure", {})
    prod_dir = os.path.join(output_root, out_struct.get("products_dir", "产品"))
    sol_dir = os.path.join(output_root, out_struct.get("solutions_dir", "解决方案"))

    for kind, base_dir in [("product", prod_dir), ("industry", sol_dir)]:
        if not os.path.isdir(base_dir):
            continue
        for entry in os.scandir(base_dir):
            if not entry.is_dir():
                continue
            for f in os.scandir(entry.path):
                if f.is_file() and f.name.endswith(".md"):
                    with open(f.path, encoding="utf-8") as fh:
                        md = fh.read()
                    metrics = _extract_md_metrics(md)
                    rel_path = os.path.relpath(f.path, output_root)
                    baseline["items"][rel_path] = {
                        "type": kind,
                        "metrics": metrics,
                    }
    return baseline


def evaluate_against_baseline(output_root, config, quality_baseline_path):
    """Compare current extraction results against quality baseline, report regressions."""
    current = generate_quality_baseline(output_root, config)
    if not os.path.exists(quality_baseline_path):
        return {"status": "no_baseline", "message": "质量基线不存在，请先运行 --quality-init"}

    with open(quality_baseline_path, encoding="utf-8") as f:
        previous = json.load(f)

    regressions = []
    improvements = []
    new_items = []
    missing_items = []

    prev_items = previous.get("items", {})
    curr_items = current.get("items", {})

    all_paths = set(prev_items.keys()) | set(curr_items.keys())
    for path in sorted(all_paths):
        if path not in prev_items:
            new_items.append(path)
            continue
        if path not in curr_items:
            missing_items.append(path)
            continue

        prev_m = prev_items[path]["metrics"]
        curr_m = curr_items[path]["metrics"]
        issues = []

        # Heading regression: fewer headings at any level
        for lvl in ["h1", "h2", "h3"]:
            if curr_m["headings"][lvl] < prev_m["headings"][lvl]:
                issues.append(f"{lvl}: {prev_m['headings'][lvl]}→{curr_m['headings'][lvl]}")
        # Table regression
        if curr_m["table_rows"] < prev_m["table_rows"] * 0.8:
            issues.append(f"table_rows: {prev_m['table_rows']}→{curr_m['table_rows']}")
        # More empty headers is regression
        if curr_m["empty_table_headers"] > prev_m["empty_table_headers"]:
            issues.append(f"empty_headers: {prev_m['empty_table_headers']}→{curr_m['empty_table_headers']}")
        # Image regression
        if curr_m["images_local"] < prev_m["images_local"]:
            issues.append(f"local_images: {prev_m['images_local']}→{curr_m['images_local']}")
        # More remote images is regression
        if curr_m["images_remote"] > prev_m["images_remote"]:
            issues.append(f"remote_images: {prev_m['images_remote']}→{curr_m['images_remote']}")
        # Wiki link regression
        if curr_m["wiki_links"] < prev_m["wiki_links"]:
            issues.append(f"wiki_links: {prev_m['wiki_links']}→{curr_m['wiki_links']}")
        # Noise regression
        if curr_m["noise_count"] > prev_m["noise_count"]:
            issues.append(f"noise: {prev_m['noise_count']}→{curr_m['noise_count']}")
        # Word count dropped significantly
        if curr_m["word_count"] < prev_m["word_count"] * 0.7:
            issues.append(f"word_count: {prev_m['word_count']}→{curr_m['word_count']}")

        if issues:
            regressions.append({"path": path, "issues": issues})
        # Check improvements
        elif (curr_m["images_local"] > prev_m["images_local"]
              or curr_m["wiki_links"] > prev_m["wiki_links"]
              or curr_m["noise_count"] < prev_m["noise_count"]):
            improvements.append(path)

    return {
        "status": "ok",
        "regressions": regressions,
        "improvements": improvements,
        "new_items": new_items,
        "missing_items": missing_items,
        "total_compared": len(all_paths) - len(new_items) - len(missing_items),
    }


def _print_quality_report(result):
    """Print quality evaluation report in Obsidian-compatible format."""
    if result["status"] == "no_baseline":
        print(f"⚠️ {result['message']}")
        return

    r = result["regressions"]
    n_new = len(result["new_items"])
    n_miss = len(result["missing_items"])
    n_imp = len(result["improvements"])
    total = result["total_compared"]

    # Summary callout
    if not r and not n_miss:
        print(f"\n> [!success] 质量评估通过")
    else:
        print(f"\n> [!warning] 质量评估发现 {len(r)} 项退化")

    print(f"> 对比 {total} 项 | 退化 {len(r)} | 改善 {n_imp} | 新增 {n_new} | 缺失 {n_miss}")

    # Regressions table
    if r:
        print(f"\n### 退化项\n")
        print(f"| 文件 | 问题 |")
        print(f"|------|------|")
        for item in r:
            issues_str = "<br>".join(item["issues"])
            print(f"| `{item['path']}` | {issues_str} |")

    # Improvements
    if n_imp:
        print(f"\n### 改善项\n")
        for p in result["improvements"]:
            print(f"- ✅ `{p}`")

    # New items
    if n_new:
        print(f"\n### 新增项\n")
        for p in result["new_items"]:
            print(f"- 🆕 `{p}`")

    # Missing items
    if n_miss:
        print(f"\n> [!danger] 缺失项")
        for p in result["missing_items"]:
            print(f"> - ❌ `{p}`")


def init_baseline_batched(config, baseline_path, batch_size=20):
    """分批初始化基线，支持断点续传。"""
    ckpt = load_init_checkpoint()
    if ckpt.get("items"):
        items = ckpt["items"]
        done = set(ckpt.get("done_ids", []))
        print(f"📋 恢复进度：已完成 {len(done)}/{len(items)}")
    else:
        print("🔍 收集所有产品/方案 ID...")
        items = collect_all_items(config)
        ckpt = {"items": items, "done_ids": []}
        save_init_checkpoint(ckpt)
        print(f"共 {len(items)} 项，分批处理（每批 {batch_size}）")

    state = load_baseline(baseline_path)
    total = len(items)

    for i in range(0, total, batch_size):
        batch = items[i:i+batch_size]
        batch_new = [(k, pid, n, c) for (k, pid, n, c, _) in batch if pid not in ckpt["done_ids"]]
        if not batch_new:
            continue
        print(f"  处理批次 {i//batch_size + 1}/{(total+batch_size-1)//batch_size} ({len(batch_new)} 项)...")
        for kind, pid, name, cat in batch_new:
            try:
                if kind == "product":
                    html = fm_get(fetch_product_detail(pid, config), config, "product_html", "")
                else:
                    html = fm_get(fetch_industry_detail(pid, config), config, "industry_html", "")
                state[kind_key(kind)][pid] = {"name": name, "category": cat, "fp": text_fp(html)}
                ckpt["done_ids"].append(pid)
            except Exception as e:
                print(f"    ⚠️ {name} ({pid}): {e}")
        save_baseline(state, baseline_path)
        save_init_checkpoint(ckpt)
        time.sleep(0.2)  # 避免过快触发限流

    # 清理 checkpoint
    if os.path.exists(INIT_CHECKPOINT):
        os.remove(INIT_CHECKPOINT)
    print(f"✅ 已为 {len(ckpt['done_ids'])}/{total} 项建立基线")


def _loss_guard(original, refined, threshold=0.05):
    """Token-diff check: reject refined if >threshold of original's non-whitespace tokens are missing."""
    orig_tokens = set(re.findall(r'[^\s]+', original))
    ref_tokens = set(re.findall(r'[^\s]+', refined))
    if not orig_tokens:
        return True
    lost = orig_tokens - ref_tokens
    loss_ratio = len(lost) / len(orig_tokens)
    return loss_ratio <= threshold


def _llm_refine(md, config, ctx):
    cfg = config.get("llm_refine", {})
    if not (cfg.get("enabled") and md.strip()):
        return md
    max_len = cfg.get("max_input_chars", 12000)
    if len(md) > max_len:
        _log_refine_skip(ctx, md, "oversized")
        return md
    try:
        prompt = _build_refine_prompt(md, cfg, ctx)
        refined = _llm_complete(prompt, cfg)
        refined = refined.strip()
        if not refined:
            return md
        if not _loss_guard(md, refined):
            _log_refine_skip(ctx, md, "content_loss")
            return md
        return refined
    except Exception as e:
        return md


def _log_refine_skip(ctx, md, reason):
    try:
        log_dir = os.path.join(ctx.get("save_dir", ""), "_refine_review") if isinstance(ctx, dict) else ""
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "skipped.txt"), "a", encoding="utf-8") as f:
                f.write(f"{reason}\t{ctx.get('current_name', '')}\t{len(md)}\n")
    except Exception:
        pass


def _build_refine_prompt(md, cfg, ctx):
    tasks = set(cfg.get("tasks", ["remove_noise"]))
    parts = ["You clean HTML-extracted markdown. Output ONLY markdown, no commentary. If unsure whether text is noise, keep it."]
    if "remove_noise" in tasks:
        parts.append("- Remove leftover nav/boilerplate/footer fragments and orphan lines rules missed.")
    parts.append(f"\n---\n{md}")
    return "\n".join(parts)


def _llm_complete(prompt, cfg):
    import requests
    provider = cfg.get("provider", "omlx")
    if provider == "minimax":
        url = cfg.get("endpoint") or "https://api.minimax.chat/v1/text/chatcompletion_v2"
        headers = {"Authorization": f"Bearer {cfg.get('api_key','')}"}
    else:
        url = cfg.get("endpoint","http://localhost:11434/v1").rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {cfg.get('api_key','')}"} if cfg.get("api_key") else {}
    body = {"model": cfg.get("model","qwen2.5"), "messages":[{"role":"user","content":prompt}], "max_tokens": cfg.get("max_tokens",4096), "temperature": 0}
    r = requests.post(url, headers=headers, json=body, timeout=cfg.get("timeout",60))
    return r.json()["choices"][0]["message"]["content"]


def _format_output(md, fmt, config, ctx):
    """Transform extracted markdown to the requested output format."""
    md = apply_hooks("post_markdown", md, config, ctx)
    if fmt == "obsidian":
        return _format_obsidian(md, config, ctx)
    if fmt == "json":
        return _format_json(md, config, ctx)
    return md


def _format_obsidian(md, config, ctx):
    """Obsidian-flavored MD: YAML frontmatter + internal .md links → [[wiki]] links."""
    title = ctx.get("name", "")
    date = __import__("datetime").date.today().isoformat()
    source = ctx.get("source_url", "")
    frontmatter = f"---\ntitle: {title}\ndate: {date}\nsource: {source}\ntags: []\n---\n\n"
    # ponytail: regex assumes internal links end in .md; external http(s) links untouched
    md = re.sub(r'\[([^\]]+)\]\([^)]+\.md\)', r'[[\1]]', md)
    return frontmatter + md


def _format_json(md, config, ctx):
    """Structured JSON: title, content, images, resources, headings, wiki_links."""
    return json.dumps({
        "title": ctx.get("name", ""),
        "content": md,
        "images": re.findall(r'!\[.*?\]\(.*?\)', md),
        "resources": re.findall(r'\[[^\]]+\]\([^)]+\.(?:pdf|docx?|xlsx?|pptx?|zip|rar)\)', md, re.IGNORECASE),
        "wiki_links": re.findall(r'\[\[.*?\]\]', md),
        "headings": re.findall(r'^#{1,6}\s+.*', md, re.MULTILINE),
        "source_url": ctx.get("source_url", ""),
        "extract_date": __import__("datetime").date.today().isoformat(),
    }, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="通用网站提取引擎")
    parser.add_argument("--site", help="站点名，自动读取 config/{site}/")
    parser.add_argument("--page-type", help="分类类型（product/industry等），加载 common.json + {page_type}.json")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--baseline", default=DEFAULT_BASELINE)
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--quality-init", action="store_true", help="生成质量基线 _quality_baseline.json")
    parser.add_argument("--quality-check", action="store_true", help="对照质量基线评估当前提取结果")
    parser.add_argument("--llm-refine", action="store_true", help="使用本地LLM精修提取的Markdown")
    parser.add_argument("--format", choices=["md", "obsidian", "json"], default="md", help="输出格式：md(默认)/obsidian(YAML frontmatter+wiki链接)/json(结构化)")
    args = parser.parse_args()
    
    if args.site:
        config_dir = os.path.join(os.path.dirname(__file__), "config", args.site)
        args.config = os.path.join(config_dir, "common.json")
        args.baseline = os.path.join(config_dir, "_baseline.json")

    config = load_config(args.config, page_type=args.page_type)
    if args.llm_refine:
        config.setdefault("llm_refine", {})["enabled"] = True
    print(f"====== 通用提取引擎 v1.0 ======")
    print(f"站点: {config.get('site_name')} | 配置: {args.config}")
    print(f"输出: {config.get('output_root')}")
    print()

    # Quality baseline operations
    if args.quality_init:
        qb_path = os.path.join(os.path.dirname(args.config), "_quality_baseline.json")
        qb = generate_quality_baseline(config["output_root"], config)
        with open(qb_path, "w", encoding="utf-8") as f:
            json.dump(qb, f, ensure_ascii=False, indent=2)
        print(f"✅ 质量基线已生成: {qb_path} ({len(qb['items'])} 项)")
        return

    if args.quality_check:
        qb_path = os.path.join(os.path.dirname(args.config), "_quality_baseline.json")
        result = evaluate_against_baseline(config["output_root"], config, qb_path)
        _print_quality_report(result)
        return

    if args.init:
        init_baseline_batched(config, args.baseline)
        return

    if args.check:
        state = load_baseline(args.baseline)
        changes = detect_changes(state, config)
        if not changes["new"] + changes["changed"]:
            print("✅ 无变更")
        else:
            for c in changes["new"]: print(f"  + 新增 {c[0]}: {c[2]}")
            for c in changes["changed"]: print(f"  ~ 变更 {c[0]}: {c[2]}")
        return

    # Read entity types from config (default: product + industry)
    extract_cfg = config.get("extraction", {})
    entity_types = extract_cfg.get("entity_types", ["product", "industry"])
    cat_filter = extract_cfg.get("category_filter", [])  # optional: only extract named categories

    menu = fetch_menu(config)
    out = config["output_root"]
    config["__spec_records"] = []
    config["__resource_records"] = []
    prod_dir_name = config["output_structure"]["products_dir"]
    out_struct = config["output_structure"]
    sol_dir_name = out_struct.get("industries_dir", out_struct["solutions_dir"]) if config.get("page_type") == "industry" else out_struct["solutions_dir"]

    if args.incremental:
        state = load_baseline(args.baseline)
        changes = detect_changes(state, config)
        to_run = changes["new"] + changes["changed"]
        if not to_run:
            print("✅ 无变更，无需重跑"); return
        print(f"检测到 {len(to_run)} 项变更，定向重跑...")
        items = to_run
    else:
        items = collect_all_items(config)
        print(f"全量提取 {len(items)} 项...")

    count = 0
    # Filter by configured entity types
    if entity_types:
        items = [i for i in items if i[0] in entity_types]
    # Filter by category name (if configured)
    if cat_filter:
        items = [i for i in items if i[3] in cat_filter]
    print(f"提取 {len(items)} 项（类型: {entity_types}）")
    cat_name_map = config.get("category_name_map", {})
    for kind, pid, name, cat, item_dict in items:
        cat = cat_name_map.get(cat, cat)
        try:
            # Load page-type-specific config (e.g. product.json, industry.json)
            page_type = "product" if kind == "product" else "industry"
            cfg = load_config(args.config, page_type=page_type)
            # Extract features and cover from menu item dict if available
            features = fm_get(item_dict, cfg, "item_features", "") if item_dict else ""
            cover = fm_get(item_dict, cfg, "item_cover", "") if item_dict else ""
            _ctx = {"name": name, "source_url": cfg.get("base_url", "")}
            _ext = ".json" if args.format == "json" else ".md"
            if kind == "product":
                save_dir = os.path.join(out, prod_dir_name, sanitize(cat), sanitize(name))
                blocks = extract_product_recursive(pid, name, features, cover, save_dir, cfg)
                blocks.append(tpl(cfg, "extract_time_footer", date=__import__('datetime').date.today(), url=cfg.get("base_url", "")) or "\n\n---\n*提取时间：未知*")
                _out = _format_output("\n".join(blocks), args.format, cfg, _ctx)
                md_path = os.path.join(save_dir, f"{sanitize(name)}{_ext}")
                os.makedirs(save_dir, exist_ok=True)
                with open(md_path, "w", encoding="utf-8") as f: f.write(_out)
            else:
                sol_sub = sanitize(cat) if cat else sanitize(name)
                sol_dir = os.path.join(out, sol_dir_name, sol_sub, sanitize(name))
                md = extract_industry(pid, name, cfg, save_dir=sol_dir)
                _out = _format_output(md, args.format, cfg, _ctx)
                md_path = os.path.join(sol_dir, f"{sanitize(name)}{_ext}")
                os.makedirs(sol_dir, exist_ok=True)
                with open(md_path, "w", encoding="utf-8") as f: f.write(_out)
            print(f"  ✅ {name}"); count += 1
        except Exception as e:
            print(f"  ❌ {name}: {e}")

    if args.incremental:
        refresh_baseline(items, config, args.baseline)
    print(f"\n🎉 完成 {count}/{len(items)}")

    # Post-extraction: generate summary / index if configured
    # ponytail: generate_summary and generate_index now both gate the same
    # write_standard_docs() call (writes all 3 docs unconditionally).
    # The two keys act as a single "generate any standard docs" on/off.
    doc_cfg = config.get("document", {})
    if doc_cfg.get("generate_summary") or doc_cfg.get("generate_index"):
        from library_docs import write_standard_docs
        paths = write_standard_docs(out, config)
        print(f"📄 全库文档已生成: {paths['readme']} {paths['index']} {paths['summary']}")

    spec_path = write_specs_output(out, config.get("__spec_records", []))
    if spec_path:
        print(f"📊 结构化属性已生成: {spec_path}")

    resource_path = write_resources_output(out, config.get("__resource_records", []))
    if resource_path:
        print(f"📎 资料索引已生成: {resource_path}")


if __name__ == "__main__":
    main()
