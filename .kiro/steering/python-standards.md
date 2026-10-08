# My Company — Python Standards

> Distributed via the `my-company-coding-standards` Power. How My Company sets up
> and checks Python code.

## Environment: virtualenv

- **Local development uses a virtual environment.** Never install project
  dependencies into the system/global Python. Create and activate a venv first:

  ```bash
  python3 -m venv .venv
  source .venv/bin/activate        # Windows: .venv\Scripts\activate
  pip3 install -r requirements.txt -r requirements-dev.txt
  ```

- The `.venv/` directory is git-ignored and never committed.
- **In a CI/CD pipeline, do NOT create a virtualenv.** The build runs in an
  isolated, ephemeral container that is already a clean environment — a venv there just adds a useless layer. Install straight into the runner:

  ```bash
  pip3 install -r requirements.txt -r requirements-dev.txt
  ```

- Decide by context, not by habit: interactive/local machine → venv; automated
  build agent → no venv. When unsure whether you're in a pipeline, check for CI
  env vars (e.g. `CI`, `CODEBUILD_BUILD_ID`) before creating one.

## Dependencies: runtime vs dev

- **`requirements.txt`** holds only the dependencies the app needs to build and
  run in production. Keep it minimal — nothing in here that isn't required at
  runtime.
- **`requirements-dev.txt`** holds the tooling that is **not** mandatory for the
  build or install: linters, test and coverage tools, auditors — e.g. `pytest`,
  `coverage`, `pylint`, `pip-audit`. It does not duplicate runtime dependencies;
  development and CI setup install both requirements files explicitly.
- The build/install path (and the production image) installs `requirements.txt`
  only. Dev machines and the CI check stage install both `requirements.txt` and
  `requirements-dev.txt`.
- Pin versions in both files (no open-ended ranges) so builds are reproducible.

## Code review tooling

Every change is checked with the full toolchain below before it's considered done.
Run them locally in the venv and again in the pipeline without one. Checks 1–3 are
independent and run **in parallel**; check 4 runs after they all pass — see
"Order & gating" for how the two phases gate.

1. **Tests — `pytest` via the `testing` skill.** Do not hand-roll the test/coverage
   commands: **use this Power's `testing` skill**, which puts tests in `tests/`,
   runs them under branch coverage, and enforces the `fail_under` configured in
   `.coveragerc`. The skill is the source of truth for how we test.

2. **Lint — `pylint` via the `linting` skill.** Do not hand-roll the pylint
   command or config: **use this Power's `linting` skill**, which generates the
   `.pylintrc` with pylint itself, runs `pylint --recursive=y .` across the whole
   repo, and defines how to read/suppress findings. The skill is the source of
   truth for how we lint.

3. **Dependency audit — `pip-audit`.** Scan runtime and development dependency
   manifests for known CVEs. A finding blocks the change until the dependency is
   upgraded or, if no fix exists, explicitly risk-accepted in review.

   ```bash
   pip-audit -r requirements.txt
   pip-audit -r requirements-dev.txt
   ```

4. **Security review — Kiro.** Run Kiro's AI security review over the change to
   catch what the scanners miss (injection, secrets, unsafe deserialization,
   broken authz, leaked internals). Address findings before merge; this complements
   `pip-audit`, it does not replace it.

## Order & gating

The four checks run in **two phases**. A change is "done" only when all four pass.
Skipping a check requires explicit approval in review.

**Phase 1 — the three mechanical checks run in parallel.** They are independent
(no shared state, no ordering between them), so dispatch all three at once rather
than one after another:

1. **Tests** — `testing` skill (pytest + branch coverage at the `.coveragerc`
   `fail_under`).
2. **Lint** — `linting` skill (pylint; exclude `.venv`/`cdk.out` per that skill).
3. **Dependency audit** — `pip-audit -r requirements.txt` and
   `pip-audit -r requirements-dev.txt`.

Run them concurrently — e.g. launch each as its own background process and
collect the results, or dispatch one subagent per check in a single turn. Wait for
**all three** to finish, then gate: if **any** of the three fails, the change is
blocked — fix it and re-run Phase 1. Do not start Phase 2 until all three are
green.

**Phase 2 — security review, only after Phase 1 is fully green.** Run Kiro's AI
security review over the change (it needs the code in its final, passing form).
This stays sequential and last on purpose: no point reviewing code the scanners
would reject.

Rationale: the three mechanical checks are fast and independent, so parallelizing
them shortens the loop without weakening any gate — each still has to pass. The
security review depends on that clean baseline, so it does not join the parallel
phase.
