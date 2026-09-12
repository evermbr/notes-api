# tech-summit-demo

A small AWS CDK (Python) project used as a Tech Summit demo for **Kiro-driven quality &
security gating**. It ships a tiny real workload and a CI pipeline that runs Kiro CLI
agents for the standard Python checks — locally (on save) and in CI/CD (headless in CodeBuild).

## Stacks

`app.py` selects which stack to synthesize/deploy via the `deployment_type` CDK context
variable, passed with `-c`:

- `cdk deploy -c deployment_type=app` → the **application stack** (`TechSummitStack`).
- `cdk deploy` (deployment_type unset) → the **CI pipeline stack** (`PipelineStack`).

### Application stack (`TechSummitStack`)

A **URL shortener**: a Lambda (`UrlShortenerFunction`, Python 3.12) behind a **Function URL**, backed by a **DynamoDB** table (`UrlShortenerTable`, pay-per-request, encrypted, PITR):

- `POST /` with `{"url": "https://..."}` → stores a 7-char code, returns `{code, url}`.
- `GET /{code}` → `302` redirect to the original URL (`404` if unknown).

### Pipeline stack (`PipelineStack`)

A classic CodePipeline (`aws_cdk.aws_codepipeline`) with three stages:

- **Source** — pulls the code from GitHub via an AWS CodeConnections connection.
- **QualityCheck** — a CodeBuild action running the `python-quality-agent` (headless).
- **SecurityCheck** — a CodeBuild action running the `python-security-agent` (headless).

The Kiro CLI API key is read from AWS Secrets Manager at build time; the value is never stored in the template. Configure via `-c` context variables: `github_owner`, `github_repo`, `github_branch`, `connection_arn`, `kiro_secret_arn`.

cdk-nag (`AwsSolutionsChecks`) runs on synth. `app.py` requires `ACCOUNT_ID` and `AWS_REGION` environment variables and fails fast with a clear message if they are unset.

## Kiro context engineering

Everything under `.kiro/` makes the checks reproducible and governed:

- **`.kiro/agents/`**
  - `python-security-agent` — runs **bandit** (SAST), **pip-audit** (dependency CVEs) and **gitleaks** (secrets in the tree + git history), plus a Kiro-native AI vulnerability review. Ends with `SECURITY_GATE: PASS|FAIL`.
  - `python-quality-agent` — runs **pylint**, **pytest** and **coverage** (branch, with XML + JUnit reports), plus an AI code-quality review. Ends with `QUALITY_GATE: PASS|FAIL`.
  - `doc-agent` — on a save, decides whether the change makes the docs stale and, only if necessary, edits `README.md` (and nothing else).
  - Each agent's instructions live in a paired `*.prompt.md` (referenced via `file://`). The check agents are read-only; the doc-agent may write only `README.md`. Deploy/destroy/delete/`git push` are denied by `permissions.rules`.
- **`.kiro/hooks/`** — `PostFileSave` hooks that ask Kiro to run the matching agent against the changed files on save:
  - `quality-gate-on-save` → `python-quality-agent`
  - `security-gate-on-save` → `python-security-agent`
  - `doc-on-save` → `doc-agent`
- **`.kiro/steering/`** — org → team → project standards layered as context.

Scanner configs live at the repo root: `.bandit`, `.pylintrc`, `.coveragerc`.

## CI/CD

The pipeline's two CodeBuild actions run the agents in **headless mode** (`kiro-cli chat --agent <name> --no-interactive`). Their buildspecs are defined **inline** inside `tech_summit/pipeline_stack.py` (`BuildSpec.from_object`), so there are no external buildspec files to keep in sync. A build fails (blocking the pipeline) when the agent returns a `FAIL` verdict. `KIRO_API_KEY` is pulled from Secrets Manager — never inlined.

## Project layout

```
app.py                           # CDK app entry; selects stack via -c deployment_type
tech_summit/tech_summit_stack.py # application stack (URL shortener)
tech_summit/pipeline_stack.py    # CI pipeline stack (Source → Quality → Security)
lambda/url_shortener/handler.py  # Lambda handler for the shortener
tests/unit/                      # stack assertions + handler unit tests
.kiro/                           # agents, hooks, steering
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

# Lint
pylint app.py tech_summit --fail-under=8.0

# Synthesize (env vars required). Default = pipeline stack; add -c deployment_type=app for the app stack
ACCOUNT_ID=<account> AWS_REGION=<region> cdk synth
ACCOUNT_ID=<account> AWS_REGION=<region> cdk synth -c deployment_type=app

# Deploy the application stack
ACCOUNT_ID=<account> AWS_REGION=<region> cdk deploy -c deployment_type=app

# Deploy the CI pipeline (provide the source + secret context vars)
ACCOUNT_ID=<account> AWS_REGION=<region> cdk deploy \
  -c connection_arn=<codeconnections-arn> \
  -c kiro_secret_arn=<secretsmanager-arn> \
  -c github_owner=<owner> -c github_repo=<repo> -c github_branch=main
```
