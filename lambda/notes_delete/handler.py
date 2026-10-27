"""Lambda handler for deleting a note (``DELETE /notes/{id}``), backed by DynamoDB.

Single-purpose function: it only reads and conditionally deletes notes, and its
execution role is granted exactly ``dynamodb:GetItem`` + ``dynamodb:DeleteItem`` on the
table. Invoked through an API Gateway REST (non-proxy) integration on the
``/notes/{id}`` resource, which supplies the id as a path parameter.

Optimistic concurrency: the client sends the version it last read in a case-insensitive
``If-Match`` header (an unquoted positive decimal integer). The delete is one atomic
conditional ``DeleteItem`` that only succeeds when the stored version matches; a legacy
item with no stored version matches ``If-Match: 1``.

Failure modes:
  * missing ``id`` path parameter            -> 400
  * missing/invalid ``If-Match`` header      -> 400
  * note does not exist                      -> 404
  * stored version differs from ``If-Match`` -> 409
Every other AWS/service error is re-raised so the invocation fails visibly.
"""

# Each Lambda asset is deliberately self-contained and independently deployable
# (see design.md): the small _table/_error/_match_version helpers are duplicated
# across handlers on purpose rather than extracted into shared runtime code.
# pylint: disable=duplicate-code

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


def _error(status: int, message: str) -> dict:
    """Build a JSON API error response for the API Gateway integration."""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": message}),
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


def _conflict_response(table, note_id: str) -> dict:
    """Distinguish a missing note (404) from a stale version (409) after a failure."""
    if table.get_item(Key={"id": note_id}).get("Item"):
        return _error(409, "Note version does not match If-Match.")
    return _error(404, "Note not found.")


def handler(event: dict, _context=None) -> dict:
    """Validate the request and perform one atomic conditional delete of the note."""
    note_id = (event.get("pathParameters") or {}).get("id", "")
    if not note_id:
        return _error(400, "Provide a note id: DELETE /notes/{id}.")

    expected = _match_version(event.get("headers"))
    if expected is None:
        return _error(400, "Provide a positive integer If-Match header.")

    table = _table()
    try:
        table.delete_item(
            Key={"id": note_id},
            ConditionExpression=(
                "attribute_exists(id) AND (version = :expected OR "
                "(attribute_not_exists(version) AND :expected = :initial))"
            ),
            ExpressionAttributeValues={
                ":expected": expected,
                ":initial": _INITIAL_VERSION,
            },
            ReturnValues="ALL_OLD",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return _conflict_response(table, note_id)
        raise

    return {"statusCode": 204, "headers": {}, "body": ""}
