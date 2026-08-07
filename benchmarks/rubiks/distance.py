"""
Exact optimal-distance machinery for 3x3x3 Rubik's Cube instances.

Motivation: the number of random
scramble moves d is a *generator parameter*, not a measured planning depth --
consecutive moves can cancel or merge, so the true optimal distance d* can be
smaller than d.  This module computes the exact optimal solution distance
d* (half-turn metric, 18 face turns U D L R F B / ' / 2) for any state within
a configurable horizon, so that benchmark instances can be generated at a
*verified* fixed depth and grouped by measured distance instead of scramble
length.

Design notes
------------
* State representation: the 54-sticker net exactly as printed by the
  ``magiccube`` library (faces U, L, F, R, B, D, each 3x3 row-major),
  encoded as ``bytes`` of length 54.  Centres are fixed under face turns,
  so the net uniquely identifies a cube position.
* Move semantics are *derived from magiccube itself* (see
  ``derive_move_permutations``): each of the 18 face turns is recovered as a
  permutation of the 54 net positions by matching sticker colours across
  several random states.  This removes any risk of a hand-coded convention
  mismatch between the solver and the evaluation library.
* Distance queries use meet-in-the-middle: a BFS table of all states within
  ``table_depth`` (default 5) of solved, plus an iterative-deepening DFS
  from the query state.  Exact distances up to ``table_depth + dfs_depth``
  are feasible in pure Python (d* <= 10 in seconds per instance).

Verification hooks (used by tests/test_rubiks_distance.py):
* BFS level sizes must match the published HTM position counts
  (18, 243, 3240, 43239, 574908 at depths 1-5).
* Differential test: the engine must agree with magiccube on the resulting
  net for random move sequences.
* Every witness solution returned by ``solve_optimal`` must solve the state
  when replayed through magiccube.
"""

from __future__ import annotations

import json
import os
import random
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Net layout constants
# ---------------------------------------------------------------------------

FACE_ORDER = ("up", "left", "front", "right", "back", "down")
FACE_OFFSET = {name: 9 * i for i, name in enumerate(FACE_ORDER)}

# The 18 half-turn-metric face moves (Singmaster notation, magiccube accepts
# all of them via Cube.rotate).
FACES = ("U", "D", "L", "R", "F", "B")
OPPOSITE = {"U": "D", "D": "U", "L": "R", "R": "L", "F": "B", "B": "F"}
SUFFIXES = ("", "'", "2")
MOVES = tuple(f + s for f in FACES for s in SUFFIXES)

# Published number of 3x3x3 positions at exact HTM depth (OEIS A080601).
KNOWN_HTM_COUNTS = {0: 1, 1: 18, 2: 243, 3: 3240, 4: 43239, 5: 574908}

# Caches live in <repo root>/data/ (this file is benchmarks/rubiks/distance.py)
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
_PERM_CACHE_PATH = os.path.join(_REPO_ROOT, "data", "rubiks_move_perms.json")
_TABLE_CACHE_PATH = os.path.join(_REPO_ROOT, "data",
                                 "rubiks_depth_table_d{depth}.bin")


# ---------------------------------------------------------------------------
# Net extraction from magiccube
# ---------------------------------------------------------------------------

def net_from_cube(cube) -> bytes:
    """Flatten a magiccube Cube into the canonical 54-byte net.

    Uses the same parsed representation as the benchmark prompt
    (benchmarks.rubiks.benchmark.cube_text_to_json) so solver, prompt, and evaluator
    all share one layout.
    """
    from benchmarks.rubiks.benchmark import cube_text_to_json
    grids = cube_text_to_json(str(cube))
    stickers: List[str] = []
    for name in FACE_ORDER:
        face = grids[name]
        if len(face) != 3 or any(len(row) != 3 for row in face):
            raise ValueError(f"Malformed face {name!r} in cube net: {face}")
        for row in face:
            stickers.extend(row)
    return "".join(stickers).encode("ascii")


def net_to_grids(net: bytes) -> Dict[str, List[List[str]]]:
    """Inverse of net_from_cube: 54-byte net -> six 3x3 colour grids."""
    s = net.decode("ascii")
    grids = {}
    for name in FACE_ORDER:
        off = FACE_OFFSET[name]
        grids[name] = [[s[off + 3 * r + c] for c in range(3)] for r in range(3)]
    return grids


# ---------------------------------------------------------------------------
# Move permutations (derived from magiccube, cached to data/)
# ---------------------------------------------------------------------------

