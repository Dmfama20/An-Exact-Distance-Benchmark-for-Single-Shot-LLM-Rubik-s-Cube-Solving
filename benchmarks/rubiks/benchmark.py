"""
Rubik's Cube benchmark: true single-shot, verified-depth, blinded prompt.

One API call, no tools, no simulator access.  The model receives the cube
state (six 3x3 colour grids in JSON) and must return a complete Singmaster
move sequence; the sequence is applied to an independent copy of the state
and verified deterministically (magiccube).

Instances come exclusively from a pre-generated manifest
(scripts/gen_rubiks_instances.py): each is certified at its exact optimal
solution distance d*, stored with a stable ID and a state hash, and served
byte-identically to every configuration.
"""

import copy
import json
import re
import time
from typing import Optional

from scaffold.llm import RobustLLM


# ---------------------------------------------------------------------------
#  Cube-state rendering (shared layout for prompt, solver, and verifier)
# ---------------------------------------------------------------------------

def cube_text_to_json(cube_text: str) -> dict:
    """Convert magiccube ASCII representation to a JSON-friendly dict."""
    cube_text = re.sub(r'\x1b\[[0-9;]*m', '', cube_text)
    lines = cube_text.strip().split('\n')
    up, left, front, right, back, down = [], [], [], [], [], []
    for i in range(3):
        if i < len(lines):
            row = lines[i].strip().split()
            if row:
                up.append(row[:3])
    for i in range(3):
        idx = 3 + i
        if idx < len(lines):
            parts = lines[idx].split()
            if len(parts) >= 12:
                left.append(parts[0:3]); front.append(parts[3:6])
                right.append(parts[6:9]); back.append(parts[9:12])
    for i in range(3):
        idx = 6 + i
        if idx < len(lines):
            row = lines[idx].strip().split()
            if row:
                down.append(row[:3])
    return {"up": up, "left": left, "front": front,
            "right": right, "back": back, "down": down}


# ---------------------------------------------------------------------------
#  Move parser — action space aligned with the distance metric
# ---------------------------------------------------------------------------

# The ONLY legal action space is the 18 HTM face turns — exactly the move
# set in which d* is computed. Slice moves (M E S), whole-cube rotations
# (x y z), and wide moves are rejected: allowing them would let solutions
# use a different metric than the one that defines the difficulty axis.
HTM_MOVES = frozenset(
    f + s for f in "ULFRBD" for s in ("", "'", "2"))


def _is_move(token: str) -> bool:
    return token in HTM_MOVES


def _parse_moves(text: str):
    """Extract the answer move sequence. Returns (moves, parse_mode).

    Primary (scored) parse is STRICT: the last non-empty line must consist
    solely of legal HTM face turns — exactly what the prompt instructs.
    A lenient fallback (code block / any pure-move line / longest token
    run) is additionally computed for logging only: it quantifies how many
    failures are interface non-compliance rather than wrong plans, but it
    never affects the score.

    parse_mode: "strict" | "lenient" | "none".
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines:
        tokens = lines[-1].split()
        if tokens and all(_is_move(t) for t in tokens):
            return " ".join(tokens), "strict"

    # --- lenient fallback (diagnostics only, still HTM-restricted) ---
    code_block = re.search(r'```[^\n]*\n?(.*?)```', text, re.DOTALL)
    if code_block:
        tokens = [t for t in code_block.group(1).split() if _is_move(t)]
        if tokens:
            return " ".join(tokens), "lenient"

    for line in lines:
        tokens = line.split()
        if tokens and all(_is_move(t) for t in tokens):
            return line, "lenient"

    best_run: list = []
    cur_run: list = []
    for tok in text.split():
        if _is_move(tok):
            cur_run.append(tok)
        else:
            if len(cur_run) > len(best_run):
                best_run = cur_run
            cur_run = []
    if len(cur_run) > len(best_run):
        best_run = cur_run
    if best_run:
        return " ".join(best_run), "lenient"

    return None, "none"


# ---------------------------------------------------------------------------
#  Instance loading (hash-verified reconstruction)
# ---------------------------------------------------------------------------

def _cube_from_instance(instance: dict):
    """Reconstruct the manifest instance's cube state and verify its hash.

    The scramble sequence is replayed through magiccube (the same library
    that evaluates candidate solutions) and the resulting net is checked
    against the manifest's state hash, so a silent library or layout change
    fails loudly instead of corrupting the pairing across configurations.
    """
    import hashlib
    from magiccube import Cube
    cube = Cube(3)
    cube.rotate(instance["scramble"])
    cube_json = cube_text_to_json(str(cube))
    canonical = json.dumps(cube_json, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    if digest != instance["state_hash"]:
        raise RuntimeError(
            f"Instance {instance.get('id')}: reconstructed state hash "
            f"{digest[:12]}… does not match manifest "
            f"{instance['state_hash'][:12]}…")
    return cube, cube_json


# ---------------------------------------------------------------------------
#  Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert Rubik's cube solver. You will be shown the state of a
scrambled 3×3×3 cube in JSON format and you must return the complete solution
move sequence that solves it.

Notation rules (Singmaster, outer face turns ONLY):
- Face moves: R L U D F B  (clockwise 90°)
- Counter-clockwise: R' L' U' D' F' B'
- Half-turns: R2 L2 U2 D2 F2 B2
These 18 moves are the only legal moves. Slice moves (M, E, S), whole-cube
rotations (x, y, z), and wide moves (e.g. Rw) are NOT allowed and will be
rejected.

Face colours in the solved state:
  Up=White  Down=Yellow  Front=Green  Back=Blue  Left=Orange  Right=Red

Your response must end with a single line containing ONLY the move sequence,
with moves separated by spaces. No other text on that line.
Example final line:  R U R' U' R' F R2 U' R' U' R U R' F'
"""


