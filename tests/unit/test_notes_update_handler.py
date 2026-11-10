"""Unit tests for the single-purpose ``PATCH /notes/{id}`` Lambda."""

# pylint: disable=duplicate-code  # parallel tests for parallel, independent handlers

import json

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
    """Load the update handler with an in-memory table honouring the condition."""
    monkeypatch.setenv("TABLE_NAME", "test-table")
    module = load_lambda_handler("notes_update", "notes_update_handler")
    store = {}
    calls = {"update": 0, "get": 0, "error": None}

    class FakeTable:
        """In-memory DynamoDB table double emulating the version condition."""

        def update_item(self, **kwargs):
            """Apply the update only when the version condition holds."""
            calls["update"] += 1
            if calls["error"]:
                raise calls["error"]  # pylint: disable=raising-bad-type
            item = store.get(kwargs["Key"]["id"])
            values = kwargs["ExpressionAttributeValues"]
            names = kwargs["ExpressionAttributeNames"]
            assert kwargs["ConditionExpression"].startswith("attribute_exists(id)")
            assert kwargs["ReturnValues"] == "ALL_NEW"
            current = None if item is None else item.get("version", values[":initial"])
            if item is None or current != values[":expected"]:
                raise _client_error("ConditionalCheckFailedException")
            for placeholder, value in values.items():
                if placeholder.startswith(":f"):
                    item[names["#f" + placeholder[2:]]] = value
            item["version"] = current + values[":increment"]
            return {"Attributes": dict(item)}

        def get_item(self, **kwargs):
            """Return an item when present."""
            calls["get"] += 1
            item = store.get(kwargs["Key"]["id"])
            return {"Item": item} if item else {}

    monkeypatch.setattr(module, "_table", FakeTable)
    return module, store, calls


def _event(note_id="n1", headers=None, body=None):
    return {
        "httpMethod": "PATCH",
        "pathParameters": {"id": note_id} if note_id is not None else None,
        "headers": {"If-Match": "1"} if headers is None else headers,
        "body": json.dumps({"title": "New"}) if body is None else body,
    }


def _note(version=1):
    note = {"id": "n1", "title": "Old", "content": "Text"}
    if version is not None:
        note["version"] = version
    return note


def test_update_title_success(handler_and_store):
    """A matching version updates the field and increments the version by one."""
    module, store, _calls = handler_and_store
    store["n1"] = _note(3)
    response = module.handler(_event(headers={"If-Match": "3"}))
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {
        "id": "n1",
        "title": "New",
        "content": "Text",
        "version": 4,
    }
    assert store["n1"]["version"] == 4


def test_update_title_and_content_with_lowercase_header(handler_and_store):
    """Header lookup is case-insensitive and both fields can be updated."""
    module, store, _calls = handler_and_store
    store["n1"] = _note(1)
    response = module.handler(
        _event(headers={"if-match": "1"}, body=json.dumps({"title": "A", "content": "B"}))
    )
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert (body["title"], body["content"], body["version"]) == ("A", "B", 2)


def test_update_legacy_item_without_version(handler_and_store):
    """A legacy item matches If-Match 1 and becomes version 2."""
    module, store, _calls = handler_and_store
    store["n1"] = _note(None)
    response = module.handler(_event())
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["version"] == 2


def test_update_legacy_item_rejects_other_version(handler_and_store):
    """A legacy item does not match any version other than 1."""
    module, store, _calls = handler_and_store
    store["n1"] = _note(None)
    response = module.handler(_event(headers={"If-Match": "2"}))
    assert response["statusCode"] == 409
    assert "version" not in store["n1"]


def test_update_stale_version_returns_409(handler_and_store):
    """A stale version returns 409 and leaves the note unchanged."""
    module, store, calls = handler_and_store
    store["n1"] = _note(5)
    response = module.handler(_event(headers={"If-Match": "4"}))
    assert response["statusCode"] == 409
    assert store["n1"] == _note(5)
    assert calls["update"] == 1
    assert calls["get"] == 1


def test_update_missing_note_returns_404(handler_and_store):
    """A missing note returns 404."""
    module, _store, calls = handler_and_store
    response = module.handler(_event())
    assert response["statusCode"] == 404
    assert calls["update"] == 1
    assert calls["get"] == 1


def test_update_unexpected_error_is_raised(handler_and_store):
    """Non-conditional AWS errors propagate and do no follow-up read."""
    module, _store, calls = handler_and_store
    calls["error"] = _client_error("ProvisionedThroughputExceededException")
    with pytest.raises(ClientError):
        module.handler(_event())
    assert calls["get"] == 0


@pytest.mark.parametrize("note_id", [None, ""])
def test_update_requires_id(handler_and_store, note_id):
    """A missing or empty path id returns 400."""
    module, _store, calls = handler_and_store
    assert module.handler(_event(note_id=note_id))["statusCode"] == 400
    assert calls["update"] == 0


@pytest.mark.parametrize(
    "headers",
    [
        {},
        None,
        {"If-Match": "0"},
        {"If-Match": "-1"},
        {"If-Match": "abc"},
        {"If-Match": '"1"'},
        {"If-Match": "1.5"},
        {"If-Match": " 1"},
        {"If-Match": ""},
        {"If-Match": "\u0661"},
        {"If-Match": 1},
        {"Other": "1"},
    ],
)
def test_update_rejects_invalid_if_match(handler_and_store, headers):
    """Missing or invalid If-Match returns 400 without touching the table."""
    module, store, calls = handler_and_store
    store["n1"] = _note(1)
    event = _event()
    if headers is None:
        del event["headers"]
    else:
        event["headers"] = headers
    assert module.handler(event)["statusCode"] == 400
    assert calls["update"] == 0
    assert store["n1"] == _note(1)


@pytest.mark.parametrize(
    "body",
    [
        None,
        "",
        "{not json",
        json.dumps(["title"]),
        json.dumps({}),
        json.dumps({"unknown": "x"}),
        json.dumps({"title": "ok", "version": 9}),
        json.dumps({"title": "   "}),
        json.dumps({"content": ""}),
        json.dumps({"title": 5}),
    ],
)
def test_update_rejects_invalid_body(handler_and_store, body):
    """Malformed JSON, unsupported fields and invalid values return 400."""
    module, store, calls = handler_and_store
    store["n1"] = _note(1)
    event = _event()
    event["body"] = body
    assert module.handler(event)["statusCode"] == 400
    assert calls["update"] == 0
    assert store["n1"] == _note(1)


def test_table_uses_configured_name(monkeypatch):
    """The table factory uses TABLE_NAME from the environment."""
    monkeypatch.setenv("TABLE_NAME", "some-table")
    module = load_lambda_handler("notes_update", "notes_update_real")
    assert_configured_table_name(module, monkeypatch)
