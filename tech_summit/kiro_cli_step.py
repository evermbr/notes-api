"""L2 construct for running a Kiro CLI agent as a CodeBuild step.

``KiroCliStep`` is a ``PipelineProject`` whose inline buildspec installs the Kiro CLI and
runs a given agent in headless mode against the source. The build fails when the agent
prints ``<gate>: FAIL``. The Kiro CLI API key is injected as a Secrets Manager environment
variable, so its value never appears in the template.
"""

from constructs import Construct
from aws_cdk.aws_codebuild import (
    PipelineProject,
    BuildSpec,
    BuildEnvironment,
    IBuildImage,
    BuildEnvironmentVariable,
    BuildEnvironmentVariableType,
)
from aws_cdk.aws_secretsmanager import ISecret


class KiroCliStep(PipelineProject):
    """A CodeBuild project that runs a single Kiro CLI agent headless as a pipeline gate."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        agent: str,
        gate: str,
        task: str,
        kiro_secret: ISecret,
        build_image: IBuildImage,
        out_file: str | None = None,
        extra_install: list[str] | None = None,
        extra_env: dict | None = None,
    ) -> None:
        out_file = out_file or f"{agent}.out"

        env_vars = {
            "KIRO_API_KEY": BuildEnvironmentVariable(
                type=BuildEnvironmentVariableType.SECRETS_MANAGER,
                value=kiro_secret.secret_arn,
            ),
        }
        for key, val in (extra_env or {}).items():
            env_vars[key] = BuildEnvironmentVariable(value=val)

        build_spec = BuildSpec.from_object({
            "version": "0.2",
            "phases": {
                "install": {
                    "commands": [
                        "pip3 install -r requirements.txt -r requirements-dev.txt",
                        *(extra_install or []),
                        "curl -fsSL https://cli.kiro.dev/install | bash",
                    ],
                },
                "build": {
                    "commands": [
                        # kiro-cli installs to ~/.local/bin; PATH is set inline because
                        # exports from the install phase do not persist to build.
                        # --trust-tools lets the agent run read/shell tools without an
                        # interactive approver (headless CI). Dangerous commands are
                        # still blocked by the agent's permissions.rules deny list.
                        'export PATH="$HOME/.local/bin:$PATH" && '
                        f"kiro-cli chat --agent {agent} --no-interactive "
                        f'--trust-tools=read,shell "{task}" '
                        f"2>&1 | tee {out_file}",
                        f'if grep -q "{gate}: FAIL" {out_file}; then '
                        f'echo "{gate} FAILED"; exit 1; fi',
                        f'echo "{gate} PASSED"',
                    ],
                },
            },
            "artifacts": {"files": [out_file]},
        })

        super().__init__(
            scope,
            construct_id,
            build_spec=build_spec,
            environment=BuildEnvironment(build_image=build_image),
            environment_variables=env_vars,
        )

        kiro_secret.grant_read(self)
