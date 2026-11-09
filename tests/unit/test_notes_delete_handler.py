"""Unit tests for the single-purpose ``DELETE /notes/{id}`` Lambda."""

import pytest
from botocore.exceptions import ClientError

from tests.unit.lambda_test_utils import (
    install_versioned_table,
    assert_configured_table_name,
    load_lambda_handler,
)

_NOTE = {"id": "n1", "title": "T", "content": "C", "version": 4}


@pytest.fixture(name="module")
def _module(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "test-table")
    return load_lambda_handler("notes_delete", "notes_delete_handler")


def _event(if_match="4", header="If-Match", note_id="n1"):
    return {
        "httpMethod": "DELETE",
        "pathParameters": {"id": note_id},
        "headers": {} if if_match is None else {header: if_match},
    }


def test_delete_success(module, monkeypatch):
    """A matching version deletes the note and returns an empty 204."""
    table = install_versioned_table(module, monkeypatch, {"n1": dict(_NOTE)})

    response = module.handler(_event())

    assert response["statusCode"] == 204
    assert response["body"] == ""
    assert not table.items
    assert [name for name, _ in table.calls] == ["delete_item"]


def test_delete_case_insensitive_header(module, monkeypatch):
    """The If-Match header is found regardless of casing."""
    install_versioned_table(module, monkeypatch, {"n1": dict(_NOTE)})

    assert module.handler(_event(header="IF-MATCH"))["statusCode"] == 204


def test_delete_legacy_note_matches_version_one(module, monkeypatch):
    """A legacy note without a version is deleted with If-Match 1."""
    legacy = {"id": "n1", "title": "T", "content": "C"}
    table = install_versioned_table(module, monkeypatch, {"n1": legacy})

    response = module.handler(_event(if_match="1"))

    assert response["statusCode"] == 204
    assert not table.items
    assert "attribute_not_exists(#version)" in table.calls[0][1]["ConditionExpression"]


def test_delete_non_initial_version_has_no_legacy_clause(module, monkeypatch):
    """Only If-Match 1 may match a note lacking a version attribute."""
    table = install_versioned_table(module, monkeypatch, {"n1": dict(_NOTE)})

    module.handler(_event())

    assert "attribute_not_exists" not in table.calls[0][1]["ConditionExpression"]


def test_delete_stale_version_conflicts(module, monkeypatch):
    """A stale version returns 409 and keeps the note."""
    table = install_versioned_table(module, monkeypatch, {"n1": dict(_NOTE)})

    response = module.handler(_event(if_match="3"))

    assert response["statusCode"] == 409
    assert table.items["n1"] == _NOTE
    assert [name for name, _ in table.calls] == ["delete_item", "get_item"]


def test_delete_legacy_note_rejects_other_version(module, monkeypatch):
    """A legacy note does not match If-Match 2."""
    legacy = {"id": "n1", "title": "T", "content": "C"}
    table = install_versioned_table(module, monkeypatch, {"n1": legacy})

    assert module.handler(_event(if_match="2"))["statusCode"] == 409
    assert "n1" in table.items


def test_delete_missing_note_not_found(module, monkeypatch):
    """A missing note returns 404."""
    table = install_versioned_table(module, monkeypatch, {})

    assert module.handler(_event())["statusCode"] == 404
    assert [name for name, _ in table.calls] == ["delete_item", "get_item"]


@pytest.mark.parametrize("if_match", [None, "", "abc", "0", "-2", "2.0", '"4"'])
def test_delete_invalid_if_match(module, monkeypatch, if_match):
    """Missing or malformed If-Match returns 400 without deleting."""
    table = install_versioned_table(module, monkeypatch, {"n1": dict(_NOTE)})

    assert module.handler(_event(if_match=if_match))["statusCode"] == 400
    assert not table.calls
    assert "n1" in table.items


def test_delete_headers_missing_entirely(module, monkeypatch):
    """An event with no headers at all returns 400."""
    table = install_versioned_table(module, monkeypatch, {"n1": dict(_NOTE)})

    assert module.handler({"pathParameters": {"id": "n1"}})["statusCode"] == 400
    assert not table.calls


@pytest.mark.parametrize("event", [{"headers": {"If-Match": "1"}}, {"pathParameters": {"id": ""}}])
def test_delete_requires_id(module, monkeypatch, event):
    """A missing or empty id path parameter returns 400."""
    table = install_versioned_table(module, monkeypatch)

    assert module.handler(event)["statusCode"] == 400
    assert not table.calls


def test_delete_unexpected_error_is_raised(module, monkeypatch):
    """Errors other than a failed condition propagate."""
    error = ClientError(
        {"Error": {"Code": "InternalServerError", "Message": "x"}}, "DeleteItem"
    )
    install_versioned_table(module, monkeypatch, {"n1": dict(_NOTE)}, error)

    with pytest.raises(ClientError):
        module.handler(_event())


def test_table_uses_configured_name(monkeypatch):
    """The table factory uses TABLE_NAME from the environment."""
    monkeypatch.setenv("TABLE_NAME", "some-table")
    module = load_lambda_handler("notes_delete", "notes_delete_real")
    assert_configured_table_name(module, monkeypatch)
