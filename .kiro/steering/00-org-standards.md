---
inclusion: always
---

# Organization Standards (Org Level — Tier 1)

> Distributed from the central `~/.kiro/steering` registry to every developer in the
> organization. Governed by the **Developer Experience Platform** team. Changes require
> a CR reviewed by two platform maintainers (see governance model in the README).
> Precedence: org-level rules are the floor. Team and project steering may add stricter
> rules but may never relax an org rule.

## Non-negotiable engineering principles

- **Security is a gate, not a step.** No credentials, secrets, private keys, or tokens
  in source, logs, comments, or commit messages. Reference secrets by name only.
- **Least privilege by default.** IAM policies, roles, and MCP tool scopes grant the
  minimum permissions required. No wildcard `"*"` actions on resources unless justified
  in a comment and approved.
- **Everything is reviewed.** No direct pushes to `main`/`master`. All changes land via
  code review. Production-affecting changes require human-in-the-loop approval.
- **Reproducible builds.** Pin dependency versions. No open-ended version ranges in
  production dependency manifests.
- **Inclusive language** in all code, comments, and docs (allowlist/denylist, primary/replica).

## Language & style baselines

- Prefer explicit over clever. Optimize for the next reader.
- Public functions and modules carry docstrings describing intent, inputs, and failure modes.
- Handle errors explicitly; never swallow exceptions silently.

## AWS / cloud baselines

- Tag every provisioned resource with `Owner`, `CostCenter`, and `Environment`.
- Encrypt data at rest and in transit by default.
- Never disable termination/deletion protection, versioning, or backups on production
  resources without explicit written approval.
