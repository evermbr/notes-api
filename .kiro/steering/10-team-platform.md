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

## Review expectations

- IaC changes require a `cdk diff` in the CR description.
- Any new IAM statement is called out explicitly in the CR for security review.
- Prefer L2/L3 CDK constructs over raw Cfn resources unless a feature is unavailable.

## Definition of done (team)

1. `cdk synth` succeeds with no errors.
2. Unit tests pass (`pytest`).
3. Quality-gate hooks (lint, tests, security scan) are green.
4. README updated if behavior or interface changed.
