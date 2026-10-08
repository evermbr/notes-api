---
name: linting
description: My Company's Python linting workflow with pylint. Use when linting Python code, setting up or updating a pylint config, or when asked to check code style/quality. Runs pylint across the whole repo against a versioned .pylintrc that is generated with pylint itself.
---

# Linting (My Company standard)

How My Company lints Python with **pylint**. Every change is linted across the
whole repo against a versioned `.pylintrc` before it's considered done.

## The config is generated with pylint

Do not hand-write `.pylintrc` from scratch. Generate the baseline with pylint so
it always matches the installed version's options, then commit it:

```bash
pylint --generate-rcfile > .pylintrc
```

- Commit `.pylintrc` to the repo root — the config is versioned, not per-machine.
- Regenerate and merge it when you bump the pylint version, so new/renamed options
  don't silently drift.
- Configure pylint here, not on the command line. The command line stays short;
  the policy lives in the file.

Minimum settings we keep in `.pylintrc`:

```ini
[MAIN]
# use all cores; 0 = auto-detect
jobs = 0
# fail the run if the score drops below this
fail-under = 10.0

[MESSAGES CONTROL]
# example: silence checks we deliberately don't enforce, with a reason in the PR
# disable = missing-module-docstring
```

## Exclude non-source trees (.venv, cdk.out)

`pylint --recursive=y .` walks **everything** under the repo root, including the
virtualenv (`.venv/`) and CDK synth output (`cdk.out/`) when they exist. Those are
not our source — linting them is wrong (thousands of third-party/generated
findings) and slow (the run can hang for minutes or time out).

**When either `.venv` or `cdk.out` (or both) is present, do not run the plain
`--recursive=y .` form.** Instead, list the real source files and feed those to
pylint, skipping those directories:

```bash
find . -name '*.py' ! -path './cdk.out/*' ! -path './.venv/*' | xargs pylint
```

- Same `.pylintrc`, same findings/score gate — the only difference is which files
  pylint sees. `find` selects our `.py` files and the `! -path` filters drop the
  artifact trees before pylint ever opens them.
- Add a `! -path` filter per directory you need to exclude; the pattern is matched
  against the path `find` prints, so keep the leading `./`.
- If neither directory is present, the plain `pylint --recursive=y .` from the
  repo root is fine.

## Running pylint against the whole repo

Lint the entire repository, not just the file you touched, so cross-file issues
surface:

```bash
pylint --recursive=y .
```

- `--recursive=y .` walks the tree and lints every discovered package/module.
- **If `.venv` or `cdk.out` exist, use the `find ... | xargs pylint` form from
  "Exclude non-source trees" below instead** — `--recursive=y .` would lint those
  artifact trees.
- Respect the project layout: if code lives under `src/`, `pylint --recursive=y .`
  from the repo root still finds it; make sure `src` is importable (e.g. venv +
  `pythonpath`) so pylint resolves imports and doesn't false-positive.
- Run it in the local **venv** (see python-standards) and again in the pipeline
  without one — same command, same `.pylintrc`.

## Reading and acting on results

- pylint prints message IDs like `C0114` (missing-docstring), `W0611`
  (unused-import), `E1101` (no-member) and ends with a **score out of 10**. The
  run fails when the score is below `fail-under`.
- **Fix the finding; don't blanket-disable.** If a check genuinely doesn't apply, disable it as narrowly as possible with a reason:

  ```python
  x = get()  # pylint: disable=no-member  # dynamic attribute set by the ORM
  ```

- Prefer an **inline** disable on the offending line over a file-level or global
  disable. A global disable in `.pylintrc` needs a comment and review sign-off.
- Message categories: `C` convention, `R` refactor, `W` warning, `E` error,
  `F` fatal. Never merge with `E`/`F` outstanding.

## Handy flags (occasional use)

- `pylint --recursive=y . --output-format=colorized` — readable local output.
- `pylint --recursive=y . --output-format=json` — machine-readable for CI parsing.
- `pylint --list-msgs` — look up what a message ID means.
- `pylint --disable=all --enable=E,F --recursive=y .` — quick "errors only" pass.

## Workflow

1. If `.pylintrc` is missing, generate it: `pylint --generate-rcfile > .pylintrc`
   and commit it.
2. Run pylint from the repo root (in the venv locally): `pylint --recursive=y .`,
   or the `find . -name '*.py' ! -path './cdk.out/*' ! -path './.venv/*' | xargs pylint`
   form when `.venv`/`cdk.out` are present (see "Exclude non-source trees").
3. Fix findings; narrowly disable with a justification only when a check doesn't
   apply. No `E`/`F` left, score at or above `fail-under`.
4. The change is done for linting only when the run exits clean.
