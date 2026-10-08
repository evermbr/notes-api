#!/usr/bin/env python3
"""CDK application entry point.

``deployment_type`` selects the application or pipeline stack; ``environment`` selects
its deployment stage (``dev``, ``staging`` or ``prod``). Both stacks require the target
AWS account and Region through ``ACCOUNT_ID`` and ``AWS_REGION``. The cdk-nag
``AwsSolutionsChecks`` aspect is applied to every stack.
"""

import os

from aws_cdk import App, Aspects, Environment, Tags
from cdk_nag import AwsSolutionsChecks

from tech_summit.pipeline_stack import PipelineStack
from tech_summit.source_config import PipelineConfig, SourceConfig
from tech_summit.tech_summit_stack import TechSummitStack

_ALLOWED_DEPLOYMENT_TYPES = {"app", "pipeline"}
_ALLOWED_ENVIRONMENTS = {"dev", "staging", "prod"}
_ALLOWED_DATA_CLASSIFICATIONS = {"public", "internal", "confidential", "restricted"}


def _require_env(name: str) -> str:
    """Return a required environment variable or terminate with an actionable error."""
    value = os.environ.get(name)
    if not value:
        raise SystemExit(
            f"Environment variable {name} is not set. "
            f"Please set it before running the CDK app, e.g. export {name}=<value>"
        )
    return value


def _require_choice(name: str, value: str, allowed: set[str]) -> str:
    """Validate a context value against its allowed set and return it."""
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise SystemExit(f"CDK context {name} must be one of: {choices}")
    return value


app = App()
ctx = app.node.try_get_context

deployment_type = _require_choice(
    "deployment_type",
    ctx("deployment_type") or "pipeline",
    _ALLOWED_DEPLOYMENT_TYPES,
)
environment_name = _require_choice(
    "environment",
    ctx("environment") or "dev",
    _ALLOWED_ENVIRONMENTS,
)
data_classification = _require_choice(
    "data_classification",
    ctx("data_classification") or "internal",
    _ALLOWED_DATA_CLASSIFICATIONS,
)
application_name = ctx("application") or "tech-summit-demo"

aws_environment = Environment(
    account=_require_env("ACCOUNT_ID"),
    region=_require_env("AWS_REGION"),
)

# Mandatory tags are applied once at app level and inherited by every taggable resource.
tags = Tags.of(app)
tags.add("Owner", ctx("owner_tag") or "team-platform")
tags.add("CostCenter", ctx("cost_center") or "tech-summit-demo")
tags.add("Environment", environment_name)
tags.add("Application", application_name)
tags.add("ManagedBy", "cdk")
tags.add("DataClassification", data_classification)

environment_suffix = environment_name.capitalize()
if deployment_type == "app":
    stack = TechSummitStack(
        app,
        f"TechSummit{environment_suffix}Stack",
        env=aws_environment,
        environment_name=environment_name,
    )
else:
    source_config = ctx("sourceConfig") or {}
    source = SourceConfig(
        owner=source_config["owner"],
        repo=source_config["repo"],
        branch=source_config["branch"],
        connection_arn=source_config["connectionArn"],
    )
    stack = PipelineStack(
        app,
        f"TechSummitPipeline{environment_suffix}Stack",
        env=aws_environment,
        config=PipelineConfig(
            environment_name=environment_name,
            source=source,
            kiro_secret_arn=source_config["kiroSecretArn"],
        ),
    )

Aspects.of(stack).add(AwsSolutionsChecks(verbose=True))
app.synth()
