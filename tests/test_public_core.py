import json
from pathlib import Path

from extract_generic import collect_all_items, convert_html_to_md, load_config, validate_config
from library_docs import write_standard_docs
from quality_report import build_report


ROOT = Path(__file__).resolve().parents[1]


def test_public_configs_validate():
    config = load_config(str(ROOT / "config" / "generic" / "common.json"), page_type="product")
    errors, _warnings = validate_config(config)
    assert errors == []


def test_generic_conversion_removes_page_chrome():
    config = load_config(str(ROOT / "config" / "generic" / "common.json"), page_type="product")
    html = "<body><nav>Menu</nav><main><h1>Example</h1><p>Useful text.</p></main><footer>Legal</footer></body>"
    markdown = convert_html_to_md(html, config)
    assert "Example" in markdown
    assert "Useful text." in markdown
    assert "Menu" not in markdown
    assert "Legal" not in markdown


def test_url_list_keeps_explicit_root_url():
    config = load_config(str(ROOT / "config" / "generic" / "common.json"), page_type="product")
    items = collect_all_items(config)
    assert [(kind, url, name) for kind, url, name, _category, _item in items] == [
        ("product", "https://example.com", "Example Domain")
    ]


def test_standard_docs_create_empty_output_root(tmp_path):
    output_root = tmp_path / "new-output"
    paths = write_standard_docs(str(output_root), {"site_id": "generic", "site_name": "Generic"})
    assert all(Path(path).exists() for path in paths.values())
    assert "extract_generic.py --site generic" in Path(paths["readme"]).read_text(encoding="utf-8")


def test_quality_report_scores_markdown(tmp_path):
    (tmp_path / "page.md").write_text("# Example\n\nUseful text.\n", encoding="utf-8")
    report = build_report(str(tmp_path), site="generic")
    assert report["files_scored"] == 1
    assert report["site_score"] > 0
    json.dumps(report)
