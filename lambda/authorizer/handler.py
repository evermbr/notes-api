"""Lambda TOKEN authorizer for the URL shortener API.

Validates the caller-supplied API key (sent in the ``Authorization`` header) against the
value stored in Secrets Manager. The secret value is fetched once and cached for the
lifetime of the execution environment.
"""

import os

import boto3

_SECRET_ARN = os.environ.get("API_KEY_SECRET_ARN", "")
_secretsmanager = boto3.client("secretsmanager")
_cached_key: str | None = None


def _expected_key() -> str:
    """Return the expected API key, fetching it from Secrets Manager once and caching it."""
    global _cached_key  # pylint: disable=global-statement
    if _cached_key is None:
        resp = _secretsmanager.get_secret_value(SecretId=_SECRET_ARN)
        _cached_key = resp["SecretString"]
    return _cached_key


def _policy(effect: str, resource: str) -> dict:
    """Build an IAM policy document allowing or denying access to the method ARN."""
    return {
        "principalId": "url-shortener-client",
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
    """Allow the request when the token matches the stored API key, otherwise deny."""
    token = event.get("authorizationToken", "")
    method_arn = event.get("methodArn", "*")
    effect = "Allow" if token and token == _expected_key() else "Deny"
    return _policy(effect, method_arn)
