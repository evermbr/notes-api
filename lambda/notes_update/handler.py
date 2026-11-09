"""Lambda handler for updating a note (``PATCH /notes/{id}``), backed by DynamoDB.

Single-purpose function with optimistic concurrency: the caller sends the version it
last read in the ``If-Match`` header and the update is applied only if the stored
version still matches (one atomic conditional ``UpdateItem``). Notes stored before
versioning have no ``version`` attribute and match ``If-Match: 1``.

Request body: JSON object with ``title``, ``content`` or both (non-empty strings).

Failure modes:
  * missing/invalid path id or ``If-Match``, malformed body, unknown fields -> 400
  * no note stored under that id                                           -> 404
  * note exists but its version differs                                    -> 409
Any other AWS error is re-raised so the invocation fails visibly.
"""

# pylint: disable=duplicate-code  # self-contained Lambda assets deliberately repeat small helpers (design.md)

import json
import os

import boto3
from botocore.exceptions import ClientError

_TABLE_NAME = os.environ.get("TABLE_NAME", "")
_INITIAL_VERSION = 1
_MAX_VERSION_DIGITS = 18
_ALLOWED_FIELDS = ("title", "content")

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


def _parse_if_match(event: dict):
    """Return the expected version from ``If-Match`` or None if absent/invalid."""
    headers = event.get("headers") or {}
    value = next(
        (v for k, v in headers.items() if k.lower() == "if-match"),
        None,
    )
    if not isinstance(value, str):
        return None
    value = value.strip()
    if (
        not value.isascii()
        or not value.isdigit()
        or len(value) > _MAX_VERSION_DIGITS
    ):
        return None
    version = int(value)
    return version if version > 0 else None


def _parse_updates(event: dict):
    """Return the validated fields to update, or None if the body is invalid."""
    try:
        payload = json.loads(event.get("body") or "")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or not payload:
        return None
    for key, value in payload.items():
        if key not in _ALLOWED_FIELDS:
            return None
        if not isinstance(value, str) or not value.strip():
            return None
    return payload


def _is_conditional_failure(error: ClientError) -> bool:
    """Return True only for DynamoDB ConditionalCheckFailedException."""
    return error.response.get("Error", {}).get("Code") == (
        "ConditionalCheckFailedException"
    )


def handler(event: dict, _context=None) -> dict:
    """Apply a version-checked partial update and return the complete new note."""
    note_id = (event.get("pathParameters") or {}).get("id", "")
    if not note_id:
        return _response(400, {"error": "Provide a note id: PATCH /notes/{id}."})

    expected = _parse_if_match(event)
    if expected is None:
        return _response(
            400, {"error": "Header 'If-Match' must be a positive integer version."}
        )

    updates = _parse_updates(event)
    if updates is None:
        return _response(
            400,
            {
                "error": (
                    "Body must be a JSON object with 'title' and/or 'content' "
                    "as non-empty strings."
                )
            },
        )

    names = {"#version": "version"}
    values = {":expected": expected, ":initial": _INITIAL_VERSION, ":increment": 1}
    assignments = []
    for key, value in updates.items():
        names[f"#{key}"] = key
        values[f":{key}"] = value
        assignments.append(f"#{key} = :{key}")
    assignments.append("#version = if_not_exists(#version, :initial) + :increment")

    condition = "#version = :expected"
    if expected == _INITIAL_VERSION:
        condition = f"({condition} OR attribute_not_exists(#version))"

    table = _table()
    try:
        result = table.update_item(
            Key={"id": note_id},
            UpdateExpression="SET " + ", ".join(assignments),
            ConditionExpression=f"attribute_exists(id) AND {condition}",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
    except ClientError as error:
        if not _is_conditional_failure(error):
            raise
        # Strongly consistent so a just-written note is never reported as 404.
        if table.get_item(Key={"id": note_id}, ConsistentRead=True).get("Item"):
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
