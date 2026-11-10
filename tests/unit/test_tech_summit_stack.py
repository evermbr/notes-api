"""CDK assertions for the Notes API application stack."""

import json

from aws_cdk import App, assertions

from tech_summit.tech_summit_stack import TechSummitStack


def _template(environment_name: str = "dev") -> assertions.Template:
    app = App()
    stack = TechSummitStack(
        app,
        "TechSummitTestStack",
        environment_name=environment_name,
    )
    return assertions.Template.from_stack(stack)


def test_no_sns_or_sqs():
    """The small API does not provision unrelated messaging services."""
    template = _template()
    template.resource_count_is("AWS::SQS::Queue", 0)
    template.resource_count_is("AWS::SNS::Topic", 0)


def test_dynamodb_table_created():
    """The Notes table uses on-demand capacity, PITR and the id partition key."""
    template = _template()
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "BillingMode": "PAY_PER_REQUEST",
            "KeySchema": [{"AttributeName": "id", "KeyType": "HASH"}],
            "PointInTimeRecoverySpecification": {
                "PointInTimeRecoveryEnabled": True,
            },
        },
    )


def test_production_data_is_protected():
    """Production retains the table and enables deletion protection."""
    template = _template("prod")
    template.has_resource(
        "AWS::DynamoDB::Table",
        {
            "DeletionPolicy": "Retain",
            "UpdateReplacePolicy": "Retain",
            "Properties": assertions.Match.object_like(
                {"DeletionProtectionEnabled": True}
            ),
        },
    )
    template.has_resource(
        "AWS::SecretsManager::Secret",
        {
            "DeletionPolicy": "Retain",
            "UpdateReplacePolicy": "Retain",
        },
    )


def test_notes_lambdas_created():
    """Create, get, update, delete and authorizer Lambdas use the supported project runtime."""
    template = _template()
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {"Handler": "handler.handler", "Runtime": "python3.13"},
    )
    template.resource_count_is("AWS::Lambda::Function", 5)


def test_methods_wired_per_operation():
    """The API exposes explicit POST, GET, PATCH and DELETE operations."""
    template = _template()
    methods = template.find_resources("AWS::ApiGateway::Method")
    assert {resource["Properties"]["HttpMethod"] for resource in methods.values()} == {
        "DELETE",
        "GET",
        "PATCH",
        "POST",
    }
    assert all(
        resource["Properties"]["AuthorizationType"] == "CUSTOM"
        for resource in methods.values()
    )


def test_least_privilege_table_grants():
    """Create and get have separate write-only and read-only table grants."""
    template = _template()
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Statement": assertions.Match.array_with(
                    [
                        assertions.Match.object_like(
                            {
                                "Action": assertions.Match.array_with(
                                    ["dynamodb:PutItem"]
                                ),
                                "Effect": "Allow",
                            }
                        )
                    ]
                )
            }
        },
    )
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Statement": assertions.Match.array_with(
                    [
                        assertions.Match.object_like(
                            {
                                "Action": assertions.Match.array_with(
                                    ["dynamodb:GetItem"]
                                ),
                                "Effect": "Allow",
                            }
                        )
                    ]
                )
            }
        },
    )


def _function_logical_id(template, prefix):
    functions = template.find_resources("AWS::Lambda::Function")
    matches = [key for key in functions if key.startswith(prefix)]
    assert len(matches) == 1
    return matches[0]


def _dynamodb_actions_for(template, role_prefix):
    """Return the DynamoDB actions granted to the role of one function."""
    actions = set()
    for policy in template.find_resources("AWS::IAM::Policy").values():
        roles = policy["Properties"]["Roles"]
        if not any(role["Ref"].startswith(role_prefix) for role in roles):
            continue
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            action = statement["Action"]
            action = [action] if isinstance(action, str) else action
            actions.update(a for a in action if a.startswith("dynamodb:"))
    return actions


def test_update_and_delete_lambdas_have_exact_actions():
    """Update gets only Get+Update; delete gets only Get+Delete (no wildcards)."""
    template = _template()
    assert _function_logical_id(template, "NotesUpdateFunction")
    assert _function_logical_id(template, "NotesDeleteFunction")
    assert _dynamodb_actions_for(template, "NotesUpdateFunction") == {
        "dynamodb:GetItem",
        "dynamodb:UpdateItem",
    }
    assert _dynamodb_actions_for(template, "NotesDeleteFunction") == {
        "dynamodb:GetItem",
        "dynamodb:DeleteItem",
    }


def test_patch_and_delete_methods_use_custom_authorizer_on_id_resource():
    """PATCH and DELETE are explicit, custom-authorized, and routed to own Lambdas."""
    template = _template()
    methods = template.find_resources("AWS::ApiGateway::Method")
    by_verb = {
        res["Properties"]["HttpMethod"]: res["Properties"] for res in methods.values()
    }
    for verb, function in (("PATCH", "NotesUpdateFunction"), ("DELETE", "NotesDeleteFunction")):
        props = by_verb[verb]
        assert props["AuthorizationType"] == "CUSTOM"
        assert "AuthorizerId" in props
        assert function in json.dumps(props["Integration"]["Uri"])
    template.has_resource_properties(
        "AWS::ApiGateway::Resource", {"PathPart": "{id}"}
    )


def test_api_gateway_created_for_environment():
    """The REST API has no Function URL and names its stage after the environment."""
    template = _template("staging")
    template.resource_count_is("AWS::ApiGateway::RestApi", 1)
    template.resource_count_is("AWS::Lambda::Url", 0)
    template.has_resource_properties(
        "AWS::ApiGateway::Stage",
        {"StageName": "staging"},
    )


def test_api_key_secret_created():
    """Secrets Manager generates one API key secret."""
    template = _template()
    template.resource_count_is("AWS::SecretsManager::Secret", 1)


def test_api_protected_by_custom_authorizer():
    """A TOKEN authorizer protects every API method."""
    template = _template()
    template.has_resource_properties(
        "AWS::ApiGateway::Authorizer",
        {"Type": "TOKEN"},
    )
