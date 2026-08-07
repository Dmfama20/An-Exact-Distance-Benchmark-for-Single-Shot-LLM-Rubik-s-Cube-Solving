"""Rubik's Cube benchmark plugin (verified optimal distance d*)."""

from scaffold.registry import Benchmark

from .benchmark import solve


def select_instances(manifest_data: dict, difficulty: int):
    """Instances of one verified-depth cell, in stable (paired) order."""
    return sorted(
        (inst for inst in manifest_data["instances"]
         if inst.get("nominal_length", inst.get("d_star")) == difficulty),
        key=lambda inst: inst["id"])


BENCHMARK = Benchmark(
    name="rubiks",
    solve=solve,
    select_instances=select_instances,
    difficulty_help=("verified optimal solution distance d* (HTM); "
                     "cells present in the manifest, e.g. 1..10"),
)
