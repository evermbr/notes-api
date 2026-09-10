# tech-summit-demo

A small AWS CDK (Python) project used as a Tech Summit demo for **Kiro-driven quality &
security gating**. It ships a tiny real workload and wires it to Kiro CLI agents that run
the standard Python checks — locally (on save) and in CI/CD (headless in CodeBuild).

## What's deployed (`TechSummitStack`)

- **SNS → SQS fan-out** — an SNS topic (`TechSummitTopic`) with an SQS queue
  (`TechSummitQueue`) subscribed to it.
- **URL shortener** — a Lambda (`UrlShortenerFunction`, Python 3.12) behind a **Function
  URL**, backed by a **DynamoDB** table (`UrlShortenerTable`, pay-per-request, encrypted,
  PITR):
  - `POST /` with `{"url": "https://..."}` → stores a 7-char code, returns `{code, url}`.
  - `GET /{code}` → `302` redirect to the original URL (`404` if unknown).

cdk-nag (`AwsSolutionsChecks`) runs on synth. `app.py` requires `ACCOUNT_ID` and
`AWS_REGION` environment variables and fails fast with a clear message if they are unset.

## Kiro context engineering

Everything under `.kiro/` makes the checks reproducible and governed:

- **`.kiro/agents/`**
  - `python-security-agent` — runs **bandit** (SAST), **pip-audit** (dependency CVEs) and
    **gitleaks** (secrets in the tree + git history), plus a Kiro-native AI vulnerability
    review. Ends with `SECURITY_GATE: PASS|FAIL`.
  - `python-quality-agent` — runs **pylint**, **pytest** and **coverage** (branch, with
    XML + JUnit reports), plus an AI code-quality review. Ends with `QUALITY_GATE: PASS|FAIL`.
  - Each agent's instructions live in a paired `*.prompt.md` (referenced via `file://`).
    Agents are read-only: they may run the scanners and read files, but deploy/destroy/
    delete/`git push` are denied by `permissions.rules`.
- **`.kiro/hooks/`** — `PostFileSave` hooks that, on saving a `.py` file, ask Kiro to run
  the matching agent against the changed files:
  - `quality-gate-on-save` → `python-quality-agent`
  - `security-gate-on-save` → `python-security-agent`
- **`.kiro/steering/`** — org → team → project standards layered as context.

Scanner configs live at the repo root: `.bandit`, `.pylintrc`, `.coveragerc`.

## CI/CD

`ci/buildspec-security.yml` and `ci/buildspec-quality.yml` run the same agents in
**headless mode** (`kiro-cli chat --agent <name> --no-interactive`) as CodeBuild steps in
a CodePipeline. The build fails (blocking the pipeline) when the agent returns a `FAIL`
verdict. `KIRO_API_KEY` is pulled from Secrets Manager — never inlined.

## Project layout

```
app.py                          # CDK app entry (reads ACCOUNT_ID / AWS_REGION)
tech_summit/tech_summit_stack.py# the stack (SNS→SQS + URL shortener)
lambda/url_shortener/handler.py # Lambda handler for the shortener
tests/unit/                     # stack assertions + handler unit tests
ci/                             # CodeBuild buildspecs (security, quality)
.kiro/                          # agents, hooks, steering
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

# Synthesize the CloudFormation template (env vars required)
ACCOUNT_ID=<account> AWS_REGION=<region> cdk synth

# Diff against the deployed stack
ACCOUNT_ID=<account> AWS_REGION=<region> cdk diff

# Deploy
ACCOUNT_ID=<account> AWS_REGION=<region> cdk deploy
```
