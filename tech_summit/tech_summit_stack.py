"""CDK stack for the tech_summit workload.

A small URL shortener: a Lambda fronted by an API Gateway REST API (protected by a
Lambda token authorizer that checks an auto-generated Secrets Manager API key), backed
by a DynamoDB table.
"""

from constructs import Construct
from aws_cdk import (
    Duration,
    Stack,
    RemovalPolicy,
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
    aws_apigateway as apigw,
    aws_secretsmanager as secretsmanager,
)


class TechSummitStack(Stack):
    """Provisions a DynamoDB-backed URL shortener Lambda fronted by API Gateway."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        table = dynamodb.Table(
            self, "UrlShortenerTable",
            partition_key=dynamodb.Attribute(
                name="code", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True,
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )

        shortener = lambda_.Function(
            self, "UrlShortenerFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset("lambda/url_shortener"),
            timeout=Duration.seconds(10),
            environment={"TABLE_NAME": table.table_name},
        )

        # Least privilege: the function only needs read/write on this table.
        table.grant_read_write_data(shortener)

        # --- API key + Lambda authorizer -------------------------------------
        # Secrets Manager auto-generates the API key; nothing is hard-coded.
        api_key_secret = secretsmanager.Secret(
            self, "ApiKeySecret",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                password_length=40,
                exclude_punctuation=True,
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )

        authorizer_fn = lambda_.Function(
            self, "ApiAuthorizerFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset("lambda/authorizer"),
            timeout=Duration.seconds(10),
            environment={"API_KEY_SECRET_ARN": api_key_secret.secret_arn},
        )
        api_key_secret.grant_read(authorizer_fn)

        authorizer = apigw.TokenAuthorizer(
            self, "ApiTokenAuthorizer",
            handler=authorizer_fn,
            # Cache authorizer results per token to avoid invoking the Lambda every call.
            results_cache_ttl=Duration.minutes(5),
        )

        # API Gateway REST API fronting the Lambda. Every method/path is proxied to the
        # handler and protected by the token authorizer.
        apigw.LambdaRestApi(
            self, "UrlShortenerApi",
            handler=shortener,
            proxy=True,
            default_method_options=apigw.MethodOptions(
                authorizer=authorizer,
                authorization_type=apigw.AuthorizationType.CUSTOM,
            ),
        )
