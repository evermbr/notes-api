"""Unit tests for the single-purpose ``DELETE /notes/{id}`` Lambda."""

# The in-memory FakeTable that enforces the shared version condition mirrors the
# update handler's test double by design; the two conditional operations are tested
# in parallel rather than through a shared fixture.
# pylint: disable=duplicate-code

import pytest
from botocore.exceptions import ClientError

from tests.unit.lambda_test_utils import (
    assert_configured_table_name,
    load_lambda_handler,
)

_CONDITIONAL_FAILURE = ClientError(
    {"Error": {"Code": "ConditionalCheckFailedException", "Message": "no"}},
    "DeleteItem",
)


@pytest.fixture(name="handler_and_store")
def _handler_and_store(monkeypatch):
    """Load the delete handler with an in-memory table that enforces the condition."""
    monkeypatch.setenv("TABLE_NAME", "test-table")
    module = load_lambda_handler("notes_delete", "notes_delete_handler")
    store = {}

    class FakeTable:
        """In-memory DynamoDB double honouring the version condition expression."""

        def get_item(self, **kwargs):
            """Return an item from the in-memory store when present."""
            item = store.get(kwargs["Key"]["id"])
            return {"Item": item} if item else {}

        def delete_item(self, **kwargs):
            """Apply the atomic conditional delete or raise on a condition miss."""
            note_id = kwargs["Key"]["id"]
            values = kwargs["ExpressionAttributeValues"]
            expected = values[":expected"]
            initial = values[":initial"]
            item = store.get(note_id)
            current = item.get("version") if item else None
            matches = item is not None and (
                current == expected
                or (current is None and expected == initial)
            )
            if not matches:
                raise _CONDITIONAL_FAILURE
            removed = store.pop(note_id)
            return {"Attributes": removed}

    monkeypatch.setattr(module, "_table", FakeTable)
    return module, store


def _delete_event(note_id, headers=None):
    return {
        "httpMethod": "DELETE",
        "path": f"/notes/{note_id}",
        "pathParameters": {"id": note_id},
        "headers": headers if headers is not None else {"If-Match": "1"},
    }


def test_delete_success(handler_and_store):
    """A matching version deletes the note and returns 204 with an empty body."""
    handler_module, store = handler_and_store
    store["abc123"] = {
        "id": "abc123",
        "title": "Note",
        "content": "body",
        "version": 1,
    }

    response = handler_module.handler(_delete_event("abc123"))

    assert response["statusCode"] == 204
    assert response["body"] == ""
    assert "abc123" not in store


def test_delete_legacy_item_matches_version_one(handler_and_store):
    """A legacy item with no version is deleted by If-Match: 1."""
    handler_module, store = handler_and_store
    store["legacy1"] = {"id": "legacy1", "title": "Old", "content": "legacy"}

    response = handler_module.handler(_delete_event("legacy1"))

    assert response["statusCode"] == 204
    assert "legacy1" not in store


def test_delete_case_insensitive_if_match(handler_and_store):
    """The If-Match header is resolved case-insensitively."""
    handler_module, store = handler_and_store
    store["abc123"] = {
        "id": "abc123",
        "title": "Note",
        "content": "body",
        "version": 2,
    }

    response = handler_module.handler(
        _delete_event("abc123", {"IF-MATCH": "2"})
    )

    assert response["statusCode"] == 204


def test_delete_if_match_after_other_headers(handler_and_store):
    """If-Match is found even when other headers precede it in iteration order."""
    handler_module, store = handler_and_store
    store["abc123"] = {
        "id": "abc123",
        "title": "Note",
        "content": "body",
        "version": 1,
    }

    response = handler_module.handler(
        _delete_event("abc123", {"Accept": "application/json", "If-Match": "1"})
    )

    assert response["statusCode"] == 204


def test_delete_missing_id(handler_and_store):
    """A missing path parameter returns 400 without deleting."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(
        {"httpMethod": "DELETE", "headers": {"If-Match": "1"}}
    )
    assert response["statusCode"] == 400


def test_delete_missing_if_match(handler_and_store):
    """A missing If-Match header returns 400."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(_delete_event("abc123", {}))
    assert response["statusCode"] == 400


@pytest.mark.parametrize("value", ["0", "-2", "x", "2.0", '"2"', "", "  "])
def test_delete_invalid_if_match(handler_and_store, value):
    """A non-positive-integer If-Match value returns 400."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(
        _delete_event("abc123", {"If-Match": value})
    )
    assert response["statusCode"] == 400


def test_delete_missing_item_returns_404(handler_and_store):
    """A conditional failure with no stored item maps to 404."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(_delete_event("missing"))
    assert response["statusCode"] == 404


def test_delete_stale_version_returns_409(handler_and_store):
    """A conditional failure with a stored item maps to 409 and keeps the note."""
    handler_module, store = handler_and_store
    store["abc123"] = {
        "id": "abc123",
        "title": "Note",
        "content": "body",
        "version": 7,
    }

    response = handler_module.handler(
        _delete_event("abc123", {"If-Match": "1"})
    )

    assert response["statusCode"] == 409
    assert "abc123" in store


def test_delete_reraises_unexpected_error(handler_and_store, monkeypatch):
    """A non-conditional ClientError is re-raised, not swallowed."""
    handler_module, _store = handler_and_store
    other = ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "x"}},
        "DeleteItem",
    )

    class FailingTable:  # pylint: disable=too-few-public-methods  # test double
        """Table double whose delete always raises an unexpected error."""

        def delete_item(self, **_kwargs):
            """Raise a non-conditional ClientError."""
            raise other

    monkeypatch.setattr(handler_module, "_table", FailingTable)

    with pytest.raises(ClientError) as excinfo:
        handler_module.handler(_delete_event("abc123"))
    assert excinfo.value.response["Error"]["Code"] == "ResourceNotFoundException"


def test_table_uses_configured_name(monkeypatch):
    """The table factory uses TABLE_NAME from the environment."""
    monkeypatch.setenv("TABLE_NAME", "some-table")
    module = load_lambda_handler("notes_delete", "notes_delete_real")
    assert_configured_table_name(module, monkeypatch)
