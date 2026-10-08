"""Typed configuration objects for the tech_summit pipeline."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceConfig:
    """Describe the GitHub repository consumed by CodePipeline."""

    owner: str
    repo: str
    branch: str
    connection_arn: str


@dataclass(frozen=True)
class PipelineConfig:
    """Group application, environment, source and Kiro settings for the pipeline."""

    application_name: str
    environment_name: str
    source: SourceConfig
    kiro_secret_arn: str
