"""CDK assertions for the Kiro-gated CI/CD pipeline stack."""

import json

from aws_cdk import App, Stack, assertions
from aws_cdk.aws_codebuild import LinuxBuildImage
from aws_cdk.aws_secretsmanager import Secret

from tech_summit.kiro_cli_step import KiroCliStep
from tech_summit.kiro_cli_step_props import KiroCliStepProps
from tech_summit.pipeline_stack import PipelineStack
from tech_summit.source_config import PipelineConfig, SourceConfig


def _template(environment_name: str = "dev") -> assertions.Template:
    app = App()
    stack = PipelineStack(
        app,
        "TechSummitPipelineTestStack",
        config=PipelineConfig(
            application_name="tech-summit-demo",
            environment_name=environment_name,
            source=SourceConfig(
                owner="example",
                repo="tech-summit-demo",
                branch="main",
                connection_arn=(
                    "arn:aws:codeconnections:us-east-1:123456789012:connection/"
                    "00000000-0000-0000-0000-000000000000"
                ),
            ),
            kiro_secret_arn=(
                "arn:aws:secretsmanager:us-east-1:123456789012:"
                "secret:kiro-cli-key-AbCdEf"
            ),
        ),
    )
    return assertions.Template.from_stack(stack)


def _buildspecs(template: assertions.Template) -> list[dict]:
    projects = template.find_resources("AWS::CodeBuild::Project")
    return [
        json.loads(project["Properties"]["Source"]["BuildSpec"])
        for project in projects.values()
    ]


def test_pipeline_resources_are_created():
    """The pipeline has one pipeline and three single-purpose CodeBuild projects."""
    template = _template()
    template.resource_count_is("AWS::CodePipeline::Pipeline", 1)
    template.resource_count_is("AWS::CodeBuild::Project", 3)
    template.has_resource_properties(
        "AWS::CodePipeline::Pipeline",
        {"Name": "tech-summit-demo-dev"},
    )


def test_artifacts_are_encrypted_logged_and_retained():
    """Pipeline artifacts use a rotating KMS key and server access logging."""
    template = _template()
    template.has_resource_properties(
        "AWS::KMS::Key",
        {"EnableKeyRotation": True},
    )
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "BucketEncryption": assertions.Match.any_value(),
            "LoggingConfiguration": assertions.Match.any_value(),
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
            "VersioningConfiguration": {"Status": "Enabled"},
        },
    )


def test_production_requires_manual_approval():
    """Production inserts a manual approval action before deployment."""
    template = _template("prod")
    template.has_resource_properties(
        "AWS::CodePipeline::Pipeline",
        {
            "Stages": assertions.Match.array_with(
                [
                    assertions.Match.object_like(
                        {
                            "Name": "ApproveDeploy",
                            "Actions": assertions.Match.array_with(
                                [
                                    assertions.Match.object_like(
                                        {
                                            "ActionTypeId": assertions.Match.object_like(
                                                {"Category": "Approval"}
                                            )
                                        }
                                    )
                                ]
                            ),
                        }
                    )
                ]
            )
        },
    )


def test_non_production_has_no_manual_approval():
    """Development remains automated after its validation gates pass."""
    template = _template("dev")
    pipeline = next(
        iter(template.find_resources("AWS::CodePipeline::Pipeline").values())
    )
    stage_names = {stage["Name"] for stage in pipeline["Properties"]["Stages"]}
    assert "ApproveDeploy" not in stage_names


def test_agent_commands_are_fail_closed_and_shell_safe():
    """Every agent command uses pipefail and requires an exact final verdict."""
    buildspecs = _buildspecs(_template())
    for buildspec in buildspecs:
        commands = buildspec["phases"]["build"]["commands"]
        assert "set -o pipefail" in commands[0]
        assert "`" not in commands[0]
        assert "tail -n 1" in commands[1]
        assert "missing expected success verdict" in commands[1]


def test_tool_versions_and_download_are_pinned():
    """Builds use exact CDK and Kiro CLI versions with checksum verification."""
    buildspecs = _buildspecs(_template())
    install_commands = [
        command
        for buildspec in buildspecs
        for command in buildspec["phases"]["install"]["commands"]
    ]
    assert any("aws-cdk@2.1139.0" in command for command in install_commands)
    assert all(
        any("/2.28.0/kirocli-x86_64-linux.zip" in command for command in commands)
        for commands in (
            buildspec["phases"]["install"]["commands"] for buildspec in buildspecs
        )
    )
    assert all(
        any("sha256sum -c -" in command for command in commands)
        for commands in (
            buildspec["phases"]["install"]["commands"] for buildspec in buildspecs
        )
    )


def test_secret_environment_variable_selects_json_key():
    """CodeBuild injects only KIRO_API_KEY from the configured JSON secret."""
    projects = _template().find_resources("AWS::CodeBuild::Project")
    for project in projects.values():
        variables = project["Properties"]["Environment"]["EnvironmentVariables"]
        api_key = next(item for item in variables if item["Name"] == "KIRO_API_KEY")
        assert api_key["Value"].endswith(":KIRO_API_KEY")
        assert api_key["Type"] == "SECRETS_MANAGER"


def test_custom_gate_options_are_rendered():
    """Custom output and environment settings are rendered into CodeBuild."""
    app = App()
    stack = Stack(app, "GateTestStack")
    secret = Secret.from_secret_complete_arn(
        stack,
        "ImportedSecret",
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:test-AbCdEf",
    )
    KiroCliStep(
        stack,
        "CustomGate",
        KiroCliStepProps(
            agent="custom-agent",
            gate="CUSTOM_GATE",
            task="Validate safely.",
            kiro_secret=secret,
            build_image=LinuxBuildImage.STANDARD_7_0,
            out_file="custom.out",
            extra_env={"EXTRA_SETTING": "enabled"},
        ),
    )
    template = assertions.Template.from_stack(stack)
    project = next(iter(template.find_resources("AWS::CodeBuild::Project").values()))
    variables = project["Properties"]["Environment"]["EnvironmentVariables"]
    assert {item["Name"]: item["Value"] for item in variables}["EXTRA_SETTING"] == "enabled"
    buildspec = json.loads(project["Properties"]["Source"]["BuildSpec"])
    assert buildspec["artifacts"]["files"] == ["custom.out"]
