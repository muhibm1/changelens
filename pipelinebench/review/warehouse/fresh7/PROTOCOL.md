# fresh7: protocol, committed before any repository was mined

Fresh set 4 (`../fresh6/`) scored ChangeLens v8 at 28 of 40 (70%), under the 93% bar, so by its own protocol it
became practice data. fresh7 is the next held-out set. Every rule of `../fresh6/PROTOCOL.md` holds unchanged
(about 40 in-scope bugs, at least half `compiles: true`, `needs_data` kept as its own bucket, an independent
labeller that never reads ChangeLens, labels committed before scoring, four reviewers scored once, the same judge and
the same maintainer-decided false-alarm rule), with one addition: every repository in `../fresh6/repos.txt` is now
excluded too.
