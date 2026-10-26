"""Guard against inert extraction settings and removed service entry points."""

from __future__ import annotations

import ast
from pathlib import Path

from app.config import Settings


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
OLD_ENV_KEYS = (
    "PPSTRUCTURE_URL",
    "PPSTRUCTURE_ACCESS_TOKEN",
    "PPSTRUCTURE_TIMEOUT_SECONDS",
    "PPSTRUCTURE_USE_DOC_ORIENTATION_CLASSIFY",
    "PPSTRUCTURE_USE_DOC_UNWARPING",
    "PPSTRUCTURE_USE_TEXTLINE_ORIENTATION",
    "PPSTRUCTURE_USE_TABLE_RECOGNITION",
    "PPSTRUCTURE_USE_SEAL_RECOGNITION",
    "PPSTRUCTURE_USE_REGION_DETECTION",
    "LAYOUT_PARSING_URL",
    "LAYOUT_PARSING_TIMEOUT",
    "COMPARE_EXTRACTOR",
    "COMPARE_DOCUMENT_EXTRACTOR",
    "COMPARE_REQUIRE_STRUCTURED_OCR",
    "HYBRID_EXTRACTOR",
    "HYBRID_COMPONENT_PARALLEL_ENABLED",
    "LAYOUT_ANALYSIS_MODE",
    "NATIVE_FAST_PATH",
    "NATIVE_PAGE_MODE",
    "DOCUMENT_EXTRACTOR",
    "PYMUPDF_MIN_TEXT_CHARS",
    "PPDOCLAYOUT_LAYOUT_NMS",
)


def test_old_environment_keys_do_not_change_settings(monkeypatch) -> None:
    before = Settings(_env_file=None).model_dump()
    for key in OLD_ENV_KEYS:
        monkeypatch.setenv(key, "legacy-value")
    after = Settings(_env_file=None).model_dump()
    assert after == before
    assert all(key.lower() not in Settings.model_fields for key in OLD_ENV_KEYS)


def test_all_flat_config_keys_have_a_runtime_consumer() -> None:
    config = ast.parse((APP / "config.py").read_text(encoding="utf-8"))
    settings_class = next(node for node in config.body if isinstance(node, ast.ClassDef) and node.name == "Settings")
    fields = {
        node.target.id
        for node in settings_class.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    # These are grouped objects, not environment variable declarations.
    fields -= {"matching", "document_understanding", "ppocrv5", "auth"}

    used_in_runtime: set[str] = set()
    for path in (*APP.rglob("*.py"), *(BACKEND / "scripts").glob("*.py")):
        if path == APP / "config.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        used_in_runtime.update(
            node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and node.attr in fields
        )
        # Settings(base_dir=...) is used by benchmark scripts.
        used_in_runtime.update(
            node.arg for node in ast.walk(tree)
            if isinstance(node, ast.keyword) and node.arg in fields
        )

    forwarded = {
        node.attr
        for node in ast.walk(config)
        if isinstance(node, ast.Attribute)
        and isinstance(node.ctx, ast.Load)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and node.attr.startswith(("match_", "auth_", "oidc_", "document_understanding_"))
    }
    dead_config_keys = sorted(fields - used_in_runtime - forwarded)
    print(f"dead_config_keys={dead_config_keys}")
    assert dead_config_keys == [], f"dead_config_keys={dead_config_keys}"


def test_removed_config_and_layout_endpoint_have_no_app_consumer() -> None:
    forbidden = (
        "PPSTRUCTURE_",
        "LAYOUT_PARSING_",
        "COMPARE_EXTRACTOR",
        "HYBRID_EXTRACTOR",
        "NATIVE_FAST_PATH",
        "NATIVE_PAGE_MODE",
        "/layout-parsing",
    )
    for path in APP.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert not any(term in source for term in forbidden), path
