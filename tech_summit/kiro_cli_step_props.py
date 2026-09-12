"""Configuration object for :class:`~tech_summit.kiro_cli_step.KiroCliStep`."""

from dataclasses import dataclass, field

from aws_cdk.aws_codebuild import IBuildImage
from aws_cdk.aws_secretsmanager import ISecret


@dataclass
class KiroCliStepProps:  # pylint: disable=too-many-instance-attributes  # cohesive config object for a build step
    """Inputs for a KiroCliStep: which agent to run, its gate token, and build shaping."""

    agent: str
    gate: str
    task: str
    kiro_secret: ISecret
    build_image: IBuildImage
    out_file: str | None = None
    extra_install: list[str] = field(default_factory=list)
    extra_env: dict = field(default_factory=dict)
