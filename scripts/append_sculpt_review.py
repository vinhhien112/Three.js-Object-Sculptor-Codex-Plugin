#!/usr/bin/env python3
"""Append one authoritative review for the current sculpt pass."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sculpt_contract import (
    REFINEMENT_ACTIONS,
    STRATEGY_RESET_ACTION,
    correction_batch_from_plan,
    correction_batch_from_verdict,
    effective_pass_config,
    evidence_type,
    file_sha256,
    parse_json,
    pipeline_status,
    refinement_budget,
    review_failures,
    review_spec_hash,
    sculpt_representation_signature,
    sync_pipeline,
    visual_acceptance_threshold,
    visual_evidence_integrity_failures,
    visual_evidence_manifest_sha256,
    visual_preflight_failures,
    write_spec_atomic,
)
from sculpt_module_review import (
    PASS_REVIEW_ARTIFACT_TYPE,
    PASS_REVIEW_VERSION,
    _issue_lineage_keys,
    review_contract_failures,
)
from sculpt_modules import load_document, module_status, save_document
from sculpt_pass_orchestrator import pass_specific_gaps


VALID_ACTIONS = {
    "continue",
    *REFINEMENT_ACTIONS,
    STRATEGY_RESET_ACTION,
    "request-input",
    "stop",
}
VALID_ROOT_CAUSES = {
    "camera-framing",
    "spec",
    "geometry",
    "material",
    "lighting",
    "evidence",
    "performance",
    "mixed",
}
VALID_CORRECTION_ACTIONS = {"set", "scale", "translate", "rotate", "replace", "inspect"}
PASS_PREFLIGHT_ARTIFACT_TYPE = "threejs-sculpt-pass-preflight"
PASS_PREFLIGHT_VERSION = 1


def split_items(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(";") if item.strip()]


def load_json_argument(value: str | None, label: str, default: Any) -> Any:
    if not value:
        return default
    stripped = value.lstrip()
    if stripped.startswith(("{", "[")):
        text = value
    else:
        candidate = Path(value).expanduser()
        try:
            text = candidate.read_text(encoding="utf-8") if candidate.is_file() else value
        except OSError:
            text = value
    try:
        return parse_json(text, label)
    except ValueError as exc:
        raise ValueError(f"{label} must be valid inline JSON or a JSON file path: {exc}") from exc


def score(value: float | None, label: str, default: float = 0.0) -> float:
    if value is None:
        return default
    if not math.isfinite(float(value)) or not 0 <= float(value) <= 1:
        raise ValueError(f"{label} must be from 0 to 1")
    return float(value)


def is_virtual_path(value: str) -> bool:
    return "://" in value or value.startswith(("data:", "blob:"))


def validate_local_path(value: Any, label: str, allow_missing: bool) -> None:
    if allow_missing or not isinstance(value, str) or not value or is_virtual_path(value):
        return
    if not Path(value).expanduser().exists():
        raise FileNotFoundError(f"{label} does not exist: {value}")


def validate_views(views: list[dict[str, Any]], allow_missing: bool) -> None:
    seen: set[str] = set()
    for index, view in enumerate(views):
        view_id = str(view.get("viewId") or "primary")
        if view_id in seen:
            raise ValueError(f"duplicate evidence viewId {view_id!r}")
        seen.add(view_id)
        view["viewId"] = view_id
        for field in ("referenceImage", "renderScreenshot", "comparisonImage"):
            validate_local_path(view.get(field), f"evidence view {index}.{field}", allow_missing)


def _pass_preflight_path(spec_path: Path, pass_id: str) -> Path:
    safe_pass_id = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in pass_id
    ).strip("-") or "pass"
    return (
        spec_path.parent
        / ".sculpt-cache"
        / spec_path.stem
        / f"pass-preflight-{safe_pass_id}.json"
    )


def _existing_local_file(value: Any, root: Path | None = None) -> Path | None:
    if not isinstance(value, str) or not value.strip() or is_virtual_path(value):
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute() and root is not None:
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
        return resolved if resolved.is_file() else None
    except OSError:
        return None


def _evidence_file_snapshot(evidence: dict[str, Any]) -> dict[str, str]:
    values: list[Any] = [evidence.get("comparisonImage")]
    for view in evidence.get("views", []):
        if not isinstance(view, dict):
            continue
        values.extend(
            view.get(field)
            for field in ("referenceImage", "renderScreenshot", "comparisonImage")
        )
    snapshot: dict[str, str] = {}
    for value in values:
        path = _existing_local_file(value)
        if path is not None:
            snapshot[str(path)] = file_sha256(path)
    return dict(sorted(snapshot.items()))


def _evidence_render_hashes(evidence: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(view.get("renderSha256"))
            for view in evidence.get("views", [])
            if isinstance(view, dict) and isinstance(view.get("renderSha256"), str)
        }
    )


def _latest_pending_pass_refinement(
    spec: dict[str, Any],
    pass_id: str,
) -> dict[str, Any] | None:
    history = spec.get("reviewHistory", [])
    if not isinstance(history, list):
        return None
    for entry in reversed(history):
        if not isinstance(entry, dict) or entry.get("passId") != pass_id:
            continue
        if entry.get("action") in REFINEMENT_ACTIONS:
            return entry
        return None
    return None


def _latest_pending_pass_strategy_reset(
    spec: dict[str, Any],
    pass_id: str,
) -> dict[str, Any] | None:
    history = spec.get("reviewHistory", [])
    if not isinstance(history, list):
        return None
    for entry in reversed(history):
        if not isinstance(entry, dict) or entry.get("passId") != pass_id:
            continue
        return entry if entry.get("action") == STRATEGY_RESET_ACTION else None
    return None


def _pass_refinement_progress_failures(
    spec: dict[str, Any],
    pass_id: str,
    verdict: dict[str, Any],
) -> list[str]:
    previous = _latest_pending_pass_refinement(spec, pass_id)
    if previous is None:
        return []
    previous_overall = previous.get("aiVisionScore", previous.get("estimatedFidelity"))
    current_overall = verdict.get("overallScore")
    improved = (
        isinstance(previous_overall, (int, float))
        and not isinstance(previous_overall, bool)
        and isinstance(current_overall, (int, float))
        and not isinstance(current_overall, bool)
        and float(current_overall) >= float(previous_overall) + 0.01
    )
    previous_layers = (
        previous.get("layerScores")
        if isinstance(previous.get("layerScores"), dict)
        else {}
    )
    current_layers = (
        verdict.get("layerScores")
        if isinstance(verdict.get("layerScores"), dict)
        else {}
    )
    if not improved:
        improved = any(
            isinstance(previous_layers.get(layer), (int, float))
            and not isinstance(previous_layers.get(layer), bool)
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and float(value) >= float(previous_layers[layer]) + 0.01
            for layer, value in current_layers.items()
        )
    failures: list[str] = []
    if not improved:
        failures.append("refinement did not improve any independently reviewed quality score")
    previous_blockers = {
        issue.get("rootCauseKey")
        for issue in previous.get("reviewIssues", [])
        if isinstance(issue, dict)
        and issue.get("status") == "open"
        and issue.get("severity") in {"critical", "major"}
        and isinstance(issue.get("rootCauseKey"), str)
    }
    unresolved = previous_blockers - {
        item
        for item in verdict.get("resolvedRootCauseKeys", [])
        if isinstance(item, str)
    }
    if unresolved:
        failures.append(
            "previous blocking root causes were not explicitly resolved: "
            + ", ".join(sorted(unresolved))
        )
    reopened = {
        issue.get("rootCauseKey")
        for issue in verdict.get("issues", [])
        if isinstance(issue, dict)
        and issue.get("status") == "open"
        and issue.get("rootCauseKey") in set(verdict.get("resolvedRootCauseKeys", []))
    }
    if reopened:
        failures.append(
            "a root cause cannot be resolved and reopened under a new issue id: "
            + ", ".join(sorted(str(item) for item in reopened))
        )
    current_blockers = {
        issue.get("rootCauseKey")
        for issue in verdict.get("issues", [])
        if isinstance(issue, dict)
        and issue.get("status") == "open"
        and issue.get("severity") in {"critical", "major"}
        and isinstance(issue.get("rootCauseKey"), str)
    }
    new_blockers = current_blockers - previous_blockers
    if new_blockers:
        failures.append(
            "a new blocking root cause cannot be introduced within the same strategy; "
            "record strategy-reset before changing defect identity: "
            + ", ".join(sorted(new_blockers))
        )
    previous_lineages = set(
        item
        for item in previous.get("issueLineageKeys", [])
        if isinstance(item, str)
    ) or _issue_lineage_keys(
        previous.get("reviewIssues", []),
        previous.get("reviewCorrections", []),
    )
    current_lineages = _issue_lineage_keys(
        verdict.get("issues", []),
        verdict.get("corrections", []),
    )
    if previous_lineages & current_lineages:
        failures.append(
            "a blocking defect remains open under the same canonical issue lineage"
        )
    return failures


def _pending_pass_batch_failures(
    spec: dict[str, Any],
    pass_id: str,
    evidence: dict[str, Any],
) -> list[str]:
    previous = _latest_pending_pass_refinement(spec, pass_id)
    if previous is None:
        strategy_reset = _latest_pending_pass_strategy_reset(spec, pass_id)
        if strategy_reset is None:
            return []
        failures: list[str] = []
        reset_signature = strategy_reset.get("representationSignature")
        if (
            not isinstance(reset_signature, str)
            or reset_signature == sculpt_representation_signature(spec)
        ):
            failures.append(
                "strategy-reset requires a different topology/geometry representation before rendering"
            )
        previous_evidence = strategy_reset.get("evidence")
        if (
            isinstance(previous_evidence, dict)
            and previous_evidence.get("comparisonSha256") == evidence.get("comparisonSha256")
        ):
            failures.append("strategy-reset reused the previous comparison image")
        previous_views = (
            previous_evidence.get("views", [])
            if isinstance(previous_evidence, dict)
            else []
        )
        previous_render_hashes = {
            view.get("renderSha256")
            for view in previous_views
            if isinstance(view, dict)
            and isinstance(view.get("renderSha256"), str)
        }
        current_render_hashes = {
            view.get("renderSha256")
            for view in evidence.get("views", [])
            if isinstance(view, dict) and isinstance(view.get("renderSha256"), str)
        }
        if previous_render_hashes and previous_render_hashes == current_render_hashes:
            failures.append("strategy-reset produced no new render artifact")
        return failures
    batch = previous.get("correctionBatch")
    if not isinstance(batch, dict):
        return []
    failures: list[str] = []
    scopes = set(
        item for item in batch.get("scopes", []) if isinstance(item, str)
    )
    if "spec" in scopes and previous.get("specHash") == review_spec_hash(spec, pass_id):
        failures.append(
            "pending correction batch requires a spec change before rendering"
        )
    previous_evidence = previous.get("evidence")
    if isinstance(previous_evidence, dict):
        if previous_evidence.get("comparisonSha256") == evidence.get("comparisonSha256"):
            failures.append("pending correction batch reused the previous comparison image")
        if _evidence_render_hashes(previous_evidence) == _evidence_render_hashes(evidence):
            failures.append("pending correction batch produced no new render artifact")
    return list(dict.fromkeys(failures))


def _pass_preflight_binding(
    spec_path: Path,
    spec: dict[str, Any],
    pass_id: str,
    evidence: dict[str, Any],
    evidence_argument: str | None,
) -> dict[str, Any]:
    evidence_input = _existing_local_file(evidence_argument)
    source = _existing_local_file(spec.get("sourceImage"), spec_path.parent)
    policy = spec.get("viewHypothesisPolicy")
    policy = policy if isinstance(policy, dict) else {}
    hypothesis_manifest = _existing_local_file(policy.get("manifestPath"), spec_path.parent)
    return {
        "passId": pass_id,
        "reviewSpecHash": review_spec_hash(spec, pass_id),
        "evidenceManifestSha256": evidence.get("manifestSha256"),
        "evidencePayloadSha256": visual_evidence_manifest_sha256(evidence),
        "comparisonSha256": evidence.get("comparisonSha256"),
        "evidenceInputPath": str(evidence_input) if evidence_input is not None else "",
        "evidenceInputSha256": file_sha256(evidence_input) if evidence_input is not None else "",
        "evidenceFiles": _evidence_file_snapshot(evidence),
        "sourceImagePath": str(source) if source is not None else "",
        "sourceImageSha256": file_sha256(source) if source is not None else "",
        "hypothesisManifestPath": (
            str(hypothesis_manifest) if hypothesis_manifest is not None else ""
        ),
        "hypothesisManifestSha256": (
            file_sha256(hypothesis_manifest) if hypothesis_manifest is not None else ""
        ),
    }


def _write_pass_preflight_receipt(
    spec_path: Path,
    spec: dict[str, Any],
    pass_id: str,
    evidence: dict[str, Any],
    evidence_argument: str | None,
    failures: list[str],
) -> Path:
    receipt_path = _pass_preflight_path(spec_path, pass_id)
    write_spec_atomic(
        receipt_path,
        {
            "artifactType": PASS_PREFLIGHT_ARTIFACT_TYPE,
            "version": PASS_PREFLIGHT_VERSION,
            "ok": not failures,
            "binding": _pass_preflight_binding(
                spec_path,
                spec,
                pass_id,
                evidence,
                evidence_argument,
            ),
            "failures": failures,
            "recordedAt": datetime.now(timezone.utc).isoformat(),
        },
    )
    return receipt_path


def _require_pass_preflight_receipt(
    spec_path: Path,
    spec: dict[str, Any],
    pass_id: str,
    evidence: dict[str, Any],
    evidence_argument: str | None,
) -> Path:
    receipt_path = _pass_preflight_path(spec_path, pass_id)
    if not receipt_path.is_file():
        raise ValueError(
            "modular pass review requires a current passing preflight receipt; "
            "run --preflight-only before spawning the independent reviewer"
        )
    receipt = parse_json(receipt_path.read_text(encoding="utf-8"), "pass preflight receipt")
    if not isinstance(receipt, dict):
        raise ValueError("pass preflight receipt must be a JSON object")
    failures: list[str] = []
    if receipt.get("artifactType") != PASS_PREFLIGHT_ARTIFACT_TYPE:
        failures.append("artifact type is invalid")
    if receipt.get("version") != PASS_PREFLIGHT_VERSION:
        failures.append(f"version must be {PASS_PREFLIGHT_VERSION}")
    if receipt.get("ok") is not True:
        failures.append("latest pass preflight did not pass")
    current_binding = _pass_preflight_binding(
        spec_path,
        spec,
        pass_id,
        evidence,
        evidence_argument,
    )
    recorded_binding = receipt.get("binding")
    if not isinstance(recorded_binding, dict):
        failures.append("binding is missing")
    elif recorded_binding != current_binding:
        changed = sorted(
            key
            for key in set(recorded_binding) | set(current_binding)
            if recorded_binding.get(key) != current_binding.get(key)
        )
        failures.append("bound inputs changed after preflight: " + ", ".join(changed))
    if failures:
        raise ValueError(
            "modular pass review requires a fresh passing preflight: "
            + "; ".join(failures)
        )
    return receipt_path


def _consume_pass_preflight_receipt(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--pass-id", required=True)
    parser.add_argument("--action", choices=sorted(VALID_ACTIONS))
    parser.add_argument("--summary")
    parser.add_argument(
        "--verdict-json",
        type=Path,
        help="Hash-bound verdict written by a fresh independent reviewer context.",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Run deterministic evidence vetoes and exit before spawning a reviewer.",
    )
    parser.add_argument("--fidelity", type=float, help="Optional human estimate from 0 to 1")
    parser.add_argument("--matched", help="Semicolon-separated matched criteria")
    parser.add_argument("--mismatches", help="Semicolon-separated mismatches")
    parser.add_argument("--spec-fixes", help="Semicolon-separated spec tasks")
    parser.add_argument("--code-fixes", help="Semicolon-separated code tasks")
    parser.add_argument("--evidence", help="Semicolon-separated extra artifact paths or notes")
    parser.add_argument("--root-cause", choices=sorted(VALID_ROOT_CAUSES))
    parser.add_argument(
        "--correction-plan-json",
        help="JSON array/file of {target,parameterPath,action,reason,value?} corrections",
    )

    parser.add_argument(
        "--evidence-set-json",
        help="JSON array/file of {viewId,referenceImage,renderScreenshot,comparisonImage} views",
    )
    parser.add_argument("--reference-screenshot", help="Legacy single-view reference path/URL")
    parser.add_argument("--render-screenshot", help="Legacy single-view render path/URL")
    parser.add_argument("--comparison-image", help="Legacy single-view comparison sheet path/URL")
    parser.add_argument("--camera-view", help="Legacy single-view id")
    parser.add_argument("--visual-notes")
    parser.add_argument("--ai-vision-notes")
    parser.add_argument(
        "--reviewer-model",
        help="AI vision model that inspected the exact comparison artifact hash.",
    )
    parser.add_argument("--ai-vision-score", type=float)
    parser.add_argument(
        "--visual-threshold",
        type=float,
        help="Optional stricter threshold; it cannot lower the spec quality bar.",
    )
    parser.add_argument("--layer-scores-json", help="JSON object/file of AI layer scores")
    parser.add_argument(
        "--feature-reviews-json",
        help=(
            "JSON array/file of semantic feature scores; include viewIds for targets "
            "that require dedicated face/hand evidence"
        ),
    )

    parser.add_argument("--runtime-checks-json", help="JSON object/file of named runtime booleans")
    parser.add_argument("--metrics-json", help="JSON object/file of measured numeric performance values")
    parser.add_argument("--artifacts-json", help="JSON object/file of evidence artifact paths")
    parser.add_argument("--performance-capture", help="Shortcut for artifacts.performanceCapture")
    parser.add_argument(
        "--allow-missing-local-files",
        action="store_true",
        help="Allow planned paths; normally local evidence must already exist.",
    )
    parser.add_argument(
        "--require-screenshot-files",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("--in-place", action="store_true")
    output_group.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    spec_path = args.spec.expanduser().resolve()
    document = load_document(spec_path)
    if document.modular and not module_status(spec_path, document.raw)["assemblyReady"]:
        raise ValueError(
            "final pass review is locked until every required module is accepted; "
            "use `sculpt module review` for a visual module or `module accept` for a structural module"
        )
    spec = document.resolved
    status = pipeline_status(spec, spec_path)
    if status["currentPass"] == "complete":
        raise ValueError("all build passes are already complete")
    if args.pass_id != status["currentPass"]:
        raise ValueError(
            f"only the current pass may be reviewed; current={status['currentPass']!r}, "
            f"requested={args.pass_id!r}"
        )

    evidence_payload = load_json_argument(args.evidence_set_json, "--evidence-set-json", [])
    evidence_manifest = evidence_payload if isinstance(evidence_payload, dict) else None
    views = evidence_payload
    if isinstance(evidence_payload, dict):
        views = evidence_payload.get("views", evidence_payload.get("evidenceSet", []))
    if not isinstance(views, list) or not all(isinstance(item, dict) for item in views):
        raise ValueError("--evidence-set-json must be an array of view objects")
    if not views and any((args.reference_screenshot, args.render_screenshot, args.comparison_image)):
        views = [
            {
                "viewId": args.camera_view or "primary",
                "referenceImage": args.reference_screenshot or spec.get("sourceImage", ""),
                "renderScreenshot": args.render_screenshot or "",
                "comparisonImage": args.comparison_image or "",
                "notes": args.visual_notes or "",
            }
        ]
    validate_views(views, args.allow_missing_local_files)
    kind = evidence_type(spec, args.pass_id)
    config = effective_pass_config(spec, args.pass_id)
    visual_review_required = kind == "visual" or config.get(
        "requiredPostOptimizationVisualReview"
    ) is True
    normalized_evidence = (
        {**evidence_manifest, "views": views, "type": "visual"}
        if evidence_manifest is not None
        else None
    )
    if args.preflight_only:
        if not visual_review_required:
            raise ValueError("--preflight-only applies only to visual evidence gates")
        if normalized_evidence is None:
            raise ValueError("--preflight-only requires the manifest object written by sculpt compare")
        failures = pass_specific_gaps(spec, args.pass_id)
        failures.extend(
            visual_preflight_failures(
                spec,
                normalized_evidence,
                args.pass_id,
                spec_path,
            )
        )
        failures.extend(
            _pending_pass_batch_failures(
                spec,
                args.pass_id,
                normalized_evidence,
            )
        )
        receipt_path = _write_pass_preflight_receipt(
            spec_path,
            spec,
            args.pass_id,
            normalized_evidence,
            args.evidence_set_json,
            failures,
        )
        review_history = spec.get("reviewHistory", [])
        pass_records = (
            [
                entry
                for entry in review_history
                if isinstance(entry, dict) and entry.get("passId") == args.pass_id
            ]
            if isinstance(review_history, list)
            else []
        )
        print(
            json.dumps(
                {
                    "ok": not failures,
                    "passId": args.pass_id,
                    "comparisonSha256": normalized_evidence.get("comparisonSha256"),
                    "preflightReceipt": str(receipt_path),
                    "refinementBudget": refinement_budget(pass_records),
                    "failures": failures,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0 if not failures else 1

    action = args.action
    summary = args.summary
    verdict: dict[str, Any] | None = None
    resolved_verdict_path: Path | None = None
    pass_preflight_receipt: Path | None = None
    if args.verdict_json is not None:
        if not visual_review_required:
            raise ValueError("--verdict-json applies only to a visual evidence gate")
        if normalized_evidence is None:
            raise ValueError("--verdict-json requires the manifest object written by sculpt compare")
        if document.modular:
            pass_preflight_receipt = _require_pass_preflight_receipt(
                spec_path,
                spec,
                args.pass_id,
                normalized_evidence,
                args.evidence_set_json,
            )
        resolved_verdict_path = args.verdict_json.expanduser().resolve()
        verdict_value = load_json_argument(
            str(resolved_verdict_path),
            "--verdict-json",
            {},
        )
        if not isinstance(verdict_value, dict):
            raise ValueError("--verdict-json must contain a JSON object")
        verdict = verdict_value
        contract_evidence = dict(normalized_evidence)
        contract_evidence["declaredViewIds"] = sorted({
            item.get("id")
            for item in spec.get("viewEvidence", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("id")
        })
        verdict_failures = review_contract_failures(
            verdict,
            contract_evidence,
            PASS_REVIEW_ARTIFACT_TYPE,
            PASS_REVIEW_VERSION,
        )
        expected_spec_hash = review_spec_hash(spec, args.pass_id)
        if verdict.get("passId") != args.pass_id:
            verdict_failures.append(f"verdict passId must be {args.pass_id!r}")
        if verdict.get("specHash") != expected_spec_hash:
            verdict_failures.append("verdict specHash is stale for the current pass")
        if verdict_failures:
            raise ValueError("invalid independent pass verdict: " + "; ".join(dict.fromkeys(verdict_failures)))
        verdict_action = str(verdict.get("action"))
        verdict_summary = str(verdict.get("summary"))
        if action is not None and action != verdict_action:
            raise ValueError("--action cannot override the independent verdict action")
        if summary is not None and summary.strip() != verdict_summary.strip():
            raise ValueError("--summary cannot override the independent verdict summary")
        action = verdict_action
        summary = verdict_summary
    elif document.modular and visual_review_required:
        raise ValueError(
            "modular visual review requires --verdict-json from a fresh independent reviewer; "
            "manual --ai-vision-score/--reviewer-model input cannot approve or refine the pass"
        )

    if action not in VALID_ACTIONS:
        raise ValueError("--action is required when no independent verdict supplies it")
    review_history = spec.get("reviewHistory", [])
    pass_records = (
        [
            entry
            for entry in review_history
            if isinstance(entry, dict) and entry.get("passId") == args.pass_id
        ]
        if isinstance(review_history, list)
        else []
    )
    if action in REFINEMENT_ACTIONS and refinement_budget(pass_records)["exhausted"]:
        raise ValueError(
            "atomic refinement budget is exhausted; record one strategy-reset with a "
            "different representation before any further refinement"
        )
    if action == STRATEGY_RESET_ACTION:
        budget = refinement_budget(pass_records)
        if budget.get("remainingStrategyResets", 0) < 1:
            raise ValueError("strategy-reset budget is exhausted")
        active_root_causes: set[str] = set()
        for record in reversed(pass_records):
            if not isinstance(record, dict):
                continue
            if record.get("action") not in REFINEMENT_ACTIONS:
                break
            active_root_causes.update(
                issue.get("rootCauseKey")
                for issue in record.get("reviewIssues", [])
                if isinstance(issue, dict)
                and issue.get("status") == "open"
                and issue.get("severity") in {"critical", "major"}
                and isinstance(issue.get("rootCauseKey"), str)
            )
        declared_root_causes = set(verdict.get("rootCauseKeys", [])) if verdict else set()
        if not active_root_causes:
            raise ValueError("strategy-reset requires a failed refinement cycle")
        if not declared_root_causes or not declared_root_causes <= active_root_causes:
            raise ValueError(
                "strategy-reset rootCauseKeys must reference blockers from the active failed cycle"
            )
    if action in REFINEMENT_ACTIONS and verdict is not None:
        progress_failures = _pass_refinement_progress_failures(
            spec,
            args.pass_id,
            verdict,
        )
        if progress_failures:
            raise ValueError(
                "another refinement batch requires independently measured progress and closure "
                "of prior blockers; use request-input or stop: "
                + "; ".join(progress_failures)
            )
    if action == "refine-batch" and verdict is None:
        raise ValueError("refine-batch requires --verdict-json with per-correction spec/code scope")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("--summary is required when no independent verdict supplies it")
    if action == "continue" and args.allow_missing_local_files:
        raise ValueError("action=continue cannot use --allow-missing-local-files")
    if action == "continue" and visual_review_required:
        if evidence_manifest is None:
            raise ValueError(
                "action=continue requires the manifest object written by sculpt compare; "
                "a path-only evidence array is not trustworthy"
            )
        manifest_failures = visual_evidence_integrity_failures(
            {**evidence_manifest, "views": views, "type": "visual"}
        )
        if manifest_failures:
            raise ValueError("visual evidence integrity failed: " + "; ".join(manifest_failures))
        if verdict is None and args.ai_vision_score is None:
            raise ValueError("--ai-vision-score is required for action=continue")
        if verdict is None and (
            not isinstance(args.reviewer_model, str) or not args.reviewer_model.strip()
        ):
            raise ValueError("--reviewer-model is required for action=continue")
        if verdict is None and (
            not isinstance(args.ai_vision_notes, str) or len(args.ai_vision_notes.strip()) < 12
        ):
            raise ValueError("--ai-vision-notes must explain the visual verdict")

    if verdict is not None and any(
        value is not None
        for value in (
            args.ai_vision_score,
            args.reviewer_model,
            args.ai_vision_notes,
            args.layer_scores_json,
            args.feature_reviews_json,
        )
    ):
        raise ValueError(
            "manual AI score/model/notes/layer/feature fields cannot override --verdict-json"
        )

    layer_scores = (
        verdict.get("layerScores", {})
        if verdict is not None
        else load_json_argument(args.layer_scores_json, "--layer-scores-json", {})
    )
    if not isinstance(layer_scores, dict):
        raise ValueError("--layer-scores-json must be a JSON object")
    for name, value in layer_scores.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"layer score {name!r} must be numeric")
        layer_scores[name] = score(float(value), f"layer score {name!r}")
    feature_reviews = (
        verdict.get("featureReviews", [])
        if verdict is not None
        else load_json_argument(args.feature_reviews_json, "--feature-reviews-json", [])
    )
    if not isinstance(feature_reviews, list) or not all(isinstance(item, dict) for item in feature_reviews):
        raise ValueError("--feature-reviews-json must be an array of objects")
    for index, review in enumerate(feature_reviews):
        if not isinstance(review.get("id"), str) or not review["id"].strip():
            raise ValueError(f"feature review {index}.id is required")
        if "score" in review:
            if not isinstance(review.get("score"), (int, float)) or isinstance(review.get("score"), bool):
                raise ValueError(f"feature review {index}.score must be numeric")
            review["score"] = score(review.get("score"), f"feature review {index}.score")
    correction_plan = load_json_argument(
        args.correction_plan_json,
        "--correction-plan-json",
        [],
    )
    if not isinstance(correction_plan, list) or not all(
        isinstance(item, dict) for item in correction_plan
    ):
        raise ValueError("--correction-plan-json must be an array of objects")
    for index, correction in enumerate(correction_plan):
        for field in ("target", "parameterPath", "action", "reason"):
            if not isinstance(correction.get(field), str) or not correction[field].strip():
                raise ValueError(f"correction {index}.{field} is required")
        if correction.get("action") not in VALID_CORRECTION_ACTIONS:
            raise ValueError(
                f"correction {index}.action must be one of: "
                + ", ".join(sorted(VALID_CORRECTION_ACTIONS))
            )

    runtime_checks = load_json_argument(args.runtime_checks_json, "--runtime-checks-json", {})
    metrics = load_json_argument(args.metrics_json, "--metrics-json", {})
    artifacts = load_json_argument(args.artifacts_json, "--artifacts-json", {})
    if not isinstance(runtime_checks, dict):
        raise ValueError("--runtime-checks-json must be a JSON object")
    if not all(isinstance(value, bool) for value in runtime_checks.values()):
        raise ValueError("--runtime-checks-json values must be boolean")
    if not isinstance(metrics, dict):
        raise ValueError("--metrics-json must be a JSON object")
    if not all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        for value in metrics.values()
    ):
        raise ValueError("--metrics-json values must be finite numbers")
    if not isinstance(artifacts, dict):
        raise ValueError("--artifacts-json must be a JSON object")
    if args.performance_capture:
        artifacts["performanceCapture"] = args.performance_capture
    for name, value in artifacts.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"artifact {name!r} must be a non-empty path or URL")
        validate_local_path(value, f"artifact {name!r}", args.allow_missing_local_files)

    required_threshold = visual_acceptance_threshold(spec)
    if args.visual_threshold is not None and args.visual_threshold < required_threshold:
        raise ValueError(
            f"--visual-threshold cannot lower the spec threshold {required_threshold}"
        )
    threshold = max(
        required_threshold,
        score(args.visual_threshold, "--visual-threshold", required_threshold),
    )
    ai_score = (
        score(verdict.get("overallScore"), "verdict.overallScore")
        if verdict is not None
        else score(args.ai_vision_score, "--ai-vision-score")
        if args.ai_vision_score is not None
        else None
    )
    fidelity = (
        ai_score or 0.0
        if verdict is not None
        else score(args.fidelity, "--fidelity", ai_score or 0.0)
    )
    review_issues = verdict.get("issues", []) if verdict is not None else []
    review_corrections = verdict.get("corrections", []) if verdict is not None else []
    correction_batch = correction_batch_from_verdict(verdict)
    if not correction_batch:
        correction_batch = correction_batch_from_plan(
            action,
            f"{args.pass_id}-{len(spec.get('reviewHistory', [])) + 1}",
            correction_plan,
        )
    verdict_mismatches = [
        str(issue.get("reason"))
        for issue in review_issues
        if isinstance(issue, dict)
        and issue.get("status") == "open"
        and isinstance(issue.get("reason"), str)
    ]
    batch_corrections = (
        correction_batch.get("corrections", []) if correction_batch else []
    )
    batch_spec_fixes = [
        str(correction.get("change"))
        for correction in batch_corrections
        if isinstance(correction, dict) and correction.get("scope") == "spec"
    ]
    batch_code_fixes = [
        str(correction.get("change"))
        for correction in batch_corrections
        if isinstance(correction, dict) and correction.get("scope") == "code"
    ]
    reviewed_at = datetime.now(timezone.utc).isoformat()
    entry: dict[str, Any] = {
        "timestamp": reviewed_at,
        "specHash": review_spec_hash(spec, args.pass_id),
        "passId": args.pass_id,
        "action": action,
        "summary": summary,
        "estimatedFidelity": fidelity,
        "matched": [] if verdict is not None else split_items(args.matched),
        "mismatches": verdict_mismatches if verdict is not None else split_items(args.mismatches),
        "specFixes": (
            batch_spec_fixes
        ) if verdict is not None else split_items(args.spec_fixes),
        "codeFixes": (
            batch_code_fixes
        ) if verdict is not None else split_items(args.code_fixes),
        "rootCause": str(verdict.get("rootCause") or "") if verdict is not None else args.root_cause or "",
        "correctionPlan": [] if verdict is not None else correction_plan,
        "artifacts": artifacts,
        "representationSignature": sculpt_representation_signature(spec),
    }
    if correction_batch:
        entry["correctionBatch"] = correction_batch
    if verdict is not None and resolved_verdict_path is not None:
        entry["reviewId"] = verdict.get("reviewId")
        entry["reviewIssues"] = review_issues
        entry["reviewCorrections"] = review_corrections
        entry["issueLineageKeys"] = sorted(
            _issue_lineage_keys(review_issues, review_corrections)
        )
        entry["resolvedIssueIds"] = verdict.get("resolvedIssueIds", [])
        entry["resolvedRootCauseKeys"] = verdict.get("resolvedRootCauseKeys", [])
        entry["strategyId"] = verdict.get("strategyId")
        entry["strategyChange"] = verdict.get("strategyChange")
        entry["rootCauseKeys"] = verdict.get("rootCauseKeys", [])
        entry["falsifyingCheck"] = verdict.get("falsifyingCheck")
        entry["requiredEvidence"] = verdict.get("requiredEvidence", [])
        entry["stopReason"] = verdict.get("stopReason")
        entry["stopEvidence"] = verdict.get("stopEvidence", [])
        entry["reviewVerdict"] = str(resolved_verdict_path)
        entry["reviewVerdictSha256"] = file_sha256(resolved_verdict_path)
    if views:
        if evidence_manifest is not None:
            entry["evidence"] = {
                key: value
                for key, value in evidence_manifest.items()
                if key != "evidenceSet"
            }
            entry["evidence"]["type"] = "visual"
            entry["evidence"]["views"] = views
        else:
            entry["evidence"] = {"type": "visual", "views": views}
        entry["aiVisionScore"] = ai_score
        entry["visualAcceptanceThreshold"] = threshold
        entry["layerScores"] = layer_scores
        entry["featureReviews"] = feature_reviews
        entry["aiVisionNotes"] = summary if verdict is not None else args.ai_vision_notes or ""
        if verdict is not None and resolved_verdict_path is not None:
            reviewer = verdict.get("reviewer", {})
            builder = verdict.get("builder", {})
            entry["reviewerEvidence"] = {
                "type": "ai-vision",
                "model": reviewer.get("model", ""),
                "role": reviewer.get("role", ""),
                "builderContextId": builder.get("contextId", ""),
                "reviewerContextId": reviewer.get("contextId", ""),
                "reviewId": verdict.get("reviewId", ""),
                "reviewedArtifactSha256": entry["evidence"].get("comparisonSha256"),
                "reviewVerdict": str(resolved_verdict_path),
                "reviewVerdictSha256": file_sha256(resolved_verdict_path),
                "reviewedAt": reviewed_at,
            }
        elif args.reviewer_model:
            entry["reviewerEvidence"] = {
                "type": "ai-vision",
                "model": args.reviewer_model.strip(),
                "reviewedArtifactSha256": entry["evidence"].get("comparisonSha256"),
                "reviewedAt": reviewed_at,
            }
    if runtime_checks:
        entry["runtimeChecks"] = runtime_checks
    if metrics:
        entry["metrics"] = metrics
    extra_evidence = split_items(args.evidence)
    if extra_evidence:
        entry["extraEvidence"] = extra_evidence

    if action == "continue":
        failures = pass_specific_gaps(spec, args.pass_id)
        failures.extend(review_failures(spec, entry, args.pass_id, spec_path))
        if failures:
            raise ValueError(f"{kind} gate failed: {'; '.join(failures)}")

    history = spec.setdefault("reviewHistory", [])
    if not isinstance(history, list):
        raise ValueError("reviewHistory must be an array")
    history.append(entry)
    sync_pipeline(spec)

    output = spec_path if args.in_place else (args.out.expanduser().resolve() if args.out else None)
    if output:
        save_document(document, output)
        _consume_pass_preflight_receipt(pass_preflight_receipt)
        print(output)
    else:
        print(json.dumps(spec, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
