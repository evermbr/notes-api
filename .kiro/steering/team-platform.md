---
inclusion: always
---

# Team Standards — Platform / Infrastructure Team (Team Level — Tier 2)

> Owned by the Platform team. Distributed to members of the Platform team only.
> Inherits and tightens the org standards. Managed via the team's steering repo and
> synced into each member's workspace `.kiro/steering`.

## Team conventions

- **IaC language:** AWS CDK in Python is the team default. Terraform is allowed only for
  multi-cloud or when a customer mandates it — document the rationale in the project README.
- **Testing:** every stack change ships with an updated assertion test under `tests/unit`.
  Use `aws_cdk.assertions.Template` to assert on synthesized resources.
- **Stack naming:** `<Domain><Purpose>Stack` (PascalCase), e.g. `TechSummitStack`.
- **Construct IDs:** PascalCase, descriptive, stable (renaming replaces resources).

## Naming & tagging strategy

The org mandates *which* tags exist (`Owner`, `CostCenter`, `Environment`); the team
standardizes *how* resources are named and tagged across all our CDK projects.

**Naming**

- **Stacks:** `<Domain><Purpose>Stack` PascalCase — e.g. `TechSummitStack`, `TechSummitPipelineStack`.
- **Construct IDs:** PascalCase, descriptive, stable — e.g. `UrlShortenerTable`, `ApiTokenAuthorizer`.
- **Physical resource names:** don't hard-code them; let CDK auto-name so resources are
  replaceable and environment-agnostic. Only set a physical name when a consumer requires
  a stable, known value, and then prefix it `<project>-<resource>` (kebab-case), e.g.
  `tech-summit-url-shortener`.
- **Pipelines:** `<project>` kebab-case — e.g. `tech-summit-demo`.

**Tagging** — apply org tags once at the app/stack level so every resource inherits them,
rather than per-construct:

```python
Tags.of(app).add("Owner", "<team-or-owner>")
Tags.of(app).add("CostCenter", "<cost-center>")
Tags.of(app).add("Environment", deployment_type)   # e.g. "app" / "pipeline" / "prod"
```

- `Environment` reflects what is being deployed/where; keep its allowed values documented
  per project.
- Additional team tags (optional but encouraged): `Project`, `ManagedBy=cdk`.

## Review expectations

- IaC changes require a `cdk diff` in the CR description.
- Any new IAM statement is called out explicitly in the CR for security review.
- Prefer L2/L3 CDK constructs over raw Cfn resources unless a feature is unavailable.

## Definition of done (team)

1. `cdk synth` succeeds with no errors.
2. Unit tests pass (`pytest`).
3. Quality-gate hooks (lint, tests, security scan) are green.
4. README updated if behavior or interface changed.
