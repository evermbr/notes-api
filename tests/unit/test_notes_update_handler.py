"""Unit tests for the single-purpose ``PATCH /notes/{id}`` Lambda."""

import json

import pytest
from botocore.exceptions import ClientError

from tests.unit.lambda_test_utils import (
    install_versioned_table,
    assert_configured_table_name,
    load_lambda_handler,
)

_NOTE = {"id": "n1", "title": "Old", "content": "Body", "version": 3}


@pytest.fixture(name="module")
def _module(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "test-table")
    return load_lambda_handler("notes_update", "notes_update_handler")


def _event(body, if_match="3", header="If-Match", note_id="n1"):
    event = {
        "httpMethod": "PATCH",
        "pathParameters": {"id": note_id},
        "headers": {} if if_match is None else {header: if_match},
        "body": body if isinstance(body, str) or body is None else json.dumps(body),
    }
    return event


def test_update_success_increments_version(module, monkeypatch):
    """A matching version updates supplied fields and returns the new note."""
    table = install_versioned_table(module, monkeypatch, {"n1": dict(_NOTE)})

    response = module.handler(_event({"title": "New"}))

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {
        "id": "n1",
        "title": "New",
        "content": "Body",
        "version": 4,
    }
    assert table.items["n1"]["version"] == 4
    assert [name for name, _ in table.calls] == ["update_item"]


def test_update_both_fields_and_case_insensitive_header(module, monkeypatch):
    """Title and content can change together; header casing is irrelevant."""
    install_versioned_table(module, monkeypatch, {"n1": dict(_NOTE)})

    response = module.handler(
        _event({"title": "T", "content": "C"}, header="if-match")
    )

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert (body["title"], body["content"], body["version"]) == ("T", "C", 4)


def test_update_legacy_note_matches_version_one(module, monkeypatch):
    """A legacy note without a version matches If-Match 1 and becomes version 2."""
    legacy = {"id": "n1", "title": "Old", "content": "Body"}
    table = install_versioned_table(module, monkeypatch, {"n1": legacy})

    response = module.handler(_event({"content": "Fresh"}, if_match=" 1 "))

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["version"] == 2
    condition = table.calls[0][1]["ConditionExpression"]
    assert "attribute_not_exists(#version)" in condition


def test_update_non_initial_version_has_no_legacy_clause(module, monkeypatch):
    """Only If-Match 1 may match a note lacking a version attribute."""
    table = install_versioned_table(module, monkeypatch, {"n1": dict(_NOTE)})

    module.handler(_event({"title": "New"}))

    condition = table.calls[0][1]["ConditionExpression"]
    assert "attribute_not_exists" not in condition
    assert "attribute_exists(id)" in condition


def test_update_legacy_note_rejects_other_version(module, monkeypatch):
    """A legacy note does not match If-Match 2."""
    legacy = {"id": "n1", "title": "Old", "content": "Body"}
    install_versioned_table(module, monkeypatch, {"n1": legacy})

    assert module.handler(_event({"title": "x"}, if_match="2"))["statusCode"] == 409


def test_update_stale_version_conflicts(module, monkeypatch):
    """A stale version returns 409 and leaves the note unchanged."""
    table = install_versioned_table(module, monkeypatch, {"n1": dict(_NOTE)})

    response = module.handler(_event({"title": "New"}, if_match="2"))

    assert response["statusCode"] == 409
    assert table.items["n1"] == _NOTE
    assert [name for name, _ in table.calls] == ["update_item", "get_item"]


def test_update_missing_note_not_found(module, monkeypatch):
    """A missing note returns 404."""
    table = install_versioned_table(module, monkeypatch, {})

    response = module.handler(_event({"title": "New"}))

    assert response["statusCode"] == 404
    assert [name for name, _ in table.calls] == ["update_item", "get_item"]


@pytest.mark.parametrize("if_match", [None, "", "abc", "0", "-1", "1.5", '"3"', "3 4"])
def test_update_invalid_if_match(module, monkeypatch, if_match):
    """Missing or malformed If-Match returns 400 without touching the table."""
    table = install_versioned_table(module, monkeypatch, {"n1": dict(_NOTE)})

    response = module.handler(_event({"title": "New"}, if_match=if_match))

    assert response["statusCode"] == 400
    assert not table.calls


def test_update_headers_missing_entirely(module, monkeypatch):
    """An event with no headers at all returns 400."""
    table = install_versioned_table(module, monkeypatch, {"n1": dict(_NOTE)})
    event = {"pathParameters": {"id": "n1"}, "body": '{"title": "x"}'}

    assert module.handler(event)["statusCode"] == 400
    assert not table.calls


@pytest.mark.parametrize(
    "body",
    [
        None,
        "",
        "{not json",
        "[]",
        "{}",
        {"title": "x", "owner": "y"},
        {"version": 9},
        {"id": "other"},
        {"title": ""},
        {"title": "   "},
        {"content": 5},
        {"title": None},
        {"title": "ok", "content": ["x"]},
    ],
)
def test_update_invalid_body(module, monkeypatch, body):
    """Malformed JSON, no/unknown fields, or bad values return 400, no write."""
    table = install_versioned_table(module, monkeypatch, {"n1": dict(_NOTE)})

    response = module.handler(_event(body))

    assert response["statusCode"] == 400
    assert not table.calls
    assert table.items["n1"] == _NOTE


@pytest.mark.parametrize("event", [{"headers": {"If-Match": "1"}}, {"pathParameters": {"id": ""}}])
def test_update_requires_id(module, monkeypatch, event):
    """A missing or empty id path parameter returns 400."""
    table = install_versioned_table(module, monkeypatch)

    assert module.handler(event)["statusCode"] == 400
    assert not table.calls


def test_update_unexpected_error_is_raised(module, monkeypatch):
    """Errors other than a failed condition propagate."""
    error = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}},
        "UpdateItem",
    )
    table = install_versioned_table(module, monkeypatch, {"n1": dict(_NOTE)}, error)

    with pytest.raises(ClientError):
        module.handler(_event({"title": "New"}))
    assert [name for name, _ in table.calls] == ["update_item"]


def test_table_uses_configured_name(monkeypatch):
    """The table factory uses TABLE_NAME from the environment."""
    monkeypatch.setenv("TABLE_NAME", "some-table")
    module = load_lambda_handler("notes_update", "notes_update_real")
    assert_configured_table_name(module, monkeypatch)
