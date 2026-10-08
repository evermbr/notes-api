---
inclusion: fileMatch
fileMatchPattern: ['app.py', 'tech_summit/**/*.py', 'cdk.json']
---

# My Company — CDK Standards

> Distributed via the `my-company-coding-standards` Power. CDK-specific conventions
> for constructs, logical IDs, and stacks. For physical resource names see
> `naming-strategy.md`; for tags see `tagging-strategy.md`.

## Constructs and logical IDs

- **Construct IDs are PascalCase and describe the role, not the type.** Prefer
  `OrdersTable` over `DynamoTable1`; prefer `IngestQueue` over `Queue`.
- **Do not hardcode physical names by default.** Let CDK auto-generate physical
  names (it appends a stable hash and keeps logical IDs stable). Only set an
  explicit physical name when a name is externally referenced (e.g. a bucket used
  by another account) — and then it must follow the canonical pattern in
  `naming-strategy.md`.
- **Keep construct IDs stable.** Renaming a construct ID changes its CloudFormation
  logical ID, which replaces the resource. Never rename the construct ID of a
  stateful resource (table, bucket, queue with data) to "tidy up".
- Build names from context, not string literals — derive `<app>` and `<env>` from
  stack props/context so a single construct is correct in every environment.

## Stacks

- Stack names follow `<App><Env>Stack` in PascalCase for the CDK id (e.g.
  `TaskApiProdStack`), which also yields a predictable CloudFormation stack
  name.
- One environment per stack instance. Never mix `dev` and `prod` resources in the
  same stack.

## Constructs

- Prefer **L2 constructs**; use **L3 patterns** for common architectures; avoid L1
  (`Cfn*`) unless nothing else exists, and justify it.
- Follow the language's idioms (TypeScript, Python, Java, C#, Go). Use meaningful
  construct IDs and logical names.
- Keep the store/config seams clean so stacks stay testable.

## cdk-nag is mandatory — a change is not done until synth is clean

Every stack runs the **cdk-nag `AwsSolutions`** pack (wired via `Aspects` in the
app entry). This is not optional and it is not a deploy-time step — it is the
definition of done for **any** change to CDK code.

- **After every change to a stack, run `cdk synth` and read the output.** A
  successful exit code is not enough: cdk-nag prints findings as `AwsSolutions-*`
  errors during synth. The change is complete only when synth finishes with
  **zero unaddressed findings**.
- **Never hand back or commit CDK code you have not synthesized.** Passing unit
  tests (`Template.from_stack`) do **not** substitute for a full `cdk synth` —
  tests assert shape, cdk-nag asserts security posture.
- **Resolve findings by fixing the code first.** Reach for a real fix (enable
  encryption, add access logs, tighten the policy) before considering a
  suppression.
- **Suppress only with evidence.** When a finding genuinely does not apply, add a
  `NagSuppressions` entry scoped to the specific resource and rule id, with a
  one-line `reason` that states *why* it is safe. A suppression without a concrete
  justification is a review failure.
- New AWS resources usually bring their own findings (S3 wants access logs and
  SSL; IAM grants surface wildcard `IAM5`; Lambda wants the latest runtime `L1`).
  Expect them and handle each one — fix or justified suppression — before you
  call the change done.
