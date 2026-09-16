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
            runtime=Runtime.PYTHON_3_12,
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
            runtime=Runtime.PYTHON_3_12,
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
        LambdaRestApi(
            self,
            "UrlShortenerApi",
            handler=shortener,
            proxy=True,
            default_method_options=MethodOptions(
                authorizer=authorizer,
                authorization_type=AuthorizationType.CUSTOM,
            ),
        )
