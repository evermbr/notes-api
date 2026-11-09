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
    template.resource_count_is("AWS::Lambda::Function", 5)


def test_methods_wired_per_operation():
    """The API exposes exactly POST, GET, PATCH and DELETE operations."""
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


def _dynamodb_actions_by_role(template: assertions.Template) -> dict:
    """Map each execution-role logical ID to its allowed DynamoDB actions."""
    result = {}
    for policy in template.find_resources("AWS::IAM::Policy").values():
        roles = policy["Properties"]["Roles"]
        role_ref = roles[0]["Ref"]
        actions = set()
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            action = statement["Action"]
            action = [action] if isinstance(action, str) else action
            actions.update(a for a in action if a.startswith("dynamodb:"))
        if actions:
            result[role_ref] = actions
    return result


def _function_role_ids(template: assertions.Template) -> dict:
    """Map each Lambda function logical ID to its role logical ID (via Role)."""
    return {
        function_id: function["Properties"]["Role"]["Fn::GetAtt"][0]
        for function_id, function in template.find_resources(
            "AWS::Lambda::Function"
        ).items()
        if "Fn::GetAtt" in function["Properties"].get("Role", {})
    }


def _actions_for_function(template: assertions.Template, prefix: str) -> set:
    """Return the DynamoDB actions granted to the one function with this ID prefix."""
    role_ids = [
        role_id
        for function_id, role_id in _function_role_ids(template).items()
        if function_id.startswith(prefix)
    ]
    assert len(role_ids) == 1
    return _dynamodb_actions_by_role(template).get(role_ids[0], set())


def test_update_and_delete_lambdas_use_dedicated_assets():
    """Update and delete are separate Functions with the table name configured."""
    template = _template()
    functions = template.find_resources("AWS::Lambda::Function")
    table_envs = [
        fn
        for fn in functions.values()
        if "TABLE_NAME" in fn["Properties"].get("Environment", {}).get("Variables", {})
    ]
    assert len(table_envs) == 4
    for logical_prefix in ("NotesUpdateFunction", "NotesDeleteFunction"):
        assert any(key.startswith(logical_prefix) for key in functions)


def test_patch_and_delete_methods_on_note_resource():
    """PATCH and DELETE are explicit, custom-authorized methods on /notes/{id}."""
    template = _template()
    resources = template.find_resources("AWS::ApiGateway::Resource")
    note_ids = [
        key
        for key, res in resources.items()
        if res["Properties"]["PathPart"] == "{id}"
    ]
    assert len(note_ids) == 1
    methods = template.find_resources("AWS::ApiGateway::Method")
    by_verb = {m["Properties"]["HttpMethod"]: m["Properties"] for m in methods.values()}
    for verb in ("GET", "PATCH", "DELETE"):
        assert by_verb[verb]["ResourceId"] == {"Ref": note_ids[0]}
        assert by_verb[verb]["AuthorizationType"] == "CUSTOM"
        assert "AuthorizerId" in by_verb[verb]
        assert by_verb[verb]["Integration"]["Type"] == "AWS_PROXY"
    assert methods and len(methods) == 4


def test_update_and_delete_have_exact_narrow_actions():
    """Update gets only Get+Update; delete gets only Get+Delete on the table."""
    template = _template()
    assert _actions_for_function(template, "NotesUpdateFunction") == {
        "dynamodb:GetItem",
        "dynamodb:UpdateItem",
    }
    assert _actions_for_function(template, "NotesDeleteFunction") == {
        "dynamodb:GetItem",
        "dynamodb:DeleteItem",
    }
    actions = _dynamodb_actions_by_role(template)
    for acts in actions.values():
        assert "dynamodb:*" not in acts
