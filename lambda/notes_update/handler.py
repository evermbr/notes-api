"""Lambda handler for updating a note (``PATCH /notes/{id}``), backed by DynamoDB.

Single-purpose function: it only reads and conditionally updates notes, and its
execution role is granted exactly ``dynamodb:GetItem`` + ``dynamodb:UpdateItem`` on the
table. Invoked through an API Gateway REST (non-proxy) integration on the
``/notes/{id}`` resource, which supplies the id as a path parameter.

Optimistic concurrency: the client sends the version it last read in a case-insensitive
``If-Match`` header (an unquoted positive decimal integer). The update is one atomic
conditional ``UpdateItem`` that only succeeds when the stored version matches; a legacy
item with no stored version matches ``If-Match: 1`` and becomes version ``2``.

Failure modes:
  * missing ``id`` path parameter                          -> 400
  * missing/invalid ``If-Match`` header                    -> 400
  * malformed JSON / non-object body                       -> 400
  * no supplied ``title``/``content`` or unsupported field -> 400
  * empty/whitespace/non-string ``title``/``content``      -> 400
  * note does not exist                                    -> 404
  * stored version differs from ``If-Match``               -> 409
Every other AWS/service error is re-raised so the invocation fails visibly.
"""

# Each Lambda asset is deliberately self-contained and independently deployable
# (see design.md): the small _table/_response/_match_version helpers are duplicated
# across handlers on purpose rather than extracted into shared runtime code.
# pylint: disable=duplicate-code

import json
import os

import boto3
from botocore.exceptions import ClientError

_TABLE_NAME = os.environ.get("TABLE_NAME", "")
_ALLOWED_FIELDS = ("title", "content")
_INITIAL_VERSION = 1

_dynamodb = boto3.resource("dynamodb")


def _table():
    """Return the DynamoDB table resource (looked up lazily so tests can patch it)."""
    return _dynamodb.Table(_TABLE_NAME)


def _is_non_empty_str(value) -> bool:
    """Return True if value is a non-empty, non-whitespace string."""
    return isinstance(value, str) and bool(value.strip())


def _response(status: int, body: dict) -> dict:
    """Build a JSON API response for the API Gateway integration."""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _match_version(headers) -> int | None:
    """Return the positive integer from a case-insensitive ``If-Match`` header.

    Returns ``None`` when the header is absent or not an unquoted positive integer.
    """
    for key, value in (headers or {}).items():
        if key.lower() == "if-match":
            raw = value.strip() if isinstance(value, str) else ""
            if raw.isdigit() and int(raw) > 0:
                return int(raw)
            return None
    return None


def _parse_updates(raw_body: str):
    """Parse and validate the request body.

    Return ``(updates, error_response)``; ``updates`` is None when the body is not
    valid JSON, is not an object, or fails field validation.
    """
    try:
        payload = json.loads(raw_body or "")
    except json.JSONDecodeError:
        return None, _response(400, {"error": "Request body must be valid JSON."})
    if not isinstance(payload, dict):
        return None, _response(400, {"error": "Request body must be a JSON object."})
    return _validate_fields(payload)


def _validate_fields(payload: dict):
    """Return (updates, error_response). ``updates`` is None when validation fails."""
    if any(key not in _ALLOWED_FIELDS for key in payload):
        return None, _response(
            400, {"error": "Only 'title' and 'content' may be updated."}
        )
    updates = {key: payload[key] for key in _ALLOWED_FIELDS if key in payload}
    if not updates:
        return None, _response(
            400, {"error": "Provide at least one of 'title' or 'content'."}
        )
    if not all(_is_non_empty_str(value) for value in updates.values()):
        return None, _response(
            400,
            {"error": "Fields 'title' and 'content' must be non-empty strings."},
        )
    return updates, None


def _build_update(updates: dict, expected: int) -> dict:
    """Build the atomic conditional UpdateItem keyword arguments."""
    set_clauses = ["version = if_not_exists(version, :initial) + :increment"]
    names = {}
    values = {
        ":expected": expected,
        ":initial": _INITIAL_VERSION,
        ":increment": 1,
    }
    for index, (field, value) in enumerate(updates.items()):
        name_token = f"#f{index}"
        value_token = f":v{index}"
        set_clauses.append(f"{name_token} = {value_token}")
        names[name_token] = field
        values[value_token] = value
    return {
        "UpdateExpression": "SET " + ", ".join(set_clauses),
        "ConditionExpression": (
            "attribute_exists(id) AND (version = :expected OR "
            "(attribute_not_exists(version) AND :expected = :initial))"
        ),
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": values,
        "ReturnValues": "ALL_NEW",
    }


def _conflict_response(table, note_id: str) -> dict:
    """Distinguish a missing note (404) from a stale version (409) after a failure."""
    if table.get_item(Key={"id": note_id}).get("Item"):
        return _response(409, {"error": "Note version does not match If-Match."})
    return _response(404, {"error": "Note not found."})


def handler(event: dict, _context=None) -> dict:
    """Validate the request and perform one atomic conditional update of the note."""
    note_id = (event.get("pathParameters") or {}).get("id", "")
    if not note_id:
        return _response(400, {"error": "Provide a note id: PATCH /notes/{id}."})

    expected = _match_version(event.get("headers"))
    if expected is None:
        return _response(
            400, {"error": "Provide a positive integer If-Match header."}
        )

    updates, error = _parse_updates(event.get("body"))
    if error is not None:
        return error

    table = _table()
    try:
        result = table.update_item(
            Key={"id": note_id}, **_build_update(updates, expected)
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return _conflict_response(table, note_id)
        raise

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
