"""Shared helpers for loading isolated Lambda handler modules in unit tests."""

import importlib.util
from pathlib import Path
from types import ModuleType

from botocore.exceptions import ClientError

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


class FakeVersionedTable:
    """In-memory DynamoDB table double emulating the versioned conditional writes.

    It evaluates the same semantics the handlers request (note exists and either the
    stored version equals the expected one or, for expected 1, no version is stored)
    and records every call so tests can assert exactly which operations ran.
    """

    def __init__(self, items=None, error=None):
        self.items = dict(items or {})
        self.calls = []
        self.error = error

    @staticmethod
    def conditional_failure():
        """Return the botocore error DynamoDB raises for a failed condition."""
        return ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "no"}},
            "WriteItem",
        )

    def _check(self, key, expected):
        if self.error is not None:
            raise self.error
        item = self.items.get(key)
        if item is None:
            raise self.conditional_failure()
        stored = item.get("version")
        if stored is None:
            stored = 1 if expected == 1 else None
        if stored != expected:
            raise self.conditional_failure()
        return item

    def get_item(self, **kwargs):
        """Return the stored item when present."""
        self.calls.append(("get_item", kwargs))
        item = self.items.get(kwargs["Key"]["id"])
        return {"Item": dict(item)} if item else {}

    def update_item(self, **kwargs):
        """Apply a conditional update of the supplied fields and bump the version."""
        self.calls.append(("update_item", kwargs))
        key = kwargs["Key"]["id"]
        values = kwargs["ExpressionAttributeValues"]
        item = self._check(key, values[":expected"])
        for field in ("title", "content"):
            if f":{field}" in values:
                item[field] = values[f":{field}"]
        item["version"] = item.get("version", values[":initial"]) + values[":increment"]
        return {"Attributes": dict(item)}

    def delete_item(self, **kwargs):
        """Apply a conditional delete."""
        self.calls.append(("delete_item", kwargs))
        key = kwargs["Key"]["id"]
        self._check(key, kwargs["ExpressionAttributeValues"][":expected"])
        return {"Attributes": self.items.pop(key)}


def install_versioned_table(module, monkeypatch, items=None, error=None):
    """Patch a handler module to use a FakeVersionedTable and return the table."""
    table = FakeVersionedTable(items, error)
    monkeypatch.setattr(module, "_table", lambda: table)
    return table