def _build_prompt(cube_json: dict) -> str:
    """Blinded task prompt.

    Deliberately identical across all depth conditions: neither the
    verified optimal distance, nor a scramble length, nor any qualitative
    difficulty label is disclosed to the model.  The state itself is the
    only thing that changes between conditions.
    """
    return (
        f"The cube below is scrambled.\n"
        f"Analyse the cube state and provide the complete solution.\n\n"
        f"Cube state (JSON):\n{json.dumps(cube_json, indent=2)}\n\n"
        f"Provide your solution move sequence as the last line of your response."
    )


# ---------------------------------------------------------------------------
#  Secondary metric
# ---------------------------------------------------------------------------

def _sticker_match_fraction(cube) -> float:
    """Fraction of stickers matching a solved cube (secondary metric).

    1.0 = solved; a random scrambled 3x3x3 sits around ~0.3-0.5.
    Computed by comparing each face's stickers against that face's
    centre colour (centres are invariant under face turns).
    """
    faces = cube_text_to_json(str(cube))
    total = matched = 0
    for face in faces.values():
        if not face or len(face) < 2:
            continue
        centre = face[1][1] if len(face[1]) >= 2 else None
        for row in face:
            for sticker in row:
                total += 1
                if sticker == centre:
                    matched += 1
    return matched / total if total else 0.0


def _make_log(test_id, difficulty, instance_id, model, raw_response,
              moves_str, success, elapsed, initial_cube_json,
              sticker_match=None, parse_mode=None,
              axis_label="verified optimal distance d*",
              verified_d_star=None):
    # axis_label names the run axis correctly per manifest type: the
    # verified distance d* (Module A) or the NOMINAL length (Module B) —
    # the audit logs must not present a nominal length as a verified
    # distance.
    lines = [
        f"Test {test_id} — {axis_label}={difficulty}",
        *([f"Verified optimal distance d*={verified_d_star}"]
          if verified_d_star is not None else []),
        f"Instance: {instance_id}",
        f"Model: {model}",
        f"Parse mode: {parse_mode or 'n/a'}",
        "=" * 70,
        "",
        "--- Initial cube state (JSON, for exact replay) ---",
        json.dumps(initial_cube_json),
        "",
        "--- Model response ---",
        raw_response or "(empty)",
        "",
        "--- Parsed moves ---",
        moves_str or "(none)",
        "",
        "=" * 70,
        f"Success: {success}",
    ]
    if sticker_match is not None:
        lines.append(f"Sticker-match fraction (post-solution): {sticker_match:.3f}")
    lines.append(f"Time: {elapsed:.1f}s")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
#  Trial (scaffold entry point)
# ---------------------------------------------------------------------------

