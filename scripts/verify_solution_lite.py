#!/usr/bin/env python3
"""Dependency-free STATE-INTEGRITY and WITNESS-VALIDITY checker
(standard library only).

Scope (precise): this script verifies state integrity (scramble
replay, state hash) and witness validity (the stored witness solves
the state in the claimed number of moves). A valid witness of length
d* establishes an UPPER bound on the optimal distance; it does NOT by
itself prove that no shorter solution exists. Optimality certification
comes from the exact solver's bidirectional search with lower-bound
exclusion (benchmarks/rubiks/distance.py, validated in
tests/test_rubiks_distance.py); use that for full re-certification.

Purpose: allow anyone to re-check these properties
and any graded model solution WITHOUT installing the pinned cube
dependency. The only inputs are the released artifacts:

    data/rubiks_move_perms.json   the 18 HTM move permutations over the
                                  54-facelet net (extracted from the
                                  evaluation library and independently
                                  validated: move+inverse = identity,
                                  quarter^2 = half, quarter^4 = identity)
    instances/*.json              manifest instances (scramble, facelet
                                  net, state hash, optimal witness)

Checks per instance: (1) the scramble replays to the stored facelet
net, (2) the stored net matches the manifest state hash, (3) the
optimal witness solves the state, (4) the witness length equals the
claimed distance. With --sequence, additionally verifies an arbitrary
candidate move sequence against one instance (exit 0 iff it solves).

Usage:
    python scripts/verify_solution_lite.py \
        [--manifest instances/rubiks_dstar_manifest_v1.json]
    python scripts/verify_solution_lite.py --instance rubiks-d03-i001 \
        --sequence "R U R'"
"""

import argparse
import hashlib
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERMS_PATH = os.path.join(REPO, "data", "rubiks_move_perms.json")


def load_perms():
    with open(PERMS_PATH) as fh:
        perms = {m: tuple(p) for m, p in json.load(fh).items()}
    if len(perms) != 18:
        sys.exit("move permutation file must contain exactly 18 moves")
    return perms


def apply_seq(state, seq, perms):
    """Apply a space-separated HTM move sequence to a 54-tuple state."""
    for mv in seq.split():
        if mv not in perms:
            raise ValueError(f"illegal move token {mv!r}")
        p = perms[mv]
        state = tuple(state[p[i]] for i in range(54))
    return state


def net_to_state(net):
    """Flatten the facelet dict (up/left/front/right/back/down, 3x3
    rows) into the 54-tuple order used by the move permutations."""
    order = ("up", "left", "front", "right", "back", "down")
    out = []
    for face in order:
        for row in net[face]:
            out.extend(row)
    return tuple(out)


def state_to_net(state):
    order = ("up", "left", "front", "right", "back", "down")
    net, i = {}, 0
    for face in order:
        net[face] = [list(state[i + 3 * r:i + 3 * r + 3]) for r in range(3)]
        i += 9
    return net


def net_hash(net):
    canonical = json.dumps(net, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def solved_state(net):
    """The solved net has uniform faces with this net's centre colours
    (centres are invariant under face turns)."""
    return tuple(c for face in ("up", "left", "front", "right", "back",
                                "down")
                 for c in [net[face][1][1]] * 9)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest",
                    default="instances/rubiks_dstar_manifest_v1.json")
    ap.add_argument("--instance", default=None,
                    help="verify a single instance by id")
    ap.add_argument("--sequence", default=None,
                    help="candidate move sequence to check against "
                         "--instance (exit 0 iff it solves the state)")
    args = ap.parse_args()

    perms = load_perms()
    with open(args.manifest) as fh:
        manifest = json.load(fh)
    instances = manifest["instances"]
    if args.instance:
        instances = [i for i in instances if i["id"] == args.instance]
        if not instances:
            sys.exit(f"instance {args.instance!r} not in manifest")

    bad = 0
    for inst in instances:
        stored = net_to_state(inst["facelets"])
        solved = solved_state(inst["facelets"])
        replay = apply_seq(solved, inst["scramble"], perms)
        d = inst.get("d_star", inst.get("verified_d_star"))
        problems = []
        if replay != stored:
            problems.append("scramble does not replay to stored net")
        if net_hash(inst["facelets"]) != inst["state_hash"]:
            problems.append("state hash mismatch")
        if apply_seq(stored, inst["optimal_witness"], perms) != solved:
            problems.append("witness does not solve the state")
        if len(inst["optimal_witness"].split()) != d:
            problems.append("witness length differs from claimed d*")
        if problems:
            bad += 1
            print(f"FAIL {inst['id']}: {'; '.join(problems)}")

    if args.sequence is not None:
        inst = instances[0]
        stored = net_to_state(inst["facelets"])
        final = apply_seq(stored, args.sequence, perms)
        ok = final == solved_state(inst["facelets"])
        print(f"candidate sequence on {inst['id']}: "
              f"{'SOLVES' if ok else 'does NOT solve'} the state")
        sys.exit(0 if ok and bad == 0 else 1)

    print(f"{len(instances) - bad}/{len(instances)} instances checked "
          f"(scramble replay, state hash, witness validity/length — "
          f"upper-bound check; optimality is certified by the exact "
          f"solver)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
