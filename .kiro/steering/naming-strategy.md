---
inclusion: fileMatch
fileMatchPattern: ['app.py', 'tech_summit/**/*.py', 'cdk.json']
---

# My Company — Resource Naming Strategy

> Distributed via the `my-company-coding-standards` Power. Naming conventions for
> the AWS resources we provision. Makes names predictable, searchable, and safe
> across accounts and environments, regardless of the IaC tool used.

## Why naming matters

Consistent names make resources greppable in the console, unambiguous across
`dev`/`staging`/`prod`, and safe from cross-environment collisions. Names are part
of the contract — treat renaming a stateful resource as a breaking change.

## The canonical pattern

**CDK auto-generates physical names by default.** Do not set a physical name merely
for readability; construct IDs and tags provide that context without coupling deployments
to a name. When an external consumer genuinely requires a stable, explicit physical name,
it follows:

```
<app>-<env>-<resource>[-<qualifier>]
```

- `<app>` — the application/service short name, kebab-case (e.g. `task-api`).
- `<env>` — one of `dev` | `staging` | `prod`. Never omit it.
- `<resource>` — the resource role, kebab-case (e.g. `orders`, `ingest`, `cache`).
- `<qualifier>` — optional disambiguator when there are several of a kind (e.g.
  `dlq`, `replica`, `blue`).

Example: `task-api-prod-orders`, `task-api-prod-orders-dlq`.

Lowercase and hyphen-separated everywhere the service allows it. Do not use
spaces, underscores, or uppercase in physical names.

## Resource-specific rules for explicit names

Apply these only when a physical name is required under the exception above:

- **S3 buckets:** globally unique, so include env and a short account/region
  suffix when needed: `task-api-prod-assets`. Buckets are lowercase, 3–63
  chars, no dots unless required for static hosting.
- **DynamoDB tables:** `<app>-<env>-<entity>` (e.g. `task-api-prod-tasks`).
- **Lambda functions:** `<app>-<env>-<action>` (e.g. `task-api-prod-create`).
- **SQS/SNS:** append `-queue` / `-topic`; dead-letter queues append `-dlq`.
- **IAM roles/policies:** `<app>-<env>-<purpose>-role`; keep them least-privilege.
- **Log groups:** `/<app>/<env>/<component>`.

## Tags vs names

When a physical name is explicit, it encodes `app` and `env`; the mandatory tags
(`Owner`, `CostCenter`, `Environment`, `Application`, `ManagedBy`,
`DataClassification`) still apply to every taggable resource. `Environment` and
`Application` must match the explicit name. Auto-generated names rely on those tags
instead of duplicating metadata in a hard-coded name.
