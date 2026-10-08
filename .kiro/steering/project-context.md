# Project Context — tech-summit-demo

> Always-on orientation for this repository. Cross-project infrastructure rules live in
> `cdk-standards.md`, `naming-strategy.md` and `tagging-strategy.md`; Python environment
> and gate rules live in `python-standards.md` plus the `linting` and `testing` skills.

## Purpose

This AWS CDK (Python) repository demonstrates **Kiro-driven quality, security and deploy
gating**. It contains a deliberately small Notes API and a CodePipeline that runs Kiro
CLI agents headlessly. Keep the workload easy to understand so live changes visibly
exercise the gates.

## Context and stacks

`app.py` requires `ACCOUNT_ID` and `AWS_REGION`, then reads two independent CDK context
values:

- `deployment_type`: `app` or `pipeline` (defaults to `pipeline`).
- `environment`: `dev`, `staging` or `prod` (defaults to `dev`).

Stack IDs include the environment: `TechSummit<Env>Stack` for the workload and
`TechSummitPipeline<Env>Stack` for CI/CD. Invalid context values fail before synthesis.
`AwsSolutionsChecks` is applied to **every** synthesized stack.

Six mandatory tags are applied once with `Tags.of(app)` and inherited by every taggable
resource: `Owner`, `CostCenter`, `Environment`, `Application`, `ManagedBy=cdk` and
`DataClassification`. Values come from CDK context with demo-safe defaults in `cdk.json`.

## Application stack

`TechSummitStack` implements a Notes REST API using one Lambda per operation:

- `NotesTable`: DynamoDB partition key `id`, on-demand billing, AWS-managed encryption
  and point-in-time recovery.
- `NotesCreateFunction` (`lambda/notes_create/`): `POST /notes`, write-only table grant.
- `NotesGetFunction` (`lambda/notes_get/`): `GET /notes/{id}`, read-only table grant.
- `NotesApi`: `LambdaRestApi(proxy=False)` with explicit methods and a stage named after
  the selected environment.

In `prod`, the table has deletion protection and both the table and API-key secret use
`RemovalPolicy.RETAIN`. Non-production environments remain disposable for the demo.

Only create and read exist intentionally. `GET /notes`, `PUT /notes/{id}` and
`DELETE /notes/{id}` are live-demo extension points. Add each operation as a new
single-purpose Lambda directory, a narrow grant and an explicit API method.

### Authorization

Every method uses `ApiTokenAuthorizer`, backed by `lambda/authorizer/handler.py` and an
auto-generated Secrets Manager API key. The handler performs a constant-time comparison
and returns a stage-wide execute-api policy so the five-minute authorizer cache works
across all API routes for the same token.

### Application cdk-nag scope

Documented, resource-scoped suppressions cover the demo-only trade-offs: Lambda basic
execution policy/runtime (`IAM4`, `L1`), no API-key rotation (`SMG4`), and API Gateway
logging/validation/WAF/Cognito (`APIG1`, `APIG2`, `APIG3`, `APIG6`, `COG4`). New
findings must be fixed first or suppressed with concrete evidence.

## Pipeline stack

`PipelineStack` receives one `PipelineConfig` containing environment, `SourceConfig` and
the Kiro secret ARN. It provisions:

1. **Source** — GitHub via CodeConnections, with a full clone for history scanning.
2. **CodeValidation** — quality and security CodeBuild actions in parallel.
3. **ApproveDeploy** — present only in `prod`; requires human approval.
4. **Deploy** — a constrained deploy agent targeting the application stack and selected
   environment.

Pipeline artifacts use a dedicated versioned S3 bucket encrypted by a rotating KMS key,
with TLS enforcement, public access blocked and server access logs sent to a separate
retained log bucket. Physical names are left to CDK.

### Fail-closed Kiro gates

`KiroCliStep` is a reusable L2 `PipelineProject`. Its inline buildspec:

- installs both pinned Python requirements files;
- downloads pinned Kiro CLI 2.28.0 and verifies its SHA-256 checksum;
- runs under bash with `pipefail` and shell-quotes all dynamic arguments;
- injects only the `KIRO_API_KEY` JSON field from Secrets Manager;
- accepts the build only if Kiro CLI exits successfully and the final output line is the
  exact expected verdict (`QUALITY_GATE: PASS`, `SECURITY_GATE: PASS`, or
  `DEPLOY: SUCCESS`). Missing output and agent crashes fail the build.

The deploy project installs pinned AWS CDK CLI 2.1139.0. Its role may only assume the
account-local `cdk-*` bootstrap roles. cdk-nag runs on the pipeline; IAM5 suppressions
are scoped to the CodeBuild/pipeline constructs and document L2-generated suffix,
object-path and bootstrap-role wildcards. The artifact KMS key rotates and artifact S3
access is logged.

## Kiro configuration

- `.kiro/agents/`: quality, security, deploy and README-only documentation agents.
  Local agents create/activate `.venv` and install both requirements files; CI uses its
  ephemeral environment. Shell allowlists include only the tools each prompt requires.
- `.kiro/hooks/`: PostFileSave hooks for documentation, quality and security review.
- `.kiro/skills/`: repository-local `linting` and `testing` workflows.
- `.kiro/steering/`: this always-on context and Python standards; CDK/naming/tagging
  standards use `fileMatch` for `app.py`, `tech_summit/**/*.py` and `cdk.json`.

## Testing conventions

Lambda entry points all use the filename `handler.py`. Tests load each one by absolute
file path under a unique module name using `tests/unit/lambda_test_utils.py` to avoid
module collisions. Stack changes require `aws_cdk.assertions.Template` tests.

Coverage uses `source = .` and excludes tests/generated artifacts plus the top-level CDK
entrypoint; all first-party modules under `lambda/` and `tech_summit/` are measured. The
configured `.coveragerc` threshold is the gate. Pylint runs across every Python file and
uses the threshold in `.pylintrc`.

## Definition of done

1. Run tests under line and branch coverage; meet `.coveragerc` `fail_under`.
2. Run whole-repository pylint; meet `.pylintrc` `fail-under`.
3. Run runtime and dev dependency audits plus the configured security scanners.
4. Synthesize both affected stacks and inspect their cdk-nag CSV reports; every row must
   be `Compliant` or justified `Suppressed`.
5. Update README when interfaces, architecture, setup or Kiro configuration change.
