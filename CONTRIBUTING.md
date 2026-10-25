# Contributing

The rules for every change are in [CLAUDE.md](CLAUDE.md). This note is about GitHub Actions minutes, which are a fixed monthly budget.

## Push once per ready state

Every push to an open, non-draft pull request runs CI. Agents and people alike:

- Work locally until the change is ready: `.venv/bin/ruff check src tests` and `.venv/bin/pytest -q` both pass.
- Batch commits and push once. Do not push each fix, a work in progress or a "retry".
- Open the pull request as a draft while it is still moving; CI skips drafts. Mark it ready when it is done.
- Fix review comments together and push them in one go.
- Never push an empty commit or close and reopen a pull request to restart CI. Use "Re-run jobs" on the failed run instead.

## What runs, and when

| Workflow | Runs on | Skips |
|---|---|---|
| `ci` | pull requests (non-draft), every push to `main`, by hand | changes to top-level `*.md` and `docs/` only |
| `fathom-gate` | pull requests touching `fixture/` (non-draft) | drafts |
| `fathom-review` | the `fathom-review` label, or by hand with a PR number | everything else; it never runs on a push |
| `fathom-propose` | Mondays 03:17 UTC, or by hand | |

A newer push cancels a pull request's older run, and each job has a short timeout. Ask for Fathom's self-review once, when the pull request is ready, by adding the `fathom-review` label; the label comes off after the run, so add it again for a second look after a substantial change.

## When Actions is out of minutes

Rebase on `main`, run the full local suite and lint, say in the pull request that CI was skipped and what you ran locally, then merge. Re-run CI on `main` once minutes reset.
