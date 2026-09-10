#!/usr/bin/env python3
"""CDK application entry point for the tech_summit stack.

Instantiates :class:`TechSummitStack` with the target account/region read from the
``ACCOUNT_ID`` and ``AWS_REGION`` environment variables, applies the cdk-nag
``AwsSolutionsChecks`` aspect, and synthesizes the CloudFormation template.
"""

import os

from aws_cdk import App, Aspects, Environment
from cdk_nag import AwsSolutionsChecks

from tech_summit.tech_summit_stack import TechSummitStack


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
TechSummitStack(
    app,
    "TechSummitStack",
    env=Environment(
        account=_require_env("ACCOUNT_ID"),
        region=_require_env("AWS_REGION"),
    ),
)
Aspects.of(app).add(AwsSolutionsChecks(verbose=True))

app.synth()
