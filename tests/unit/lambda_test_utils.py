"""Shared helpers for loading isolated Lambda handler modules in unit tests."""

import importlib.util
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[2]


def load_lambda_handler(directory: str, module_name: str) -> ModuleType:
    """Load a Lambda handler file under a unique module name."""
    handler_path = _REPO_ROOT / "lambda" / directory / "handler.py"
    spec = importlib.util.spec_from_file_location(module_name, handler_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load Lambda handler: {handler_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_configured_table_name(module: ModuleType, monkeypatch) -> None:
    """Assert that a handler resolves its table from the TABLE_NAME environment value."""
    captured = {}
    dynamodb = getattr(module, "_dynamodb")
    monkeypatch.setattr(
        dynamodb,
        "Table",
        lambda name: captured.setdefault("name", name),
    )
    table_factory = getattr(module, "_table")
    table_factory()
    assert captured["name"] == "some-table"
