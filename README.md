# tech-summit-demo

A small AWS CDK (Python) project used as a Tech Summit demo for **Kiro-driven quality &
security gating**. It ships a tiny real workload and a CI pipeline that runs Kiro CLI
agents for the standard Python checks — locally (on save) and in CI/CD (headless in CodeBuild).

## Stacks

`app.py` selects which stack to synthesize/deploy via the `deployment_type` CDK context
variable, passed with `-c`:

- `cdk deploy -c deployment_type=app` → the **application stack** (`TechSummitStack`).
- `cdk deploy` (deployment_type unset) → the **CI pipeline stack** (`PipelineStack`).

Both stacks read the target account/region from the `ACCOUNT_ID` and `AWS_REGION`
environment variables and fail fast if either is unset. Org tags (`Owner`, `CostCenter`,
`Environment`) are applied app-wide so every resource inherits them.

### Application stack (`TechSummitStack`)

A **URL shortener**: a Lambda (`UrlShortenerFunction`, Python 3.13) fronted by an **API
Gateway REST API**, backed by a **DynamoDB** table (`UrlShortenerTable`, pay-per-request,
AWS-managed encryption, PITR):

- `POST /` with `{"url": "https://..."}` → stores a 7-char code, returns `{code, url}`.
- `GET /{code}` → `302` redirect to the original URL (`404` if unknown).

The API is protected by a **Lambda token authorizer** (`ApiAuthorizerFunction`) that
validates the `Authorization` header against an **auto-generated Secrets Manager API key**
(`ApiKeySecret`) — nothing is hard-coded. Requests without a valid key get `403`.

cdk-nag (`AwsSolutionsChecks`) runs on synth for this stack only. Findings that are out of
scope for a demo (API Gateway logging/validation/WAF, Cognito, secret rotation, Lambda
runtime, managed exec-role policy) are suppressed with justifications via `NagSuppressions`.

### Pipeline stack (`PipelineStack`)

A classic CodePipeline (`aws_cdk.aws_codepipeline`) with three stages:

- **Source** — pulls the code from GitHub via an AWS CodeConnections connection
  (`code_build_clone_output=True`, so `.git` is present for gitleaks history scanning).
- **CodeValidation** — two CodeBuild actions that run **in parallel**: `QualityCheck`
  (`python-quality-agent`) and `SecurityCheck` (`python-security-agent`), headless.
- **Deploy** — a CodeBuild action running the `deploy-agent`, which runs
  `cdk deploy -c deployment_type=app`.

Each gate is a reusable `KiroCliStep` L2 construct (`tech_summit/kiro_cli_step.py`) whose
buildspec is defined **inline** — no external buildspec files. The Kiro CLI API key is read
from Secrets Manager at build time (never inlined), and `CI=true` + `runtime-versions`
(Node 22 / Python 3.12) are set for every step.

Source and secret settings come from the `sourceConfig` object in `cdk.json` (context):
`owner`, `repo`, `branch`, `connectionArn`, `kiroSecretArn`.

## Kiro context engineering

Everything under `.kiro/` makes the checks reproducible and governed:

- **`.kiro/agents/`**
  - `python-security-agent` — runs **bandit** (SAST), **pip-audit** (dependency CVEs) and
    **gitleaks** (secrets in the tree + git history), plus a Kiro-native AI vulnerability
    review. Ends with `SECURITY_GATE: PASS|FAIL`.
  - `python-quality-agent` — runs **pylint**, **pytest** and **coverage** (branch, with
    XML + JUnit reports), plus an AI code-quality review. Ends with `QUALITY_GATE: PASS|FAIL`.
  - `deploy-agent` — runs `cdk synth` / `diff` / `deploy` (deploy uses
    `--require-approval never`); may deploy but never destroys. Ends with `DEPLOY: SUCCESS|FAILED`.
  - `doc-agent` — on a save, decides whether the change makes the docs stale and, only if
    necessary, edits `README.md` (and nothing else).
  - Each agent's instructions live in a paired `*.prompt.md` (referenced via `file://`).
    Agents are scoped by `permissions.rules`: the check agents are read-only; the deploy
    agent may `cdk deploy` but not `cdk destroy`; the doc-agent may write only `README.md`.
  - **Virtualenv rule:** locally (`CI` unset) agents always use a `.venv`; in CI (`CI` set)
    they never do (CodeBuild is ephemeral).
- **`.kiro/hooks/`** — `PostFileSave` hooks that ask Kiro to run the matching agent against
  the changed files on save:
  - `quality-gate-on-save` → `python-quality-agent`
  - `security-gate-on-save` → `python-security-agent`
  - `doc-on-save` → `doc-agent`
- **`.kiro/steering/`** — `org-standards` → `team-platform` → `project-tech-summit`,
  layered as context (org is the floor; team/project may tighten). The team steering owns
  the naming & tagging strategy.

Scanner/tool configs live at the repo root: `.bandit`, `.pylintrc` (pylint gate lives here),
`.coveragerc` (coverage `fail_under = 80`).

## CI/CD gate mechanics

Each CodeBuild action runs its agent in **headless mode**
(`kiro-cli chat --agent <name> --no-interactive --trust-all-tools`). The agent runs the
tools itself and prints a verdict line; the buildspec greps it and fails the build when the
agent reports `FAIL`, blocking the pipeline. Destructive commands stay blocked by each
agent's `permissions.rules` even under `--trust-all-tools`.

## Project layout

```
app.py                            # CDK app entry; selects stack via -c deployment_type; app-wide tags
tech_summit/tech_summit_stack.py  # application stack (URL shortener + API GW + authorizer)
tech_summit/pipeline_stack.py     # CI pipeline stack (Source → CodeValidation → Deploy)
tech_summit/kiro_cli_step.py      # L2 construct: a CodeBuild step running a Kiro agent headless
tech_summit/kiro_cli_step_props.py# config object for KiroCliStep
tech_summit/source_config.py      # GitHub source config (owner/repo/branch/connection)
lambda/url_shortener/handler.py   # URL shortener handler (API Gateway REST proxy event shape)
lambda/authorizer/handler.py      # Lambda token authorizer
tests/unit/                       # stack assertions + handler unit tests
.kiro/                            # agents, hooks, steering
```

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

## Common commands

```bash
# Tests + coverage
coverage erase && python3 -m coverage run --branch -m pytest -v && coverage report

# Lint (gate threshold comes from .pylintrc)
pylint app.py tech_summit

# Synthesize (env vars required). Default = pipeline stack; add -c deployment_type=app for the app stack
ACCOUNT_ID=<account> AWS_REGION=<region> cdk synth
ACCOUNT_ID=<account> AWS_REGION=<region> cdk synth -c deployment_type=app

# Deploy the application stack
ACCOUNT_ID=<account> AWS_REGION=<region> cdk deploy -c deployment_type=app

# Deploy the CI pipeline (source + secret come from cdk.json -> context.sourceConfig)
ACCOUNT_ID=<account> AWS_REGION=<region> cdk deploy
```

## Calling the deployed API

The API requires the generated key in the `Authorization` header. Fetch it from Secrets
Manager, then call the endpoint:

```bash
KEY=$(aws secretsmanager get-secret-value --secret-id <ApiKeySecret-arn> \
  --query SecretString --output text)
curl -X POST "$API_URL/" -H "Authorization: $KEY" \
  -d '{"url": "https://example.com"}'
```
