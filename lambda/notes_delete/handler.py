"""Lambda handler for deleting a note (``DELETE /notes/{id}``), backed by DynamoDB.

Single-purpose function with optimistic concurrency. Its execution role is granted
only ``dynamodb:GetItem`` and ``dynamodb:DeleteItem`` on the table.

Request: ``If-Match: <positive integer version>`` header.

Responses:
  * missing ``id`` or invalid/missing ``If-Match`` -> 400
  * note not found                                  -> 404
  * stored version differs from ``If-Match``        -> 409
  * success                                          -> 204 (empty body)

Legacy items without a stored version match ``If-Match: 1``.
"""

# pylint: disable=duplicate-code  # intentional: each Lambda asset is self-contained

import json
import os

import boto3
from botocore.exceptions import ClientError

_TABLE_NAME = os.environ.get("TABLE_NAME", "")
_INITIAL_VERSION = 1
_CONDITION = (
    "attribute_exists(id) AND (#version = :expected OR "
    "(attribute_not_exists(#version) AND :expected = :initial))"
)

_dynamodb = boto3.resource("dynamodb")


def _table():
    """Return the DynamoDB table resource (looked up lazily so tests can patch it)."""
    return _dynamodb.Table(_TABLE_NAME)


def _response(status: int, body: dict | None = None) -> dict:
    """Build an API response; a ``None`` body yields an empty body."""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": "" if body is None else json.dumps(body),
    }


def _parse_if_match(headers) -> int | None:
    """Return the positive integer from the If-Match header, or None if invalid."""
    value = None
    for name, header_value in (headers or {}).items():
        if name.lower() == "if-match":
            value = header_value
            break
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        return None
    version = int(value)
    return version if version > 0 else None


def _is_conditional_failure(error: ClientError) -> bool:
    """Return True only for a DynamoDB ConditionalCheckFailedException."""
    code = error.response.get("Error", {}).get("Code")
    return code == "ConditionalCheckFailedException"


def handler(event: dict, _context=None) -> dict:
    """Validate the request, then conditionally delete the note atomically."""
    note_id = (event.get("pathParameters") or {}).get("id", "")
    if not note_id:
        return _response(400, {"error": "Provide a note id: DELETE /notes/{id}."})

    expected = _parse_if_match(event.get("headers"))
    if expected is None:
        return _response(
            400, {"error": "Header 'If-Match' must be a positive integer version."}
        )

    table = _table()
    try:
        table.delete_item(
            Key={"id": note_id},
            ConditionExpression=_CONDITION,
            ExpressionAttributeNames={"#version": "version"},
            ExpressionAttributeValues={
                ":expected": expected,
                ":initial": _INITIAL_VERSION,
            },
        )
    except ClientError as error:
        if not _is_conditional_failure(error):
            raise
        if table.get_item(Key={"id": note_id}).get("Item"):
            return _response(409, {"error": "Note version does not match If-Match."})
        return _response(404, {"error": "Note not found."})

    return _response(204)
