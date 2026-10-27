"""Unit tests for the single-purpose ``DELETE /notes/{id}`` Lambda."""

import pytest
from botocore.exceptions import ClientError

from tests.unit.lambda_test_utils import (
    assert_configured_table_name,
    load_lambda_handler,
)

_CONDITIONAL_FAILURE = ClientError(
    {"Error": {"Code": "ConditionalCheckFailedException", "Message": "stale"}},
    "DeleteItem",
)


class FakeTable:
    """In-memory DynamoDB double honouring the handler's version condition."""

    def __init__(self, store):
        self._store = store

    def get_item(self, **kwargs):
        """Return a stored item when present."""
        item = self._store.get(kwargs["Key"]["id"])
        return {"Item": item} if item else {}

    def delete_item(self, **kwargs):
        """Delete the item when the version condition holds, else raise."""
        note_id = kwargs["Key"]["id"]
        values = kwargs["ExpressionAttributeValues"]
        expected = values[":expected"]
        initial = values[":initial"]
        item = self._store.get(note_id)
        if item is None:
            raise _CONDITIONAL_FAILURE
        current = item.get("version")
        matches = current == expected or (current is None and expected == initial)
        if not matches:
            raise _CONDITIONAL_FAILURE
        del self._store[note_id]
        return {}


@pytest.fixture(name="handler_and_store")
def _handler_and_store(monkeypatch):
    """Load the delete handler with an in-memory DynamoDB table."""
    monkeypatch.setenv("TABLE_NAME", "test-table")
    module = load_lambda_handler("notes_delete", "notes_delete_handler")
    store = {}
    monkeypatch.setattr(module, "_table", lambda: FakeTable(store))
    return module, store


def _delete_event(note_id, if_match=None, header_name="If-Match"):
    event = {
        "httpMethod": "DELETE",
        "path": f"/notes/{note_id}",
        "pathParameters": {"id": note_id},
    }
    if if_match is not None:
        event["headers"] = {header_name: if_match}
    return event


def test_delete_success_returns_204(handler_and_store):
    """A matching If-Match deletes the note and returns an empty 204."""
    handler_module, store = handler_and_store
    store["abc123"] = {"id": "abc123", "title": "A", "content": "B", "version": 4}

    response = handler_module.handler(_delete_event("abc123", "4"))

    assert response["statusCode"] == 204
    assert response["body"] == ""
    assert "abc123" not in store


def test_delete_legacy_item_matches_version_one(handler_and_store):
    """A legacy item with no version matches If-Match 1 and is deleted."""
    handler_module, store = handler_and_store
    store["legacy"] = {"id": "legacy", "title": "A", "content": "B"}

    response = handler_module.handler(_delete_event("legacy", "1"))

    assert response["statusCode"] == 204
    assert "legacy" not in store


def test_delete_case_insensitive_if_match_header(handler_and_store):
    """The If-Match header is read case-insensitively."""
    handler_module, store = handler_and_store
    store["id1"] = {"id": "id1", "title": "A", "content": "B", "version": 2}

    response = handler_module.handler(
        _delete_event("id1", "2", header_name="IF-MATCH")
    )

    assert response["statusCode"] == 204


def test_delete_missing_id(handler_and_store):
    """Missing path parameters return 400."""
    handler_module, _store = handler_and_store
    response = handler_module.handler({"httpMethod": "DELETE"})
    assert response["statusCode"] == 400


def test_delete_missing_if_match(handler_and_store):
    """A missing If-Match header returns 400."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(_delete_event("id1"))
    assert response["statusCode"] == 400


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "1.5", "  ", "2x"])
def test_delete_invalid_if_match(handler_and_store, bad):
    """Non-positive-integer If-Match values return 400."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(_delete_event("id1", bad))
    assert response["statusCode"] == 400


def test_delete_non_string_if_match(handler_and_store):
    """A non-string If-Match header value returns 400."""
    handler_module, _store = handler_and_store
    event = _delete_event("id1")
    event["headers"] = {"If-Match": 5}
    response = handler_module.handler(event)
    assert response["statusCode"] == 400


def test_delete_skips_unrelated_and_non_string_header_keys(handler_and_store):
    """Header lookup skips non-matching and non-string keys to find If-Match."""
    handler_module, store = handler_and_store
    store["id1"] = {"id": "id1", "title": "A", "content": "B", "version": 1}
    event = _delete_event("id1")
    event["headers"] = {"Content-Type": "application/json", 42: "x", "If-Match": "1"}
    response = handler_module.handler(event)
    assert response["statusCode"] == 204
    assert "id1" not in store


def test_delete_missing_item_returns_404(handler_and_store):
    """A conditional failure with no stored item maps to 404."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(_delete_event("missing", "1"))
    assert response["statusCode"] == 404


def test_delete_stale_version_returns_409(handler_and_store):
    """A conditional failure with a stored item maps to 409 and is not deleted."""
    handler_module, store = handler_and_store
    store["id1"] = {"id": "id1", "title": "A", "content": "B", "version": 5}
    response = handler_module.handler(_delete_event("id1", "3"))
    assert response["statusCode"] == 409
    assert "id1" in store


def test_delete_reraises_unexpected_error(handler_and_store, monkeypatch):
    """A non-conditional AWS error is re-raised rather than mapped to a response."""
    handler_module, _store = handler_and_store

    def _raise(**_kwargs):
        raise ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException"}},
            "DeleteItem",
        )

    class BrokenTable:  # pylint: disable=too-few-public-methods  # test double
        """Table whose delete_item raises an unexpected error."""

        delete_item = staticmethod(_raise)

    monkeypatch.setattr(handler_module, "_table", BrokenTable)
    with pytest.raises(ClientError):
        handler_module.handler(_delete_event("id1", "1"))


def test_table_uses_configured_name(monkeypatch):
    """The table factory uses TABLE_NAME from the environment."""
    monkeypatch.setenv("TABLE_NAME", "some-table")
    module = load_lambda_handler("notes_delete", "notes_delete_real")
    assert_configured_table_name(module, monkeypatch)
