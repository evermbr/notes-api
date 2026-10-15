# Tasks — Versioned Notes

- [ ] **1. Add versioning to existing operations**
  - Update create to store and return `version: 1`.
  - Update get to return stored version or `1` for a legacy item.
  - Update create/get unit tests without weakening existing assertions.
  - Validate the targeted handler tests before continuing.

- [ ] **2. Implement conditional update**
  - Create `lambda/notes_update/handler.py`.
  - Validate path parameters, case-insensitive `If-Match`, JSON shape, allowed fields,
    and non-empty string values.
  - Perform one conditional atomic update and map missing/stale results to `404`/`409`.
  - Add complete happy-path, legacy-item, invalid-input, missing-item, stale-version, and
    unexpected-error tests.

- [ ] **3. Implement conditional delete**
  - Create `lambda/notes_delete/handler.py`.
  - Validate path parameters and case-insensitive `If-Match`.
  - Perform one conditional delete and map missing/stale results to `404`/`409`.
  - Add complete happy-path, legacy-item, invalid-input, missing-item, stale-version, and
    unexpected-error tests.

- [ ] **4. Wire CDK with least privilege**
  - Add `NotesUpdateFunction` and `NotesDeleteFunction`.
  - Add explicit `PATCH` and `DELETE` methods on `/notes/{id}`.
  - Preserve the existing custom authorizer.
  - Grant update only GetItem+UpdateItem and delete only GetItem+DeleteItem.
  - Extend stack assertions for resources, routes, authorization, and exact actions.

- [ ] **5. Run independent gates and bounded review**
  - Have `test-agent` run tests, coverage, pylint, dependency audit, and app synth.
  - Have `review-agent` inspect compatibility, DynamoDB concurrency semantics, IAM,
    error handling, test strength, and cdk-nag output in parallel with testing.
  - If either returns a failing verdict, send its findings to `feature-developer` and
    repeat both gates. Stop after at most two revision cycles and report the blocker.

- [ ] **6. Document and deliver**
  - Update README with PATCH/DELETE contracts and concurrency responses.
  - Re-run both gates after documentation/code fixes.
  - Open a pull request only when `TEST_GATE: PASS` and `REVIEW_GATE: PASS`.
  - Do not merge or deploy.
