"""CDK stack for the tech_summit CI pipeline.

A classic CodePipeline with three stages:

  * Source        — pulls the code from GitHub via an AWS CodeConnections connection.
  * QualityCheck  — a KiroCliStep running the python-quality-agent (headless).
  * SecurityCheck — a KiroCliStep running the python-security-agent (headless).
"""

from constructs import Construct
from aws_cdk import Stack
from aws_cdk.aws_codepipeline import Pipeline, Artifact, StageProps
from aws_cdk.aws_codepipeline_actions import (
    CodeStarConnectionsSourceAction,
    CodeBuildAction,
)
from aws_cdk.aws_codebuild import LinuxBuildImage
from aws_cdk.aws_secretsmanager import Secret
from aws_cdk import aws_iam as iam

from tech_summit.source_config import SourceConfig
from tech_summit.kiro_cli_step import KiroCliStep
from tech_summit.kiro_cli_step_props import KiroCliStepProps


class PipelineStack(Stack):
    """CodePipeline: Source (GitHub/CodeConnections) → QualityCheck → SecurityCheck."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        source: SourceConfig,
        kiro_secret_arn: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- Kiro CLI API key from Secrets Manager ---------------------------
        # The secret is expected to be a JSON secret with a "KIRO_API_KEY" field.
        kiro_secret = Secret.from_secret_complete_arn(
            self, "KiroApiKeySecret", kiro_secret_arn
        )

        # --- source ----------------------------------------------------------
        source_output = Artifact("SourceOutput")
        source_action = CodeStarConnectionsSourceAction(
            action_name="Source",
            owner=source.owner,
            repo=source.repo,
            branch=source.branch,
            connection_arn=source.connection_arn,
            output=source_output,
            # Full git clone (includes .git) so gitleaks can scan commit history.
            code_build_clone_output=True,
        )

        # --- gate steps (Kiro CLI agents, headless) --------------------------
        quality_step = KiroCliStep(self, "QualityCheckProject", KiroCliStepProps(
            agent="python-quality-agent",
            gate="QUALITY_GATE",
            task=(
                "Validate the code quality of this repository. Follow your prompt: run "
                "pylint, pytest and coverage, then your full-repo AI code-quality review. "
                "Output the summary table and end with the QUALITY_GATE verdict."
            ),
            kiro_secret=kiro_secret,
            build_image=LinuxBuildImage.STANDARD_7_0,
        ))
        security_step = KiroCliStep(self, "SecurityCheckProject", KiroCliStepProps(
            agent="python-security-agent",
            gate="SECURITY_GATE",
            task=(
                "Validate the security of this repository. Follow your prompt: run bandit, "
                "pip-audit and gitleaks, then your full-repo AI vulnerability review. "
                "Output the summary table and end with the SECURITY_GATE verdict."
            ),
            kiro_secret=kiro_secret,
            build_image=LinuxBuildImage.STANDARD_7_0,
            # gitleaks (pinned linux_x64 asset). CI=true (set by KiroCliStep) makes the
            # agent run `gitleaks detect` over the full git history.
            extra_install=[
                'curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/'
                'v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz" '
                "| tar -xz -C /usr/local/bin gitleaks"
            ],
        ))

        # --- deploy step (Kiro CLI deploy agent, headless) -------------------
        deploy_step = KiroCliStep(self, "DeployProject", KiroCliStepProps(
            agent="deploy-agent",
            gate="DEPLOY",
            task=(
                "Deploy the application stack: run "
                "`cdk deploy -c deployment_type=app --require-approval never`. "
                "Report the stack status and outputs, and end with the DEPLOY verdict."
            ),
            kiro_secret=kiro_secret,
            build_image=LinuxBuildImage.STANDARD_7_0,
            # Node is needed for the AWS CDK CLI in the deploy step.
            extra_install=["npm install -g aws-cdk"],
        ))
        # Least privilege: `cdk deploy` (bootstrap v2) works by assuming the CDK
        # bootstrap roles, which in turn talk to CloudFormation and create resources.
        # The CodeBuild role therefore only needs to assume those cdk-* roles.
        deploy_step.role.add_to_principal_policy(iam.PolicyStatement(
            actions=["sts:AssumeRole"],
            resources=[f"arn:aws:iam::{self.account}:role/cdk-*"],
        ))

        quality_action = CodeBuildAction(
            action_name="QualityCheck",
            project=quality_step,
            input=source_output,
        )
        security_action = CodeBuildAction(
            action_name="SecurityCheck",
            project=security_step,
            input=source_output,
        )
        deploy_action = CodeBuildAction(
            action_name="Deploy",
            project=deploy_step,
            input=source_output,
        )

        # --- pipeline --------------------------------------------------------
        # QualityCheck and SecurityCheck share the CodeValidation stage, so they
        # run in parallel (both default to run order 1).
        Pipeline(
            self, "TechSummitPipeline",
            pipeline_name="tech-summit-demo",
            stages=[
                StageProps(stage_name="Source", actions=[source_action]),
                StageProps(
                    stage_name="CodeValidation",
                    actions=[quality_action, security_action],
                ),
                StageProps(stage_name="Deploy", actions=[deploy_action]),
            ],
        )
