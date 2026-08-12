import json
from pathlib import Path

from extract_generic import (
    _fetch_crawl,
    _fetch_sitemap,
    _inherit_run_state,
    _llm_complete,
    _llm_refine,
    _retry_get,
    _same_origin,
    _web_fetch_product_children,
    collect_all_items,
    convert_html_to_md,
    download_image,
    detect_changes,
    load_config,
    sanitize,
    validate_config,
)
from agentweb2md_paths import validate_site_id
from http_utils import ResponseTooLarge
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
    assert "agentweb2md --site generic" in Path(paths["readme"]).read_text(encoding="utf-8")


def test_quality_report_scores_markdown(tmp_path):
    (tmp_path / "page.md").write_text("# Example\n\nUseful text.\n", encoding="utf-8")
    report = build_report(str(tmp_path), site="generic")
    assert report["files_scored"] == 1
    assert report["site_score"] > 0
    json.dumps(report)


def test_automatic_discovery_stays_on_origin():
    assert _same_origin("https://example.com/products/1", "https://example.com")
    assert not _same_origin("https://cdn.example.net/file", "https://example.com")
    assert not _same_origin("http://example.com/products/1", "https://example.com")
    assert not _same_origin("file:///etc/passwd", "https://example.com")


def test_sitemap_and_crawl_drop_external_urls(monkeypatch):
    class SitemapResponse:
        encoding = "utf-8"
        text = (
            "https://example.com/products/inside.html "
            "https://other.example/products/outside.html"
        )

    monkeypatch.setattr("extract_generic._retry_get", lambda *_args, **_kwargs: SitemapResponse())
    config = {"base_url": "https://example.com"}
    menu = _fetch_sitemap(config, {"product_pattern": "/products/"})
    assert [item["id"] for item in menu["productTypeList"][0]["children"]] == [
        "https://example.com/products/inside.html"
    ]

    html = (
        '<a href="/products/inside">Inside</a>'
        '<a href="https://other.example/products/outside">Outside</a>'
    )
    monkeypatch.setattr("extract_generic.fetch_page", lambda *_args, **_kwargs: html)
    menu = _fetch_crawl(config, {"product_pattern": "/products/", "nav_selector": "a"})
    assert [item["id"] for item in menu["productTypeList"][0]["children"]] == [
        "https://example.com/products/inside"
    ]


def test_sanitize_blocks_parent_segments():
    assert sanitize("..") == "_"
    assert sanitize(".") == "_"


def test_config_rejects_inline_llm_key():
    config = load_config(str(ROOT / "config" / "generic" / "common.json"), page_type="product")
    config["llm_refine"] = {"api_key": "placeholder"}
    errors, _warnings = validate_config(config)
    assert "llm_refine.api_key is not allowed; use api_key_env" in errors