def derive_move_permutations(num_samples: int = 16, seed: int = 0,
                             ) -> Dict[str, Tuple[int, ...]]:
    """Recover each move's net permutation from magiccube ground truth.

    A face turn is a fixed permutation pi of the 54 net positions
    (state_after[j] = state_before[pi[j]]).  Colours alone do not identify
    positions on a single state, so we intersect the colour-consistency
    constraint over several random states until each target position has a
    unique source candidate.
    """
    from magiccube import Cube

    rng = random.Random(seed)
    perms: Dict[str, Tuple[int, ...]] = {}
    # Pre-generate random scrambles (face turns only) for the sample states.
    scrambles = [
        " ".join(rng.choice(MOVES) for _ in range(30))
        for _ in range(num_samples)
    ]

    for move in MOVES:
        befores, afters = [], []
        for scramble in scrambles:
            cube = Cube(3)
            cube.rotate(scramble)
            befores.append(net_from_cube(cube))
            cube.rotate(move)
            afters.append(net_from_cube(cube))

        perm: List[int] = []
        for j in range(54):
            candidates = [
                i for i in range(54)
                if all(b[i] == a[j] for b, a in zip(befores, afters))
            ]
            if len(candidates) != 1:
                raise RuntimeError(
                    f"Move {move}: position {j} has {len(candidates)} source "
                    f"candidates; increase num_samples")
            perm.append(candidates[0])
        pi = tuple(perm)
        if sorted(pi) != list(range(54)):
            raise RuntimeError(f"Move {move}: derived mapping is not a permutation")
        perms[move] = pi
    return perms


def load_move_permutations(cache_path: str = _PERM_CACHE_PATH,
                           ) -> Dict[str, Tuple[int, ...]]:
    """Load derived move permutations, deriving and caching on first use."""
    if os.path.exists(cache_path):
        with open(cache_path) as fh:
            raw = json.load(fh)
        return {m: tuple(p) for m, p in raw.items()}
    perms = derive_move_permutations()
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w") as fh:
        json.dump({m: list(p) for m, p in perms.items()}, fh)
    return perms


def apply_move(net: bytes, perm: Tuple[int, ...]) -> bytes:
    return bytes(map(net.__getitem__, perm))


def invert_move(move: str) -> str:
    if move.endswith("'"):
        return move[:-1]
    if move.endswith("2"):
        return move
    return move + "'"


def invert_sequence(seq: str) -> str:
    return " ".join(invert_move(m) for m in reversed(seq.split()))


# ---------------------------------------------------------------------------
# Distance solver
# ---------------------------------------------------------------------------

