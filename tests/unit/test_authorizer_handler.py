"""Unit tests for the API Gateway Lambda token authorizer."""

import pytest

from tests.unit.lambda_test_utils import load_lambda_handler


@pytest.fixture(name="authorizer")
def _authorizer(monkeypatch):
    """Load the authorizer with a Secrets Manager test double."""
    monkeypatch.setenv(
        "API_KEY_SECRET_ARN",
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:test",
    )
    module = load_lambda_handler("authorizer", "authorizer_handler")

    class FakeSecrets:  # pylint: disable=too-few-public-methods  # SDK test double
        """Return a fixed secret value."""

        def get_secret_value(self, **kwargs):
            """Return the configured secret and verify the requested identifier."""
            assert kwargs["SecretId"].endswith(":secret:test")
            return {"SecretString": "the-secret-key"}

    setattr(module, "_secretsmanager", FakeSecrets())
    setattr(module, "_cached_key", None)
    return module


def _event(token):
    return {
        "authorizationToken": token,
        "methodArn": (
            "arn:aws:execute-api:us-east-1:123456789012:api/dev/GET/notes/abc"
        ),
    }


def test_allows_matching_key(authorizer):
    """A matching token is allowed for every method in the API stage."""
    response = authorizer.handler(_event("the-secret-key"))
    statement = response["policyDocument"]["Statement"][0]
    assert statement["Effect"] == "Allow"
    assert statement["Resource"].endswith("api/dev/*/*")
    assert response["principalId"] == "notes-api-client"


def test_denies_wrong_key(authorizer):
    """An incorrect token is denied."""
    response = authorizer.handler(_event("wrong-key"))
    statement = response["policyDocument"]["Statement"][0]
    assert statement["Effect"] == "Deny"


def test_denies_missing_token(authorizer):
    """A missing token is denied without fetching the secret."""
    response = authorizer.handler(_event(""))
    statement = response["policyDocument"]["Statement"][0]
    assert statement["Effect"] == "Deny"


def test_malformed_method_arn_is_preserved(authorizer):
    """An unexpected method ARN shape is preserved rather than guessed."""
    response = authorizer.handler(
        {"authorizationToken": "the-secret-key", "methodArn": "*"}
    )
    assert response["policyDocument"]["Statement"][0]["Resource"] == "*"


def test_secret_value_is_cached(authorizer):
    """A warm invocation reuses the cached secret value."""
    authorizer.handler(_event("the-secret-key"))

    class FailingSecrets:  # pylint: disable=too-few-public-methods  # SDK test double
        """Fail if the authorizer attempts a second Secrets Manager call."""

        def get_secret_value(self, **_kwargs):
            """Raise because the cached value should make this unreachable."""
            raise AssertionError("secret should be cached, not re-fetched")

    setattr(authorizer, "_secretsmanager", FailingSecrets())
    response = authorizer.handler(_event("the-secret-key"))
    assert response["policyDocument"]["Statement"][0]["Effect"] == "Allow"
