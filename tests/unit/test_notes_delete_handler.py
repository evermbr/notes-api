"""Unit tests for the single-purpose ``DELETE /notes/{id}`` Lambda."""

# pylint: disable=duplicate-code  # parallel tests for parallel, independent handlers

import pytest
from botocore.exceptions import ClientError

from tests.unit.lambda_test_utils import (
    assert_configured_table_name,
    load_lambda_handler,
)


def _client_error(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "Operation")


@pytest.fixture(name="handler_and_store")
def _handler_and_store(monkeypatch):
    """Load the delete handler with an in-memory table honouring the condition."""
    monkeypatch.setenv("TABLE_NAME", "test-table")
    module = load_lambda_handler("notes_delete", "notes_delete_handler")
    store = {}
    calls = {"delete": 0, "get": 0, "error": None}

    class FakeTable:
        """In-memory DynamoDB table double emulating the version condition."""

        def delete_item(self, **kwargs):
            """Delete the item only when the version condition holds."""
            calls["delete"] += 1
            if calls["error"]:
                raise calls["error"]  # pylint: disable=raising-bad-type
            item = store.get(kwargs["Key"]["id"])
            values = kwargs["ExpressionAttributeValues"]
            assert kwargs["ConditionExpression"].startswith("attribute_exists(id)")
            current = None if item is None else item.get("version", values[":initial"])
            if item is None or current != values[":expected"]:
                raise _client_error("ConditionalCheckFailedException")
            del store[kwargs["Key"]["id"]]

        def get_item(self, **kwargs):
            """Return an item when present."""
            calls["get"] += 1
            item = store.get(kwargs["Key"]["id"])
            return {"Item": item} if item else {}

    monkeypatch.setattr(module, "_table", FakeTable)
    return module, store, calls


def _event(note_id="n1", headers=None):
    return {
        "httpMethod": "DELETE",
        "pathParameters": {"id": note_id} if note_id is not None else None,
        "headers": {"If-Match": "1"} if headers is None else headers,
    }


def test_delete_success(handler_and_store):
    """A matching version deletes the note and returns 204 with an empty body."""
    module, store, _calls = handler_and_store
    store["n1"] = {"id": "n1", "title": "T", "content": "C", "version": 4}
    response = module.handler(_event(headers={"if-match": "4"}))
    assert response["statusCode"] == 204
    assert response["body"] == ""
    assert not store


def test_delete_legacy_item_without_version(handler_and_store):
    """A legacy item matches If-Match 1."""
    module, store, _calls = handler_and_store
    store["n1"] = {"id": "n1", "title": "T", "content": "C"}
    assert module.handler(_event())["statusCode"] == 204
    assert not store


def test_delete_stale_version_returns_409(handler_and_store):
    """A stale version returns 409 and keeps the note."""
    module, store, calls = handler_and_store
    store["n1"] = {"id": "n1", "title": "T", "content": "C", "version": 2}
    response = module.handler(_event())
    assert response["statusCode"] == 409
    assert "n1" in store
    assert calls["delete"] == 1
    assert calls["get"] == 1


def test_delete_missing_note_returns_404(handler_and_store):
    """A missing note returns 404."""
    module, _store, calls = handler_and_store
    assert module.handler(_event())["statusCode"] == 404
    assert calls["delete"] == 1
    assert calls["get"] == 1


def test_delete_unexpected_error_is_raised(handler_and_store):
    """Non-conditional AWS errors propagate and do no follow-up read."""
    module, _store, calls = handler_and_store
    calls["error"] = _client_error("InternalServerError")
    with pytest.raises(ClientError):
        module.handler(_event())
    assert calls["get"] == 0


@pytest.mark.parametrize("note_id", [None, ""])
def test_delete_requires_id(handler_and_store, note_id):
    """A missing or empty path id returns 400."""
    module, _store, calls = handler_and_store
    assert module.handler(_event(note_id=note_id))["statusCode"] == 400
    assert calls["delete"] == 0


@pytest.mark.parametrize(
    "headers",
    [
        {},
        None,
        {"If-Match": "0"},
        {"If-Match": "-3"},
        {"If-Match": "abc"},
        {"If-Match": '"1"'},
        {"If-Match": ""},
        {"If-Match": "\u0661"},
        {"If-Match": 1},
        {"Other": "1"},
    ],
)
def test_delete_rejects_invalid_if_match(handler_and_store, headers):
    """Missing or invalid If-Match returns 400 without deleting anything."""
    module, store, calls = handler_and_store
    store["n1"] = {"id": "n1", "title": "T", "content": "C", "version": 1}
    event = _event()
    if headers is None:
        del event["headers"]
    else:
        event["headers"] = headers
    assert module.handler(event)["statusCode"] == 400
    assert calls["delete"] == 0
    assert "n1" in store


def test_table_uses_configured_name(monkeypatch):
    """The table factory uses TABLE_NAME from the environment."""
    monkeypatch.setenv("TABLE_NAME", "some-table")
    module = load_lambda_handler("notes_delete", "notes_delete_real")
    assert_configured_table_name(module, monkeypatch)
