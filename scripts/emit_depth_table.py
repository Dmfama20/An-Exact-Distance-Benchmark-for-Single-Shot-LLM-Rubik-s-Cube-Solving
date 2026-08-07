#!/usr/bin/env python3
"""Deterministically (re)build the depth-5 distance-table cache and
print its SHA-256 (purpose: release the exact-distance lookup
artifact with checksums plus an independent validation path).

The cache is the benchmark solver's own binary table
(data/rubiks_depth_table_d5.bin, length-prefixed custom format — no
pickle). Building it twice must yield byte-identical files; this
script builds, rebuilds into a temporary copy, compares, and prints
the hash. The EXPECTED hash for the released instance sets is
recorded in AUDIT_README.md.

Validation without trusting this artifact at all is also possible:
scripts/independent_distance_check.py re-derives every manifest
distance from a third-party engine, and the solver checks its layer
sizes against the published HTM ball on every start.

Usage:  python scripts/emit_depth_table.py
"""

import hashlib
import os
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from benchmarks.rubiks import distance  # noqa: E402


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    cache = os.path.join(REPO, "data", "rubiks_depth_table_d5.bin")
    if os.path.exists(cache):
        os.remove(cache)
    print("building depth-5 table (first build)...", flush=True)
    distance.DistanceSolver(table_depth=5, verbose=False, use_cache=True)
    h1 = sha256(cache)

    backup = cache + ".rebuild"
    shutil.move(cache, backup)
    print("rebuilding (determinism check)...", flush=True)
    distance.DistanceSolver(table_depth=5, verbose=False, use_cache=True)
    h2 = sha256(cache)
    os.remove(backup)

    if h1 != h2:
        sys.exit(f"NOT deterministic: {h1[:16]} vs {h2[:16]}")
    print(f"deterministic: two independent builds are byte-identical")
    print(f"data/rubiks_depth_table_d5.bin sha256: {h1}")


if __name__ == "__main__":
    main()
