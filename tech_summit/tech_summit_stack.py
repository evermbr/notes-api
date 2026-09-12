"""CDK stack for the tech_summit workload.

A small URL shortener: a Lambda behind a Function URL, backed by a DynamoDB table.
"""

from constructs import Construct
from aws_cdk import (
    Duration,
    Stack,
    RemovalPolicy,
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
)


class TechSummitStack(Stack):
    """Provisions a DynamoDB-backed URL shortener Lambda exposed via a Function URL."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        table = dynamodb.Table(
            self, "UrlShortenerTable",
            partition_key=dynamodb.Attribute(
                name="code", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery=True,
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

        shortener.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
        )
