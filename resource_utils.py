"""共享资源处理逻辑 — 供 extract_generic.py 和 download_resources.py 复用。"""
import os
import re
from typing import Optional
from bs4 import BeautifulSoup


def decide_save_dir(file_name: str, product_dir: str, category_root_res: str, config: dict) -> Optional[str]:
    res_cfg = config.get("resources", {})
    general_kws = res_cfg.get("general_keywords", [])
    model_pat = res_cfg.get("model_pattern", r'\d{4}')
    res_subdir = config.get("output_structure", {}).get("resource_subdir", "资料")

    if general_kws and re.search('|'.join(map(re.escape, general_kws)), file_name):
        return category_root_res

    models = re.findall(model_pat, file_name)
    if models:
        dir_name = os.path.basename(product_dir)
        if any(m not in dir_name for m in models):
            return None
        return os.path.join(product_dir, res_subdir)

    return os.path.join(product_dir, res_subdir)


def parse_download_table(panel_html: str, config: dict) -> list:
    soup = BeautifulSoup(panel_html, "html.parser")
    files, header = [], []
    patterns = config.get("download_link_patterns", ["download"])
    for i, row in enumerate(soup.find_all("tr")):
        cells = [c.get_text().strip() for c in row.find_all(["td", "th"])]
        if i == 0:
            header = cells
            continue
        if not any(cells):
            continue
        a = None
        for tag in row.find_all("a"):
            href = tag.get("href", "")
            if any(p in href for p in patterns):
                a = tag
                break
        if not a:
            continue
        info = {"url": a.get("href", "")}
        for j, h in enumerate(header):
            if j < len(cells):
                if "文件名" in h: info["name"] = cells[j]
                elif "分类" in h: info["category"] = cells[j]
                elif "格式" in h: info["format"] = cells[j]
                elif "日期" in h: info["date"] = cells[j]
        if "name" not in info:
            info["name"] = f"资料_{i}"
        files.append(info)
    return files


def post_cleanup_misplaced(root_dir: str, config: dict) -> int:
    removed = 0
    res_cfg = config.get("resources", {})
    model_pat = res_cfg.get("model_pattern", r'\d{4}')
    cross_rules = res_cfg.get("cross_category_rules", {})
    res_subdir = config.get("output_structure", {}).get("resource_subdir", "资料")

    for dp, _, fns in os.walk(root_dir):
        if os.path.basename(dp) != res_subdir:
            continue
        dir_name = os.path.basename(os.path.dirname(dp))
        for fn in fns:
            if not fn.lower().endswith(('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.zip', '.rar')):
                continue
            models = re.findall(model_pat, fn)
            for m in models:
                if m not in dir_name:
                    fp = os.path.join(dp, fn)
                    try: os.remove(fp)
                    except FileNotFoundError: pass
                    else:
                        print(f"  [清理放错] {os.path.relpath(fp, root_dir)}")
                        removed += 1
                    break
            for cat, rule in cross_rules.items():
                if cat in dir_name:
                    forbidden = rule.get("forbidden_in_name", [])
                    if any(fw in fn for fw in forbidden):
                        fp = os.path.join(dp, fn)
                        try: os.remove(fp)
                        except FileNotFoundError: pass
                        else:
                            print(f"  [清理跨分类] {os.path.relpath(fp, root_dir)}")
                            removed += 1
    return removed


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', '_', name).strip().replace(' ', '_')


def build_category_root_res(product_dir: str, config: dict) -> str:
    out_root = config.get("output_root", "")
    prod_dir_name = config.get("output_structure", {}).get("products_dir", "产品")
    res_subdir = config.get("output_structure", {}).get("resource_subdir", "资料")
    if not out_root:
        return os.path.join(os.path.dirname(product_dir), res_subdir)
    rel = os.path.relpath(product_dir, out_root)
    parts = rel.split(os.sep)
    if len(parts) >= 2 and parts[0] == prod_dir_name:
        cat = parts[1]
        return os.path.join(out_root, prod_dir_name, cat, res_subdir)
    return os.path.join(os.path.dirname(product_dir), res_subdir)