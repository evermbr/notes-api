"""CDK stack for the tech_summit Notes API workload.

The API uses one single-purpose Lambda per operation behind API Gateway, a shared
DynamoDB table, and a Lambda token authorizer backed by Secrets Manager.
"""

from constructs import Construct
from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk.aws_apigateway import (
    AuthorizationType,
    LambdaIntegration,
    LambdaRestApi,
    MethodOptions,
    StageOptions,
    TokenAuthorizer,
)
from aws_cdk.aws_dynamodb import (
    Attribute,
    AttributeType,
    BillingMode,
    PointInTimeRecoverySpecification,
    Table,
    TableEncryption,
)
from aws_cdk.aws_lambda import Code, Function, Runtime
from aws_cdk.aws_secretsmanager import Secret, SecretStringGenerator
from cdk_nag import NagSuppressions


class TechSummitStack(Stack):
    """Provision the authenticated, DynamoDB-backed Notes API."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        environment_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        data_removal_policy = (
            RemovalPolicy.RETAIN
            if environment_name == "prod"
            else RemovalPolicy.DESTROY
        )

        table = Table(
            self,
            "NotesTable",
            partition_key=Attribute(name="id", type=AttributeType.STRING),
            billing_mode=BillingMode.PAY_PER_REQUEST,
            encryption=TableEncryption.AWS_MANAGED,
            point_in_time_recovery_specification=PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True,
            ),
            deletion_protection=environment_name == "prod",
            removal_policy=data_removal_policy,
        )

        # One single-purpose Lambda per operation; each gets only the table access
        # required for that operation.
        create_fn = Function(
            self,
            "NotesCreateFunction",
            runtime=Runtime.PYTHON_3_13,
            handler="handler.handler",
            code=Code.from_asset("lambda/notes_create"),
            timeout=Duration.seconds(10),
            environment={"TABLE_NAME": table.table_name},
        )
        table.grant_write_data(create_fn)

        get_fn = Function(
            self,
            "NotesGetFunction",
            runtime=Runtime.PYTHON_3_13,
            handler="handler.handler",
            code=Code.from_asset("lambda/notes_get"),
            timeout=Duration.seconds(10),
            environment={"TABLE_NAME": table.table_name},
        )
        table.grant_read_data(get_fn)

        # Secrets Manager generates the API key; no credential is stored in source.
        api_key_secret = Secret(
            self,
            "ApiKeySecret",
            generate_secret_string=SecretStringGenerator(
                password_length=40,
                exclude_punctuation=True,
            ),
            removal_policy=data_removal_policy,
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
            results_cache_ttl=Duration.minutes(5),
        )

        # proxy=False keeps routing explicit: every operation maps to its own Lambda.
        # LambdaRestApi still requires a default handler, but it creates no catch-all
        # method in this mode; create_fn is therefore only a construction-time default.
        api = LambdaRestApi(
            self,
            "NotesApi",
            handler=create_fn,
            proxy=False,
            deploy_options=StageOptions(stage_name=environment_name),
            default_method_options=MethodOptions(
                authorizer=authorizer,
                authorization_type=AuthorizationType.CUSTOM,
            ),
        )

        notes = api.root.add_resource("notes")
        notes.add_method("POST", LambdaIntegration(create_fn))

        note = notes.add_resource("{id}")
        note.add_method("GET", LambdaIntegration(get_fn))

        # The three Lambda service roles use the standard CDK-managed basic execution
        # policy solely for CloudWatch Logs. Python 3.13 is deliberately pinned for the
        # demo rather than tracking a newly released runtime automatically.
        NagSuppressions.add_resource_suppressions(
            [create_fn, get_fn, authorizer_fn],
            [
                {
                    "id": "AwsSolutions-IAM4",
                    "reason": (
                        "Lambda execution role uses AWSLambdaBasicExecutionRole only "
                        "for standard CloudWatch Logs write access."
                    ),
                    "appliesTo": [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/"
                        "service-role/AWSLambdaBasicExecutionRole"
                    ],
                },
                {
                    "id": "AwsSolutions-L1",
                    "reason": (
                        "Python 3.13 is the supported runtime selected for this demo; "
                        "runtime upgrades are tested and performed deliberately."
                    ),
                },
            ],
            apply_to_children=True,
        )

        NagSuppressions.add_resource_suppressions(
            api_key_secret,
            [
                {
                    "id": "AwsSolutions-SMG4",
                    "reason": (
                        "This demo uses a generated API key without a rotation-aware "
                        "consumer; automatic rotation is outside its scope."
                    ),
                },
            ],
        )

        # The API is intentionally small and guarded by a custom token authorizer. The
        # suppressed controls are documented demo trade-offs rather than silent skips.
        NagSuppressions.add_resource_suppressions(
            api,
            [
                {
                    "id": "AwsSolutions-APIG1",
                    "reason": "Access logging is outside the scope of this short demo.",
                },
                {
                    "id": "AwsSolutions-APIG2",
                    "reason": "Each single-purpose Lambda validates its request input.",
                },
                {
                    "id": "AwsSolutions-APIG6",
                    "reason": "Per-method logging is outside the scope of this demo.",
                },
                {
                    "id": "AwsSolutions-APIG3",
                    "reason": "A WAF web ACL is outside the scope of this demo.",
                },
                {
                    "id": "AwsSolutions-COG4",
                    "reason": (
                        "Every method uses the intended Lambda token authorizer instead "
                        "of a Cognito user-pool authorizer."
                    ),
                },
            ],
            apply_to_children=True,
        )
