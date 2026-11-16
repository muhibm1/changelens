# Warehouse rules on real bugs: what was measured

The warehouse rules (`warehouse_rules.py`, `pipelinebench/warehouse_replay.py`) check a dbt diff for row-count changes, nullability and casts, and documented columns that are no longer selected. They were scored against hand-written labels of real fixed bugs in public dbt repositories, and against ordinary commits to count false alarms.

| Set | Repos | Rules version | Result |
|---|---|---|---|
| Tuned on | cal-itp | v2 | 14 of 15 |
| Tuned on | cal-itp | v3 | 15 of 15 (fitted to the set) |
| Blind 1, scored once | tuva, ol-data-platform | v2, frozen | 3 of 24 in scope |
| Blind 2, scored once | spellbook, opensource-observer | v3, frozen | 2 of 23 in scope |

False alarms on ordinary commits: 19% (spellbook) and 41% (opensource-observer) on unseen repos; 20 to 25% on the tuned repos.

Reading: the rules do not generalise. About 10% of real bugs on unseen warehouses. The bugs no diff rule sees are wrong or undeclared keys, upstream types, lineage layering and dedup semantics. Rules plus a model on the blind sets was not run.

Label files and raw outputs: `finance-90/changelens-bugs/` (dev.yaml, blind.yaml, blind2.yaml, results/). The v3 output still carries the label `v2` in `RULES_VERSION`.
