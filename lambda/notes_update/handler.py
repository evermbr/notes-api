"""Lambda handler for updating a note (``PATCH /notes/{id}``), backed by DynamoDB.

Single-purpose function with ``dynamodb:GetItem`` and ``dynamodb:UpdateItem`` access
only. Implements optimistic concurrency: the caller sends the version it last read in
the ``If-Match`` header and the update succeeds only if that version is still current.

Request body: a JSON object with ``title``, ``content`` or both (non-empty strings).

Responses:
  * success                                          -> 200 with the full updated note
  * bad id / If-Match / JSON / fields / values       -> 400
  * note does not exist                              -> 404
  * note exists with a different version             -> 409
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
_ALLOWED_FIELDS = ("title", "content")
_INITIAL_VERSION = 1
_VERSION_PATTERN = re.compile(r"[0-9]{1,18}")

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


def _is_non_empty_str(value) -> bool:
    """Return True if value is a non-empty, non-whitespace string."""
    return isinstance(value, str) and bool(value.strip())


def _parse_changes(event: dict):
    """Return ``(changes, error)`` parsed from the request body."""
    try:
        payload = json.loads(event.get("body") or "")
    except json.JSONDecodeError:
        return None, "Request body must be valid JSON."

    if not isinstance(payload, dict) or not payload:
        return None, "Request body must be a JSON object with 'title' or 'content'."
    if any(field not in _ALLOWED_FIELDS for field in payload):
        return None, "Only 'title' and 'content' may be updated."
    if not all(_is_non_empty_str(value) for value in payload.values()):
        return None, "Fields 'title' and 'content' must be non-empty strings."
    return payload, None


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
    """Atomically apply a versioned update, mapping stale/missing notes to 409/404."""
    note_id = (event.get("pathParameters") or {}).get("id", "")
    if not note_id:
        return _response(400, {"error": "Provide a note id: PATCH /notes/{id}."})

    expected = _if_match_version(event)
    if expected is None:
        return _response(
            400, {"error": "Header 'If-Match' must be a positive integer version."}
        )

    changes, error = _parse_changes(event)
    if error:
        return _response(400, {"error": error})

    names = {"#version": "version"}
    values = {
        ":expected": expected,
        ":initial": _INITIAL_VERSION,
        ":increment": 1,
    }
    assignments = ["#version = if_not_exists(#version, :initial) + :increment"]
    for field, value in changes.items():
        names[f"#{field}"] = field
        values[f":{field}"] = value
        assignments.append(f"#{field} = :{field}")

    table = _table()
    try:
        result = table.update_item(
            Key={"id": note_id},
            UpdateExpression="SET " + ", ".join(assignments),
            ConditionExpression=_version_condition(expected),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
    except ClientError as error_info:
        if error_info.response.get("Error", {}).get("Code") != (
            "ConditionalCheckFailedException"
        ):
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
