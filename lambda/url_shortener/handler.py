"""Lambda handler for a minimal URL shortener backed by DynamoDB.

Invoked through a Lambda Function URL:
  * ``POST /``            body ``{"url": "https://..."}`` -> creates a short code
  * ``GET  /{code}``      -> HTTP 302 redirect to the original URL (404 if unknown)
"""

import json
import os
import secrets
import string
from urllib.parse import urlparse

import boto3

_TABLE_NAME = os.environ.get("TABLE_NAME", "")
_CODE_ALPHABET = string.ascii_letters + string.digits
_CODE_LENGTH = 7

_dynamodb = boto3.resource("dynamodb")


def _table():
    """Return the DynamoDB table resource (looked up lazily so tests can patch it)."""
    return _dynamodb.Table(_TABLE_NAME)


def _generate_code() -> str:
    """Return a random URL-safe short code."""
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


def _is_valid_url(url: str) -> bool:
    """Return True if the string is an http(s) URL with a network location."""
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _response(status: int, body: dict) -> dict:
    """Build a JSON API response for the Function URL."""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _create_short_url(event: dict) -> dict:
    """Handle POST: validate the URL, store it under a fresh code, return the code."""
    raw_body = event.get("body") or ""
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return _response(400, {"error": "Request body must be valid JSON."})

    url = payload.get("url", "")
    if not _is_valid_url(url):
        return _response(400, {"error": "Field 'url' must be a valid http(s) URL."})

    code = _generate_code()
    _table().put_item(Item={"code": code, "url": url})
    return _response(201, {"code": code, "url": url})


def _redirect(code: str) -> dict:
    """Handle GET /{code}: look up the code and 302-redirect, or 404."""
    item = _table().get_item(Key={"code": code}).get("Item")
    if not item:
        return _response(404, {"error": "Short code not found."})
    return {"statusCode": 302, "headers": {"Location": item["url"]}, "body": ""}


def handler(event: dict, _context=None) -> dict:
    """Route an API Gateway REST request to create or redirect based on method/path."""
    method = event.get("httpMethod", "GET")
    path = event.get("path", "/")

    if method == "POST":
        return _create_short_url(event)

    if method == "GET":
        code = path.strip("/")
        if code:
            return _redirect(code)
        return _response(400, {"error": "Provide a short code: GET /{code}."})

    return _response(405, {"error": f"Method {method} not allowed."})
