"""Unit tests for the API Gateway Lambda token authorizer.

The authorizer module is loaded from its file path (its filename ``handler.py`` collides
with the url_shortener handler, so it is imported under a distinct module name here).
Secrets Manager access is stubbed; no AWS calls are made.
"""

import importlib.util
import os

import pytest

_AUTHORIZER_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "lambda", "authorizer", "handler.py"
)


@pytest.fixture(name="authorizer")
def _authorizer(monkeypatch):
    """Load the authorizer module fresh with a stubbed Secrets Manager client."""
    monkeypatch.setenv("API_KEY_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:1:secret:k")
    spec = importlib.util.spec_from_file_location("authorizer_handler", _AUTHORIZER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class FakeSecrets:
        def get_secret_value(self, SecretId):  # noqa: N803 (boto3 kwarg name)
            return {"SecretString": "the-secret-key"}

    module._secretsmanager = FakeSecrets()
    module._cached_key = None
    return module


def _event(token):
    return {
        "authorizationToken": token,
        "methodArn": "arn:aws:execute-api:us-east-1:1:api/prod/GET/abc",
    }


def test_allows_matching_key(authorizer):
    resp = authorizer.handler(_event("the-secret-key"))
    stmt = resp["policyDocument"]["Statement"][0]
    assert stmt["Effect"] == "Allow"


def test_denies_wrong_key(authorizer):
    resp = authorizer.handler(_event("wrong-key"))
    stmt = resp["policyDocument"]["Statement"][0]
    assert stmt["Effect"] == "Deny"


def test_denies_missing_token(authorizer):
    resp = authorizer.handler(_event(""))
    stmt = resp["policyDocument"]["Statement"][0]
    assert stmt["Effect"] == "Deny"


def test_secret_value_is_cached(authorizer):
    # Second call must not re-fetch: swap the client to one that raises if called.
    authorizer.handler(_event("the-secret-key"))

    class Boom:
        def get_secret_value(self, SecretId):  # noqa: N803
            raise AssertionError("secret should be cached, not re-fetched")

    authorizer._secretsmanager = Boom()
    resp = authorizer.handler(_event("the-secret-key"))
    assert resp["policyDocument"]["Statement"][0]["Effect"] == "Allow"
