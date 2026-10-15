# Requirements — Versioned Notes

## Goal

Prevent clients from silently overwriting or deleting a note that changed after they
read it. Extend the existing Notes API with optimistic concurrency while preserving its
current authentication and one-Lambda-per-operation architecture.

## Requirements

### 1. Versioned create and read

1. WHEN a client creates a valid note through `POST /notes`, THE SYSTEM SHALL store the
   note with integer `version` equal to `1`.
2. WHEN create succeeds, THE SYSTEM SHALL preserve the existing `201` response and
   existing `id`, `title`, and `content` fields, and SHALL add `version`.
3. WHEN a client reads an existing note through `GET /notes/{id}`, THE SYSTEM SHALL
   preserve the existing `200` response and fields and SHALL return its integer
   `version`.
4. WHEN a legacy note has no stored version, THE SYSTEM SHALL expose it as version `1`.
5. Existing validation, `400` behavior, and `404` behavior SHALL remain compatible.

### 2. Conditional update

1. THE SYSTEM SHALL expose `PATCH /notes/{id}` through a new single-purpose Lambda.
2. The request SHALL require an `If-Match` header containing a positive integer version.
3. The JSON body SHALL contain at least one of `title` or `content`.
4. Any supplied `title` or `content` SHALL be a non-empty, non-whitespace string.
5. Missing or invalid `If-Match`, malformed JSON, unsupported fields, and invalid values
   SHALL return `400` without modifying the note.
6. WHEN the note exists and its current version matches `If-Match`, THE SYSTEM SHALL
   atomically update the supplied fields, increment the version by exactly one, and
   return `200` with the complete updated note.
7. WHEN the note does not exist, THE SYSTEM SHALL return `404`.
8. WHEN the note exists but its version differs, THE SYSTEM SHALL return `409` and SHALL
   NOT modify it.
9. A legacy note without a version SHALL match `If-Match: 1` and become version `2` after
   a successful update.

### 3. Conditional delete

1. THE SYSTEM SHALL expose `DELETE /notes/{id}` through a new single-purpose Lambda.
2. The request SHALL require an `If-Match` header containing a positive integer version.
3. Missing or invalid `If-Match` SHALL return `400` without deleting anything.
4. WHEN the note exists and its current version matches, THE SYSTEM SHALL atomically
   delete it and return `204` with an empty body.
5. WHEN the note does not exist, THE SYSTEM SHALL return `404`.
6. WHEN the note exists but its version differs, THE SYSTEM SHALL return `409` and SHALL
   NOT delete it.
7. A legacy note without a version SHALL match `If-Match: 1`.

### 4. Architecture and security

1. Update and delete SHALL each use a separate Lambda asset directory and CDK Function.
2. API Gateway SHALL use explicit `PATCH` and `DELETE` methods on `/notes/{id}`.
3. Every new method SHALL use the existing custom Lambda token authorizer.
4. The update Lambda SHALL receive only `dynamodb:GetItem` and
   `dynamodb:UpdateItem` access to `NotesTable`.
5. The delete Lambda SHALL receive only `dynamodb:GetItem` and
   `dynamodb:DeleteItem` access to `NotesTable`.
6. No new secret, public endpoint, wildcard IAM action, or hard-coded physical resource
   name SHALL be introduced.
7. The implementation SHALL NOT deploy or mutate AWS resources; it ends with a pull
   request for human review.

### 5. Quality and verification

1. Every new behavior SHALL have happy-path and failure-path unit tests under
   `tests/unit/`.
2. CDK assertion tests SHALL verify both new Lambdas, explicit API methods, custom
   authorization, and narrow DynamoDB actions.
3. Existing tests SHALL continue to pass.
4. Whole-repository pylint SHALL score `10.00/10`.
5. Line and branch coverage SHALL meet `.coveragerc`; the target for this change is 100%.
6. `cdk synth -c deployment_type=app -c environment=dev` SHALL complete with zero
   unaddressed cdk-nag findings.
7. README SHALL document the final API contract only after implementation.
