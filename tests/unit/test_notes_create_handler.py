"""Unit tests for the single-purpose ``POST /notes`` Lambda."""

import json

import pytest

from tests.unit.lambda_test_utils import (
    assert_configured_table_name,
    load_lambda_handler,
)


@pytest.fixture(name="handler_and_store")
def _handler_and_store(monkeypatch):
    """Load the create handler with an in-memory DynamoDB table."""
    monkeypatch.setenv("TABLE_NAME", "test-table")
    module = load_lambda_handler("notes_create", "notes_create_handler")
    store = {}

    class FakeTable:  # pylint: disable=too-few-public-methods  # boto3 test double
        """Minimal write-only DynamoDB table test double."""

        def put_item(self, **kwargs):
            """Persist an item in the in-memory store."""
            item = kwargs["Item"]
            store[item["id"]] = item

    monkeypatch.setattr(module, "_table", FakeTable)
    return module, store


def _post_event(body):
    return {"httpMethod": "POST", "path": "/notes", "body": body}


def test_create_note_success(handler_and_store):
    """A valid request stores and returns a generated note."""
    handler_module, store = handler_and_store
    response = handler_module.handler(
        _post_event(json.dumps({"title": "Shopping", "content": "milk and eggs"}))
    )

    assert response["statusCode"] == 201
    body = json.loads(response["body"])
    assert body["title"] == "Shopping"
    assert body["content"] == "milk and eggs"
    assert len(body["id"]) == 10
    assert body["version"] == 1
    assert store[body["id"]]["content"] == "milk and eggs"
    assert store[body["id"]]["version"] == 1


def test_create_rejects_missing_fields(handler_and_store):
    """A request missing content is rejected."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(
        _post_event(json.dumps({"title": "no content"}))
    )
    assert response["statusCode"] == 400


def test_create_rejects_empty_fields(handler_and_store):
    """Whitespace-only fields are rejected."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(
        _post_event(json.dumps({"title": "   ", "content": "x"}))
    )
    assert response["statusCode"] == 400


def test_create_rejects_bad_json(handler_and_store):
    """Malformed JSON is rejected."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(_post_event("{not json"))
    assert response["statusCode"] == 400


def test_create_rejects_non_object_body(handler_and_store):
    """A JSON value other than an object is rejected."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(
        _post_event(json.dumps(["not", "an", "object"]))
    )
    assert response["statusCode"] == 400


def test_table_uses_configured_name(monkeypatch):
    """The table factory uses TABLE_NAME from the environment."""
    monkeypatch.setenv("TABLE_NAME", "some-table")
    module = load_lambda_handler("notes_create", "notes_create_real")
    assert_configured_table_name(module, monkeypatch)
