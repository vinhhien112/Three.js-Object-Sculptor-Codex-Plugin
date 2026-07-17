#!/usr/bin/env python3
"""Migrate an ObjectSculptSpec explicitly without inventing missing geometry."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

from sculpt_contract import (
    CURRENT_SCHEMA_VERSION,
    parse_schema_version,
    sync_pipeline,
    write_spec_atomic,
)
from sculpt_modules import is_module_manifest, read_raw_spec


TARGET_SCHEMA = CURRENT_SCHEMA_VERSION
SUPPORTED_SOURCE_VERSIONS = {(2, 0, 0), (3, 0, 0), (3, 1, 0)}


def migrate_spec(spec: dict[str, Any], target: str = TARGET_SCHEMA) -> tuple[dict[str, Any], dict[str, Any]]:
    if target != TARGET_SCHEMA:
        raise ValueError(f"only migration target {TARGET_SCHEMA!r} is supported")
    source = str(spec.get("schemaVersion") or "2.0")
    source_version = parse_schema_version(source)
    if source_version > parse_schema_version(target):
        raise ValueError(f"cannot migrate newer schema {source!r} down to {target!r}")
    if source_version not in SUPPORTED_SOURCE_VERSIONS:
        raise ValueError(f"unsupported source schemaVersion {source!r}")
    if source == target:
        return copy.deepcopy(spec), {
            "changed": False,
            "fromVersion": source,
            "toVersion": target,
            "componentsUpdated": 0,
            "reviewHistoryPreserved": True,
        }

    migrated = copy.deepcopy(spec)
    updated = 0
    components = migrated.get("componentTree")
    if isinstance(components, list):
        for component in components:
            if not isinstance(component, dict):
                continue
            if "componentType" not in component:
                component["componentType"] = "part"
                updated += 1
            if component.get("componentType") == "part":
                descriptor = component.get("geometryDescriptor")
                if descriptor is None:
                    descriptor = {}
                    component["geometryDescriptor"] = descriptor
                if isinstance(descriptor, dict):
                    descriptor.setdefault("parameters", {})

    migrated["schemaVersion"] = target
    revision = migrated.get("specRevision", 0)
    migrated["specRevision"] = revision + 1 if isinstance(revision, int) else 1
    sync_pipeline(migrated)
    return migrated, {
        "changed": True,
        "fromVersion": source,
        "toVersion": target,
        "componentsUpdated": updated,
        "reviewHistoryPreserved": True,
        "reviewPolicy": (
            "Review history is retained for audit. Relevant reviews remain stale until the migrated "
            "geometry is validated again; hashes are never rewritten to manufacture a pass."
        ),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--to", default=TARGET_SCHEMA, choices=(TARGET_SCHEMA,))
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument("--in-place", action="store_true")
    destination.add_argument("--out", type=Path)
    parser.add_argument("--report-json", action="store_true")
    args = parser.parse_args(argv)

    source = args.spec.expanduser().resolve()
    raw_spec = read_raw_spec(source)
    if is_module_manifest(raw_spec):
        raise ValueError(
            "schema 4.0 is already the compositional manifest; use `sculpt module resolve` "
            "to export a schema 3.1 compatibility spec"
        )
    migrated, report = migrate_spec(raw_spec, args.to)
    output = source if args.in_place else (args.out.expanduser().resolve() if args.out else None)
    if output is not None:
        write_spec_atomic(output, migrated)
        report["output"] = str(output)
    if args.report_json or output is None:
        print(json.dumps({"report": report, "spec": migrated if output is None else None}, indent=2, ensure_ascii=False))
    else:
        print(output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
