"""CDK stack for the Kiro-gated tech_summit CI/CD pipeline."""

from constructs import Construct
from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_iam as iam
from aws_cdk.aws_codebuild import LinuxBuildImage
from aws_cdk.aws_codepipeline import Artifact, Pipeline, StageProps
from aws_cdk.aws_codepipeline_actions import (
    CodeBuildAction,
    CodeStarConnectionsSourceAction,
    ManualApprovalAction,
)
from aws_cdk.aws_kms import Key
from aws_cdk.aws_s3 import BlockPublicAccess, Bucket, BucketEncryption
from aws_cdk.aws_secretsmanager import ISecret, Secret
from cdk_nag import NagSuppressions

from tech_summit.kiro_cli_step import KiroCliStep
from tech_summit.kiro_cli_step_props import KiroCliStepProps
from tech_summit.source_config import PipelineConfig, SourceConfig

_CDK_CLI_VERSION = "2.1139.0"
_GITLEAKS_VERSION = "8.30.1"
_GITLEAKS_SHA256 = "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"


class PipelineStack(Stack):
    """Provision source, validation, approval and deployment stages."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: PipelineConfig,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        kiro_secret = Secret.from_secret_complete_arn(
            self,
            "KiroApiKeySecret",
            config.kiro_secret_arn,
        )
        source_output = Artifact("SourceOutput")
        source_action = self._create_source_action(config.source, source_output)
        projects = self._create_gate_projects(kiro_secret, config.environment_name)
        access_logs_bucket, artifact_bucket = self._create_storage()
        stages = self._create_stages(
            config.environment_name,
            source_action,
            source_output,
            projects,
        )
        pipeline = Pipeline(
            self,
            "TechSummitPipeline",
            pipeline_name=(
                f"{config.application_name}-{config.environment_name}"
            ),
            artifact_bucket=artifact_bucket,
            stages=stages,
        )
        self._add_nag_suppressions(access_logs_bucket, projects, pipeline)

    @staticmethod
    def _create_source_action(
        source: SourceConfig,
        source_output: Artifact,
    ) -> CodeStarConnectionsSourceAction:
        return CodeStarConnectionsSourceAction(
            action_name="Source",
            owner=source.owner,
            repo=source.repo,
            branch=source.branch,
            connection_arn=source.connection_arn,
            output=source_output,
            # Full clone is required so gitleaks can inspect commit history in CI.
            code_build_clone_output=True,
        )

    def _create_gate_projects(
        self,
        kiro_secret: ISecret,
        environment_name: str,
    ) -> tuple[KiroCliStep, KiroCliStep, KiroCliStep]:
        quality_step = KiroCliStep(
            self,
            "QualityCheckProject",
            KiroCliStepProps(
                agent="python-quality-agent",
                gate="QUALITY_GATE",
                task=(
                    "Validate this repository using the configured linting and testing "
                    "skills, then perform the full-repository code-quality review. End "
                    "with the required QUALITY_GATE verdict."
                ),
                kiro_secret=kiro_secret,
                build_image=LinuxBuildImage.STANDARD_7_0,
            ),
        )
        security_step = KiroCliStep(
            self,
            "SecurityCheckProject",
            KiroCliStepProps(
                agent="python-security-agent",
                gate="SECURITY_GATE",
                task=(
                    "Run every configured security scanner and the full-repository "
                    "vulnerability review. End with the required SECURITY_GATE verdict."
                ),
                kiro_secret=kiro_secret,
                build_image=LinuxBuildImage.STANDARD_7_0,
                extra_install=[
                    'curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/'
                    f'v{_GITLEAKS_VERSION}/gitleaks_{_GITLEAKS_VERSION}_linux_x64.tar.gz" '
                    "-o /tmp/gitleaks.tar.gz",
                    f'echo "{_GITLEAKS_SHA256}  /tmp/gitleaks.tar.gz" '
                    "| sha256sum -c -",
                    "tar -xzf /tmp/gitleaks.tar.gz -C /usr/local/bin gitleaks",
                    "rm -f /tmp/gitleaks.tar.gz",
                ],
            ),
        )
        deploy_step = KiroCliStep(
            self,
            "DeployProject",
            KiroCliStepProps(
                agent="deploy-agent",
                gate="DEPLOY",
                success_value="SUCCESS",
                task=(
                    "Deploy the application stack with cdk deploy, using context "
                    f"deployment_type=app and environment={environment_name}, and pass "
                    "--require-approval never. Report stack status and outputs, then end "
                    "with the required DEPLOY verdict."
                ),
                kiro_secret=kiro_secret,
                build_image=LinuxBuildImage.STANDARD_7_0,
                extra_install=[f"npm install -g aws-cdk@{_CDK_CLI_VERSION}"],
                extra_env={
                    "ACCOUNT_ID": self.account,
                    "AWS_REGION": self.region,
                },
            ),
        )
        deploy_step.role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["sts:AssumeRole"],
                resources=[
                    (
                        f"arn:aws:iam::{self.account}:role/cdk-*-deploy-role-"
                        f"{self.account}-{self.region}"
                    ),
                    (
                        f"arn:aws:iam::{self.account}:role/cdk-*-file-publishing-role-"
                        f"{self.account}-{self.region}"
                    ),
                    (
                        f"arn:aws:iam::{self.account}:role/cdk-*-image-publishing-role-"
                        f"{self.account}-{self.region}"
                    ),
                    (
                        f"arn:aws:iam::{self.account}:role/cdk-*-lookup-role-"
                        f"{self.account}-{self.region}"
                    ),
                ],
            )
        )
        return quality_step, security_step, deploy_step

    def _create_storage(self) -> tuple[Bucket, Bucket]:
        access_logs_bucket = Bucket(
            self,
            "PipelineAccessLogsBucket",
            encryption=BucketEncryption.S3_MANAGED,
            block_public_access=BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        artifact_key = Key(
            self,
            "PipelineArtifactsKey",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        artifact_bucket = Bucket(
            self,
            "PipelineArtifactsBucket",
            encryption=BucketEncryption.KMS,
            encryption_key=artifact_key,
            block_public_access=BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            server_access_logs_bucket=access_logs_bucket,
            removal_policy=RemovalPolicy.RETAIN,
        )
        return access_logs_bucket, artifact_bucket

    @staticmethod
    def _create_stages(
        environment_name: str,
        source_action: CodeStarConnectionsSourceAction,
        source_output: Artifact,
        projects: tuple[KiroCliStep, KiroCliStep, KiroCliStep],
    ) -> list[StageProps]:
        quality_step, security_step, deploy_step = projects
        stages = [
            StageProps(stage_name="Source", actions=[source_action]),
            StageProps(
                stage_name="CodeValidation",
                actions=[
                    CodeBuildAction(
                        action_name="QualityCheck",
                        project=quality_step,
                        input=source_output,
                    ),
                    CodeBuildAction(
                        action_name="SecurityCheck",
                        project=security_step,
                        input=source_output,
                    ),
                ],
            ),
        ]
        if environment_name == "prod":
            stages.append(
                StageProps(
                    stage_name="ApproveDeploy",
                    actions=[
                        ManualApprovalAction(
                            action_name="ApproveProductionDeployment",
                            additional_information=(
                                "Review the validated change before production deploy."
                            ),
                        )
                    ],
                )
            )
        stages.append(
            StageProps(
                stage_name="Deploy",
                actions=[
                    CodeBuildAction(
                        action_name="Deploy",
                        project=deploy_step,
                        input=source_output,
                    )
                ],
            )
        )
        return stages

    @staticmethod
    def _add_nag_suppressions(
        access_logs_bucket: Bucket,
        projects: tuple[KiroCliStep, KiroCliStep, KiroCliStep],
        pipeline: Pipeline,
    ) -> None:
        NagSuppressions.add_resource_suppressions(
            access_logs_bucket,
            [
                {
                    "id": "AwsSolutions-S1",
                    "reason": (
                        "This bucket is the dedicated server-access-log destination; "
                        "logging it to itself would create recursive logs."
                    ),
                }
            ],
        )
        for project in projects:
            NagSuppressions.add_resource_suppressions(
                project,
                [
                    {
                        "id": "AwsSolutions-IAM5",
                        "reason": (
                            "CodeBuild uses narrow generated suffix and object-path "
                            "wildcards scoped to its secret, artifact bucket and the "
                            "account-local CDK bootstrap role prefix."
                        ),
                    }
                ],
                apply_to_children=True,
            )
        NagSuppressions.add_resource_suppressions(
            pipeline,
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": (
                        "CodePipeline L2 action roles use action-family and object-path "
                        "wildcards scoped to this pipeline's artifacts and actions."
                    ),
                }
            ],
            apply_to_children=True,
        )
