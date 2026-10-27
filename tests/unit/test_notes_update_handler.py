"""Unit tests for the single-purpose ``PATCH /notes/{id}`` Lambda."""

import json

import pytest
from botocore.exceptions import ClientError

from tests.unit.lambda_test_utils import (
    assert_configured_table_name,
    load_lambda_handler,
)

_CONDITIONAL_FAILURE = ClientError(
    {"Error": {"Code": "ConditionalCheckFailedException", "Message": "stale"}},
    "UpdateItem",
)


class FakeTable:
    """In-memory DynamoDB double honouring the handler's version condition."""

    def __init__(self, store):
        self._store = store

    def get_item(self, **kwargs):
        """Return a stored item when present."""
        item = self._store.get(kwargs["Key"]["id"])
        return {"Item": item} if item else {}

    def update_item(self, **kwargs):
        """Apply the conditional update or raise ConditionalCheckFailedException."""
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
        updated = dict(item)
        base = current if current is not None else initial
        updated["version"] = base + values[":increment"]
        for key, value in values.items():
            if key in (":expected", ":initial", ":increment"):
                continue
            updated[key.lstrip(":")] = value
        self._store[note_id] = updated
        return {"Attributes": updated}


@pytest.fixture(name="handler_and_store")
def _handler_and_store(monkeypatch):
    """Load the update handler with an in-memory DynamoDB table."""
    monkeypatch.setenv("TABLE_NAME", "test-table")
    module = load_lambda_handler("notes_update", "notes_update_handler")
    store = {}
    monkeypatch.setattr(module, "_table", lambda: FakeTable(store))
    return module, store


def _patch_event(note_id, body, if_match=None, header_name="If-Match"):
    event = {
        "httpMethod": "PATCH",
        "path": f"/notes/{note_id}",
        "pathParameters": {"id": note_id},
        "body": body,
    }
    if if_match is not None:
        event["headers"] = {header_name: if_match}
    return event


def test_update_success_increments_version(handler_and_store):
    """A matching If-Match updates fields and increments version by one."""
    handler_module, store = handler_and_store
    store["abc123"] = {
        "id": "abc123",
        "title": "Old",
        "content": "old body",
        "version": 3,
    }

    response = handler_module.handler(
        _patch_event("abc123", json.dumps({"title": "New"}), "3")
    )

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body == {
        "id": "abc123",
        "title": "New",
        "content": "old body",
        "version": 4,
    }
    assert store["abc123"]["version"] == 4


def test_update_both_fields(handler_and_store):
    """Both title and content can be updated together."""
    handler_module, store = handler_and_store
    store["id1"] = {"id": "id1", "title": "A", "content": "B", "version": 1}

    response = handler_module.handler(
        _patch_event("id1", json.dumps({"title": "X", "content": "Y"}), "1")
    )

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["title"] == "X"
    assert body["content"] == "Y"
    assert body["version"] == 2


def test_update_legacy_item_matches_version_one(handler_and_store):
    """A legacy item with no version matches If-Match 1 and becomes version 2."""
    handler_module, store = handler_and_store
    store["legacy"] = {"id": "legacy", "title": "Old", "content": "no version"}

    response = handler_module.handler(
        _patch_event("legacy", json.dumps({"content": "updated"}), "1")
    )

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["version"] == 2
    assert body["content"] == "updated"


def test_update_case_insensitive_if_match_header(handler_and_store):
    """The If-Match header is read case-insensitively."""
    handler_module, store = handler_and_store
    store["id1"] = {"id": "id1", "title": "A", "content": "B", "version": 2}

    response = handler_module.handler(
        _patch_event("id1", json.dumps({"title": "C"}), "2", header_name="if-match")
    )

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["version"] == 3


