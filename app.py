#!/usr/bin/env python3
"""CDK application entry point.

Which stack is synthesized/deployed depends on the ``deployment_type`` CDK context
variable, passed with ``-c``:

  * ``cdk deploy -c deployment_type=app``  -> the application stack (:class:`TechSummitStack`)
  * ``cdk deploy``  (deployment_type unset) -> the CI pipeline stack (:class:`PipelineStack`)

Both stacks read the target account/region from the ``ACCOUNT_ID`` and ``AWS_REGION``
environment variables. The cdk-nag ``AwsSolutionsChecks`` aspect is applied only to the
application stack, not to the CI pipeline stack.
"""

import os

from aws_cdk import App, Aspects, Environment
from cdk_nag import AwsSolutionsChecks

from tech_summit.tech_summit_stack import TechSummitStack
from tech_summit.pipeline_stack import PipelineStack
from tech_summit.source_config import SourceConfig


def _require_env(name: str) -> str:
    """Return the value of a required environment variable.

    Raises a clear error asking the user to set it if it is missing or empty.
    """
    value = os.environ.get(name)
    if not value:
        raise SystemExit(
            f"Environment variable {name} is not set. "
            f"Please set it before running the CDK app, e.g. export {name}=<value>"
        )
    return value


app = App()

env = Environment(
    account=_require_env("ACCOUNT_ID"),
    region=_require_env("AWS_REGION"),
)

# `-c deployment_type=app` deploys the application stack; anything else (including
# the default of it being unset) deploys the CI pipeline stack.
deployment_type = app.node.try_get_context("deployment_type")

if deployment_type == "app":
    app_stack = TechSummitStack(app, "TechSummitStack", env=env)
    # cdk-nag runs only on the application stack, not on the CI pipeline (whose
    # CodePipeline/CodeBuild L2 constructs generate expected wildcard policies).
    Aspects.of(app_stack).add(AwsSolutionsChecks(verbose=True))
else:
    cfg = app.node.try_get_context("sourceConfig") or {}
    source = SourceConfig(
        owner=cfg["owner"],
        repo=cfg["repo"],
        branch=cfg["branch"],
        connection_arn=cfg["connectionArn"],
    )
    PipelineStack(
        app,
        "TechSummitPipelineStack",
        env=env,
        source=source,
        kiro_secret_arn=cfg["kiroSecretArn"],
    )

app.synth()
