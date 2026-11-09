"""Unit tests for the single-purpose ``PATCH /notes/{id}`` Lambda."""

# pylint: disable=duplicate-code  # parallel update/delete test doubles

import json

import pytest
from botocore.exceptions import ClientError

from tests.unit.lambda_test_utils import (
    assert_configured_table_name,
    load_lambda_handler,
)


_STRICT_CONDITION = "attribute_exists(id) AND #version = :expected"
_LEGACY_CONDITION = (
    "attribute_exists(id) AND (#version = :expected OR attribute_not_exists(#version))"
)

_CONSISTENT_GET = {"Key": {"id": "n1"}, "ConsistentRead": True}


def _client_error(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "UpdateItem")


@pytest.fixture(name="handler_and_table")
def _handler_and_table(monkeypatch):
    """Load the update handler with an in-memory table honoring the version check."""
    monkeypatch.setenv("TABLE_NAME", "test-table")
    module = load_lambda_handler("notes_update", "notes_update_handler")

    class FakeTable:
        """DynamoDB table double evaluating the version condition in memory."""

        def __init__(self):
            self.store = {}
            self.update_calls = []
            self.get_calls = []
            self.errors = []

        def update_item(self, **kwargs):
            """Apply the update only when the existence/version condition holds."""
            self.update_calls.append(kwargs)
            if self.errors:
                raise self.errors[0]
            item = self.store.get(kwargs["Key"]["id"])
            values = kwargs["ExpressionAttributeValues"]
            legacy_ok = kwargs["ConditionExpression"] == _LEGACY_CONDITION
            matches = item is not None and (
                item.get("version") == values[":expected"]
                or ("version" not in item and legacy_ok)
            )
            if not matches:
                raise _client_error("ConditionalCheckFailedException")
            for key in ("title", "content"):
                if f":{key}" in values:
                    item[key] = values[f":{key}"]
            item["version"] = item.get("version", values[":initial"]) + 1
            return {"Attributes": dict(item)}

        def get_item(self, **kwargs):
            """Return an item from the in-memory store when present."""
            self.get_calls.append(kwargs)
            item = self.store.get(kwargs["Key"]["id"])
            return {"Item": item} if item else {}

    table = FakeTable()
    monkeypatch.setattr(module, "_table", lambda: table)
    return module, table


def _patch_event(body, if_match="3", note_id="n1", header="If-Match"):
    headers = {} if if_match is None else {header: if_match}
    return {
        "httpMethod": "PATCH",
        "pathParameters": {"id": note_id},
        "headers": headers,
        "body": body if isinstance(body, str) or body is None else json.dumps(body),
    }


def _note(version=3):
    note = {"id": "n1", "title": "Old", "content": "Old text"}
    if version is not None:
        note["version"] = version
    return note


def test_update_title_success(handler_and_table):
    """A matching version updates the title and increments the version."""
    module, table = handler_and_table
    table.store["n1"] = _note(3)

    response = module.handler(_patch_event({"title": "New"}))

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {
        "id": "n1",
        "title": "New",
        "content": "Old text",
        "version": 4,
    }
    assert table.store["n1"]["version"] == 4
    assert len(table.update_calls) == 1
    assert table.get_calls == []
    call = table.update_calls[0]
    assert call["ReturnValues"] == "ALL_NEW"
    assert call["Key"] == {"id": "n1"}
    assert call["ConditionExpression"] == _STRICT_CONDITION
    assert call["UpdateExpression"] == (
        "SET #title = :title, #version = if_not_exists(#version, :initial) + :increment"
    )
    assert call["ExpressionAttributeNames"] == {
        "#version": "version",
        "#title": "title",
    }
    assert call["ExpressionAttributeValues"] == {
        ":expected": 3,
        ":initial": 1,
        ":increment": 1,
        ":title": "New",
    }


def test_update_both_fields_with_lowercase_header(handler_and_table):
    """Header names are case-insensitive and both fields can be updated."""
    module, table = handler_and_table
    table.store["n1"] = _note(3)

    response = module.handler(
        _patch_event({"title": "T", "content": "C"}, header="if-match")
    )

    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert (body["title"], body["content"], body["version"]) == ("T", "C", 4)


def test_update_legacy_note_matches_version_one(handler_and_table):
    """A note without a stored version matches If-Match 1 and becomes version 2."""
    module, table = handler_and_table
    table.store["n1"] = _note(None)

    response = module.handler(_patch_event({"content": "C"}, if_match=" 1 "))

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["version"] == 2
    assert table.store["n1"]["version"] == 2
    call = table.update_calls[0]
    assert call["ConditionExpression"] == _LEGACY_CONDITION
    assert call["ExpressionAttributeValues"][":expected"] == 1