class DistanceSolver:
    """Exact HTM optimal-distance queries via meet-in-the-middle."""

    def __init__(self, table_depth: int = 5, verbose: bool = True,
                 use_cache: bool = True):
        from magiccube import Cube
        self.perms = load_move_permutations()
        self.move_names = list(MOVES)
        self.move_perms = [self.perms[m] for m in self.move_names]
        self.move_faces = [m[0] for m in self.move_names]
        self.solved = net_from_cube(Cube(3))
        self.table_depth = table_depth
        self.verbose = verbose
        self.table = self._build_or_load_table(use_cache)

    # -- BFS table -----------------------------------------------------

    # The cache uses a trivial custom binary format instead of pickle:
    # unpickling executes code from the file, so a tampered cache would be
    # an arbitrary-code-execution vector. Format: for each depth level
    # 0..table_depth, an 8-byte big-endian count followed by that many
    # 54-byte states. Loading is pure byte slicing.
    def _write_table_bin(self, path: str, table: Dict[bytes, int]):
        levels: Dict[int, list] = {}
        for state, depth in table.items():
            levels.setdefault(depth, []).append(state)
        with open(path, "wb") as fh:
            for depth in range(self.table_depth + 1):
                states = sorted(levels.get(depth, []))
                fh.write(len(states).to_bytes(8, "big"))
                for st in states:
                    fh.write(st)

    def _read_table_bin(self, path: str) -> Optional[Dict[bytes, int]]:
        table: Dict[bytes, int] = {}
        with open(path, "rb") as fh:
            data = fh.read()
        pos = 0
        for depth in range(self.table_depth + 1):
            if pos + 8 > len(data):
                return None
            count = int.from_bytes(data[pos:pos + 8], "big")
            pos += 8
            if KNOWN_HTM_COUNTS.get(depth) not in (None, count):
                return None  # level size contradicts published counts
            end = pos + count * 54
            if end > len(data):
                return None
            for off in range(pos, end, 54):
                table[data[off:off + 54]] = depth
            pos = end
        if pos != len(data):
            return None
        return table

    def _build_or_load_table(self, use_cache: bool) -> Dict[bytes, int]:
        cache = _TABLE_CACHE_PATH.format(depth=self.table_depth)
        if use_cache and os.path.exists(cache):
            table = self._read_table_bin(cache)
            if (table is not None and table.get(self.solved) == 0
                    and all(table.get(apply_move(self.solved, p)) == 1
                            for p in self.move_perms)):
                if self.verbose:
                    print(f"[solver] loaded depth-{self.table_depth} table "
                          f"({len(table):,} states, count-verified) "
                          f"from {cache}")
                return table
            print(f"[solver] cache {cache} failed verification — rebuilding")

        table: Dict[bytes, int] = {self.solved: 0}
        frontier = [self.solved]
        for depth in range(1, self.table_depth + 1):
            nxt: List[bytes] = []
            for state in frontier:
                for perm in self.move_perms:
                    child = apply_move(state, perm)
                    if child not in table:
                        table[child] = depth
                        nxt.append(child)
            if depth in KNOWN_HTM_COUNTS and len(nxt) != KNOWN_HTM_COUNTS[depth]:
                raise RuntimeError(
                    f"BFS level {depth} has {len(nxt)} states, expected "
                    f"{KNOWN_HTM_COUNTS[depth]} — move engine is wrong")
            if self.verbose:
                print(f"[solver] BFS depth {depth}: {len(nxt):,} new states "
                      f"(matches published count)")
            frontier = nxt

        if use_cache:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            self._write_table_bin(cache, table)
        return table

    # -- queries ---------------------------------------------------------

    def distance(self, net: bytes, max_depth: int = 12) -> Optional[int]:
        """Exact optimal distance of *net*, or None if > max_depth."""
        hit = self.table.get(net)
        if hit is not None:
            return hit
        for total in range(self.table_depth + 1, max_depth + 1):
            if self._reachable_within(net, total):
                return total
        return None

    def solve_optimal(self, net: bytes, max_depth: int = 12) -> Optional[str]:
        """One optimal solution sequence for *net* (witness), or None."""
        d = self.distance(net, max_depth=max_depth)
        if d is None:
            return None
        moves: List[str] = []
        state = net
        remaining = d
        while remaining > 0:
            for name, perm in zip(self.move_names, self.move_perms):
                child = apply_move(state, perm)
                child_d = self.table.get(child)
                if child_d is None and remaining - 1 > self.table_depth:
                    if self._reachable_within(child, remaining - 1):
                        child_d = remaining - 1
                if child_d is not None and child_d == remaining - 1:
                    moves.append(name)
                    state = child
                    remaining -= 1
                    break
            else:
                raise RuntimeError("optimal descent failed (engine bug)")
        return " ".join(moves)

    # -- internals -------------------------------------------------------

    def _reachable_within(self, net: bytes, total: int) -> bool:
        """Is optimal distance of *net* <= total?  DFS depth total - table_depth
        with same-face and commuting-opposite-face pruning, probing the BFS
        table at every node."""
        budget = total - self.table_depth
        if budget <= 0:
            hit = self.table.get(net)
            return hit is not None and hit <= total
        return self._dfs(net, budget, last_face="")

    def _dfs(self, state: bytes, budget: int, last_face: str) -> bool:
        # Any table hit certifies d(state) <= table_depth, and the DFS has
        # consumed at most total - table_depth moves, so d(net) <= total.
        if state in self.table:
            return True
        if budget == 0:
            return False
        for name, perm, face in zip(self.move_names, self.move_perms,
                                    self.move_faces):
            if face == last_face:
                continue  # same-face merge is never needed on an optimal path
            if OPPOSITE[face] == last_face and face > last_face:
                continue  # canonical order for commuting opposite faces
            if self._dfs(apply_move(state, perm), budget - 1, face):
                return True
        return False


# ---------------------------------------------------------------------------
# Scramble helpers (used by the instance generator)
# ---------------------------------------------------------------------------

def random_scramble(length: int, rng: random.Random) -> str:
    """Random face-turn scramble with immediate same-face repeats suppressed.

    Cancellation across commuting opposite faces (e.g. "U D U'") is *not*
    suppressed; the generator relies on distance verification, not on the
    scramble shape, to certify d*.
    """
    seq: List[str] = []
    last_face = ""
    for _ in range(length):
        face = rng.choice([f for f in FACES if f != last_face])
        seq.append(face + rng.choice(SUFFIXES))
        last_face = face
    return " ".join(seq)


def apply_sequence(net: bytes, sequence: str,
                   perms: Dict[str, Tuple[int, ...]]) -> bytes:
    for move in sequence.split():
        net = apply_move(net, perms[move])
    return net
