"""Unit tests for the single-purpose ``PATCH /notes/{id}`` Lambda."""

import json

import pytest
from botocore.exceptions import ClientError

from tests.unit.lambda_test_utils import (
    assert_configured_table_name,
    load_lambda_handler,
)

_CONDITIONAL_FAILURE = ClientError(
    {"Error": {"Code": "ConditionalCheckFailedException", "Message": "no"}},
    "UpdateItem",
)


@pytest.fixture(name="handler_and_store")
def _handler_and_store(monkeypatch):
    """Load the update handler with an in-memory table that enforces the condition."""
    monkeypatch.setenv("TABLE_NAME", "test-table")
    module = load_lambda_handler("notes_update", "notes_update_handler")
    store = {}

    class FakeTable:
        """In-memory DynamoDB double honouring the version condition expression."""

        def get_item(self, **kwargs):
            """Return an item from the in-memory store when present."""
            item = store.get(kwargs["Key"]["id"])
            return {"Item": item} if item else {}

        def update_item(self, **kwargs):
            """Apply the atomic conditional update or raise on a condition miss."""
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
            updated = dict(item)
            base = current if current is not None else initial
            updated["version"] = base + values[":increment"]
            for token, field in kwargs["ExpressionAttributeNames"].items():
                updated[field] = values[token.replace("#f", ":v")]
            store[note_id] = updated
            return {"Attributes": updated}

    monkeypatch.setattr(module, "_table", FakeTable)
    return module, store


def _patch_event(note_id, body, headers=None):
    return {
        "httpMethod": "PATCH",
        "path": f"/notes/{note_id}",
        "pathParameters": {"id": note_id},
        "headers": headers if headers is not None else {"If-Match": "1"},
        "body": body,
    }


def test_update_success(handler_and_store):
    """A matching version updates supplied fields and bumps the version."""
    handler_module, store = handler_and_store
    store["abc123"] = {
        "id": "abc123",
        "title": "Old",
        "content": "old body",
        "version": 1,
    }

    response = handler_module.handler(
        _patch_event("abc123", json.dumps({"title": "New"}))
    )

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {
        "id": "abc123",
        "title": "New",
        "content": "old body",
        "version": 2,
    }
    assert store["abc123"]["version"] == 2


def test_update_both_fields(handler_and_store):
    """Both title and content may be updated in one request."""
    handler_module, store = handler_and_store
    store["abc123"] = {
        "id": "abc123",
        "title": "Old",
        "content": "old",
        "version": 2,
    }

    response = handler_module.handler(
        _patch_event(
            "abc123",
            json.dumps({"title": "T", "content": "C"}),
            {"If-Match": "2"},
        )
    )

    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["title"] == "T"
    assert body["content"] == "C"
    assert body["version"] == 3


def test_update_legacy_item_becomes_version_two(handler_and_store):
    """A legacy item with no version matches If-Match: 1 and becomes version 2."""
    handler_module, store = handler_and_store
    store["legacy1"] = {"id": "legacy1", "title": "Old", "content": "legacy"}

    response = handler_module.handler(
        _patch_event("legacy1", json.dumps({"content": "updated"}))
    )

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["version"] == 2
    assert body["content"] == "updated"


def test_update_case_insensitive_if_match(handler_and_store):
    """The If-Match header is resolved case-insensitively."""
    handler_module, store = handler_and_store
    store["abc123"] = {
        "id": "abc123",
        "title": "Old",
        "content": "old",
        "version": 1,
    }

    response = handler_module.handler(
        _patch_event("abc123", json.dumps({"title": "New"}), {"if-match": "1"})
    )

    assert response["statusCode"] == 200


def test_update_if_match_after_other_headers(handler_and_store):
    """If-Match is found even when other headers precede it in iteration order."""
    handler_module, store = handler_and_store
    store["abc123"] = {
        "id": "abc123",
        "title": "Old",
        "content": "old",
        "version": 1,
    }

    response = handler_module.handler(
        _patch_event(
            "abc123",
            json.dumps({"title": "New"}),
            {"Content-Type": "application/json", "If-Match": "1"},
        )
    )

    assert response["statusCode"] == 200