def test_update_legacy_note_rejects_other_version(handler_and_table):
    """A legacy note does not match If-Match 2 and is left unmodified."""
    module, table = handler_and_table
    table.store["n1"] = _note(None)

    response = module.handler(_patch_event({"content": "C"}, if_match="2"))

    assert response["statusCode"] == 409
    assert "version" not in table.store["n1"]
    assert table.store["n1"]["content"] == "Old text"


def test_update_stale_version_returns_409(handler_and_table):
    """A differing version returns 409 and does not modify the note."""
    module, table = handler_and_table
    table.store["n1"] = _note(5)

    response = module.handler(_patch_event({"title": "New"}, if_match="3"))

    assert response["statusCode"] == 409
    assert table.store["n1"] == _note(5)
    assert len(table.update_calls) == 1
    assert table.get_calls == [_CONSISTENT_GET]


def test_update_missing_note_returns_404(handler_and_table):
    """A missing note returns 404."""
    module, table = handler_and_table

    response = module.handler(_patch_event({"title": "New"}))

    assert response["statusCode"] == 404
    assert table.get_calls == [_CONSISTENT_GET]


@pytest.mark.parametrize(
    "if_match", [None, "", "abc", "0", "-1", "1.5", "+2", '"3"', "٣", "9" * 19]
)
def test_update_rejects_invalid_if_match(handler_and_table, if_match):
    """Missing or invalid If-Match is a 400 and never touches the table."""
    module, table = handler_and_table
    table.store["n1"] = _note(3)

    response = module.handler(_patch_event({"title": "New"}, if_match=if_match))

    assert response["statusCode"] == 400
    assert table.update_calls == []
    assert table.store["n1"] == _note(3)


def test_update_rejects_non_string_if_match(handler_and_table):
    """A non-string If-Match header value is rejected."""
    module, table = handler_and_table
    response = module.handler(_patch_event({"title": "New"}, if_match=3))
    assert response["statusCode"] == 400
    assert table.update_calls == []


@pytest.mark.parametrize(
    "body",
    [
        None,
        "",
        "{not json",
        "[]",
        "{}",
        '{"title": "x", "extra": "y"}',
        '{"id": "other"}',
        '{"version": 9}',
        '{"title": ""}',
        '{"title": "   "}',
        '{"content": 5}',
        '{"title": null}',
    ],
)
def test_update_rejects_invalid_body(handler_and_table, body):
    """Malformed bodies, unknown fields and invalid values return 400."""
    module, table = handler_and_table
    table.store["n1"] = _note(3)

    response = module.handler(_patch_event(body))

    assert response["statusCode"] == 400
    assert table.update_calls == []
    assert table.store["n1"] == _note(3)


@pytest.mark.parametrize("path_parameters", [None, {}, {"id": ""}])
def test_update_rejects_missing_id(handler_and_table, path_parameters):
    """A missing or empty path id is a 400."""
    module, table = handler_and_table
    event = _patch_event({"title": "New"})
    event["pathParameters"] = path_parameters

    assert module.handler(event)["statusCode"] == 400
    assert table.update_calls == []


def test_update_handles_missing_headers(handler_and_table):
    """An event with null headers is treated as missing If-Match."""
    module, _table = handler_and_table
    event = _patch_event({"title": "New"})
    event["headers"] = None
    assert module.handler(event)["statusCode"] == 400


def test_update_reraises_unexpected_aws_error(handler_and_table):
    """Non-conditional AWS errors are not swallowed."""
    module, table = handler_and_table
    table.errors = [_client_error("ProvisionedThroughputExceededException")]

    with pytest.raises(ClientError):
        module.handler(_patch_event({"title": "New"}))
    assert table.get_calls == []


def test_update_reraises_error_without_code(handler_and_table):
    """A ClientError with an empty response is still re-raised."""
    module, table = handler_and_table
    table.errors = [ClientError({}, "UpdateItem")]

    with pytest.raises(ClientError):
        module.handler(_patch_event({"title": "New"}))


def test_table_uses_configured_name(monkeypatch):
    """The table factory uses TABLE_NAME from the environment."""
    monkeypatch.setenv("TABLE_NAME", "some-table")
    module = load_lambda_handler("notes_update", "notes_update_real")
    assert_configured_table_name(module, monkeypatch)
