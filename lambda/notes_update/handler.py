"""Lambda handler for updating a note (``PATCH /notes/{id}``), backed by DynamoDB.

Single-purpose function with optimistic concurrency. Its execution role is granted
only ``dynamodb:GetItem`` and ``dynamodb:UpdateItem`` on the table.

Request: ``If-Match: <positive integer version>`` header and a JSON object body with
``title`` and/or ``content`` (non-empty strings; unknown fields are rejected).

Responses:
  * invalid/missing ``If-Match``, bad JSON, bad fields -> 400
  * note not found                                      -> 404
  * stored version differs from ``If-Match``            -> 409
  * success: complete updated note, version + 1          -> 200

Legacy items without a stored version match ``If-Match: 1`` and become version 2.
"""

# pylint: disable=duplicate-code  # intentional: each Lambda asset is self-contained

import json
import os

import boto3
from botocore.exceptions import ClientError

_TABLE_NAME = os.environ.get("TABLE_NAME", "")
_INITIAL_VERSION = 1
_ALLOWED_FIELDS = ("title", "content")
_CONDITION = (
    "attribute_exists(id) AND (#version = :expected OR "
    "(attribute_not_exists(#version) AND :expected = :initial))"
)

_dynamodb = boto3.resource("dynamodb")


def _table():
    """Return the DynamoDB table resource (looked up lazily so tests can patch it)."""
    return _dynamodb.Table(_TABLE_NAME)


def _response(status: int, body: dict) -> dict:
    """Build a JSON API response for the API Gateway integration."""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
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


def _validate_fields(payload) -> dict | None:
    """Return the validated fields to update, or None if the payload is invalid."""
    if not isinstance(payload, dict) or not payload:
        return None
    if any(key not in _ALLOWED_FIELDS for key in payload):
        return None
    for value in payload.values():
        if not isinstance(value, str) or not value.strip():
            return None
    return payload


def _is_conditional_failure(error: ClientError) -> bool:
    """Return True only for a DynamoDB ConditionalCheckFailedException."""
    code = error.response.get("Error", {}).get("Code")
    return code == "ConditionalCheckFailedException"


def _build_update_args(expected: int, fields: dict) -> dict:
    """Build the expression arguments: set the fields and increment the version."""
    names = {"#version": "version"}
    values = {
        ":expected": expected,
        ":initial": _INITIAL_VERSION,
        ":increment": 1,
    }
    assignments = []
    for index, (key, value) in enumerate(fields.items()):
        names[f"#f{index}"] = key
        values[f":f{index}"] = value
        assignments.append(f"#f{index} = :f{index}")
    assignments.append("#version = if_not_exists(#version, :initial) + :increment")
    return {
        "UpdateExpression": "SET " + ", ".join(assignments),
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": values,
    }


def _parse_request(event: dict):
    """Return ``((note_id, expected, fields), None)`` or ``(None, error_message)``."""
    note_id = (event.get("pathParameters") or {}).get("id", "")
    if not note_id:
        return None, "Provide a note id: PATCH /notes/{id}."

    expected = _parse_if_match(event.get("headers"))
    if expected is None:
        return None, "Header 'If-Match' must be a positive integer version."

    try:
        payload = json.loads(event.get("body") or "")
    except json.JSONDecodeError:
        return None, "Request body must be valid JSON."

    fields = _validate_fields(payload)
    if fields is None:
        return None, (
            "Body must be a JSON object with 'title' and/or 'content' "
            "as non-empty strings."
        )
    return (note_id, expected, fields), None


def handler(event: dict, _context=None) -> dict:
    """Validate the request, then conditionally update the note atomically."""
    parsed, error_message = _parse_request(event)
    if parsed is None:
        return _response(400, {"error": error_message})
    note_id, expected, fields = parsed

    table = _table()
    try:
        result = table.update_item(
            Key={"id": note_id},
            ConditionExpression=_CONDITION,
            ReturnValues="ALL_NEW",
            **_build_update_args(expected, fields),
        )
    except ClientError as error:
        if not _is_conditional_failure(error):
            raise
        if table.get_item(Key={"id": note_id}).get("Item"):
            return _response(409, {"error": "Note version does not match If-Match."})
        return _response(404, {"error": "Note not found."})

    item = result["Attributes"]
    return _response(
        200,
        {
            "id": item["id"],
            "title": item["title"],
            "content": item["content"],
            "version": int(item["version"]),
        },
    )
