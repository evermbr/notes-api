# notes-api

A small AWS CDK (Python) project used to demo **Kiro-driven quality,
security and deployment gating**. It combines a minimal Notes API with a CI/CD pipeline
that runs governed Kiro CLI agents locally on save and headlessly in CodeBuild.

## Stacks and environments

`app.py` reads two independent CDK context values:

- `deployment_type=app|pipeline` selects the workload or CI/CD stack (default: pipeline).
- `environment=dev|staging|prod` selects the deployment stage (default: dev).

The resulting stack IDs are `TechSummit<Env>Stack` and
`TechSummitPipeline<Env>Stack`. Both require `ACCOUNT_ID` and `AWS_REGION` environment
variables and run cdk-nag `AwsSolutionsChecks` during synth.

Six tags are applied once at app level and inherited by every taggable resource:
`Owner`, `CostCenter`, `Environment`, `Application`, `ManagedBy` and
`DataClassification`. Their defaults live in `cdk.json` and can be overridden with CDK
context.

### Application stack

A **Notes API** with one single-purpose Lambda per operation behind an API Gateway REST
API (`proxy=False`) and a shared DynamoDB table:

- `POST /notes` with `{"title": "...", "content": "..."}` →
  `NotesCreateFunction` stores a note and returns `201` with
  `{id, title, content, version}`. A new note always starts at `version: 1`.
- `GET /notes/{id}` → `NotesGetFunction` returns `200` with
  `{id, title, content, version}`, or `404`. A legacy note stored without a version is
  reported as `version: 1`.
- `PATCH /notes/{id}` → `NotesUpdateFunction` applies an optimistic-concurrency update.
- `DELETE /notes/{id}` → `NotesDeleteFunction` applies an optimistic-concurrency delete.

The create Lambda has write-only table access and the get Lambda has read-only access.
The update Lambda is granted exactly `dynamodb:GetItem` + `dynamodb:UpdateItem`, and the
delete Lambda exactly `dynamodb:GetItem` + `dynamodb:DeleteItem` — no broad read/write or
wildcard actions. Every method uses the same Lambda token authorizer backed by an
auto-generated Secrets Manager key. The authorizer performs a constant-time comparison
and returns a stage-wide policy so its five-minute cache works across routes.

#### Optimistic concurrency (`version`)

Every note carries a monotonically increasing integer `version`. `PATCH` and `DELETE`
require an `If-Match` request header carrying the version the client last read (an
unquoted positive decimal integer; the header name is matched case-insensitively):

```http
PATCH /notes/{id}
If-Match: 3
Content-Type: application/json

{"title": "Updated"}
```

- `PATCH` accepts a JSON body with at least one of `title` or `content`; each supplied
  value must be a non-empty, non-whitespace string, and no other field is allowed. On a
  version match it atomically updates the supplied fields, increments `version` by one,
  and returns `200` with the complete updated note. A legacy note with no stored version
  matches `If-Match: 1` and becomes `version: 2`.
- `DELETE` requires only the `If-Match` header. On a version match it atomically deletes
  the note and returns `204` with an empty body. A legacy note matches `If-Match: 1`.

Both operations use a single atomic conditional DynamoDB call and map the outcome to:

| Situation | Status |
|---|---:|
| Successful update | `200` |
| Successful delete | `204` |
| Missing/invalid `If-Match`, malformed JSON, or invalid/unsupported fields | `400` |
| Note not found | `404` |
| Stored version differs from `If-Match` (stale read) | `409` |

A `400`, `404`, or `409` never modifies or deletes the note. Any unexpected DynamoDB or
service error is re-raised so the invocation fails visibly rather than masking a fault.

The API surface starts minimal on purpose: listing notes remains a live-demo extension
point. Add it as a new Lambda + least-privilege grant + explicit API method.

DynamoDB uses on-demand billing, AWS-managed encryption and point-in-time recovery. In
`prod`, deletion protection is enabled and both the table and API-key secret are retained;
non-production resources remain disposable for the demo.

### Pipeline stack

The user-facing pipeline has the stable, context-derived name
`<application>-<environment>` (default: `tech-summit-demo-dev`). It contains:

1. **Source** — GitHub through CodeConnections, full clone for gitleaks history scanning.
2. **CodeValidation** — quality and security CodeBuild actions running in parallel.
3. **ApproveDeploy** — production only; a required human approval.
4. **Deploy** — the constrained Kiro deploy agent targets the selected environment.

Artifacts use a dedicated versioned S3 bucket encrypted by a rotating KMS key, with TLS
required, public access blocked and server access logs written to a retained log bucket.
cdk-nag also runs on this stack; justified IAM5 suppressions are scoped to the constructs
whose L2-generated policies need suffix, object-path or CDK bootstrap-role wildcards.

Each CodeBuild gate is a reusable `KiroCliStep` L2 construct with an inline buildspec. It
installs pinned dependencies, checksum-verifies pinned Kiro CLI 2.28.0, runs V3 without
overriding the profile-governed model, and shell-quotes tasks. Assistant stdout is
captured separately from CLI stderr so transport logs cannot corrupt a verdict line. The
gate uses `--trust-all-tools` to prevent non-interactive approval deadlocks while each
agent's capability-level deny rules remain authoritative. It preserves the Kiro CLI exit
status and accepts exactly one success verdict; agent crashes and missing, duplicate, or contradictory
verdicts fail closed. The security step also checksum-verifies gitleaks before extraction.
The deploy project pins AWS CDK CLI 2.1139.0 and can assume only the specific CDK deploy,
publishing, and lookup role patterns.

