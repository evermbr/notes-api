# Design — Versioned Notes

## Overview

Add optimistic concurrency to Notes API with a monotonically increasing integer version.
DynamoDB condition expressions provide atomic compare-and-swap behavior; API Gateway
continues to map one HTTP operation to one single-purpose Lambda.

## API contract

### Create

`POST /notes` keeps its current request. A successful response becomes:

```json
{"id":"abc123","title":"Example","content":"Text","version":1}
```

### Read

`GET /notes/{id}` includes `version`. Items without a stored version are represented as
version `1` for backward compatibility.

### Update

```http
PATCH /notes/{id}
If-Match: 3
Content-Type: application/json

{"title":"Updated"}
```

Success returns the complete item with `version: 4`. The handler accepts `title`,
`content`, or both; unknown fields are rejected.

### Delete

```http
DELETE /notes/{id}
If-Match: 4
```

Success returns `204` and an empty body.

## DynamoDB behavior

### Update condition

Use one atomic `UpdateItem` with a condition equivalent to:

```text
attribute_exists(id) AND (
  version = :expected OR
  (attribute_not_exists(version) AND :expected = :initial)
)
```

The update expression sets only supplied fields and increments version. For a legacy item,
`if_not_exists(version, :initial) + :increment` produces version `2`.

### Delete condition

Use `DeleteItem` with the same version condition. Request `ALL_OLD` return values when
supported so success can be distinguished cleanly.

### Conditional failure mapping

Catch only DynamoDB `ConditionalCheckFailedException`. Perform `GetItem` afterward:

- no item → `404`;
- item exists → `409`.

Re-raise every other AWS error. This requires exactly Get+Update for the update Lambda and
Get+Delete for the delete Lambda.

## Handler structure

```text
lambda/
├── notes_create/handler.py   # add version=1
├── notes_get/handler.py      # return stored version or legacy version=1
├── notes_update/handler.py   # PATCH with conditional UpdateItem
├── notes_delete/handler.py   # DELETE with conditional DeleteItem
└── authorizer/handler.py     # unchanged
```

Shared production code is not introduced for a few response/header helpers; each Lambda
asset remains independently deployable. Tests may continue sharing
`tests/unit/lambda_test_utils.py`.

Headers are resolved case-insensitively because API Gateway events may preserve or
normalize header casing. `If-Match` accepts only unquoted positive decimal integers for
this demo.

## CDK design

In `TechSummitStack`:

1. Add `NotesUpdateFunction` and `NotesDeleteFunction` using Python 3.13 and the existing
   `TABLE_NAME` environment variable.
2. Add explicit `PATCH` and `DELETE` methods to the existing `{id}` API resource.
3. Apply the existing default custom authorizer through `default_method_options`.
4. Grant explicit table actions rather than broad read/write grants.
5. Include the new functions in the existing Lambda cdk-nag suppression scope where the
   same documented IAM4/L1 rationale applies.

Construct IDs are stable PascalCase; physical names remain CDK-generated.

## Error model

| Situation | Status |
|---|---:|
| Malformed JSON or invalid update fields | 400 |
| Missing/invalid `If-Match` | 400 |
| Note not found | 404 |
| Stale version | 409 |
| Successful update | 200 |
| Successful delete | 204 |
| Unexpected DynamoDB/service error | Raised; Lambda invocation fails visibly |

## Verification

- Unit tests use in-memory table doubles and explicit conditional-failure simulations.
- Stack tests inspect generated API methods, authorization, Lambda count, and IAM actions.
- Full gates: pytest under branch coverage, whole-repository pylint, dependency audit,
  Bandit, and app synth with cdk-nag report inspection.
- No deployment is part of this design.
