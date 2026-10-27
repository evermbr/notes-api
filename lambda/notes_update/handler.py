"""Lambda handler for updating a note (``PATCH /notes/{id}``), backed by DynamoDB.

Single-purpose function implementing optimistic concurrency: it only reads and
conditionally updates notes, and its execution role is granted exactly
``dynamodb:GetItem`` and ``dynamodb:UpdateItem`` on the table. Invoked through an API
Gateway REST (non-proxy) integration on the ``/notes/{id}`` resource.

Concurrency model:
  * The caller sends the version it last observed in a case-insensitive ``If-Match``
    header (an unquoted positive integer).
  * A single atomic conditional ``UpdateItem`` applies the supplied fields and
    increments ``version`` by exactly one only when the stored version matches. A
    legacy item without a stored ``version`` matches ``If-Match: 1`` and becomes
    version ``2``.

Failure modes:
  * missing ``id`` path parameter                          -> 400
  * missing/invalid ``If-Match`` header                    -> 400
  * malformed JSON body / non-object body                  -> 400
  * no supported field, unknown field, or empty value      -> 400
  * note does not exist                                    -> 404
  * note exists but stored version differs                 -> 409
Any other AWS/service error is re-raised so the invocation fails visibly.
"""

import json
import os

import boto3
from botocore.exceptions import ClientError

_TABLE_NAME = os.environ.get("TABLE_NAME", "")
_ALLOWED_FIELDS = ("title", "content")
_INITIAL_VERSION = 1
_INCREMENT = 1

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


def _header(event: dict, name: str):
    """Return a header value by case-insensitive name, or None when absent."""
    headers = event.get("headers") or {}
    target = name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == target:
            return value
    return None


def _parse_if_match(event: dict):
    """Return the positive integer ``If-Match`` version, or None when invalid."""
    raw = _header(event, "If-Match")
    if not isinstance(raw, str):
        return None
    candidate = raw.strip()
    if not candidate.isdigit():
        return None
    value = int(candidate)
    return value if value >= 1 else None


def _is_non_empty_str(value) -> bool:
    """Return True if value is a non-empty, non-whitespace string."""
    return isinstance(value, str) and bool(value.strip())


def _validate_fields(payload: dict):
    """Return the supported fields to update, or None when the body is invalid."""
    if not isinstance(payload, dict):
        return None
    if any(key not in _ALLOWED_FIELDS for key in payload):
        return None
    updates = {key: payload[key] for key in _ALLOWED_FIELDS if key in payload}
    if not updates:
        return None
    if not all(_is_non_empty_str(value) for value in updates.values()):
        return None
    return updates


def _apply_update(note_id: str, expected: int, updates: dict) -> dict:
    """Run one atomic conditional UpdateItem and return the updated attributes."""
    set_clauses = ["#v = if_not_exists(#v, :initial) + :increment"]
    names = {"#v": "version"}
    values = {
        ":expected": expected,
        ":initial": _INITIAL_VERSION,
        ":increment": _INCREMENT,
    }
    for field, value in updates.items():
        set_clauses.append(f"#{field} = :{field}")
        names[f"#{field}"] = field
        values[f":{field}"] = value

    result = _table().update_item(
        Key={"id": note_id},
        UpdateExpression="SET " + ", ".join(set_clauses),
        ConditionExpression=(
            "attribute_exists(id) AND "
            "(#v = :expected OR "
            "(attribute_not_exists(#v) AND :expected = :initial))"
        ),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )
    return result.get("Attributes", {})


def _conditional_failure_response(note_id: str) -> dict:
    """Distinguish a missing note (404) from a stale version (409) after a failure."""
    item = _table().get_item(Key={"id": note_id}).get("Item")
    if not item:
        return _response(404, {"error": "Note not found."})
    return _response(409, {"error": "Note version does not match If-Match header."})


def handler(event: dict, _context=None) -> dict:
    """Validate the request, apply a conditional update, and map the outcome."""
    note_id = (event.get("pathParameters") or {}).get("id", "")
    if not note_id:
        return _response(400, {"error": "Provide a note id: PATCH /notes/{id}."})

    expected = _parse_if_match(event)
    if expected is None:
        return _response(
            400,
            {"error": "Provide a positive integer If-Match header."},
        )

    try:
        payload = json.loads(event.get("body") or "")
    except json.JSONDecodeError:
        return _response(400, {"error": "Request body must be valid JSON."})

    updates = _validate_fields(payload)
    if updates is None:
        return _response(
            400,
            {
                "error": (
                    "Provide at least one of 'title' or 'content' as a non-empty "
                    "string; no other fields are allowed."
                )
            },
        )

    try:
        attributes = _apply_update(note_id, expected, updates)
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return _conditional_failure_response(note_id)
        raise

    return _response(
        200,
        {
            "id": attributes["id"],
            "title": attributes["title"],
            "content": attributes["content"],
            "version": int(attributes["version"]),
        },
    )
