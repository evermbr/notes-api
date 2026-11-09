"""Lambda handler for deleting a note (``DELETE /notes/{id}``), backed by DynamoDB.

Single-purpose function with ``dynamodb:GetItem`` and ``dynamodb:DeleteItem`` access
only. Implements optimistic concurrency: the caller sends the version it last read in
the ``If-Match`` header and the delete succeeds only if that version is still current.

Responses:
  * success                                  -> 204 with an empty body
  * missing id / missing or invalid If-Match -> 400
  * note does not exist                      -> 404
  * note exists with a different version     -> 409
Any other AWS error is re-raised so the invocation fails visibly.
"""

# Each Lambda asset must stay independently deployable (no shared production module),
# so the small header/condition helpers are intentionally duplicated between the
# update and delete handlers.
# pylint: disable=duplicate-code

import json
import os
import re

import boto3
from botocore.exceptions import ClientError

_TABLE_NAME = os.environ.get("TABLE_NAME", "")
_INITIAL_VERSION = 1
_VERSION_PATTERN = re.compile(r"[0-9]{1,18}")

_dynamodb = boto3.resource("dynamodb")


def _table():
    """Return the DynamoDB table resource (looked up lazily so tests can patch it)."""
    return _dynamodb.Table(_TABLE_NAME)


def _response(status: int, body: str = "") -> dict:
    """Build an API response for the API Gateway integration."""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": body,
    }


def _error(status: int, message: str) -> dict:
    """Build a JSON error response."""
    return _response(status, json.dumps({"error": message}))


def _if_match_version(event: dict):
    """Return the positive integer from the If-Match header, or None if invalid."""
    headers = event.get("headers") or {}
    raw = next(
        (value for name, value in headers.items() if name.lower() == "if-match"),
        None,
    )
    if not isinstance(raw, str) or not _VERSION_PATTERN.fullmatch(raw.strip()):
        return None
    version = int(raw.strip())
    return version if version > 0 else None


def _version_condition(expected: int) -> str:
    """Return the condition: note exists and its version matches ``expected``."""
    if expected == _INITIAL_VERSION:
        # Legacy notes have no stored version attribute and count as version 1.
        return (
            "attribute_exists(id) AND (#version = :expected OR "
            "attribute_not_exists(#version))"
        )
    return "attribute_exists(id) AND #version = :expected"


def handler(event: dict, _context=None) -> dict:
    """Atomically delete a note at the expected version, else map to 404/409."""
    note_id = (event.get("pathParameters") or {}).get("id", "")
    if not note_id:
        return _error(400, "Provide a note id: DELETE /notes/{id}.")

    expected = _if_match_version(event)
    if expected is None:
        return _error(400, "Header 'If-Match' must be a positive integer version.")

    table = _table()
    try:
        table.delete_item(
            Key={"id": note_id},
            ConditionExpression=_version_condition(expected),
            ExpressionAttributeNames={"#version": "version"},
            ExpressionAttributeValues={":expected": expected},
            ReturnValues="ALL_OLD",
        )
    except ClientError as error_info:
        if error_info.response.get("Error", {}).get("Code") != (
            "ConditionalCheckFailedException"
        ):
            raise
        if table.get_item(Key={"id": note_id}).get("Item"):
            return _error(409, "Note version does not match If-Match.")
        return _error(404, "Note not found.")

    return _response(204)
