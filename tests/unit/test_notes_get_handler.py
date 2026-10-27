"""Unit tests for the single-purpose ``GET /notes/{id}`` Lambda."""

import json

import pytest

from tests.unit.lambda_test_utils import (
    assert_configured_table_name,
    load_lambda_handler,
)


@pytest.fixture(name="handler_and_store")
def _handler_and_store(monkeypatch):
    """Load the get handler with an in-memory DynamoDB table."""
    monkeypatch.setenv("TABLE_NAME", "test-table")
    module = load_lambda_handler("notes_get", "notes_get_handler")
    store = {}

    class FakeTable:  # pylint: disable=too-few-public-methods  # boto3 test double
        """Minimal read-only DynamoDB table test double."""

        def get_item(self, **kwargs):
            """Return an item from the in-memory store when present."""
            item = store.get(kwargs["Key"]["id"])
            return {"Item": item} if item else {}

    monkeypatch.setattr(module, "_table", FakeTable)
    return module, store


def _get_event(note_id):
    return {
        "httpMethod": "GET",
        "path": f"/notes/{note_id}",
        "pathParameters": {"id": note_id},
    }


def test_get_note_found(handler_and_store):
    """An existing note is returned as JSON."""
    handler_module, store = handler_and_store
    store["abc123"] = {
        "id": "abc123",
        "title": "Note",
        "content": "remember this",
        "version": 3,
    }

    response = handler_module.handler(_get_event("abc123"))

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {
        "id": "abc123",
        "title": "Note",
        "content": "remember this",
        "version": 3,
    }


def test_get_legacy_note_defaults_to_version_one(handler_and_store):
    """A stored item with no version is exposed as version 1."""
    handler_module, store = handler_and_store
    store["legacy1"] = {
        "id": "legacy1",
        "title": "Old",
        "content": "no version stored",
    }

    response = handler_module.handler(_get_event("legacy1"))

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {
        "id": "legacy1",
        "title": "Old",
        "content": "no version stored",
        "version": 1,
    }


def test_get_note_not_found(handler_and_store):
    """An unknown note id returns 404."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(_get_event("missing"))
    assert response["statusCode"] == 404


def test_get_without_id(handler_and_store):
    """Missing path parameters return 400."""
    handler_module, _store = handler_and_store
    response = handler_module.handler({"httpMethod": "GET", "path": "/notes"})
    assert response["statusCode"] == 400


def test_get_with_empty_id(handler_and_store):
    """An empty id path parameter returns 400."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(
        {"httpMethod": "GET", "path": "/notes/", "pathParameters": {"id": ""}}
    )
    assert response["statusCode"] == 400


def test_table_uses_configured_name(monkeypatch):
    """The table factory uses TABLE_NAME from the environment."""
    monkeypatch.setenv("TABLE_NAME", "some-table")
    module = load_lambda_handler("notes_get", "notes_get_real")
    assert_configured_table_name(module, monkeypatch)