def test_update_missing_id(handler_and_store):
    """A missing path parameter returns 400 without touching the table."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(
        {"httpMethod": "PATCH", "headers": {"If-Match": "1"}, "body": "{}"}
    )
    assert response["statusCode"] == 400


def test_update_missing_if_match(handler_and_store):
    """A missing If-Match header returns 400."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(
        _patch_event("abc123", json.dumps({"title": "x"}), {})
    )
    assert response["statusCode"] == 400


@pytest.mark.parametrize("value", ["0", "-1", "abc", "1.5", '"1"', "", "  "])
def test_update_invalid_if_match(handler_and_store, value):
    """A non-positive-integer If-Match value returns 400."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(
        _patch_event("abc123", json.dumps({"title": "x"}), {"If-Match": value})
    )
    assert response["statusCode"] == 400


def test_update_rejects_bad_json(handler_and_store):
    """Malformed JSON returns 400."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(_patch_event("abc123", "{not json"))
    assert response["statusCode"] == 400


def test_update_rejects_non_object_body(handler_and_store):
    """A JSON value that is not an object returns 400."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(
        _patch_event("abc123", json.dumps(["nope"]))
    )
    assert response["statusCode"] == 400


def test_update_rejects_no_supported_fields(handler_and_store):
    """A body without title or content returns 400."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(_patch_event("abc123", json.dumps({})))
    assert response["statusCode"] == 400


def test_update_rejects_unknown_field(handler_and_store):
    """An unsupported field returns 400."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(
        _patch_event("abc123", json.dumps({"title": "x", "extra": 1}))
    )
    assert response["statusCode"] == 400


def test_update_rejects_empty_value(handler_and_store):
    """A whitespace-only value returns 400."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(
        _patch_event("abc123", json.dumps({"title": "   "}))
    )
    assert response["statusCode"] == 400


def test_update_rejects_non_string_value(handler_and_store):
    """A non-string value returns 400."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(
        _patch_event("abc123", json.dumps({"content": 5}))
    )
    assert response["statusCode"] == 400


def test_update_missing_item_returns_404(handler_and_store):
    """A conditional failure with no stored item maps to 404."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(
        _patch_event("missing", json.dumps({"title": "x"}))
    )
    assert response["statusCode"] == 404


def test_update_stale_version_returns_409(handler_and_store):
    """A conditional failure with a stored item maps to 409 and leaves it intact."""
    handler_module, store = handler_and_store
    store["abc123"] = {
        "id": "abc123",
        "title": "Old",
        "content": "old",
        "version": 5,
    }

    response = handler_module.handler(
        _patch_event("abc123", json.dumps({"title": "x"}), {"If-Match": "1"})
    )

    assert response["statusCode"] == 409
    assert store["abc123"]["title"] == "Old"
    assert store["abc123"]["version"] == 5


def test_update_reraises_unexpected_error(handler_and_store, monkeypatch):
    """A non-conditional ClientError is re-raised, not swallowed."""
    handler_module, _store = handler_and_store
    other = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}},
        "UpdateItem",
    )

    class FailingTable:  # pylint: disable=too-few-public-methods  # test double
        """Table double whose update always raises an unexpected error."""

        def update_item(self, **_kwargs):
            """Raise a non-conditional ClientError."""
            raise other

    monkeypatch.setattr(handler_module, "_table", FailingTable)

    with pytest.raises(ClientError) as excinfo:
        handler_module.handler(_patch_event("abc123", json.dumps({"title": "x"})))
    assert (
        excinfo.value.response["Error"]["Code"]
        == "ProvisionedThroughputExceededException"
    )


def test_table_uses_configured_name(monkeypatch):
    """The table factory uses TABLE_NAME from the environment."""
    monkeypatch.setenv("TABLE_NAME", "some-table")
    module = load_lambda_handler("notes_update", "notes_update_real")
    assert_configured_table_name(module, monkeypatch)
