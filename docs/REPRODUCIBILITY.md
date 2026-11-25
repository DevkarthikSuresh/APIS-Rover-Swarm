# Reproducibility notes

## Evidence provenance

The latest author-supplied INDISCON manuscript (paper 1854) is the source of the reported headline results. The author indicates that the recovered CSVs may be from older development runs. Their precise dates and correspondence to the manuscript trials are unconfirmed. They are preserved unchanged under `results/archived_development/` and must not be treated as the final paper dataset. The supplied source is also preserved unchanged; its experimental revision has not been established.

- The three supplied controller Python files and WBT world are preserved byte-for-byte. The world is Webots R2025a, with a 4 ms basic timestep and 64 ms controller step. The original controller uses a 1000 ms startup step, a multiple of 4 ms.
- The world instantiates ten Moose robots, nine with `PythonEquivalent` and `moose(8)` with `<none>`. For a new all-active trial set that rover's controller to `PythonEquivalent` in Webots. Save a copy of the world before doing so. Disable a leader only after identifying it during a trial to test crash recovery; record the actual failure timestamp and trial identifier independently.
- The controller expects all ten names, hence a majority of six. Channels 1 and 2 separate consensus and positions. All-to-all unlimited radio range is a simulation assumption.
- `leader_log.csv` includes malformed rows, likely concurrent writes, and may contain multiple runs. Original data is retained. The audit reports structurally valid rows, not verified trial identities. Use separate per-rover log files and run IDs in future trials.
- `election_metrics.csv` records two election-start and leader-elected pairs. Both intervals are 0.128 s; these do not measure failure-to-election recovery. The manuscript reports 4.17 ± 0.61 s, 3.80 ± 0.55 s and 3.42 ± 0.23 s for N=3, 5 and 10 respectively, each with 5/5 successful failovers (Table II). These cannot be recomputed from the recovered files alone. The manuscript reports a 1.95 m minimum separation (Section V-A), whereas the archived distance figure appears to have a minimum of roughly 4.4 m and crosses the 6 m threshold. Use 1.95 m when quoting the latest manuscript, and reconcile the figure and text using the original distance telemetry before claiming independent verification. Do not infer that all trials maintained the threshold.
- The implementation is RAFT-inspired, not a complete implementation of the Raft protocol: no durable term/vote state, log freshness checks, majority-acknowledged commit or authenticated membership. The hash chain checks integrity, not Byzantine fault tolerance. The collision avoidance policy is heuristic, not a formal safety guarantee.
- Full simulation and failure injection have not been rerun during packaging. Standalone component checks exercise only selected behaviors.
- The historical interface screenshot shows an earlier YOLO-related controller variant. The supplied controller does not import or execute YOLO; the current project requires only Webots' Python controller API and Python standard library.

## Manuscript versus recovered implementation

The supplied code is preserved rather than modified to appear identical to the paper. The precise experimental revision is not established.

| Item | INDISCON manuscript | Recovered source/world |
| --- | --- | --- |
| Physics step | 16 ms | 4 ms |
| Control step | 64 ms | 64 ms |
| Election timeout | 300–500 ms | 3–5 s plus priority bias |
| Swarm sizes | 3, 5, 10 | Fixed ten-name peer list; six votes required |
| Ledger commit | Majority replication described | Local append and broadcast; no majority acknowledgment protocol |
| Predictive safety | Linear extrapolation of neighbor position described | Current position and priority-based heuristics; no explicit velocity extrapolation |

The manuscript reports 15 successful failover trials (five per size) and ledger recovery following rejoin. The current archive does not contain enough trial-level evidence to independently verify those reported outcomes. The provided figure is preserved unmodified.
