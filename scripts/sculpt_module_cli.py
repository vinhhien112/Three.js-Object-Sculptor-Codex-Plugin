"""CLI adapter for composable sculpt manifests."""

from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import make_visual_comparison_sheet
from generate_threejs_factory import write_generated_spec
from sculpt_contract import file_sha256, write_spec_atomic
from sculpt_manifest import add_module, read_object, resolve_manifest
from sculpt_module_contract import (
    GATE_TYPES,
    MODULE_BUILD_RECEIPT_ARTIFACT_TYPE,
    MODULE_BUILD_RECEIPT_VERSION,
    module_build_receipt_path,
)
from sculpt_module_review import preflight_module_review, review_module
from sculpt_module_state import accept_module, check_module, module_context, module_status


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _module_build(args: argparse.Namespace) -> int:
    manifest = args.manifest.expanduser().resolve()
    build_receipt = module_build_receipt_path(manifest, args.module_id)
    build_receipt.unlink(missing_ok=True)
    resolved_out = (
        args.resolved_out.expanduser().resolve()
        if args.resolved_out
        else manifest.parent / ".sculpt-preview" / f"{args.module_id}.json"
    )
    generated_out = (
        args.out.expanduser().resolve()
        if args.out
        else manifest.parent / "src" / "generated" / f"{args.module_id}.generated.ts"
    )
    payload: dict = {
        "artifactType": "threejs-sculpt-module-build",
        "version": 1,
        "moduleId": args.module_id,
        "ok": False,
        "stages": {},
        "validationFunctions": [
            "check_module(strict_quality=True, prepare_generation=True)",
            "resolve_manifest + validate_spec(pass) once",
            "hash-bound validation reuse",
            "assert_pass_unlocked",
            "generate",
        ],
    }
    checked = check_module(
        manifest,
        args.module_id,
        strict_quality=True,
        prepare_generation=True,
        generation_pass=args.pass_id,
    )
    resolved_spec = checked.pop("_resolvedSpec", None)
    validation_proof = checked.pop("_validationProof", None)
    payload["stages"]["check"] = checked
    if not checked["ok"]:
        _print_json(payload)
        return 1
    try:
        if not isinstance(resolved_spec, dict) or validation_proof is None:
            raise ValueError("module check did not return its hash-bound generation result")
        write_spec_atomic(resolved_out, resolved_spec)
    except (OSError, ValueError) as exc:
        payload["stages"]["resolve"] = {"ok": False, "error": str(exc)}
        _print_json(payload)
        return 1
    payload["stages"]["resolve"] = {
        "ok": True,
        "output": str(resolved_out),
        "sha256": file_sha256(resolved_out),
    }
    try:
        generated = write_generated_spec(
            resolved_spec,
            generated_out,
            pass_id=args.pass_id,
            wrapper_out=args.wrapper_out,
            force=True,
            _validation_proof=validation_proof,
        )
    except (OSError, ValueError) as exc:
        payload["stages"]["generate"] = {"ok": False, "error": str(exc)}
        _print_json(payload)
        return 1
    payload["stages"]["generate"] = {
        "ok": True,
        **generated,
        "sha256": file_sha256(generated_out),
    }
    receipt = {
        "artifactType": MODULE_BUILD_RECEIPT_ARTIFACT_TYPE,
        "version": MODULE_BUILD_RECEIPT_VERSION,
        "moduleId": args.module_id,
        "moduleHash": checked.get("moduleHash"),
        "manifestPath": str(manifest),
        "resolvedSpec": str(resolved_out),
        "resolvedSpecSha256": file_sha256(resolved_out),
        "generatedOutput": str(generated_out),
        "generatedOutputSha256": file_sha256(generated_out),
        "factoryId": generated.get("factoryId"),
        "factoryExport": generated.get("factoryExport"),
        "specSha256": generated.get("specSha256"),
        "passId": generated.get("passId"),
        "expectedComponentIds": generated.get("expectedComponentIds", []),
        "expectedMeshComponentIds": generated.get("expectedMeshComponentIds", []),
        "expectedPrimitives": generated.get("expectedPrimitives", {}),
    }
    write_spec_atomic(build_receipt, receipt)
    payload["stages"]["attest"] = {
        "ok": True,
        "buildReceipt": str(build_receipt),
        "sha256": file_sha256(build_receipt),
        "factoryId": generated.get("factoryId"),
        "factoryExport": generated.get("factoryExport"),
    }
    payload["ok"] = True
    _print_json(payload)
    return 0


