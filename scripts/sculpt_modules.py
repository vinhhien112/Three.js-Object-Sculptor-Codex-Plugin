#!/usr/bin/env python3
"""Compatibility facade for composable ObjectSculptSpec v4 modules."""

from __future__ import annotations

import sys

from sculpt_manifest import (
    SculptDocument,
    add_module,
    is_module_manifest,
    load_document,
    load_modules,
    make_manifest,
    make_module,
    read_raw_spec,
    resolve_manifest,
    save_document,
)
from sculpt_module_cli import main
from sculpt_module_contract import MANIFEST_SCHEMA_VERSION, MODULE_SCHEMA_VERSION, manifest_errors
from sculpt_module_review import preflight_module_review, review_module
from sculpt_module_state import accept_module, check_module, module_context, module_hash, module_status


__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "MODULE_SCHEMA_VERSION",
    "SculptDocument",
    "accept_module",
    "add_module",
    "check_module",
    "is_module_manifest",
    "load_document",
    "load_modules",
    "main",
    "make_manifest",
    "make_module",
    "manifest_errors",
    "module_hash",
    "module_context",
    "module_status",
    "preflight_module_review",
    "read_raw_spec",
    "review_module",
    "resolve_manifest",
    "save_document",
]


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
