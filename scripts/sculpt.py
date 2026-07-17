#!/usr/bin/env python3
"""Single entry point for the adaptive Three.js object-sculpt workflow."""

from __future__ import annotations

import sys
from collections.abc import Callable

import append_sculpt_review
import extract_reference_pbr
import generate_threejs_factory
import make_visual_comparison_sheet
import migrate_sculpt_spec
import new_sculpt_spec
import probe_reference_image
import sculpt_modules
import sculpt_pass_orchestrator
import sculpt_view_hypotheses
import validate_sculpt_spec


Command = tuple[str, Callable[[list[str]], int]]


COMMANDS: dict[str, Command] = {
    "init": ("Create one spec with integrated pre-spec and an adaptive pass plan.", new_sculpt_spec.main),
    "validate": ("Validate the spec, optionally for one pass.", validate_sculpt_spec.main),
    "status": (
        "Show the authoritative current pass and required evidence.",
        lambda argv: sculpt_pass_orchestrator.main(["status", *argv]),
    ),
    "sync": (
        "Refresh the derived pipeline status stored in the spec.",
        lambda argv: sculpt_pass_orchestrator.main(["sync", *argv]),
    ),
    "check": (
        "Check whether a pass is current and ready to generate.",
        lambda argv: sculpt_pass_orchestrator.main(["check", *argv]),
    ),
    "generate": ("Generate the current pass into a user-safe *.generated.ts file.", generate_threejs_factory.main),
    "compare": ("Create a no-crop single- or multi-view comparison sheet.", make_visual_comparison_sheet.main),
    "review": ("Record one visual, runtime, or metrics review for the current pass.", append_sculpt_review.main),
    "probe": ("Inspect basic reference-image properties.", probe_reference_image.main),
    "pbr": ("Extract inferred PBR maps from a confirmed material crop.", extract_reference_pbr.main),
    "views": (
        "Register or inspect cached ImageGen unseen-view hypotheses.",
        sculpt_view_hypotheses.main,
    ),
    "migrate": ("Migrate a spec explicitly without rewriting review evidence.", migrate_sculpt_spec.main),
    "module": (
        "Add, validate, independently review, cache, or resolve one composable v4 module.",
        sculpt_modules.main,
    ),
}


def print_help() -> None:
    print("Usage: python3 scripts/sculpt.py <command> [options]\n")
    print("Commands:")
    for name, (description, _) in COMMANDS.items():
        print(f"  {name:<10} {description}")
    print("\nRun a command with --help for its detailed options.")


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print_help()
        return 0
    command = argv[0]
    if command not in COMMANDS:
        print(f"error: unknown command {command!r}\n", file=sys.stderr)
        print_help()
        return 2
    try:
        return COMMANDS[command][1](argv[1:])
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