The plain API key is stored as the configured secret's complete `SecretString` and is
injected into CodeBuild as `KIRO_API_KEY`. Source and secret settings live under `context.sourceConfig` in `cdk.json`.

## Kiro context engineering

- **`.kiro/agents/`**
  - `python-quality-agent` — whole-repository pylint, pytest, branch coverage and an AI
    quality review; ends with `QUALITY_GATE: PASS|FAIL`.
  - `python-security-agent` — bandit, runtime/dev pip-audit, gitleaks and an AI security
    review; ends with `SECURITY_GATE: PASS|FAIL`. `.gitleaks.toml` keeps the default
    rules and excludes only Kiro's non-secret `AIM_CREDS_AGENT_INJECTION` UUID shape.
  - `deploy-agent` — synth/diff/deploy only; never destroys resources; ends with
    `DEPLOY: SUCCESS|FAILED`.
  - `doc-agent` — updates only this README when documented behavior becomes stale.
  - Demo-only autonomous subagents: `feature-developer` writes within constrained source
    paths; `test-agent` and `review-agent` independently verify changes read-only.
- **`.kiro/specs/versioned-notes/`** — precommitted requirements, design, and task list
  for the cloud-session demo; the feature is intentionally not implemented on the base
  branch.
- **`.kiro/hooks/`** — PostFileSave hooks trigger documentation, quality and security
  checks.
- **`.kiro/skills/`** — repository-local `linting` and `testing` workflows.
- **`.kiro/steering/`**
  - always-on: `project-context.md`, `python-standards.md`;
  - CDK fileMatch: `cdk-standards.md`, `naming-strategy.md`, `tagging-strategy.md`.

Locally, agents use `.venv` and install both requirements files. CodeBuild installs them
directly into its ephemeral environment. Agent permissions remain constrained in headless CI, which pre-approves only the
`read` and `shell` tools required by these agents.

## Project layout

```text
app.py                              # context validation, tags, stack selection, cdk-nag
tech_summit/tech_summit_stack.py    # Notes API application stack
tech_summit/pipeline_stack.py       # source → validation → approval(prod) → deploy
tech_summit/kiro_cli_step.py        # fail-closed Kiro CLI CodeBuild L2 construct
tech_summit/kiro_cli_step_props.py  # gate configuration
tech_summit/source_config.py        # SourceConfig and PipelineConfig
lambda/notes_create/handler.py      # POST /notes (write-only table grant)
lambda/notes_get/handler.py         # GET /notes/{id} (read-only table grant)
lambda/notes_update/handler.py      # PATCH /notes/{id} (GetItem+UpdateItem grant)
lambda/notes_delete/handler.py      # DELETE /notes/{id} (GetItem+DeleteItem grant)
lambda/authorizer/handler.py        # cached Lambda TOKEN authorizer
tests/unit/                         # handler and CDK assertion tests
.gitleaks.toml                     # narrow allowlist over default secret rules
.kiro/                              # agents, hooks, skills, specs and steering
```

## Setup

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip3 install -r requirements.txt -r requirements-dev.txt
```

Runtime and development dependencies are pinned exactly.

## Validate

```bash
# Tests + configured line/branch coverage gate
coverage erase
python3 -m coverage run --branch -m pytest -v
coverage report --show-missing

# Whole-repository lint (exclude generated/third-party trees)
find . -name '*.py' ! -path './cdk.out/*' ! -path './.venv/*' | xargs pylint

# Dependency audits
pip-audit -r requirements.txt
pip-audit -r requirements-dev.txt

# Application synth + cdk-nag
ACCOUNT_ID=<account> AWS_REGION=<region> \
  cdk synth -c deployment_type=app -c environment=dev

# Pipeline synth + cdk-nag
ACCOUNT_ID=<account> AWS_REGION=<region> \
  cdk synth -c deployment_type=pipeline -c environment=dev
```

Read the generated `AwsSolutions--<Stack>-NagReport.csv`; every row must be `Compliant`
or have a justified `Suppressed` status.

## Deploy

```bash
# Application
ACCOUNT_ID=<account> AWS_REGION=<region> \
  cdk deploy -c deployment_type=app -c environment=dev

# Pipeline
ACCOUNT_ID=<account> AWS_REGION=<region> \
  cdk deploy -c deployment_type=pipeline -c environment=dev
```

Production pipeline deployments include a manual approval stage.

## Call the deployed API

```bash
KEY=$(aws secretsmanager get-secret-value --secret-id <ApiKeySecret-arn> \
  --query SecretString --output text)

curl -X POST "$API_URL/notes" \
  -H "Authorization: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"title": "Shopping", "content": "milk and eggs"}'

curl "$API_URL/notes/<id>" -H "Authorization: $KEY"

# Optimistic-concurrency update: If-Match must equal the note's current version
curl -X PATCH "$API_URL/notes/<id>" \
  -H "Authorization: $KEY" \
  -H "If-Match: 1" \
  -H "Content-Type: application/json" \
  -d '{"title": "Updated title"}'

# Optimistic-concurrency delete
curl -X DELETE "$API_URL/notes/<id>" \
  -H "Authorization: $KEY" \
  -H "If-Match: 2"
```
