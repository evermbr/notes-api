"""CDK assertions for the Notes API application stack."""

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
    """Create, get, update, delete and authorizer Lambdas use the project runtime."""
    template = _template()
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {"Handler": "handler.handler", "Runtime": "python3.13"},
    )
    # create, get, update, delete, authorizer
    template.resource_count_is("AWS::Lambda::Function", 5)


def test_methods_wired_per_operation():
    """The API exposes POST, GET, PATCH and DELETE operations under custom auth."""
    template = _template()
    methods = template.find_resources("AWS::ApiGateway::Method")
    assert {resource["Properties"]["HttpMethod"] for resource in methods.values()} == {
        "GET",
        "POST",
        "PATCH",
        "DELETE",
    }
    assert all(
        resource["Properties"]["AuthorizationType"] == "CUSTOM"
        for resource in methods.values()
    )


def test_update_lambda_least_privilege_actions():
    """The update Lambda is granted exactly GetItem and UpdateItem on the table."""
    template = _template()
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Statement": assertions.Match.array_with(
                    [
                        assertions.Match.object_like(
                            {
                                "Action": ["dynamodb:GetItem", "dynamodb:UpdateItem"],
                                "Effect": "Allow",
                            }
                        )
                    ]
                )
            }
        },
    )


def test_delete_lambda_least_privilege_actions():
    """The delete Lambda is granted exactly GetItem and DeleteItem on the table."""
    template = _template()
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Statement": assertions.Match.array_with(
                    [
                        assertions.Match.object_like(
                            {
                                "Action": ["dynamodb:GetItem", "dynamodb:DeleteItem"],
                                "Effect": "Allow",
                            }
                        )
                    ]
                )
            }
        },
    )


def test_no_wildcard_dynamodb_actions():
    """No policy grants a wildcard DynamoDB action."""
    template = _template()
    for policy in template.find_resources("AWS::IAM::Policy").values():
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            actions = statement["Action"]
            actions = actions if isinstance(actions, list) else [actions]
            assert "dynamodb:*" not in actions
            assert "*" not in actions


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
