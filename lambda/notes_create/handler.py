"""Lambda handler for creating a note (``POST /notes``), backed by DynamoDB.

Single-purpose function: it only creates notes, and its execution role is granted
write-only access to the table. Invoked through an API Gateway REST (non-proxy)
integration on the ``/notes`` resource.

Request body: ``{"title": "...", "content": "..."}`` (both non-empty strings).

Failure modes:
  * malformed JSON body / non-object body         -> 400
  * missing or empty ``title`` / ``content``       -> 400
"""

import json
import os
import secrets
import string

import boto3

_TABLE_NAME = os.environ.get("TABLE_NAME", "")
_ID_ALPHABET = string.ascii_letters + string.digits
_ID_LENGTH = 10

_dynamodb = boto3.resource("dynamodb")


def _table():
    """Return the DynamoDB table resource (looked up lazily so tests can patch it)."""
    return _dynamodb.Table(_TABLE_NAME)


def _generate_id() -> str:
    """Return a random URL-safe note id."""
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(_ID_LENGTH))


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


def handler(event: dict, _context=None) -> dict:
    """Validate the request body, store the note under a fresh id, and return it."""
    raw_body = event.get("body") or ""
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return _response(400, {"error": "Request body must be valid JSON."})

    if not isinstance(payload, dict):
        return _response(400, {"error": "Request body must be a JSON object."})

    title = payload.get("title", "")
    content = payload.get("content", "")
    if not _is_non_empty_str(title) or not _is_non_empty_str(content):
        return _response(
            400,
            {"error": "Fields 'title' and 'content' must be non-empty strings."},
        )

    note = {
        "id": _generate_id(),
        "title": title,
        "content": content,
        "version": 1,
    }
    _table().put_item(Item=note)
    return _response(201, note)
