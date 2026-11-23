# fresh9: protocol, committed before any repository was mined

Fresh set 6 (`../fresh8/`) scored ChangeLens v10 at 32 of 40 (80%), the same as Claude Opus on its own, under the
93% bar, so it became practice data. fresh9 is the next held-out set and, by decision on 2026-11-23, the last one in
this round: whatever v11 scores on it is published. Every rule of `../fresh8/PROTOCOL.md` holds (and through it
`../fresh6/` and `../fresh7/`), with one addition: every repository in `../fresh8/repos.txt`, and every repository the
fresh8 labeller cloned and dropped, is excluded too.

ChangeLens v11 is frozen, and its commit recorded in `pipelinebench/results/warehouse-agent/`, before fresh9 is
scored. The labeller's work is not read by anyone working on ChangeLens until v11 is frozen. To save usage, the
blind false-alarm reading uses one isolated reader instead of three.

## Applied before scoring

The labeller used balboa, which the fresh8 labeller had cloned and dropped, so it is excluded by the rule above. Its
bugs were removed from the set before anything was scored; nothing about them was read beyond the count.
