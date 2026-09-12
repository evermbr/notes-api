import aws_cdk as core
import aws_cdk.assertions as assertions
from tech_summit.tech_summit_stack import TechSummitStack


def test_no_sns_or_sqs():
    app = core.App()
    stack = TechSummitStack(app, "tech-summit")
    template = assertions.Template.from_stack(stack)

    template.resource_count_is("AWS::SQS::Queue", 0)
    template.resource_count_is("AWS::SNS::Topic", 0)


def test_dynamodb_table_created():
    app = core.App()
    stack = TechSummitStack(app, "tech-summit")
    template = assertions.Template.from_stack(stack)

    template.has_resource_properties("AWS::DynamoDB::Table", {
        "BillingMode": "PAY_PER_REQUEST",
        "KeySchema": [{"AttributeName": "code", "KeyType": "HASH"}],
    })


def test_shortener_lambda_created():
    app = core.App()
    stack = TechSummitStack(app, "tech-summit")
    template = assertions.Template.from_stack(stack)

    template.has_resource_properties("AWS::Lambda::Function", {
        "Handler": "handler.handler",
        "Runtime": "python3.12",
    })


def test_api_gateway_created():
    app = core.App()
    stack = TechSummitStack(app, "tech-summit")
    template = assertions.Template.from_stack(stack)

    # REST API fronting the Lambda (no public Function URL).
    template.resource_count_is("AWS::ApiGateway::RestApi", 1)
    template.resource_count_is("AWS::Lambda::Url", 0)


def test_api_key_secret_created():
    app = core.App()
    stack = TechSummitStack(app, "tech-summit")
    template = assertions.Template.from_stack(stack)

    # Secrets Manager auto-generates the API key.
    template.resource_count_is("AWS::SecretsManager::Secret", 1)


def test_api_protected_by_custom_authorizer():
    app = core.App()
    stack = TechSummitStack(app, "tech-summit")
    template = assertions.Template.from_stack(stack)

    # A Lambda token authorizer guards the API...
    template.has_resource_properties("AWS::ApiGateway::Authorizer", {
        "Type": "TOKEN",
    })
    # ...and methods require CUSTOM authorization.
    template.has_resource_properties("AWS::ApiGateway::Method", {
        "AuthorizationType": "CUSTOM",
    })
