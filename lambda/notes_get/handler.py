"""Lambda handler for reading a note (``GET /notes/{id}``), backed by DynamoDB.

Single-purpose function: it only reads notes, and its execution role is granted
read-only access to the table. Invoked through an API Gateway REST (non-proxy)
integration on the ``/notes/{id}`` resource, which supplies the id as a path parameter.

Failure modes:
  * missing ``id`` path parameter   -> 400
  * no note stored under that id     -> 404
"""

import json
import os

import boto3

_TABLE_NAME = os.environ.get("TABLE_NAME", "")

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


def handler(event: dict, _context=None) -> dict:
    """Resolve the note id from the path parameters and return the note, or 404."""
    note_id = (event.get("pathParameters") or {}).get("id", "")
    if not note_id:
        return _response(400, {"error": "Provide a note id: GET /notes/{id}."})

    item = _table().get_item(Key={"id": note_id}).get("Item")
    if not item:
        return _response(404, {"error": "Note not found."})

    return _response(
        200,
        {"id": item["id"], "title": item["title"], "content": item["content"]},
    )
