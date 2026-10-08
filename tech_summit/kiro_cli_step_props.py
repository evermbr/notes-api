"""Configuration for the reusable Kiro CLI CodeBuild gate construct."""

from dataclasses import dataclass, field

from aws_cdk.aws_codebuild import IBuildImage
from aws_cdk.aws_secretsmanager import ISecret


@dataclass
class KiroCliStepProps:  # pylint: disable=too-many-instance-attributes  # cohesive gate configuration
    """Describe the agent, verdict and CodeBuild environment for one gate."""

    agent: str
    gate: str
    task: str
    kiro_secret: ISecret
    build_image: IBuildImage
    success_value: str = "PASS"
    out_file: str | None = None
    extra_install: list[str] = field(default_factory=list)
    extra_env: dict[str, str] = field(default_factory=dict)
