---
inclusion: fileMatch
fileMatchPattern: ['app.py', 'tech_summit/**/*.py', 'cdk.json']
---

# My Company — Resource Tagging Strategy

> Distributed via the `my-company-coding-standards` Power. Every provisioned
> resource carries a consistent set of tags so we can attribute cost, find owners,
> and automate governance. Tags complement names (see `naming-strategy.md`); they
> never contradict them.

## Mandatory tags

Every resource that supports tagging carries all of these. A resource missing a
mandatory tag is a compliance failure and blocks the change.

| Tag key | Purpose | Example / allowed values |
|---|---|---|
| `Owner` | Team that owns the resource (not an individual) | `team-payments` |
| `CostCenter` | Billing/chargeback code | `CC-4711` |
| `Environment` | Deployment stage | `dev` \| `staging` \| `prod` |
| `Application` | Service/app short name | `task-api` |
| `ManagedBy` | What provisions it | `cdk` \| `cloudformation` \| `terraform` |
| `DataClassification` | Sensitivity of data handled | `public` \| `internal` \| `confidential` \| `restricted` |

When a resource has an explicit physical name, `Environment` and `Application`
must match its `<env>` and `<app>` segments. Auto-named CDK resources rely on these
tags as their authoritative environment/application metadata.

## Optional / conditional tags

Use when they apply:

- `Component` — sub-part within an app (e.g. `ingest`, `api`, `worker`).
- `Version` — release or build identifier for deployed artifacts.
- `Compliance` — regime the resource falls under (e.g. `pci`, `gdpr`), required
  when `DataClassification` is `confidential` or `restricted`.
- `ExpiresOn` — ISO date (`2026-12-31`) for temporary/sandbox resources so cleanup
  automation can reap them.

## Format conventions

- **Keys are `PascalCase`** (`CostCenter`), **values are lowercase kebab-case**
  unless the value is a code or date (`CC-4711`, `2026-12-31`).
- No secrets, PII, or free-form prose in tag values. Tags are metadata, not notes.
- Prefer a fixed allowed-value set over free text so tags stay queryable.

## How to apply (CDK)

Apply mandatory tags **once at the app or stack level** so every resource inherits
them — never tag resource by resource:

```python
from aws_cdk import Tags

tags = Tags.of(app)          # or Tags.of(stack)
tags.add("Owner", "team-payments")
tags.add("CostCenter", "CC-4711")
tags.add("Environment", env)          # derived from context, not hardcoded
tags.add("Application", "task-api")
tags.add("ManagedBy", "cdk")
tags.add("DataClassification", "internal")
```

Derive `Environment`/`Application` from the same stack props/context that drive
naming, so one definition is correct in every environment.

## Enforcement

- Treat missing or invalid mandatory tags as a hard failure — surface it in the
  IaC validation/compliance step before deploy (cfn-guard / policy checks).
- Cost and access automation relies on these tags; adding a resource without them
  breaks chargeback and ownership reporting, so it does not merge.
