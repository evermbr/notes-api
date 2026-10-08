"""Lambda TOKEN authorizer for the Notes API.

The caller sends an API key in ``Authorization``. The function compares it with the
Secrets Manager value and returns a stage-wide policy so API Gateway can safely cache
the result for the same token across multiple Notes API routes.
"""

import hmac
import os

import boto3

_SECRET_ARN = os.environ.get("API_KEY_SECRET_ARN", "")
_secretsmanager = boto3.client("secretsmanager")
_cached_key: str | None = None


def _expected_key() -> str:
    """Fetch the expected API key once per execution environment and cache it."""
    global _cached_key  # pylint: disable=global-statement
    if _cached_key is None:
        response = _secretsmanager.get_secret_value(SecretId=_SECRET_ARN)
        _cached_key = response["SecretString"]
    return _cached_key


def _stage_resource(method_arn: str) -> str:
    """Expand a method ARN to every method and resource in the same API stage."""
    parts = method_arn.split("/", maxsplit=2)
    if len(parts) < 2:
        return method_arn
    return f"{parts[0]}/{parts[1]}/*/*"


def _policy(effect: str, resource: str) -> dict:
    """Build an IAM policy allowing or denying API invocation for a stage."""
    return {
        "principalId": "notes-api-client",
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": effect,
                    "Resource": resource,
                }
            ],
        },
    }


def handler(event: dict, _context=None) -> dict:
    """Allow a matching API key and deny missing or invalid credentials."""
    token = event.get("authorizationToken", "")
    method_arn = event.get("methodArn", "*")
    is_valid = bool(token) and hmac.compare_digest(token, _expected_key())
    return _policy("Allow" if is_valid else "Deny", _stage_resource(method_arn))