async def solve(
    llm: RobustLLM,
    difficulty: int,
    test_id: int,
    verbose: bool = True,
    instance: Optional[dict] = None,
) -> dict:
    """One single-shot trial on a manifest instance.

    difficulty is the verified optimal distance d* and must match the
    instance; the returned dict maps onto scaffold.metrics.TrialMetrics.
    """
    if instance is None:
        raise ValueError(
            "The rubiks benchmark is manifest-only: every trial needs a "
            "pre-generated, hash-verified instance")
    inst_key = instance.get("nominal_length", instance.get("d_star"))
    if inst_key != difficulty:
        raise ValueError(
            f"Instance {instance.get('id')} has difficulty {inst_key} "
            f"but the run difficulty is {difficulty}")

    is_nominal = "nominal_length" in instance
    axis_label = ("nominal scramble length" if is_nominal
                  else "verified optimal distance d*")
    log_kw = dict(
        axis_label=axis_label,
        verified_d_star=(instance.get("verified_d_star")
                         if is_nominal else None))

    t0 = time.time()
    cube, cube_json = _cube_from_instance(instance)
    instance_id = instance.get("id")

    if verbose:
        print(f"\n[trial] {axis_label}={difficulty} ({instance_id}) | "
              f"model={llm.model} — calling LLM once.")

    response = await llm.call(
        [{"role": "user", "content": _build_prompt(cube_json)}],
        system=_SYSTEM_PROMPT,
    )
    elapsed = time.time() - t0

    base = {
        "num_moves": 0,
        "tokens_used": response.tokens_used,
        "size": "3x3x3",
        # complexity ALWAYS carries the verified exact distance; for
        # nominal instances that is verified_d_star, never the nominal
        # length (d*/complexity must not misrepresent nominal
        # length as verified difficulty).
        "complexity": instance.get("verified_d_star",
                                   instance.get("d_star")),
        "instance_id": instance_id,
        **response.audit_fields(),
    }

    # ---- API-level failure (empty content, transport error) ----
    if not response.success:
        return {
            **base,
            "success": False,
            "error": response.error or "LLM call failed",
            "detailed_log": _make_log(
                test_id, difficulty, instance_id, llm.model,
                response.content, None, False, elapsed, cube_json,
                **log_kw),
        }

    # ---- Parse moves (scored parse is STRICT last-line, HTM only) ----
    moves_str, parse_mode = _parse_moves(response.content or "")
    base["parse_mode"] = parse_mode
    if verbose:
        print(f"[trial] Response received. Parsed moves ({parse_mode}): "
              f"{moves_str!r}")

    def _apply(seq: str):
        """Apply seq to a fresh copy; return (solved, error, cube)."""
        c = copy.deepcopy(cube)
        try:
            c.rotate(seq)
            if c.is_done():
                return True, None, c
            return False, "Cube not solved", c
        except Exception as exc:
            return False, f"Invalid move sequence: {exc}", c

    if parse_mode != "strict":
        # Interface non-compliance is a valid failure. For diagnostics we
        # additionally record whether the lenient parse WOULD have solved
        # the cube (never affects the score).
        lenient_rescue = None
        if parse_mode == "lenient" and moves_str:
            lenient_rescue = _apply(moves_str)[0]
        return {
            **base,
            "success": False,
            "error": ("No legal HTM move sequence on the final line"
                      if parse_mode == "lenient" else
                      "No valid Singmaster moves found in response"),
            "lenient_rescue": lenient_rescue,
            "detailed_log": _make_log(
                test_id, difficulty, instance_id, llm.model,
                response.content, moves_str, False, elapsed, cube_json,
                parse_mode=parse_mode, **log_kw),
        }

    # ---- Evaluate on an independent copy of the instance state ----
    success, error_msg, eval_cube = _apply(moves_str)
    sticker_match = _sticker_match_fraction(eval_cube)

    if verbose:
        status = "✓ SOLVED" if success else f"✗ failed ({error_msg})"
        print(f"[trial] {status} — {len(moves_str.split())} moves, "
              f"sticker-match={sticker_match:.2f}, {elapsed:.1f}s")

    return {
        **base,
        "success": success,
        "num_moves": len(moves_str.split()) if success else 0,
        "error": error_msg,
        "similarity": round(sticker_match, 4),
        "detailed_log": _make_log(
            test_id, difficulty, instance_id, llm.model,
            response.content, moves_str, success, elapsed, cube_json,
            sticker_match=sticker_match, parse_mode=parse_mode,
            **log_kw),
    }