def test_update_missing_id(handler_and_store):
    """Missing path parameters return 400."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(
        {"httpMethod": "PATCH", "body": json.dumps({"title": "x"})}
    )
    assert response["statusCode"] == 400


def test_update_missing_if_match(handler_and_store):
    """A missing If-Match header returns 400."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(
        _patch_event("id1", json.dumps({"title": "x"}))
    )
    assert response["statusCode"] == 400


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "1.5", "  ", "2x"])
def test_update_invalid_if_match(handler_and_store, bad):
    """Non-positive-integer If-Match values return 400."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(
        _patch_event("id1", json.dumps({"title": "x"}), bad)
    )
    assert response["statusCode"] == 400


def test_update_non_string_if_match(handler_and_store):
    """A non-string If-Match header value returns 400."""
    handler_module, _store = handler_and_store
    event = _patch_event("id1", json.dumps({"title": "x"}))
    event["headers"] = {"If-Match": 5}
    response = handler_module.handler(event)
    assert response["statusCode"] == 400


def test_update_skips_unrelated_and_non_string_header_keys(handler_and_store):
    """Header lookup skips non-matching and non-string keys to find If-Match."""
    handler_module, store = handler_and_store
    store["id1"] = {"id": "id1", "title": "A", "content": "B", "version": 1}
    event = _patch_event("id1", json.dumps({"title": "C"}))
    event["headers"] = {"Content-Type": "application/json", 42: "x", "If-Match": "1"}
    response = handler_module.handler(event)
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["version"] == 2


def test_update_bad_json(handler_and_store):
    """Malformed JSON returns 400."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(_patch_event("id1", "{not json", "1"))
    assert response["statusCode"] == 400


def test_update_empty_body(handler_and_store):
    """An empty JSON object (no supported fields) returns 400."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(_patch_event("id1", json.dumps({}), "1"))
    assert response["statusCode"] == 400


def test_update_non_object_body(handler_and_store):
    """A JSON value other than an object returns 400."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(
        _patch_event("id1", json.dumps(["not", "object"]), "1")
    )
    assert response["statusCode"] == 400


def test_update_unknown_field(handler_and_store):
    """An unsupported field returns 400 without modifying the note."""
    handler_module, store = handler_and_store
    store["id1"] = {"id": "id1", "title": "A", "content": "B", "version": 1}
    response = handler_module.handler(
        _patch_event("id1", json.dumps({"title": "X", "extra": 1}), "1")
    )
    assert response["statusCode"] == 400
    assert store["id1"]["title"] == "A"


def test_update_empty_value(handler_and_store):
    """A whitespace-only value returns 400."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(
        _patch_event("id1", json.dumps({"title": "   "}), "1")
    )
    assert response["statusCode"] == 400


def test_update_missing_item_returns_404(handler_and_store):
    """A conditional failure with no stored item maps to 404."""
    handler_module, _store = handler_and_store
    response = handler_module.handler(
        _patch_event("missing", json.dumps({"title": "x"}), "1")
    )
    assert response["statusCode"] == 404


def test_update_stale_version_returns_409(handler_and_store):
    """A conditional failure with a stored item maps to 409 and is not modified."""
    handler_module, store = handler_and_store
    store["id1"] = {"id": "id1", "title": "A", "content": "B", "version": 5}
    response = handler_module.handler(
        _patch_event("id1", json.dumps({"title": "X"}), "3")
    )
    assert response["statusCode"] == 409
    assert store["id1"]["title"] == "A"
    assert store["id1"]["version"] == 5


def test_update_reraises_unexpected_error(handler_and_store, monkeypatch):
    """A non-conditional AWS error is re-raised rather than mapped to a response."""
    handler_module, _store = handler_and_store

    def _raise(**_kwargs):
        raise ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException"}},
            "UpdateItem",
        )

    class BrokenTable:  # pylint: disable=too-few-public-methods  # test double
        """Table whose update_item raises an unexpected error."""

        update_item = staticmethod(_raise)

    monkeypatch.setattr(handler_module, "_table", BrokenTable)
    with pytest.raises(ClientError):
        handler_module.handler(_patch_event("id1", json.dumps({"title": "x"}), "1"))


def test_table_uses_configured_name(monkeypatch):
    """The table factory uses TABLE_NAME from the environment."""
    monkeypatch.setenv("TABLE_NAME", "some-table")
    module = load_lambda_handler("notes_update", "notes_update_real")
    assert_configured_table_name(module, monkeypatch)
