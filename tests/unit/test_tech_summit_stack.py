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
        "GET",
        "POST",
        "PATCH",
        "DELETE",
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


def _function_logical_id(template, prefix):
    functions = template.find_resources("AWS::Lambda::Function")
    matches = [key for key in functions if key.startswith(prefix)]
    assert len(matches) == 1
    return matches[0]


def _table_statements_for(template, function_prefix):
    """Return the DynamoDB policy statements attached to the function's role."""
    function_id = _function_logical_id(template, function_prefix)
    role_id = template.to_json()["Resources"][function_id]["Properties"]["Role"][
        "Fn::GetAtt"
    ][0]
    statements = []
    for policy in template.find_resources("AWS::IAM::Policy").values():
        if {"Ref": role_id} not in policy["Properties"]["Roles"]:
            continue
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            actions = statement["Action"]
            if isinstance(actions, str):
                actions = [actions]
            if any(a.startswith("dynamodb:") for a in actions):
                statements.append({**statement, "Action": actions})
    return statements


def _table_actions_for(template, function_prefix):
    """Return the DynamoDB actions granted to the role of the given function."""
    return {
        action
        for statement in _table_statements_for(template, function_prefix)
        for action in statement["Action"]
        if action.startswith("dynamodb:")
    }


def test_patch_and_delete_methods_on_note_resource():
    """PATCH and DELETE are explicit custom-authorized methods on /notes/{id}."""
    template = _template()
    methods = template.find_resources("AWS::ApiGateway::Method")
    by_method = {
        resource["Properties"]["HttpMethod"]: resource["Properties"]
        for resource in methods.values()
    }
    for http_method in ("PATCH", "DELETE"):
        properties = by_method[http_method]
        assert properties["AuthorizationType"] == "CUSTOM"
        assert "AuthorizerId" in properties
        assert properties["Integration"]["Type"] == "AWS_PROXY"
    template.has_resource_properties(
        "AWS::ApiGateway::Resource", {"PathPart": "{id}"}
    )


def test_update_and_delete_functions_have_own_assets_and_table_name():
    """Update and delete use separate code assets, each configured with the table."""
    template = _template()
    functions = template.to_json()["Resources"]
    table_ids = list(template.find_resources("AWS::DynamoDB::Table"))
    assert len(table_ids) == 1
    asset_keys = {}
    for prefix in (
        "NotesCreateFunction",
        "NotesGetFunction",
        "NotesUpdateFunction",
        "NotesDeleteFunction",
    ):
        properties = functions[_function_logical_id(template, prefix)]["Properties"]
        assert properties["Environment"]["Variables"]["TABLE_NAME"] == {
            "Ref": table_ids[0]
        }
        asset_keys[prefix] = properties["Code"]["S3Key"]
    assert len(set(asset_keys.values())) == len(asset_keys)


def test_update_and_delete_have_narrow_dynamodb_actions():
    """Update gets only Get+Update; delete gets only Get+Delete."""
    template = _template()
    assert _table_actions_for(template, "NotesUpdateFunction") == {
        "dynamodb:GetItem",
        "dynamodb:UpdateItem",
    }
    assert _table_actions_for(template, "NotesDeleteFunction") == {
        "dynamodb:GetItem",
        "dynamodb:DeleteItem",
    }
    table_id = next(iter(template.find_resources("AWS::DynamoDB::Table")))
    table_arn = {"Fn::GetAtt": [table_id, "Arn"]}
    for prefix in ("NotesUpdateFunction", "NotesDeleteFunction"):
        statements = _table_statements_for(template, prefix)
        assert len(statements) == 1
        assert statements[0]["Effect"] == "Allow"
        assert statements[0]["Resource"] == [table_arn]
    # Existing operations keep their previous grants; they must not gain
    # the mutating actions reserved for update/delete.
    assert "dynamodb:DeleteItem" not in _table_actions_for(
        template, "NotesGetFunction"
    )
    assert "dynamodb:UpdateItem" not in _table_actions_for(
        template, "NotesGetFunction"
    )
