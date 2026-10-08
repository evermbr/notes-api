---
name: testing
description: My Company's testing workflow for Python code. Use when writing, running, or reviewing tests, or when asked about coverage. Tests live in tests/; coverage is measured with coverage.py and must meet the line and branch threshold configured in .coveragerc before a change is done.
---

# Testing (My Company standard)

How My Company writes and runs tests. This is a hard gate: a change is not "done"
until its tests pass and line + branch coverage meet the `fail_under` configured in
`.coveragerc`.

## Where tests live

- All tests go in the **`tests/`** directory at the repo root — never next to the
  source, never inline.
- Mirror the source layout so tests are easy to find: `src/pkg/foo.py` →
  `tests/test_foo.py` (or `tests/pkg/test_foo.py` for larger trees).
- Test files are named `test_*.py`; test functions are named `test_*`.
- Every unit of behaviour ships at least one **happy-path** and one
  **failure-path** test. New behaviour ships with new tests in the same change.

## Coverage tool

Measure coverage with **coverage.py** (`coverage` on PyPI —
https://pypi.org/project/coverage/). Install it as a dev dependency (pinned):

```bash
pip3 install coverage
```

Run the suite under coverage, then report with the gate. Use **branch coverage**,
not just line coverage:

```bash
# Run the tests under coverage (branch mode)
python3 -m coverage run --branch -m pytest

# Report and show what's missing (the gate comes from .coveragerc)
python3 -m coverage report --show-missing

# Optional: browsable HTML report
python3 -m coverage html   # writes htmlcov/index.html
```

Keep configuration in `.coveragerc` (or `[tool.coverage.*]` in
`pyproject.toml`) so the command line stays short and the gate is versioned:

```ini
# .coveragerc
[run]
branch = True
# Measure the whole project, not a single src/ package. For a CDK app the code
# lives at the repo root (app.py, the stack package, lambda/), so source = . and
# omit the non-source trees instead of narrowing source.
source = .
omit =
    app.py
    .venv/*
    __init__.py

[report]
show_missing = True
fail_under = 80
# Report measured namespace packages too, so every file shows up (not just the
# ones with an __init__.py).
include_namespace_packages = True
```

With this config the run/report shrinks to `coverage run -m pytest && coverage report`.

## The coverage gate

- **The gate is the `fail_under` you set in `.coveragerc`** — line *and* branch.
  Whatever threshold you configure there makes a shortfall a hard failure
  (non-zero exit), so it blocks the change locally and in CI. Keep the threshold
  in config, not on the command line.
- If coverage is below your `fail_under`, **add the missing tests** — do not lower
  the threshold and do not delete code to game the number.
- Excluding a line with `# pragma: no cover` is a last resort. It is allowed only
  for genuinely untestable lines (e.g. `if __name__ == "__main__":`), and each use
  carries a short comment justifying it. Reviewers reject unjustified pragmas.
- Coverage measures execution, not correctness. Hitting your threshold with weak
  assertions is still a failing review — assert on real behaviour, not just that
  code ran.

## Workflow

1. Write or change code in the project source tree (`tech_summit/`, `lambda/`,
   or `app.py`).
2. Write the tests in `tests/` (happy path + failure path).
3. `coverage run --branch -m pytest` then `coverage report --show-missing` (the
   `fail_under` in `.coveragerc` enforces the gate).
4. If the report puts coverage below your `fail_under`, add tests until it meets
   the threshold and exits 0. Only then is the change done.
