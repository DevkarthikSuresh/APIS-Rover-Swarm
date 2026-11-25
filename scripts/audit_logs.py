"""Summarize archived CSVs without repairing, merging or inferring trials."""
import argparse, csv, json, math
from collections import Counter
from pathlib import Path


def audit(root):
    with (root / 'election_metrics.csv').open(newline='', encoding='utf-8-sig') as f:
        events = list(csv.DictReader(f))
    starts = {}
    intervals = []
    for row in events:
        term, t = int(row['term']), float(row['time'])
        if row['event'] == 'ELECTION_STARTED':
            starts.setdefault(term, t)
        elif row['event'] == 'LEADER_ELECTED' and term in starts:
            intervals.append({'term': term, 'leader': row['rover'],
                              'start_to_election_s': round(t-starts[term], 6)})
    counts = Counter()
    bad, valid = 0, 0
    low, high = math.inf, -math.inf
    with (root / 'leader_log.csv').open(newline='', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            try:
                t, rover, leader, term, state = row
                t, term = float(t), int(term)
                if not (math.isfinite(t) and rover.startswith('moose') and
                        (leader == 'None' or leader.startswith('moose')) and
                        state in {'follower', 'candidate', 'leader'}):
                    raise ValueError('invalid row')
            except (ValueError, TypeError):
                bad += 1
                continue
            valid += 1
            counts[rover] += 1
            low, high = min(low,t), max(high,t)
    return {'election_events': events, 'election_intervals': intervals,
            'leader_log_valid_rows': valid, 'leader_log_malformed_rows': bad,
            'rover_row_counts': dict(sorted(counts.items())),
            'observed_time_range_s': [low,high] if valid else None,
            'warning': 'Archived development logs may predate the manuscript and combine runs; they are not verified paper-trial data. Election-start latency is not crash-to-recovery latency.'}

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1]/'results'/'archived_development')
    args=p.parse_args()
    print(json.dumps(audit(args.root), indent=2))
