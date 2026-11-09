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

        note_fns = self._create_note_functions(table)

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
            handler=note_fns["create"],
            proxy=False,
            deploy_options=StageOptions(stage_name=environment_name),
            default_method_options=MethodOptions(
                authorizer=authorizer,
                authorization_type=AuthorizationType.CUSTOM,
            ),
        )

        self._add_routes(api, note_fns)

        # The Lambda service roles use the standard CDK-managed basic execution
        # policy solely for CloudWatch Logs. Python 3.13 is deliberately pinned for the
        # demo rather than tracking a newly released runtime automatically.
        NagSuppressions.add_resource_suppressions(
            [*note_fns.values(), authorizer_fn],
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

        # CDK creates the account-level API Gateway CloudWatch role only when the
        # ``disableCloudWatchRole`` feature flag is off (it is on in cdk.json, so the
        # role is absent there). When present, it is the CDK-generated role that uses
        # the AWS-managed push-to-CloudWatch-Logs policy, the only policy API Gateway
        # accepts for this purpose; it grants no access to customer data.
        cloud_watch_role = api.node.try_find_child("CloudWatchRole")
        if cloud_watch_role is not None:
            NagSuppressions.add_resource_suppressions(
                cloud_watch_role,
                [
                    {
                        "id": "AwsSolutions-IAM4",
                        "reason": (
                            "API Gateway's account-level CloudWatch role uses the "
                            "AWS-managed AmazonAPIGatewayPushToCloudWatchLogs policy, "
                            "which only allows writing logs and is the policy AWS "
                            "documents for this role."
                        ),
                        "appliesTo": [
                            "Policy::arn:<AWS::Partition>:iam::aws:policy/"
                            "service-role/AmazonAPIGatewayPushToCloudWatchLogs"
                        ],
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

    def _create_note_functions(self, table: Table) -> dict[str, Function]:
        """Create one single-purpose Lambda per operation with its narrow table grant."""
        functions = {
            "create": self._notes_function("NotesCreateFunction", "notes_create", table),
            "get": self._notes_function("NotesGetFunction", "notes_get", table),
            "update": self._notes_function("NotesUpdateFunction", "notes_update", table),
            "delete": self._notes_function("NotesDeleteFunction", "notes_delete", table),
        }
        table.grant_write_data(functions["create"])
        table.grant_read_data(functions["get"])
        # Update and delete are version-checked: each needs GetItem to tell a missing
        # note (404) from a stale version (409), plus exactly one mutating action.
        table.grant(functions["update"], "dynamodb:GetItem", "dynamodb:UpdateItem")
        table.grant(functions["delete"], "dynamodb:GetItem", "dynamodb:DeleteItem")
        return functions

    @staticmethod
    def _add_routes(api: LambdaRestApi, functions: dict[str, Function]) -> None:
        """Add explicit methods; the default custom authorizer protects every one."""
        notes = api.root.add_resource("notes")
        notes.add_method("POST", LambdaIntegration(functions["create"]))

        note = notes.add_resource("{id}")
        note.add_method("GET", LambdaIntegration(functions["get"]))
        note.add_method("PATCH", LambdaIntegration(functions["update"]))
        note.add_method("DELETE", LambdaIntegration(functions["delete"]))

    def _notes_function(self, construct_id: str, asset_dir: str, table: Table) -> Function:
        """Create one single-purpose Notes Lambda bound to the Notes table name."""
        return Function(
            self,
            construct_id,
            runtime=Runtime.PYTHON_3_13,
            handler="handler.handler",
            code=Code.from_asset(f"lambda/{asset_dir}"),
            timeout=Duration.seconds(10),
            environment={"TABLE_NAME": table.table_name},
        )
