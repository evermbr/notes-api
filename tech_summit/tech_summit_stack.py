"""CDK stack for the tech_summit workload.

A small URL shortener: a Lambda fronted by an API Gateway REST API (protected by a
Lambda token authorizer that checks an auto-generated Secrets Manager API key), backed
by a DynamoDB table.
"""

from constructs import Construct
from aws_cdk import Duration, Stack, RemovalPolicy
from aws_cdk.aws_dynamodb import (
    Table,
    Attribute,
    AttributeType,
    BillingMode,
    TableEncryption,
    PointInTimeRecoverySpecification,
)
from aws_cdk.aws_lambda import Function, Runtime, Code
from aws_cdk.aws_apigateway import (
    LambdaRestApi,
    TokenAuthorizer,
    MethodOptions,
    AuthorizationType,
)
from aws_cdk.aws_secretsmanager import Secret, SecretStringGenerator
from cdk_nag import NagSuppressions


class TechSummitStack(Stack):
    """Provisions a DynamoDB-backed URL shortener Lambda fronted by API Gateway."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        table = Table(
            self,
            "UrlShortenerTable",
            partition_key=Attribute(name="code", type=AttributeType.STRING),
            billing_mode=BillingMode.PAY_PER_REQUEST,
            encryption=TableEncryption.AWS_MANAGED,
            point_in_time_recovery_specification=PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True,
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )

        shortener = Function(
            self,
            "UrlShortenerFunction",
            runtime=Runtime.PYTHON_3_13,
            handler="handler.handler",
            code=Code.from_asset("lambda/url_shortener"),
            timeout=Duration.seconds(10),
            environment={"TABLE_NAME": table.table_name},
        )

        # Least privilege: the function only needs read/write on this table.
        table.grant_read_write_data(shortener)

        # --- API key + Lambda authorizer -------------------------------------
        # Secrets Manager auto-generates the API key; nothing is hard-coded.
        api_key_secret = Secret(
            self,
            "ApiKeySecret",
            generate_secret_string=SecretStringGenerator(
                password_length=40,
                exclude_punctuation=True,
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )

        authorizer_fn = Function(
            self,
            "ApiAuthorizerFunction",
            runtime=Runtime.PYTHON_3_13,
            handler="handler.handler",
            code=Code.from_asset("lambda/authorizer"),
            timeout=Duration.seconds(10),
            environment={"API_KEY_SECRET_ARN": api_key_secret.secret_arn},
        )
        api_key_secret.grant_read(authorizer_fn)

        authorizer = TokenAuthorizer(
            self,
            "ApiTokenAuthorizer",
            handler=authorizer_fn,
            # Cache authorizer results per token to avoid invoking the Lambda every call.
            results_cache_ttl=Duration.minutes(5),
        )

        # API Gateway REST API fronting the Lambda. Every method/path is proxied to the
        # handler and protected by the token authorizer.
        api = LambdaRestApi(
            self,
            "UrlShortenerApi",
            handler=shortener,
            proxy=True,
            default_method_options=MethodOptions(
                authorizer=authorizer,
                authorization_type=AuthorizationType.CUSTOM,
            ),
        )

        # cdk-nag suppressions ------------------------------------------------
        # AwsSolutions-IAM4: both Lambda service roles use the AWS-managed
        # AWSLambdaBasicExecutionRole. That policy only grants CloudWatch Logs
        # write access (the standard CDK Lambda default); replacing it with a
        # customer-managed policy would just duplicate those same permissions.
        NagSuppressions.add_resource_suppressions(
            [shortener, authorizer_fn],
            [
                {
                    "id": "AwsSolutions-IAM4",
                    "reason": (
                        "Lambda execution role uses the AWS-managed "
                        "AWSLambdaBasicExecutionRole for CloudWatch Logs access only; "
                        "a customer-managed policy would merely replicate it."
                    ),
                    "appliesTo": [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/"
                        "service-role/AWSLambdaBasicExecutionRole"
                    ],
                },
                {
                    "id": "AwsSolutions-L1",
                    "reason": (
                        "Pinned to Python 3.13, a supported and stable Lambda runtime. "
                        "We intentionally stay one minor behind the newest (3.14) rather "
                        "than track the bleeding edge on every CDK release."
                    ),
                },
            ],
            apply_to_children=True,
        )

        # AwsSolutions-SMG4: the API key secret has no automatic rotation. Rotation
        # needs a rotation Lambda + a consumer that tolerates key changes; out of scope
        # for this demo, where the key is static and read by the authorizer.
        NagSuppressions.add_resource_suppressions(
            api_key_secret,
            [
                {
                    "id": "AwsSolutions-SMG4",
                    "reason": (
                        "Static API key for a demo; automatic rotation is out of scope "
                        "(no rotation Lambda / rotation-aware consumer)."
                    ),
                },
            ],
        )

        # API Gateway findings — out of scope for this demo. The API already sits behind
        # a Lambda token authorizer; access/method logging, request validation, WAF and a
        # Cognito user pool are production hardening we intentionally skip here.
        NagSuppressions.add_resource_suppressions(
            api,
            [
                {
                    "id": "AwsSolutions-APIG1",
                    "reason": "Access logging out of scope for the demo.",
                },
                {
                    "id": "AwsSolutions-APIG2",
                    "reason": "Request validation out of scope; the Lambda validates input.",
                },
                {
                    "id": "AwsSolutions-APIG6",
                    "reason": "Per-method CloudWatch logging out of scope for the demo.",
                },
                {
                    "id": "AwsSolutions-APIG3",
                    "reason": "WAFv2 web ACL out of scope for the demo.",
                },
                {
                    "id": "AwsSolutions-COG4",
                    "reason": (
                        "The API is guarded by a Lambda token authorizer by design; a "
                        "Cognito user pool is not used for this demo."
                    ),
                },
            ],
            apply_to_children=True,
        )
