"""Unit tests for the URL shortener Lambda handler.

DynamoDB access is mocked via a fake table, so no AWS calls are made.
"""

import importlib
import json

import pytest


@pytest.fixture(name="handler_module")
def _handler_module(monkeypatch):
    """Import the handler with a patched DynamoDB table backed by an in-memory dict."""
    monkeypatch.setenv("TABLE_NAME", "test-table")
    module = importlib.import_module("handler")
    importlib.reload(module)

    store = {}

    class FakeTable:
        """Minimal stand-in for a boto3 DynamoDB Table."""

        def put_item(self, Item):  # noqa: N803 (boto3 kwarg name)
            store[Item["code"]] = Item

        def get_item(self, Key):  # noqa: N803 (boto3 kwarg name)
            item = store.get(Key["code"])
            return {"Item": item} if item else {}

    monkeypatch.setattr(module, "_table", lambda: FakeTable())
    module._store = store
    return module


def _post_event(body):
    return {"httpMethod": "POST", "path": "/", "body": body}


def _get_event(path):
    return {"httpMethod": "GET", "path": path}


def test_create_short_url_success(handler_module):
    resp = handler_module.handler(_post_event(json.dumps({"url": "https://example.com"})))
    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])
    assert body["url"] == "https://example.com"
    assert len(body["code"]) == 7


def test_create_rejects_invalid_url(handler_module):
    resp = handler_module.handler(_post_event(json.dumps({"url": "not-a-url"})))
    assert resp["statusCode"] == 400


def test_create_rejects_bad_json(handler_module):
    resp = handler_module.handler(_post_event("{not json"))
    assert resp["statusCode"] == 400


def test_redirect_found(handler_module):
    created = handler_module.handler(
        _post_event(json.dumps({"url": "https://aws.amazon.com"}))
    )
    code = json.loads(created["body"])["code"]

    resp = handler_module.handler(_get_event(f"/{code}"))
    assert resp["statusCode"] == 302
    assert resp["headers"]["Location"] == "https://aws.amazon.com"


def test_redirect_not_found(handler_module):
    resp = handler_module.handler(_get_event("/missing"))
    assert resp["statusCode"] == 404


def test_get_without_code(handler_module):
    resp = handler_module.handler(_get_event("/"))
    assert resp["statusCode"] == 400


def test_method_not_allowed(handler_module):
    event = {"httpMethod": "DELETE", "path": "/"}
    resp = handler_module.handler(event)
    assert resp["statusCode"] == 405


def test_table_uses_configured_name(monkeypatch):
    """_table() resolves the real DynamoDB table by the configured TABLE_NAME.

    This imports the handler without the fixture's _table patch, so the real
    _table() body (the _dynamodb.Table lookup) is exercised.
    """
    monkeypatch.setenv("TABLE_NAME", "some-table")
    module = importlib.import_module("handler")
    importlib.reload(module)

    captured = {}
    monkeypatch.setattr(
        module._dynamodb,
        "Table",
        lambda name: captured.setdefault("name", name),
    )
    module._table()
    assert captured["name"] == module._TABLE_NAME
