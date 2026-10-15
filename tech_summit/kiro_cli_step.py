"""L2 construct for running a Kiro CLI agent as a fail-closed CodeBuild gate.

The construct installs a checksum-verified, pinned Kiro CLI release, invokes one agent
headlessly, and accepts the build only when the command succeeds and emits exactly one
machine-readable success verdict.
"""

import shlex

from constructs import Construct
from aws_cdk.aws_codebuild import (
    BuildEnvironment,
    BuildEnvironmentVariable,
    BuildEnvironmentVariableType,
    BuildSpec,
    PipelineProject,
)

from tech_summit.kiro_cli_step_props import KiroCliStepProps

_KIRO_CLI_VERSION = "2.28.0"
_KIRO_CLI_ARCHIVE = "kirocli-x86_64-linux.zip"
_KIRO_CLI_SHA256 = "4d6d20c3ffed99904081a062678b3c1978dc7d68c9f6c530f3f28f163011c8ee"
_KIRO_CLI_DOWNLOAD = (
    "https://prod.download.cli.kiro.dev/stable/"
    f"{_KIRO_CLI_VERSION}/{_KIRO_CLI_ARCHIVE}"
)


def _verdict_check_command(output_file: str, gate: str, success_value: str) -> str:
    """Build a fail-closed command requiring one exact success verdict line."""
    check_script = (
        "import pathlib,sys; "
        "lines=[line.strip() for line in "
        "pathlib.Path(sys.argv[1]).read_text(encoding='utf-8', "
        "errors='replace').splitlines()]; "
        "verdicts=[line for line in lines if line.startswith(sys.argv[2])]; "
        "raise SystemExit(0 if verdicts == [sys.argv[3]] else 1)"
    )
    arguments = " ".join(
        shlex.quote(value)
        for value in (output_file, f"{gate}: ", f"{gate}: {success_value}")
    )
    return (
        f"python3 -c {shlex.quote(check_script)} {arguments} || "
        f'{{ echo "{gate} expected exactly one success verdict"; exit 1; }}'
    )


class KiroCliStep(PipelineProject):
    """Run one Kiro CLI agent and require its exact success verdict."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        props: KiroCliStepProps,
    ) -> None:
        agent = shlex.quote(props.agent)
        task = shlex.quote(props.task)
        output_name = props.out_file or f"{props.agent}.out"
        out_file = shlex.quote(output_name)

        env_vars = {
            "KIRO_API_KEY": BuildEnvironmentVariable(
                type=BuildEnvironmentVariableType.SECRETS_MANAGER,
                value=props.kiro_secret.secret_arn,
            ),
            "CI": BuildEnvironmentVariable(value="true"),
        }
        for key, value in props.extra_env.items():
            env_vars[key] = BuildEnvironmentVariable(value=value)

        install_commands = [
            "pip3 install -r requirements.txt -r requirements-dev.txt",
            *props.extra_install,
            f'curl -fsSL "{_KIRO_CLI_DOWNLOAD}" -o /tmp/{_KIRO_CLI_ARCHIVE}',
            (
                f'echo "{_KIRO_CLI_SHA256}  /tmp/{_KIRO_CLI_ARCHIVE}" '
                "| sha256sum -c -"
            ),
            "rm -rf /tmp/kiro-cli-install && mkdir -p /tmp/kiro-cli-install",
            f"unzip -q /tmp/{_KIRO_CLI_ARCHIVE} -d /tmp/kiro-cli-install",
            "KIRO_CLI_SKIP_SETUP=1 /tmp/kiro-cli-install/kirocli/install.sh",
            f"rm -rf /tmp/kiro-cli-install /tmp/{_KIRO_CLI_ARCHIVE}",
        ]

        run_agent = (
            'export PATH="$HOME/.local/bin:$PATH" && set -o pipefail; '
            f"kiro-cli chat --v3 --agent {agent} --no-interactive "
            f"--trust-all-tools {task} "
            f"2>{shlex.quote(f'{output_name}.stderr')} | tee {out_file}; "
            "cli_status=${PIPESTATUS[0]}; "
            f"cat {shlex.quote(f'{output_name}.stderr')} >&2; "
            'exit "$cli_status"'
        )
        require_verdict = _verdict_check_command(
            output_name,
            props.gate,
            props.success_value,
        )

        build_spec = BuildSpec.from_object(
            {
                "version": "0.2",
                "env": {"shell": "bash"},
                "phases": {
                    "install": {
                        "runtime-versions": {"nodejs": "22", "python": "3.12"},
                        "commands": install_commands,
                    },
                    "build": {
                        "commands": [
                            run_agent,
                            require_verdict,
                            f'echo "{props.gate} PASSED"',
                        ],
                    },
                },
                "artifacts": {"files": [output_name]},
            }
        )

        super().__init__(
            scope,
            construct_id,
            build_spec=build_spec,
            environment=BuildEnvironment(build_image=props.build_image),
            environment_variables=env_vars,
        )

        props.kiro_secret.grant_read(self)
