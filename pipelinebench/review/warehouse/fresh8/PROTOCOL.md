# fresh8: protocol, committed before any repository was mined

Fresh set 5 (`../fresh7/`) scored ChangeLens v9 at 34 of 39 (87%), under the 93% bar, so by its own protocol it
became practice data. fresh8 is the next held-out set. Every rule of `../fresh6/PROTOCOL.md` and the declared-before-
scoring additions of `../fresh7/PROTOCOL.md` hold unchanged (about 40 in-scope bugs, at least half `compiles: true`,
`needs_data` kept as its own bucket, an independent labeller that never reads ChangeLens, labels and a manifest
committed before scoring, four reviewers scored once, the same judge and the same maintainer-decided false-alarm
rule, the blind false-alarm reading, per-bug comparison with plain Opus), with two additions:

- Every repository in `../fresh7/repos.txt` is now excluded too.
- At most 3 repositories from any one GitHub owner (organisation or user), and at most 12 of the in-scope bugs from
  any one owner, so that no single house style or templating language dominates the set. fresh7 drew 9 of its 17
  repositories from one owner.

ChangeLens v10 is frozen, and its commit recorded in `pipelinebench/results/warehouse-agent/`, before fresh8 is
scored. The labeller's work runs in a separate folder and is not read by anyone working on ChangeLens until v10 is
frozen.
