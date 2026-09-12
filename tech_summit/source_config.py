"""Configuration objects for the tech_summit pipeline."""

from dataclasses import dataclass


@dataclass
class SourceConfig:
    """GitHub source settings for the pipeline (resolved by the caller, e.g. from context)."""

    owner: str
    repo: str
    branch: str
    connection_arn: str
