---
inclusion: fileMatch
fileMatchPattern: 'tech_summit/**|tests/**|app.py|cdk.json'
---

# Project Steering — tech_summit (Project Level — Tier 3)

> Scoped to this repository only. Activated automatically when working on this project's
> CDK code or tests. Inherits org + team steering and adds project-specific detail.

## What this project is

A CDK Python app (`TechSummitStack`) that provisions an Amazon SNS topic subscribed by
an Amazon SQS queue — the sample workload used to demo the Enterprise Context Engineering
Kit end to end (spec → hooks → CLI agents → deploy).

## Project rules

- Entry point is `app.py`; the stack lives in `tech_summit/tech_summit_stack.py`.
- Keep the SNS→SQS pattern intact; new resources must be added as separate, named constructs.
- SQS queues use a visibility timeout that matches the consumer's max processing time
  (currently 300s). Add a dead-letter queue before taking any queue to production.
- Every synthesized resource must carry the org-mandated tags (`Owner`, `CostCenter`,
  `Environment`) — apply them at the `App` or `Stack` level.

## Build / test commands

- Synthesize: `cdk synth`
- Test: `pytest`
- Diff against deployed: `cdk diff`
