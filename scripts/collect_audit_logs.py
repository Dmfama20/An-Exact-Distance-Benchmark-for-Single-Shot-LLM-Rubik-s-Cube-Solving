#!/usr/bin/env python3
"""Collect the per-trial visible-response logs into audit_logs/.

The runner writes one detailed log per trial (initial state, verbatim
visible model response, parsed sequence, verifier result) into
timestamped session directories. This script maps each log to its
scored metrics row and copies it to a stable per-trial name:

    audit_logs/module{A,B}/<model_tag>__<instance_id>.log

Mapping: instance id embedded in the log + trial end time (log file
mtime vs. row timestamp), assigned one-to-one greedily with a maximum
discrepancy of 90 s. Global-deadline timeout trials have no response
and therefore no log (their metrics rows carry the timeout error).

Usage:  python scripts/collect_audit_logs.py \
            [--log-roots logs ../logs] [--out audit_logs]
"""

import argparse
import csv
import glob
import os
import re
import shutil
import sys
from datetime import datetime

MODULES = (("A", "logs/dstar_runs"), ("B", "logs/nominal_runs"))
MAX_DISCREPANCY_S = 90


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-roots", nargs="+", default=["logs", "../logs"])
    ap.add_argument("--out", default="audit_logs")
    args = ap.parse_args()

    rows = {}
    for mod, runs in MODULES:
        for p in glob.glob(os.path.join(runs, "*_metrics.csv")):
            for r in csv.DictReader(open(p)):
                ts = datetime.fromisoformat(r["timestamp"]).timestamp()
                rows[(mod, r["model_tag"], r["instance_id"])] = (
                    ts, (r.get("error") or ""))
    if not rows:
        sys.exit("no metrics rows found — run from the repository root")

    logs = []
    for root in args.log_roots:
        for p in glob.glob(os.path.join(root, "rubiks_2026*",
                                        "test_*.log")):
            m = re.search(r"Instance: (\S+)",
                          open(p, errors="replace").read(400))
            if m:
                logs.append((m.group(1), os.path.getmtime(p), p))

    cands = sorted(
        (abs(mt - ts), key, p)
        for key, (ts, _) in rows.items()
        for (iid, mt, p) in logs if iid == key[2])
    match, used = {}, set()
    for diff, key, p in cands:
        if diff > MAX_DISCREPANCY_S or key in match or p in used:
            continue
        match[key] = p
        used.add(p)

    for key, src in sorted(match.items()):
        mod, arm, iid = key
        d = os.path.join(args.out, f"module{mod}")
        os.makedirs(d, exist_ok=True)
        shutil.copy(src, os.path.join(d, f"{arm}__{iid}.log"))

    unmatched = [k for k in rows if k not in match]
    non_timeout = [k for k in unmatched
                   if not rows[k][1].startswith("Trial timeout")]
    print(f"collected {len(match)}/{len(rows)} trial logs into "
          f"{args.out}/")
    print(f"unmatched: {len(unmatched)} "
          f"(timeouts, expected: {len(unmatched) - len(non_timeout)}; "
          f"OTHER: {len(non_timeout)})")
    if non_timeout:
        for k in non_timeout[:10]:
            print("  UNEXPECTED unmatched:", k)
        sys.exit(1)


if __name__ == "__main__":
    main()
