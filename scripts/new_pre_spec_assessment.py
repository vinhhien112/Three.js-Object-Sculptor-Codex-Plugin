#!/usr/bin/env python3
"""Legacy compatibility wrapper for the pre-spec section now created by `sculpt init`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from new_sculpt_spec import make_spec


def make_payload(
    target_name: str,
    image: str | None,
    complexity: str,
    intended_use: str = "browser-prop",
    quality_profile: str = "balanced",
) -> dict:
    spec = make_spec(
        target_name,
        image,
        complexity=complexity,
        intended_use=intended_use,
        quality_profile=quality_profile,
    )
    return {
        "targetName": target_name,
        "sourceImage": spec["sourceImage"],
        "preSpecAssessment": spec["preSpecAssessment"],
        "surfaceTopologyPlan": spec["surfaceTopologyPlan"],
        "qualityContract": spec["qualityContract"],
        "buildPasses": spec["buildPasses"],
        "authoringInstruction": (
            "This compatibility output is optional. Prefer `python3 scripts/sculpt.py init`, "
            "which keeps pre-spec and the final spec in one file."
        ),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target_name")
    parser.add_argument("--image")
    parser.add_argument(
        "--complexity",
        choices=("simple", "moderate", "complex", "ultra", "ultra-complex"),
        default="moderate",
    )
    parser.add_argument(
        "--intended-use",
        choices=("static-render", "browser-prop", "game-prop", "animated", "playable", "destructible"),
        default="browser-prop",
    )
    parser.add_argument(
        "--quality-profile",
        choices=("balanced", "reference-fidelity"),
        default="balanced",
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    payload = make_payload(
        args.target_name,
        args.image,
        "ultra" if args.complexity == "ultra-complex" else args.complexity,
        args.intended_use,
        args.quality_profile,
    )
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if not args.out:
        print(text, end="")
        return 0
    output = args.out.expanduser().resolve()
    if output.exists() and not args.force:
        parser.error(f"{output} already exists; use --force to overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
