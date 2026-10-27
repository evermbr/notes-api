"""Lambda handler for deleting a note (``DELETE /notes/{id}``), backed by DynamoDB.

Single-purpose function implementing optimistic concurrency: it only reads and
conditionally deletes notes, and its execution role is granted exactly
``dynamodb:GetItem`` and ``dynamodb:DeleteItem`` on the table. Invoked through an API
Gateway REST (non-proxy) integration on the ``/notes/{id}`` resource.

Concurrency model:
  * The caller sends the version it last observed in a case-insensitive ``If-Match``
    header (an unquoted positive integer).
  * A single atomic conditional ``DeleteItem`` removes the note only when the stored
    version matches. A legacy item without a stored ``version`` matches ``If-Match: 1``.

Failure modes:
  * missing ``id`` path parameter             -> 400
  * missing/invalid ``If-Match`` header       -> 400
  * note does not exist                       -> 404
  * note exists but stored version differs    -> 409
Any other AWS/service error is re-raised so the invocation fails visibly.
On success the handler returns ``204`` with an empty body.
"""

import json
import os

import boto3
from botocore.exceptions import ClientError

_TABLE_NAME = os.environ.get("TABLE_NAME", "")
_INITIAL_VERSION = 1

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


def _no_content_response() -> dict:
    """Build a 204 response with an empty body for a successful delete."""
    return {"statusCode": 204, "headers": {}, "body": ""}


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


def _apply_delete(note_id: str, expected: int) -> None:
    """Run one atomic conditional DeleteItem; raise ClientError on a failed condition."""
    _table().delete_item(
        Key={"id": note_id},
        ConditionExpression=(
            "attribute_exists(id) AND "
            "(#v = :expected OR "
            "(attribute_not_exists(#v) AND :expected = :initial))"
        ),
        ExpressionAttributeNames={"#v": "version"},
        ExpressionAttributeValues={
            ":expected": expected,
            ":initial": _INITIAL_VERSION,
        },
    )


def _conditional_failure_response(note_id: str) -> dict:
    """Distinguish a missing note (404) from a stale version (409) after a failure."""
    item = _table().get_item(Key={"id": note_id}).get("Item")
    if not item:
        return _response(404, {"error": "Note not found."})
    return _response(409, {"error": "Note version does not match If-Match header."})


def handler(event: dict, _context=None) -> dict:
    """Validate the request, apply a conditional delete, and map the outcome."""
    note_id = (event.get("pathParameters") or {}).get("id", "")
    if not note_id:
        return _response(400, {"error": "Provide a note id: DELETE /notes/{id}."})

    expected = _parse_if_match(event)
    if expected is None:
        return _response(
            400,
            {"error": "Provide a positive integer If-Match header."},
        )

    try:
        _apply_delete(note_id, expected)
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return _conditional_failure_response(note_id)
        raise

    return _no_content_response()
