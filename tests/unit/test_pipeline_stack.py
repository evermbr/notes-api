"""CDK assertions for the Kiro-gated CI/CD pipeline stack."""

import json
import subprocess

import pytest
from aws_cdk import App, Environment, Stack, assertions
from aws_cdk.aws_codebuild import LinuxBuildImage
from aws_cdk.aws_secretsmanager import Secret

from tech_summit.kiro_cli_step import KiroCliStep, _verdict_check_command
from tech_summit.kiro_cli_step_props import KiroCliStepProps
from tech_summit.pipeline_stack import PipelineStack
from tech_summit.source_config import PipelineConfig, SourceConfig


def _template(environment_name: str = "dev") -> assertions.Template:
    app = App()
    stack = PipelineStack(
        app,
        "TechSummitPipelineTestStack",
        env=Environment(account="123456789012", region="us-east-1"),
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
        assert "--v3" in commands[0]
        assert "--model" not in commands[0]
        assert "--trust-all-tools" in commands[0]
        assert "PIPESTATUS[0]" in commands[0]
        assert "2>&1" not in commands[0]
        assert "`" not in commands[0]
        assert "python3 -c" in commands[1]
        assert "expected exactly one success verdict" in commands[1]


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


def test_secret_environment_variable_uses_plain_secret_string():
    """CodeBuild injects the complete plain-text SecretString as KIRO_API_KEY."""
    projects = _template().find_resources("AWS::CodeBuild::Project")
    for project in projects.values():
        variables = project["Properties"]["Environment"]["EnvironmentVariables"]
        api_key = next(item for item in variables if item["Name"] == "KIRO_API_KEY")
        assert not api_key["Value"].endswith(":KIRO_API_KEY")
        assert api_key["Value"].endswith("secret:kiro-cli-key-AbCdEf")
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


def _run_verdict_check(tmp_path, output: str) -> int:
    """Execute the generated verdict checker against captured agent output."""
    output_file = tmp_path / "agent.out"
    output_file.write_text(output, encoding="utf-8")
    command = _verdict_check_command(
        str(output_file),
        "SECURITY_GATE",
        "PASS",
    )
    completed = subprocess.run(
        ["bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode


@pytest.mark.parametrize(
    ("output", "expected_exit"),
    [
        ("summary\nSECURITY_GATE: PASS\n```\n", 0),
        ("summary without verdict\n", 1),
        ("SECURITY_GATE: PASS\nSECURITY_GATE: PASS\n", 1),
        ("SECURITY_GATE: PASS\nSECURITY_GATE: FAIL\n", 1),
        ("SECURITY_GATE: PASS[INFO] runtime log\n", 1),
    ],
)
def test_verdict_check_is_markdown_tolerant_and_fail_closed(
    tmp_path,
    output,
    expected_exit,
):
    """The checker tolerates fences but rejects missing or ambiguous verdicts."""
    assert _run_verdict_check(tmp_path, output) == expected_exit


def test_gitleaks_download_is_checksum_verified():
    """The security project verifies gitleaks before extracting its binary."""
    install_commands = [
        command
        for buildspec in _buildspecs(_template())
        for command in buildspec["phases"]["install"]["commands"]
    ]
    assert any("551f6fc83ea457d62a0d98237cbad105" in command for command in install_commands)
    assert any(
        "/tmp/gitleaks.tar.gz" in command and "sha256sum -c" in command
        for command in install_commands
    )
    assert not any("| tar" in command for command in install_commands)


def test_deploy_project_cannot_assume_cloudformation_execution_role():
    """The deploy project assumes only the CDK roles needed by the CLI workflow."""
    policies = _template().find_resources("AWS::IAM::Policy")
    assume_role_resources = []
    for policy in policies.values():
        statements = policy["Properties"]["PolicyDocument"]["Statement"]
        for statement in statements:
            actions = statement["Action"]
            actions = [actions] if isinstance(actions, str) else actions
            if "sts:AssumeRole" in actions:
                resources = statement["Resource"]
                candidates = [resources] if isinstance(resources, str) else resources
                if isinstance(candidates, list):
                    assume_role_resources.extend(
                        resource
                        for resource in candidates
                        if isinstance(resource, str) and ":role/cdk-" in resource
                    )

    assert len(assume_role_resources) == 4
    assert all("cloudformation-exec-role" not in arn for arn in assume_role_resources)
    assert all(
        any(
            role_kind in arn
            for role_kind in (
                "deploy-role",
                "file-publishing-role",
                "image-publishing-role",
                "lookup-role",
            )
        )
        for arn in assume_role_resources
    )


def test_deploy_project_receives_target_environment():
    """The deploy agent receives the account and Region required by app.py."""
    projects = _template().find_resources("AWS::CodeBuild::Project")
    deploy_project = next(
        project
        for logical_id, project in projects.items()
        if logical_id.startswith("DeployProject")
    )
    variables = deploy_project["Properties"]["Environment"]["EnvironmentVariables"]
    values = {variable["Name"]: variable["Value"] for variable in variables}
    assert values["ACCOUNT_ID"] == "123456789012"
    assert values["AWS_REGION"] == "us-east-1"
