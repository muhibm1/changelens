# ChangeLens for dbt teams

This is the release for data teams: what ChangeLens does on a dbt project, how to run it for two weeks without it blocking anything, and what it measured on a real warehouse's history. Every number below links to the result file it comes from.

## What it does on a pull request

1. **Pre-flight (no model).** `changelens preflight` and `changelens gate` resolve the diff into typed changes, such as a dropped column, a changed filter or a changed partition. They then walk column-level lineage to every downstream model, exposure and DAG task, and evaluate the team's constraints. The result is one verdict where every claim cites an id from the graph. If ChangeLens cannot map a path, it lists the path under `unresolved` instead of guessing.
2. **Review (optional model).** `changelens review` adds a model's reading of the diff. A model warning on a dbt or Airflow file stays at warn only when something recorded backs it (the `platform` escalation policy, the default):
   - a firing constraint,
   - a downstream reader of a column the hunk names,
   - consumers of the file's own model when the change touches its rows or values,
   - an incident on the asset,
   - a failing case confirmed against the warehouse.

   Two more things are findings on their own, with no model: a model that still selects a column the change drops or renames, and a model that reads another asset by its warehouse name (`project.dataset.table`) where `ref()` or `source()` would name it, which drops the dependency from dbt's own graph.

   Any other model warning is lowered to inform, with a note.
3. **Gate (opt in).** `changelens gate --fail-on block` fails CI. Until a team turns that on, ChangeLens only advises.

ChangeLens never writes to the warehouse or the orchestrator. Warehouse confirmation runs one read-only `SELECT` per finding, and only when `review.warehouse` is configured.

## Setup

```bash
uv pip install "changelens @ git+https://github.com/muhibm1/changelens"
```

```yaml
# changelens.yaml at the repository root
db: .changelens/graph.duckdb
adapters:
  dbt:
    project: warehouse            # the folder with dbt_project.yml; ChangeLens reads target/manifest.json, and catalog.json when present
  airflow:
    dags: airflow/dags            # optional
  git:
    paths: [warehouse, airflow]
constraints: changelens/constraints    # optional: YAML, merged by pull request
review:
  provider: anthropic             # or openai (any compatible endpoint), command, claude-code
  model: claude-haiku-5-5
```

The `anthropic` provider spends API credit and refuses to start without a dollar cap: set `CHANGELENS_API_CAP` (the reviewer workflow sets 25 cents a run). `claude-code` answers on a Claude subscription instead and needs neither a key nor a cap.

Run `dbt parse` so `target/manifest.json` exists, then `changelens sync`. On cal-itp/data-infra (625 models), the first sync takes 2 minutes 9 seconds and a re-sync takes 13 seconds (`pipelinebench/results/partner-calitp-dbt.md`).

## Two weeks in shadow mode

Copy `.github/workflows/changelens-review.yml` and change the paths. ChangeLens's own repository runs it on demand (the `changelens-review` label); for shadow mode set its trigger to `pull_request: types: [opened, ready_for_review]` so every pull request gets a review. It posts one summary comment per pull request, updated when it runs again, plus inline comments on new findings, and it never fails the build. After two weeks:

- Count the warnings per week and label each one confirmed, plausible or noise. This is the same scoring `pipelinebench history-replay` uses.
- Run `changelens postflight --summary` to set each verdict's predicted severity against what the next three runs did.
- Turn on `changelens gate` only if the false alarms are low enough for the team.

## Measured

| What | Result | Source |
| --- | --- | --- |
| cal-itp, 116 merged changes over 26 weeks, deterministic checks only | 4 warnings, 0 false alarms a week | `pipelinebench/results/calitp-history-deterministic.md` |
| cal-itp, 49 pull requests over 4 weeks (2026-10-08 to 2026-11-05), deterministic checks only, compared with the reviewers | at 0.2.0, 0 warnings a week and one miss shared with the reviewers (hardcoded table names replacing `ref()`); after the fix that study led to, 0.5 warnings a week, all on that one pull request, 0 false alarms | `pipelinebench/results/calitp-shadow-2026-11.md` |
| Same changes, model review, `platform` policy | 2.23 warnings and 0.81 false alarms a week; 3 of 6 known bugs flagged at warn | `pipelinebench/results/calitp-history-model-consumers.md` |
| Same changes, model review, every model warning kept (`model`) | 5.27 warnings and 1.62 false alarms a week; the same 3 of 6 bugs | `pipelinebench/results/calitp-history-model.md` |
| 45 held-out bugs from eight application repositories (Sonnet 5.5, 3 runs) | 0.741 surfaced at any severity, 0.615 at warn | `CHANGELOG.md`, Precision at warn |

Read these results with three caveats:

- **Labels.** One reader wrote the cal-itp labels from each diff and the upstream history, with limited checking against the code.
- **Tuned rule.** The consumer-backing rule was written after the first replay showed the gate lowering all three caught bugs. The `platform` row is therefore a tuning result, not a held-out one.
- **Newer graph.** Replays run against the current graph rather than the graph as it stood at each commit. That is why a few of the deterministic warnings are artifacts.

## Known limits

- dbt and Airflow (including gusty YAML DAGs) are the only platform adapters.
- Without column lineage for a model (4 of 625 on cal-itp), its blast radius falls back to the asset level.
- Model review costs tokens. On cal-itp, a review averaged about 150k input tokens with four Haiku passes and a Sonnet second reading. `--lens general` gives one pass, and the findings cache skips repeat calls on an unchanged diff.