def test_llm_key_is_loaded_from_environment(monkeypatch):
    captured = {}

    class Response:
        headers = {}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

        def close(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    def post(_url, **kwargs):
        captured.update(kwargs)
        return Response()

    monkeypatch.setenv("AGENTWEB2MD_TEST_KEY", "test-token")
    monkeypatch.setattr("requests.post", post)
    result = _llm_complete("prompt", {"provider": "minimax", "api_key_env": "AGENTWEB2MD_TEST_KEY"})
    assert result == "ok"
    assert captured["headers"] == {"Authorization": "Bearer test-token"}


def test_remote_llm_requires_configured_environment_key(monkeypatch):
    monkeypatch.delenv("AGENTWEB2MD_MISSING_KEY", raising=False)
    try:
        _llm_complete("prompt", {"provider": "minimax", "api_key_env": "AGENTWEB2MD_MISSING_KEY"})
    except ValueError as error:
        assert "api_key_env" in str(error)
    else:
        raise AssertionError("missing LLM key must fail before any request")


def test_config_rejects_output_path_escape():
    config = load_config(str(ROOT / "config" / "generic" / "common.json"), page_type="product")
    config["output_structure"]["products_dir"] = "../outside"
    errors, _warnings = validate_config(config)
    assert "output_structure.products_dir must stay relative to output_root" in errors


def test_config_accepts_auto_render_mode():
    config = load_config(str(ROOT / "config" / "generic" / "common.json"), page_type="product")
    config["render_mode"] = "auto"
    errors, _warnings = validate_config(config)
    assert errors == []


def test_config_rejects_unbounded_response_limit():
    config = load_config(str(ROOT / "config" / "generic" / "common.json"), page_type="product")
    config["rate_limit"]["max_response_bytes"] = 0
    errors, _warnings = validate_config(config)
    assert "rate_limit.max_response_bytes must be >= 1" in errors


def test_config_rejects_invalid_section_type():
    config = load_config(str(ROOT / "config" / "generic" / "common.json"), page_type="product")
    config["html_components"] = []
    errors, _warnings = validate_config(config)
    assert "html_components must be an object, got list" in errors


def test_child_discovery_resolves_relative_url_and_rejects_external(monkeypatch):
    from bs4 import BeautifulSoup

    html = '<a href="child">Child</a><a href="https://other.example/item">External</a>'
    monkeypatch.setattr("extract_generic._web_fetch", lambda _url, _config: BeautifulSoup(html, "html.parser"))
    config = {
        "base_url": "https://example.com",
        "web_fallback": {"enabled": True, "children_selector": "a"},
    }
    children = _web_fetch_product_children("https://example.com/products/parent", config)
    assert [item["id"] for item in children] == ["https://example.com/products/child"]


def test_image_title_cannot_escape_save_dir(tmp_path, monkeypatch):
    class Response:
        status_code = 200
        content = b"image" * 2048

    monkeypatch.setattr("extract_generic._retry_get", lambda *_args, **_kwargs: Response())
    config = {
        "base_url": "https://example.com",
        "output_structure": {"image_subdir": "images"},
        "filters": {},
        "templates": {},
    }
    rel = download_image("https://example.com/image.png", str(tmp_path), "Page", "../../outside", config)
    assert rel.startswith("images/")
    assert [path.parent for path in tmp_path.iterdir()] == [tmp_path]


def test_site_id_rejects_path_escape():
    for site_id in ("..", "../private", "site/name", "site\\name"):
        try:
            validate_site_id(site_id)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe site ID accepted: {site_id}")


def test_response_limit_rejects_declared_oversize(monkeypatch):
    class Response:
        status_code = 200
        encoding = "utf-8"
        apparent_encoding = "utf-8"
        headers = {}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield b"too large!!"

        def close(self):
            return None

    monkeypatch.setattr("requests.get", lambda *_args, **_kwargs: Response())
    try:
        _retry_get("https://example.com", {"rate_limit": {"max_retries": 1, "max_response_bytes": 10}})
    except ResponseTooLarge:
        pass
    else:
        raise AssertionError("oversized response must be rejected")


def test_page_config_inherits_run_state():
    specs = []
    resources = []
    page = _inherit_run_state(
        {"__llm_refine_cli": True, "__spec_records": specs, "__resource_records": resources},
        {},
    )
    assert page["llm_refine"]["enabled"] is True
    assert page["__spec_records"] is specs
    assert page["__resource_records"] is resources


def test_explicit_llm_refine_failure_is_not_silent(monkeypatch):
    monkeypatch.setattr("extract_generic._llm_complete", lambda *_args: (_ for _ in ()).throw(RuntimeError("failed")))
    try:
        _llm_refine("content", {"llm_refine": {"enabled": True}, "__llm_refine_cli": True}, {})
    except RuntimeError:
        pass
    else:
        raise AssertionError("explicit LLM failures must stop the run")


def test_cli_returns_failure_when_an_item_fails(tmp_path, monkeypatch):
    config_path = tmp_path / "common.json"
    config_path.write_text((ROOT / "config" / "generic" / "common.json").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "product.json").write_text('{"page_type": "product"}', encoding="utf-8")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["output_root"] = str(tmp_path / "output")
    config_path.write_text(json.dumps(config), encoding="utf-8")

    monkeypatch.setattr("extract_generic.collect_all_items", lambda _config: [
        ("product", "1", "Broken", "Samples", {}),
    ])
    monkeypatch.setattr("extract_generic.extract_product_recursive", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    from extract_generic import main

    assert main(["--config", str(config_path)]) == 1


def test_incremental_changes_are_extractable_items(monkeypatch):
    monkeypatch.setattr("extract_generic.fetch_menu", lambda _config: {
        "productTypeList": [{"children": [{"id": "1", "name": "Example"}]}],
        "industryList": [],
    })
    monkeypatch.setattr("extract_generic.fetch_product_detail", lambda *_args: {"html": "changed"})
    changes = detect_changes({"products": {}, "industries": {}}, {})
    assert changes["new"] == [("product", "1", "Example", "", {"id": "1", "name": "Example"})]