def _module_evaluate(args: argparse.Namespace) -> int:
    manifest = args.manifest.expanduser().resolve()
    comparison_out = (
        args.out.expanduser().resolve()
        if args.out
        else manifest.parent / "review" / f"{args.module_id}-comparison.png"
    )
    evidence_out = (
        args.manifest_out.expanduser().resolve()
        if args.manifest_out
        else manifest.parent / "review" / f"{args.module_id}-evidence.json"
    )
    diagnostics_dir = (
        args.diagnostics_dir.expanduser().resolve()
        if args.diagnostics_dir
        else manifest.parent / "review" / "diagnostics" / args.module_id
    )
    compare_args = [
        "--pairs-json",
        args.pairs_json,
        "--out",
        str(comparison_out),
        "--manifest-out",
        str(evidence_out),
        "--sculpt-manifest",
        str(manifest),
        "--module-id",
        args.module_id,
        "--runtime-receipt",
        str(args.runtime_receipt.expanduser().resolve()),
        "--diagnostics-dir",
        str(diagnostics_dir),
        "--panel-width",
        str(args.panel_width),
        "--panel-height",
        str(args.panel_height),
        "--gutter",
        str(args.gutter),
    ]
    captured_out = io.StringIO()
    captured_err = io.StringIO()
    with redirect_stdout(captured_out), redirect_stderr(captured_err):
        compare_code = make_visual_comparison_sheet.main(compare_args)
    payload: dict = {
        "artifactType": "threejs-sculpt-module-evaluation",
        "version": 1,
        "moduleId": args.module_id,
        "ok": False,
        "stages": {
            "compare": {
                "ok": compare_code == 0,
                "comparisonImage": str(comparison_out),
                "evidenceManifest": str(evidence_out),
            }
        },
        "validationFunctions": [
            "create_sheet_pairs",
            "visual_evidence_integrity_failures",
            "visual_evidence_authority_failures",
            "diagnostic_veto_failures",
            "refinement_preflight_failures",
        ],
    }
    if compare_code != 0:
        payload["stages"]["compare"]["error"] = (
            captured_err.getvalue().strip() or captured_out.getvalue().strip()
        )
        _print_json(payload)
        return 1
    evidence = read_object(evidence_out, "visual evidence manifest")
    payload["stages"]["compare"].update(
        {
            "comparisonSha256": evidence.get("comparisonSha256"),
            "manifestSha256": evidence.get("manifestSha256"),
        }
    )
    preflight = preflight_module_review(manifest, args.module_id, evidence_out)
    payload["stages"]["preflight"] = preflight
    payload["ok"] = preflight.get("ok") is True
    _print_json(payload)
    return 0 if payload["ok"] else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add", help="Create one module spec when that block is ready to author")
    add.add_argument("manifest", type=Path)
    add.add_argument("module_id")
    add.add_argument("--role", required=True)
    add.add_argument("--risk-score", type=float, required=True)
    add.add_argument("--depends-on", action="append", default=[])
    add.add_argument(
        "--covers",
        action="append",
        default=[],
        help="Global qualityContract.featureGroups id owned by this module (repeatable)",
    )
    add.add_argument("--gate-type", choices=sorted(GATE_TYPES), default="visual")
    add.add_argument("--template", choices=("empty", "foundation"), default="empty")

    status = subparsers.add_parser("status", help="Choose the highest-risk ready module and show cache hits")
    status.add_argument("manifest", type=Path)

    context = subparsers.add_parser(
        "context",
        help="Emit one hash-aware work packet listing only files changed since the last context call",
    )
    context.add_argument("manifest", type=Path)
    context.add_argument("--module-id")

    check = subparsers.add_parser("check", help="Validate one module plus its dependency interfaces")
    check.add_argument("manifest", type=Path)
    check.add_argument("module_id")
    check.add_argument("--strict-quality", action="store_true")

    accept = subparsers.add_parser(
        "accept", help="Accept one structural module; visual modules require independent review"
    )
    accept.add_argument("manifest", type=Path)
    accept.add_argument("module_id")
    accept.add_argument("--notes")

    review = subparsers.add_parser(
        "review",
        help="Record one independent visual verdict; refine attempts are retained and continue may accept",
    )
    review.add_argument("manifest", type=Path)
    review.add_argument("module_id")
    review.add_argument("--verdict-json", type=Path, required=True)
    review.add_argument("--evidence-manifest", type=Path, required=True)

    preflight = subparsers.add_parser(
        "preflight",
        help="Run deterministic image/hash/veto checks before spawning an independent reviewer",
    )
    preflight.add_argument("manifest", type=Path)
    preflight.add_argument("module_id")
    preflight.add_argument("--evidence-manifest", type=Path, required=True)

    resolve = subparsers.add_parser(
        "resolve",
        help="Resolve accepted modules or the current highest-risk module preview to schema 3.1",
    )
    resolve.add_argument("manifest", type=Path)
    resolve.add_argument("--module-id")
    resolve.add_argument("--out", type=Path, required=True)

    build = subparsers.add_parser(
        "build",
        help="Strict-check, resolve, validate, and generate one module in a single fail-fast call",
    )
    build.add_argument("manifest", type=Path)
    build.add_argument("module_id")
    build.add_argument("--resolved-out", type=Path)
    build.add_argument("--out", type=Path)
    build.add_argument("--pass-id")
    build.add_argument("--wrapper-out", type=Path)

    evaluate = subparsers.add_parser(
        "evaluate",
        help="Create comparison evidence and run deterministic preflight in one call",
    )
    evaluate.add_argument("manifest", type=Path)
    evaluate.add_argument("module_id")
    evaluate.add_argument("--pairs-json", required=True)
    evaluate.add_argument(
        "--runtime-receipt",
        type=Path,
        required=True,
        help="JSON receipt returned by window.__THREEJS_SCULPT_CAPTURE_RUNTIME__ for this render",
    )
    evaluate.add_argument("--out", type=Path)
    evaluate.add_argument("--manifest-out", type=Path)
    evaluate.add_argument("--diagnostics-dir", type=Path)
    evaluate.add_argument("--panel-width", type=int, default=720)
    evaluate.add_argument("--panel-height", type=int, default=720)
    evaluate.add_argument("--gutter", type=int, default=24)

    args = parser.parse_args(argv)
    if args.command == "add":
        print(
            add_module(
                args.manifest,
                args.module_id,
                args.role,
                args.risk_score,
                args.depends_on,
                args.gate_type,
                args.template,
                args.covers,
            )
        )
        return 0
    if args.command == "status":
        print(json.dumps(module_status(args.manifest), indent=2, ensure_ascii=False))
        return 0
    if args.command == "context":
        _print_json(module_context(args.manifest, args.module_id))
        return 0
    if args.command == "check":
        result = check_module(args.manifest, args.module_id, args.strict_quality)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["ok"] else 1
    if args.command == "accept":
        result = accept_module(
            args.manifest,
            args.module_id,
            None,
            None,
            None,
            None,
            args.notes,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    if args.command == "review":
        result = review_module(
            args.manifest,
            args.module_id,
            args.verdict_json,
            args.evidence_manifest,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("reviewAccepted") is True else 1
    if args.command == "preflight":
        result = preflight_module_review(
            args.manifest,
            args.module_id,
            args.evidence_manifest,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") is True else 1
    if args.command == "build":
        return _module_build(args)
    if args.command == "evaluate":
        return _module_evaluate(args)
    status_payload = module_status(args.manifest)
    if args.module_id is None and not status_payload["assemblyReady"]:
        raise ValueError(
            "final assembly is locked until every required module has a current acceptance cache"
        )
    if (
        args.module_id is not None
        and args.module_id not in status_payload.get("acceptedModules", [])
        and status_payload.get("currentModule") != args.module_id
    ):
        raise ValueError(
            "only the current highest-risk ready module or an accepted module may be resolved; "
            f"current={status_payload.get('currentModule')!r}"
        )
    resolved_spec = resolve_manifest(
        args.manifest,
        selected=[args.module_id] if args.module_id else None,
    )
    output = args.out.expanduser().resolve()
    write_spec_atomic(output, resolved_spec)
    print(output)
    return 0
