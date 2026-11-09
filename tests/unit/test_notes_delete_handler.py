"""Unit tests for the single-purpose ``DELETE /notes/{id}`` Lambda."""

# pylint: disable=duplicate-code  # parallel update/delete test doubles

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
    return ClientError({"Error": {"Code": code, "Message": code}}, "DeleteItem")


@pytest.fixture(name="handler_and_table")
def _handler_and_table(monkeypatch):
    """Load the delete handler with an in-memory table honoring the version check."""
    monkeypatch.setenv("TABLE_NAME", "test-table")
    module = load_lambda_handler("notes_delete", "notes_delete_handler")

    class FakeTable:
        """DynamoDB table double evaluating the version condition in memory."""

        def __init__(self):
            self.store = {}
            self.delete_calls = []
            self.get_calls = []
            self.errors = []

        def delete_item(self, **kwargs):
            """Delete only when the existence/version condition holds."""
            self.delete_calls.append(kwargs)
            if self.errors:
                raise self.errors[0]
            item = self.store.get(kwargs["Key"]["id"])
            expected = kwargs["ExpressionAttributeValues"][":expected"]
            legacy_ok = kwargs["ConditionExpression"] == _LEGACY_CONDITION
            matches = item is not None and (
                item.get("version") == expected
                or ("version" not in item and legacy_ok)
            )
            if not matches:
                raise _client_error("ConditionalCheckFailedException")
            return {"Attributes": self.store.pop(kwargs["Key"]["id"])}

        def get_item(self, **kwargs):
            """Return an item from the in-memory store when present."""
            self.get_calls.append(kwargs)
            item = self.store.get(kwargs["Key"]["id"])
            return {"Item": item} if item else {}

    table = FakeTable()
    monkeypatch.setattr(module, "_table", lambda: table)
    return module, table


def _delete_event(if_match="4", note_id="n1", header="If-Match"):
    headers = {} if if_match is None else {header: if_match}
    return {
        "httpMethod": "DELETE",
        "pathParameters": {"id": note_id},
        "headers": headers,
    }


def _note(version=4):
    note = {"id": "n1", "title": "T", "content": "C"}
    if version is not None:
        note["version"] = version
    return note


def test_delete_success(handler_and_table):
    """A matching version deletes the note and returns an empty 204."""
    module, table = handler_and_table
    table.store["n1"] = _note(4)

    response = module.handler(_delete_event())

    assert response["statusCode"] == 204
    assert response["body"] == ""
    assert "n1" not in table.store
    assert len(table.delete_calls) == 1
    assert table.delete_calls[0]["ReturnValues"] == "ALL_OLD"
    assert table.delete_calls[0] == {
        "Key": {"id": "n1"},
        "ConditionExpression": _STRICT_CONDITION,
        "ExpressionAttributeNames": {"#version": "version"},
        "ExpressionAttributeValues": {":expected": 4},
        "ReturnValues": "ALL_OLD",
    }
    assert table.get_calls == []


def test_delete_with_lowercase_header(handler_and_table):
    """The If-Match header name is case-insensitive."""
    module, table = handler_and_table
    table.store["n1"] = _note(4)

    assert module.handler(_delete_event(header="IF-MATCH"))["statusCode"] == 204


def test_delete_legacy_note_matches_version_one(handler_and_table):
    """A note without a stored version is deleted with If-Match 1."""
    module, table = handler_and_table
    table.store["n1"] = _note(None)

    response = module.handler(_delete_event(if_match="1"))

    assert response["statusCode"] == 204
    assert "n1" not in table.store
    assert table.delete_calls[0]["ConditionExpression"] == _LEGACY_CONDITION
    assert table.delete_calls[0]["ExpressionAttributeValues"] == {":expected": 1}


def test_delete_legacy_note_rejects_other_version(handler_and_table):
    """A legacy note is not deleted with If-Match 2."""
    module, table = handler_and_table
    table.store["n1"] = _note(None)

    response = module.handler(_delete_event(if_match="2"))

    assert response["statusCode"] == 409
    assert "n1" in table.store


def test_delete_stale_version_returns_409(handler_and_table):
    """A differing version returns 409 and keeps the note."""
    module, table = handler_and_table
    table.store["n1"] = _note(5)

    response = module.handler(_delete_event(if_match="4"))

    assert response["statusCode"] == 409
    assert table.store["n1"] == _note(5)
    assert table.get_calls == [_CONSISTENT_GET]


def test_delete_missing_note_returns_404(handler_and_table):
    """A missing note returns 404."""
    module, table = handler_and_table

    response = module.handler(_delete_event())

    assert response["statusCode"] == 404
    assert table.get_calls == [_CONSISTENT_GET]


@pytest.mark.parametrize(
    "if_match", [None, "", "abc", "0", "-1", "1.5", "+2", '"4"', "٣", "9" * 19]
)
def test_delete_rejects_invalid_if_match(handler_and_table, if_match):
    """Missing or invalid If-Match is a 400 and never deletes anything."""
    module, table = handler_and_table
    table.store["n1"] = _note(4)

    response = module.handler(_delete_event(if_match=if_match))

    assert response["statusCode"] == 400
    assert table.delete_calls == []
    assert "n1" in table.store


def test_delete_rejects_non_string_if_match(handler_and_table):
    """A non-string If-Match header value is rejected."""
    module, table = handler_and_table
    assert module.handler(_delete_event(if_match=4))["statusCode"] == 400
    assert table.delete_calls == []


@pytest.mark.parametrize("path_parameters", [None, {}, {"id": ""}])
def test_delete_rejects_missing_id(handler_and_table, path_parameters):
    """A missing or empty path id is a 400."""
    module, table = handler_and_table
    event = _delete_event()
    event["pathParameters"] = path_parameters

    assert module.handler(event)["statusCode"] == 400
    assert table.delete_calls == []


def test_delete_handles_missing_headers(handler_and_table):
    """An event with null headers is treated as missing If-Match."""
    module, _table = handler_and_table
    event = _delete_event()
    event["headers"] = None
    assert module.handler(event)["statusCode"] == 400


def test_delete_reraises_unexpected_aws_error(handler_and_table):
    """Non-conditional AWS errors are not swallowed."""
    module, table = handler_and_table
    table.errors = [_client_error("InternalServerError")]

    with pytest.raises(ClientError):
        module.handler(_delete_event())
    assert table.get_calls == []


def test_delete_reraises_error_without_code(handler_and_table):
    """A ClientError with an empty response is still re-raised."""
    module, table = handler_and_table
    table.errors = [ClientError({}, "DeleteItem")]

    with pytest.raises(ClientError):
        module.handler(_delete_event())


def test_table_uses_configured_name(monkeypatch):
    """The table factory uses TABLE_NAME from the environment."""
    monkeypatch.setenv("TABLE_NAME", "some-table")
    module = load_lambda_handler("notes_delete", "notes_delete_real")
    assert_configured_table_name(module, monkeypatch)
