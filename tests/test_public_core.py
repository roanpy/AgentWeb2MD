import json
from pathlib import Path

from extract_generic import convert_html_to_md, load_config, validate_config
from quality_report import build_report


ROOT = Path(__file__).resolve().parents[1]


def test_public_configs_validate():
    for site in ("generic", "python_docs"):
        config = load_config(str(ROOT / "config" / site / "common.json"), page_type="product")
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


def test_quality_report_scores_markdown(tmp_path):
    (tmp_path / "page.md").write_text("# Example\n\nUseful text.\n", encoding="utf-8")
    report = build_report(str(tmp_path), site="generic")
    assert report["files_scored"] == 1
    assert report["site_score"] > 0
    json.dumps(report)
