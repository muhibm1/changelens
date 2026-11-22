# fresh7: protocol, committed before any repository was mined

Fresh set 4 (`../fresh6/`) scored ChangeLens v8 at 28 of 40 (70%), under the 93% bar, so by its own protocol it
became practice data. fresh7 is the next held-out set. Every rule of `../fresh6/PROTOCOL.md` holds unchanged
(about 40 in-scope bugs, at least half `compiles: true`, `needs_data` kept as its own bucket, an independent
labeller that never reads ChangeLens, labels committed before scoring, four reviewers scored once, the same judge and
the same maintainer-decided false-alarm rule), with one addition: every repository in `../fresh6/repos.txt` is now
excluded too.

## Declared before scoring (added when the labels were committed)

- Ablations, computed from the same v9 run with no extra model calls: v9 without its verifier (every candidate the
  reviewers and the compile step proposed, counted as a bug) and v9 without the compile step (its compile-sourced
  findings removed). They show which parts of the design carry the result.
- A second false-alarm reading: every ordinary commit flagged by any reviewer is adjudicated REAL, FALSE or UNCLEAR
  by an isolated reader that is not told which reviewer flagged it. The maintainer rule stays the primary figure.
- The comparison with plain Opus is reported per bug (which bugs each caught), not only as two totals.
