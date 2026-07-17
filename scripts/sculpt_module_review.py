"""Independent, append-only visual review gate for composable sculpt modules."""

from __future__ import annotations

import hashlib
import math
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sculpt_contract import (
    CORRECTION_SCOPES,
    REFINEMENT_ACTIONS,
    STRATEGY_RESET_ACTION,
    correction_batch_from_verdict,
    file_sha256,
    refinement_budget,
    visual_evidence_authority_failures,
    visual_evidence_integrity_failures,
    write_spec_atomic,
)
from sculpt_manifest import entry_by_id, load_modules, read_object, resolve_manifest
from sculpt_module_contract import (
    MODULE_BUILD_RECEIPT_ARTIFACT_TYPE,
    MODULE_BUILD_RECEIPT_VERSION,
    module_build_receipt_path,
)
from sculpt_image_io import load_image_rgba as load_image
from sculpt_module_state import (
    _load_cache,
    cache_path,
    check_module,
    diagnostic_floor_contract,
    implementation_contract_paths,
    implementation_semantic_hashes,
    interface_hash,
    module_representation_signature,
    module_status,
    visual_gate_floor,
)


MODULE_REVIEW_ARTIFACT_TYPE = "threejs-sculpt-module-review"
MODULE_REVIEW_VERSION = 1
PASS_REVIEW_ARTIFACT_TYPE = "threejs-sculpt-pass-review"
PASS_REVIEW_VERSION = 1
REVIEW_ACTIONS = {
    "continue",
    *REFINEMENT_ACTIONS,
    STRATEGY_RESET_ACTION,
    "request-input",
    "stop",
}
BLOCKING_SEVERITIES = {"critical", "major"}
ISSUE_FAILURE_CLASSES = {
    "topology",
    "geometry",
    "proportion",
    "attachment",
    "material",
    "surface",
    "lighting",
    "evidence",
    "performance",
    "other",
}
MODULE_PREFLIGHT_ARTIFACT_TYPE = "threejs-sculpt-module-preflight"
MODULE_PREFLIGHT_VERSION = 1


def _is_score(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and 0 <= float(value) <= 1
    )


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def review_contract_failures(
    verdict: dict[str, Any],
    evidence: dict[str, Any],
    artifact_type: str = MODULE_REVIEW_ARTIFACT_TYPE,
    artifact_version: int = MODULE_REVIEW_VERSION,
) -> list[str]:
    failures: list[str] = []
    if verdict.get("artifactType") != artifact_type:
        failures.append(f"artifactType must be {artifact_type!r}")
    if verdict.get("version") != artifact_version:
        failures.append(f"version must be {artifact_version}")
    review_id = verdict.get("reviewId")
    if not isinstance(review_id, str) or not review_id.strip():
        failures.append("reviewId is required")
    action = verdict.get("action")
    if action not in REVIEW_ACTIONS:
        failures.append(
            "action must be continue, refine-spec, refine-code, refine-batch, "
            "strategy-reset, request-input, or stop"
        )
    if verdict.get("comparisonSha256") != evidence.get("comparisonSha256"):
        failures.append("verdict comparisonSha256 does not match the reviewed evidence")
    summary = verdict.get("summary")
    if not isinstance(summary, str) or len(summary.strip()) < 12:
        failures.append("summary must contain a concrete visual assessment")

    builder = verdict.get("builder")
    reviewer = verdict.get("reviewer")
    builder_context = builder.get("contextId") if isinstance(builder, dict) else None
    reviewer_context = reviewer.get("contextId") if isinstance(reviewer, dict) else None
    if not isinstance(builder_context, str) or not builder_context.strip():
        failures.append("builder.contextId is required")
    if not isinstance(reviewer_context, str) or not reviewer_context.strip():
        failures.append("reviewer.contextId is required")
    if (
        isinstance(builder_context, str)
        and isinstance(reviewer_context, str)
        and builder_context.strip() == reviewer_context.strip()
    ):
        failures.append("builder and reviewer contextId must differ")
    if not isinstance(reviewer, dict) or reviewer.get("role") != "independent-reviewer":
        failures.append("reviewer.role must be 'independent-reviewer'")
    if not isinstance(reviewer, dict) or not isinstance(reviewer.get("model"), str) or not reviewer.get("model", "").strip():
        failures.append("reviewer.model is required")

    issues = verdict.get("issues", [])
    issue_ids: set[str] = set()
    if not isinstance(issues, list):
        failures.append("issues must be an array")
        issues = []
    for index, issue in enumerate(issues):
        label = f"issues[{index}]"
        if not isinstance(issue, dict):
            failures.append(f"{label} must be an object")
            continue
        issue_id = issue.get("id")
        if not isinstance(issue_id, str) or not issue_id.strip():
            failures.append(f"{label}.id is required")
        elif issue_id in issue_ids:
            failures.append(f"duplicate issue id {issue_id!r}")
        else:
            issue_ids.add(issue_id)
        if issue.get("severity") not in {"critical", "major", "minor"}:
            failures.append(f"{label}.severity must be critical, major, or minor")
        if issue.get("status") not in {"open", "resolved"}:
            failures.append(f"{label}.status must be open or resolved")
        if issue.get("failureClass") not in ISSUE_FAILURE_CLASSES:
            failures.append(
                f"{label}.failureClass must be one of: "
                + ", ".join(sorted(ISSUE_FAILURE_CLASSES))
            )
        for field in ("target", "reason"):
            if not isinstance(issue.get(field), str) or not issue.get(field, "").strip():
                failures.append(f"{label}.{field} is required")
        for field in ("rootCauseKey", "evidenceCheck"):
            if not isinstance(issue.get(field), str) or not issue.get(field, "").strip():
                failures.append(f"{label}.{field} is required")

    open_issue_ids = {
        issue.get("id")
        for issue in issues
        if isinstance(issue, dict)
        and issue.get("status") == "open"
        and isinstance(issue.get("id"), str)
    }

    corrections = verdict.get("corrections", [])
    if not isinstance(corrections, list):
        failures.append("corrections must be an array")
        corrections = []
    for index, correction in enumerate(corrections):
        label = f"corrections[{index}]"
        if not isinstance(correction, dict):
            failures.append(f"{label} must be an object")
            continue
        if correction.get("issueId") not in issue_ids:
            failures.append(f"{label}.issueId must reference an issue in this verdict")
        elif action in REFINEMENT_ACTIONS and correction.get("issueId") not in open_issue_ids:
            failures.append(f"{label}.issueId must reference an open issue for refinement")
        scope = correction.get("scope")
        if scope is not None and scope not in CORRECTION_SCOPES:
            failures.append(f"{label}.scope must be spec or code")
        if action == "refine-batch" and scope not in CORRECTION_SCOPES:
            failures.append(f"{label}.scope is required for refine-batch")
        expected_scope = "spec" if action == "refine-spec" else "code" if action == "refine-code" else None
        if expected_scope is not None and scope is not None and scope != expected_scope:
            failures.append(
                f"{label}.scope conflicts with {action}; use refine-batch for mixed spec/code corrections"
            )
        for field in ("target", "parameterPath", "change", "expectedDelta"):
            if not isinstance(correction.get(field), str) or not correction.get(field, "").strip():
                failures.append(f"{label}.{field} is required")

    resolved = verdict.get("resolvedIssueIds", [])
    if not isinstance(resolved, list) or not all(isinstance(item, str) and item for item in resolved):
        failures.append("resolvedIssueIds must be an array of issue ids")
    resolved_root_causes = verdict.get("resolvedRootCauseKeys", [])
    if not isinstance(resolved_root_causes, list) or not all(
        isinstance(item, str) and item for item in resolved_root_causes
    ):
        failures.append("resolvedRootCauseKeys must be an array of stable root-cause keys")

    if action in REFINEMENT_ACTIONS:
        open_issues = open_issue_ids
        corrected = {
            correction.get("issueId")
            for correction in corrections
            if isinstance(correction, dict)
        }
        if not open_issues:
            failures.append(f"{action} requires at least one open issue")
        if open_issues - corrected:
            failures.append(
                "every open refine issue needs an actionable correction: "
                + ", ".join(sorted(str(item) for item in open_issues - corrected))
            )
        if action == "refine-batch":
            correction_scopes = {
                correction.get("scope")
                for correction in corrections
                if isinstance(correction, dict)
                and correction.get("issueId") in open_issues
            }
            if correction_scopes != set(CORRECTION_SCOPES):
                failures.append(
                    "refine-batch requires both spec and code correction scopes"
                )
    if action == "continue" or action in REFINEMENT_ACTIONS:
        if not _is_score(verdict.get("overallScore")):
            failures.append(f"{action} requires overallScore from 0 to 1")
        if not isinstance(verdict.get("layerScores"), dict):
            failures.append(f"{action} requires layerScores")
    if action == "continue":
        if not isinstance(verdict.get("featureReviews"), list):
            failures.append("continue requires featureReviews")
    if action == STRATEGY_RESET_ACTION:
        for field in ("strategyId", "strategyChange", "falsifyingCheck"):
            if not isinstance(verdict.get(field), str) or len(verdict.get(field, "").strip()) < 8:
                failures.append(f"strategy-reset requires a concrete {field}")
        root_causes = verdict.get("rootCauseKeys")
        if not isinstance(root_causes, list) or not all(
            isinstance(item, str) and item for item in root_causes
        ):
            failures.append("strategy-reset requires rootCauseKeys")
    if action == "request-input":
        required_evidence = verdict.get("requiredEvidence")
        if not isinstance(required_evidence, list) or not required_evidence:
            failures.append(
                "request-input requires concrete requiredEvidence; exhausted refinement budget is not evidence"
            )
        else:
            evidence_view_ids = {
                view.get("viewId")
                for view in evidence.get("views", [])
                if isinstance(view, dict)
                and isinstance(view.get("viewId"), str)
                and isinstance(view.get("referenceProvenance"), dict)
                and view["referenceProvenance"].get("origin") == "observed"
            }
            provenance = evidence.get("renderProvenance")
            declared_view_ids = set(
                _strings(
                    provenance.get("declaredViewIds")
                    if isinstance(provenance, dict)
                    else evidence.get("declaredViewIds")
                )
            )
            evidence_issue_ids = {
                issue.get("id")
                for issue in issues
                if isinstance(issue, dict)
                and issue.get("status") == "open"
                and issue.get("failureClass") == "evidence"
            }
            if not evidence_issue_ids:
                failures.append(
                    "request-input requires an open issue with failureClass='evidence'"
                )
            for index, item in enumerate(required_evidence):
                label = f"requiredEvidence[{index}]"
                if not isinstance(item, dict):
                    failures.append(f"{label} must be an object")
                    continue
                for field in ("issueId", "missingViewId", "sourceConstraint"):
                    if not isinstance(item.get(field), str) or not item.get(field, "").strip():
                        failures.append(f"{label}.{field} is required")
                for field in ("missingEvidence", "blockedCriterion", "unblockAction"):
                    if not isinstance(item.get(field), str) or len(item.get(field, "").strip()) < 8:
                        failures.append(f"{label}.{field} is required")
                if item.get("issueId") not in evidence_issue_ids:
                    failures.append(
                        f"{label}.issueId must reference an open evidence issue"
                    )
                missing_view_id = item.get("missingViewId")
                if isinstance(missing_view_id, str) and missing_view_id in evidence_view_ids:
                    failures.append(
                        f"{label}.missingViewId is already present in reviewed evidence"
                    )
                if isinstance(missing_view_id, str) and missing_view_id not in declared_view_ids:
                    failures.append(
                        f"{label}.missingViewId is not declared by the current module/global viewEvidence"
                    )
                if item.get("sourceConstraint") not in {
                    "occluded",
                    "out-of-frame",
                    "insufficient-resolution",
                    "material-ambiguity",
                }:
                    failures.append(f"{label}.sourceConstraint is invalid")
                wording = " ".join(str(item.get(field) or "") for field in item).lower()
                if any(token in wording for token in ("budget", "batch limit", "refinement limit")):
                    failures.append(
                        f"{label} describes process exhaustion, not missing source evidence"
                    )
    if action == "stop":
        if not isinstance(verdict.get("stopReason"), str) or len(verdict.get("stopReason", "").strip()) < 12:
            failures.append("stop requires a concrete stopReason")
        if not _strings(verdict.get("stopEvidence")):
            failures.append("stop requires verified stopEvidence")
    return list(dict.fromkeys(failures))


def _review_contract_failures(verdict: dict[str, Any], evidence: dict[str, Any]) -> list[str]:
    return review_contract_failures(verdict, evidence)


def _implementation_hashes(files: list[Path]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for resolved in files:
        hashes[str(resolved)] = file_sha256(resolved)
    if not hashes:
        raise ValueError("visual review requires at least one module-owned implementation file")
    return dict(sorted(hashes.items()))


def _render_provenance_failures(
    evidence: dict[str, Any],
    manifest: dict[str, Any],
    module: dict[str, Any],
    module_id: str,
    module_hash_value: str,
    implementation_files: dict[str, str],
    semantic_files: dict[str, str],
    manifest_path: Path,
) -> list[str]:
    provenance = evidence.get("renderProvenance")
    if not isinstance(provenance, dict):
        return [
            "module evidence is missing renderProvenance; rerun compare with "
            "--sculpt-manifest and --module-id"
        ]
    failures: list[str] = []
    if provenance.get("artifactType") != "threejs-sculpt-render-provenance":
        failures.append("renderProvenance artifact type is invalid")
    if provenance.get("version") != 2:
        failures.append(
            "renderProvenance version must be 2 with generated-factory runtime attestation"
        )
    if provenance.get("moduleId") != module_id:
        failures.append("renderProvenance is bound to a different module")
    if provenance.get("moduleHash") != module_hash_value:
        failures.append("renderProvenance module spec snapshot is stale")
    payload = module.get("payload") if isinstance(module.get("payload"), dict) else {}
    global_spec = manifest.get("globalSpec") if isinstance(manifest.get("globalSpec"), dict) else {}
    declared_view_ids = sorted({
        item.get("id")
        for source in (global_spec.get("viewEvidence", []), payload.get("viewEvidence", []))
        if isinstance(source, list)
        for item in source
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("id")
    })
    if provenance.get("declaredViewIds") != declared_view_ids:
        failures.append("renderProvenance declared view inventory is stale or incomplete")
    if provenance.get("implementationFiles") != implementation_files:
        failures.append("renderProvenance implementation snapshot is stale or incomplete")
    if provenance.get("implementationSemanticFiles") != semantic_files:
        failures.append("renderProvenance executable semantic snapshot is stale")
    if provenance.get("renderSha256") != _render_hashes(evidence):
        failures.append("renderProvenance does not bind the reviewed render images")
    failures.extend(
        _generated_runtime_provenance_failures(
            provenance,
            module_id,
            module_hash_value,
            manifest_path,
        )
    )
    return failures


def _generated_runtime_provenance_failures(
    provenance: dict[str, Any],
    module_id: str,
    module_hash_value: str,
    manifest_path: Path,
) -> list[str]:
    """Prove the reviewed pixels came from the current generated factory in a live scene."""

    failures: list[str] = []
    raw_build_path = provenance.get("buildReceiptPath")
    build_path = (
        Path(raw_build_path).expanduser().resolve()
        if isinstance(raw_build_path, str) and raw_build_path.strip()
        else None
    )
    if build_path != module_build_receipt_path(manifest_path, module_id):
        failures.append("render provenance is not bound to the canonical module build receipt")
    embedded_build = provenance.get("buildReceipt")
    if build_path is None or not build_path.is_file():
        failures.append("generated module build receipt is missing")
        build_receipt: dict[str, Any] = {}
    else:
        try:
            loaded_build = read_object(build_path, "module build receipt")
        except (OSError, ValueError) as exc:
            failures.append(f"generated module build receipt is invalid: {exc}")
            loaded_build = {}
        build_receipt = loaded_build if isinstance(loaded_build, dict) else {}
        if provenance.get("buildReceiptSha256") != file_sha256(build_path):
            failures.append("generated module build receipt changed after render capture")
        if embedded_build != build_receipt:
            failures.append("embedded generated build receipt differs from the current file")
    if build_receipt.get("artifactType") != MODULE_BUILD_RECEIPT_ARTIFACT_TYPE:
        failures.append("generated module build receipt artifact type is invalid")
    if build_receipt.get("version") != MODULE_BUILD_RECEIPT_VERSION:
        failures.append(
            f"generated module build receipt version must be {MODULE_BUILD_RECEIPT_VERSION}"
        )
    if build_receipt.get("moduleId") != module_id:
        failures.append("generated module build receipt belongs to another module")
    if build_receipt.get("moduleHash") != module_hash_value:
        failures.append("generated module build receipt is stale for the current module spec")
    if build_receipt.get("manifestPath") != str(manifest_path.expanduser().resolve()):
        failures.append("generated module build receipt belongs to another sculpt manifest")
    resolved_spec_data: dict[str, Any] | None = None
    generated_source = ""
    for path_field, hash_field, label in (
        ("resolvedSpec", "resolvedSpecSha256", "resolved spec"),
        ("generatedOutput", "generatedOutputSha256", "generated factory"),
    ):
        raw_path = build_receipt.get(path_field)
        current_path = (
            Path(raw_path).expanduser().resolve()
            if isinstance(raw_path, str) and raw_path.strip()
            else None
        )
        if current_path is None or not current_path.is_file():
            failures.append(f"{label} recorded by the module build is missing")
        elif build_receipt.get(hash_field) != file_sha256(current_path):
            failures.append(f"{label} changed after the attested module build")
        elif path_field == "resolvedSpec":
            try:
                resolved_value = read_object(current_path, "resolved module spec")
            except (OSError, ValueError) as exc:
                failures.append(f"resolved module spec is invalid: {exc}")
            else:
                resolved_spec_data = resolved_value
        elif path_field == "generatedOutput":
            generated_source = current_path.read_text(encoding="utf-8")
            factory_id = build_receipt.get("factoryId")
            if "export const createSculptModel" not in generated_source:
                failures.append("attested generated factory has no stable createSculptModel export")
            if not isinstance(factory_id, str) or factory_id not in generated_source:
                failures.append("attested generated factory does not contain its recorded factoryId")
    if resolved_spec_data is not None and generated_source:
        try:
            from generate_threejs_factory import (
                generate,
                generated_factory_contract_from_source,
            )

            current_manifest = read_object(manifest_path, "sculpt manifest")
            expected_resolved = resolve_manifest(
                manifest_path,
                current_manifest,
                selected=[module_id],
            )
            if resolved_spec_data != expected_resolved:
                failures.append("attested resolved spec is not the current module resolution")
            pass_id = build_receipt.get("passId")
            if not isinstance(pass_id, str) or not pass_id:
                raise ValueError("build receipt passId is missing")
            recomputed_source = generate(
                resolved_spec_data,
                pass_id,
                _geometry_prevalidated=True,
            )
            if generated_source != recomputed_source:
                failures.append("generated factory is not the deterministic output of the resolved spec")
            recomputed_contract = generated_factory_contract_from_source(recomputed_source)
            for field, value in recomputed_contract.items():
                if build_receipt.get(field) != value:
                    failures.append(
                        f"generated module build receipt {field} does not match recomputed output"
                    )
        except (OSError, ValueError, TypeError) as exc:
            failures.append(f"generated factory contract could not be recomputed: {exc}")
    for field in ("factoryId", "factoryExport", "specSha256", "passId"):
        if not isinstance(build_receipt.get(field), str) or not build_receipt.get(field):
            failures.append(f"generated module build receipt has no {field}")

    runtime_path_value = provenance.get("runtimeReceiptPath")
    runtime_path = (
        Path(runtime_path_value).expanduser().resolve()
        if isinstance(runtime_path_value, str) and runtime_path_value.strip()
        else None
    )
    if runtime_path is None or not runtime_path.is_file():
        failures.append("live scene runtime receipt is missing")
        loaded_runtime: Any = None
    else:
        if provenance.get("runtimeReceiptSha256") != file_sha256(runtime_path):
            failures.append("live scene runtime receipt changed after render capture")
        try:
            loaded_runtime = read_object(runtime_path, "live scene runtime receipt")
        except (OSError, ValueError) as exc:
            failures.append(f"live scene runtime receipt is invalid: {exc}")
            loaded_runtime = None
    runtime = provenance.get("runtimeReceipt")
    if not isinstance(runtime, dict):
        failures.append("render provenance has no generated-factory runtime receipt")
        return list(dict.fromkeys(failures))
    if not (
        loaded_runtime == runtime
        or isinstance(loaded_runtime, list)
        and sum(1 for item in loaded_runtime if item == runtime) == 1
    ):
        failures.append("embedded runtime receipt is not present in the captured runtime file")
    if runtime.get("artifactType") != "threejs-sculpt-runtime-receipt":
        failures.append("live scene runtime receipt artifact type is invalid")
    if runtime.get("version") != 1:
        failures.append("live scene runtime receipt version must be 1")
    for field in ("factoryId", "factoryExport", "specSha256", "passId"):
        if runtime.get(field) != build_receipt.get(field):
            failures.append(f"live scene runtime {field} does not match the generated build")
    if runtime.get("factoryExport") != "createSculptModel":
        failures.append("reviewed scene did not use the stable generated createSculptModel export")
    if runtime.get("rootAttachedToScene") is not True:
        failures.append("generated factory root was not attached to the rendered THREE.Scene")
    if runtime.get("rootEffectivelyVisible") is not True:
        failures.append("generated factory root was hidden in the rendered scene")
    for field in (
        "missingComponentIds",
        "missingMeshComponentIds",
        "hiddenMeshComponentIds",
        "unexpectedGeneratedDescendantMeshes",
        "unexpectedVisibleMeshes",
        "geometryChangedComponentIds",
    ):
        value = runtime.get(field)
        if not isinstance(value, list):
            failures.append(f"live scene runtime {field} must be an array")
        elif value:
            failures.append(f"live scene runtime {field} must be empty: " + ", ".join(map(str, value)))
    component_ids = set(_strings(runtime.get("componentIds")))
    mesh_ids = set(_strings(runtime.get("meshComponentIds")))
    if len(component_ids) != len(runtime.get("componentIds", [])):
        failures.append("live scene runtime componentIds contains duplicates or invalid ids")
    if len(mesh_ids) != len(runtime.get("meshComponentIds", [])):
        failures.append("live scene runtime meshComponentIds contains duplicates or invalid ids")
    if mesh_ids and not _strings(runtime.get("geometryFingerprint")):
        failures.append("live scene runtime has meshes but no geometry fingerprint")
    initial_geometry = _strings(runtime.get("initialGeometryFingerprint"))
    current_geometry = _strings(runtime.get("geometryFingerprint"))
    if initial_geometry != current_geometry:
        failures.append("runtime geometry differs from the factory-created geometry")
    fingerprint_ids = {
        value.split(":", 1)[0]
        for value in current_geometry
        if ":" in value
    }
    if not mesh_ids <= fingerprint_ids:
        failures.append("runtime geometry fingerprint does not cover every live renderable")
    expected_component_ids = set(_strings(build_receipt.get("expectedComponentIds")))
    expected_mesh_ids = set(_strings(build_receipt.get("expectedMeshComponentIds")))
    expected_primitives = build_receipt.get("expectedPrimitives")
    actual_primitives = runtime.get("componentPrimitives")
    if not isinstance(expected_primitives, dict) or not isinstance(actual_primitives, dict):
        failures.append("generated/runtime primitive inventory is invalid")
    else:
        primitive_mismatches = sorted(
            component_id
            for component_id, primitive in expected_primitives.items()
            if actual_primitives.get(component_id) != primitive
        )
        if primitive_mismatches:
            failures.append(
                "runtime primitive inventory differs from generated output: "
                + ", ".join(primitive_mismatches)
            )
    if not expected_component_ids:
        failures.append("generated module build receipt has no expected component inventory")
    missing_components = expected_component_ids - component_ids
    missing_meshes = expected_mesh_ids - mesh_ids
    if missing_components:
        failures.append(
            "rendered generated root is missing expected components: "
            + ", ".join(sorted(missing_components))
        )
    if missing_meshes:
        failures.append(
            "rendered generated root is missing expected mesh components: "
            + ", ".join(sorted(missing_meshes))
        )
    return list(dict.fromkeys(failures))


def diagnostic_veto_failures(
    manifest: dict[str, Any], module: dict[str, Any], evidence: dict[str, Any]
) -> list[str]:
    from make_visual_comparison_sheet import silhouette_diagnostics

    gate = module.get("qualityGate") if isinstance(module.get("qualityGate"), dict) else {}
    required_views = set(_strings(gate.get("requiredViews")))
    diagnostic_views = set(_strings(gate.get("diagnosticViews")))
    reviewed_views = required_views | diagnostic_views
    view_by_id = {
        view.get("viewId"): view
        for view in evidence.get("views", [])
        if isinstance(view, dict) and isinstance(view.get("viewId"), str)
    }
    failures: list[str] = []
    missing = reviewed_views - set(view_by_id)
    if missing:
        failures.append("visual evidence is missing required/diagnostic views: " + ", ".join(sorted(missing)))
    for view_id in sorted(reviewed_views & set(view_by_id)):
        view = view_by_id[view_id]
        try:
            reference = load_image(Path(str(view.get("referenceImage"))).expanduser())
            render = load_image(Path(str(view.get("renderScreenshot"))).expanduser())
            recomputed, _, _ = silhouette_diagnostics(reference, render)
        except (OSError, ValueError, TypeError) as exc:
            failures.append(f"view {view_id!r} diagnostics could not be recomputed from pixels: {exc}")
            continue
        if view.get("fitDiagnostics") != recomputed:
            failures.append(
                f"view {view_id!r} fit diagnostics do not match deterministic pixel recomputation"
            )
    thresholds = diagnostic_floor_contract(manifest)
    custom_thresholds = gate.get("diagnosticThresholds")
    if isinstance(custom_thresholds, dict):
        for field, value in custom_thresholds.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool) or field not in thresholds:
                continue
            thresholds[field] = (
                max(thresholds[field], float(value))
                if field.startswith("minimum")
                else min(thresholds[field], float(value))
            )
    for view_id in sorted(reviewed_views & set(view_by_id)):
        reviewed_view = view_by_id[view_id]
        diagnostics = reviewed_view.get("fitDiagnostics")
        if not isinstance(diagnostics, dict):
            failures.append(f"view {view_id!r} has no fit diagnostics")
            continue
        mask = diagnostics.get("maskDiagnostics")
        warnings = mask.get("warnings", []) if isinstance(mask, dict) else []
        if not isinstance(mask, dict) or not isinstance(warnings, list):
            failures.append(f"view {view_id!r} has invalid mask diagnostics")
            continue
        if warnings:
            failures.append(f"view {view_id!r} has unreliable masks: " + "; ".join(str(item) for item in warnings))
        for side in ("reference", "render"):
            info = mask.get(side)
            coverage = info.get("foregroundCoverage") if isinstance(info, dict) else None
            if not _is_score(coverage) or float(coverage) <= 0.01 or float(coverage) >= 0.95:
                failures.append(f"view {view_id!r} {side} foreground mask is unusable")
        metrics = (
            ("silhouetteIou", "minimumSilhouetteIou", lambda value, limit: value >= limit, "below"),
            ("centroidDelta", "maximumCentroidDelta", lambda value, limit: value <= limit, "above"),
            ("aspectRatioDelta", "maximumAspectRatioDelta", lambda value, limit: value <= limit, "above"),
        )
        provenance = reviewed_view.get("referenceProvenance")
        synthetic_hypothesis = (
            isinstance(provenance, dict)
            and provenance.get("origin") == "synthetic-hypothesis"
        )
        synthetic_limits = {
            "minimumSilhouetteIou": 0.38,
            "maximumCentroidDelta": 0.18,
            "maximumAspectRatioDelta": 0.30,
        }
        for field, threshold_field, predicate, relation in metrics:
            value = diagnostics.get(field)
            limit = thresholds.get(threshold_field)
            if synthetic_hypothesis:
                synthetic_limit = synthetic_limits[threshold_field]
                limit = (
                    min(float(limit), synthetic_limit)
                    if threshold_field.startswith("minimum") and _is_score(limit)
                    else max(float(limit), synthetic_limit)
                    if _is_score(limit)
                    else synthetic_limit
                )
            if not _is_score(value) or not _is_score(limit):
                failures.append(f"view {view_id!r} has invalid {field} diagnostic")
            elif not predicate(float(value), float(limit)):
                failures.append(
                    f"view {view_id!r} {field} {float(value):.3f} is {relation} veto threshold {float(limit):.3f}"
                )
        if synthetic_hypothesis:
            # ImageGen can constrain inferred volume, not unseen material truth.
            continue
        appearance = diagnostics.get("appearance")
        appearance_metrics = (
            ("detailEnergyRatio", "minimumDetailEnergyRatio", lambda value, limit: value >= limit, "below"),
            ("edgeDensityRatio", "minimumEdgeDensityRatio", lambda value, limit: value >= limit, "below"),
            (
                "foregroundHistogramIntersection",
                "minimumHistogramIntersection",
                lambda value, limit: value >= limit,
                "below",
            ),
            ("foregroundMeanColorDelta", "maximumMeanColorDelta", lambda value, limit: value <= limit, "above"),
            (
                "highlightCoverageRatio",
                "minimumHighlightCoverageRatio",
                lambda value, limit: value >= limit,
                "below",
            ),
            (
                "highlightEnergyRatio",
                "minimumHighlightEnergyRatio",
                lambda value, limit: value >= limit,
                "below",
            ),
        )
        for field, threshold_field, predicate, relation in appearance_metrics:
            value = appearance.get(field) if isinstance(appearance, dict) else None
            limit = thresholds.get(threshold_field)
            if not _is_score(value) or not _is_score(limit):
                failures.append(f"view {view_id!r} has invalid {field} appearance diagnostic")
            elif not predicate(float(value), float(limit)):
                failures.append(
                    f"view {view_id!r} {field} {float(value):.3f} is {relation} veto threshold "
                    f"{float(limit):.3f}"
                )
        counts = appearance.get("sampleCounts") if isinstance(appearance, dict) else None
        if not isinstance(counts, dict) or any(
            not isinstance(counts.get(side), int) or counts.get(side, 0) < 16
            for side in ("reference", "render")
        ):
            failures.append(f"view {view_id!r} has too few foreground samples for diagnostics")
    return list(dict.fromkeys(failures))


def _feature_gate_failures(
    manifest: dict[str, Any],
    module: dict[str, Any],
    entry: dict[str, Any],
    evidence: dict[str, Any],
    verdict: dict[str, Any],
) -> list[str]:
    payload = module.get("payload") if isinstance(module.get("payload"), dict) else {}
    targets = {
        target.get("id"): target
        for target in payload.get("featureReviewTargets", [])
        if isinstance(target, dict)
        and isinstance(target.get("id"), str)
        and (target.get("tier") == "critical" or target.get("mustPass") is True)
    }
    global_spec = manifest.get("globalSpec") if isinstance(manifest.get("globalSpec"), dict) else {}
    quality_contract = (
        global_spec.get("qualityContract")
        if isinstance(global_spec.get("qualityContract"), dict)
        else {}
    )
    group_by_id = {
        group.get("id"): group
        for group in quality_contract.get("featureGroups", [])
        if isinstance(group, dict) and isinstance(group.get("id"), str)
    }
    for feature_id in entry.get("covers", []):
        group = group_by_id.get(feature_id)
        if isinstance(group, dict):
            covered_target = dict(group)
            covered_target.setdefault("minimumScore", module.get("qualityGate", {}).get("minimumScore", 0.0))
            covered_target["requiresDedicatedEvidence"] = True
            covered_target["reviewViewIds"] = list(group.get("evidenceRefs", []))
            targets[feature_id] = covered_target

    reviews = verdict.get("featureReviews", [])
    review_by_id: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for review in reviews if isinstance(reviews, list) else []:
        if not isinstance(review, dict) or not isinstance(review.get("id"), str):
            failures.append("featureReviews entries must have an id")
            continue
        if review["id"] in review_by_id:
            failures.append(f"duplicate feature review {review['id']!r}")
        review_by_id[review["id"]] = review
    available_views = {
        view.get("viewId")
        for view in evidence.get("views", [])
        if isinstance(view, dict) and isinstance(view.get("viewId"), str)
    }
    for feature_id, target in targets.items():
        review = review_by_id.get(feature_id)
        if not isinstance(review, dict):
            failures.append(f"critical/covered feature {feature_id!r} has no independent review")
            continue
        if review.get("visible") is not True:
            failures.append(f"critical/covered feature {feature_id!r} is not explicitly visible")
        score = review.get("score")
        configured_minimum = target.get(
            "minimumScore", module.get("qualityGate", {}).get("minimumScore", 0.0)
        )
        minimum = (
            max(float(configured_minimum), visual_gate_floor(manifest, entry))
            if _is_score(configured_minimum)
            else configured_minimum
        )
        if not _is_score(score) or not _is_score(minimum):
            failures.append(f"critical/covered feature {feature_id!r} has an invalid score contract")
        elif float(score) < float(minimum):
            failures.append(
                f"critical/covered feature {feature_id!r} score {float(score):.3f} "
                f"is below {float(minimum):.3f}"
            )
        required_views = set(_strings(target.get("reviewViewIds"))) if target.get("requiresDedicatedEvidence") is True else set()
        review_views = set(_strings(review.get("viewIds")))
        missing_evidence = required_views - available_views
        missing_bindings = required_views - review_views
        if missing_evidence:
            failures.append(
                f"critical/covered feature {feature_id!r} evidence is missing views: "
                + ", ".join(sorted(missing_evidence))
            )
        if missing_bindings:
            failures.append(
                f"critical/covered feature {feature_id!r} review is not bound to views: "
                + ", ".join(sorted(missing_bindings))
            )
    return list(dict.fromkeys(failures))


def _continue_gate_failures(
    manifest: dict[str, Any],
    module: dict[str, Any],
    entry: dict[str, Any],
    evidence: dict[str, Any],
    verdict: dict[str, Any],
    diagnostics_preflighted: bool = False,
) -> list[str]:
    gate = module.get("qualityGate") if isinstance(module.get("qualityGate"), dict) else {}
    failures: list[str] = []
    overall = verdict.get("overallScore")
    configured_minimum = gate.get("minimumScore")
    minimum = (
        max(float(configured_minimum), visual_gate_floor(manifest, entry))
        if _is_score(configured_minimum)
        else configured_minimum
    )
    if _is_score(overall) and _is_score(minimum) and float(overall) < float(minimum):
        failures.append(f"overall score {float(overall):.3f} is below {float(minimum):.3f}")
    layer_scores = verdict.get("layerScores") if isinstance(verdict.get("layerScores"), dict) else {}
    for layer, threshold in gate.get("requiredLayerScores", {}).items():
        value = layer_scores.get(layer)
        if not _is_score(value):
            failures.append(f"required layer {layer!r} has no valid score")
        elif float(value) < float(threshold):
            failures.append(
                f"layer {layer!r} score {float(value):.3f} is below {float(threshold):.3f}"
            )
    for issue in verdict.get("issues", []):
        if (
            isinstance(issue, dict)
            and issue.get("status") == "open"
            and issue.get("severity") in BLOCKING_SEVERITIES
        ):
            failures.append(f"blocking issue {issue.get('id')!r} remains open")
    if not diagnostics_preflighted:
        failures.extend(diagnostic_veto_failures(manifest, module, evidence))
    failures.extend(_feature_gate_failures(manifest, module, entry, evidence, verdict))
    return list(dict.fromkeys(failures))


def _render_hashes(evidence: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(view.get("renderSha256"))
            for view in evidence.get("views", [])
            if isinstance(view, dict) and isinstance(view.get("renderSha256"), str)
        }
    )


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
        if not isinstance(value, str) or not value.strip() or "://" in value:
            continue
        path = Path(value).expanduser().resolve()
        snapshot[str(path)] = file_sha256(path) if path.is_file() else "missing"
    return dict(sorted(snapshot.items()))


def _safe_cache_segment(value: Any) -> str:
    raw = str(value or "snapshot")
    readable = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw).strip("-")[:48] or "snapshot"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return f"{readable}-{digest}"


def _snapshot_refinement_renders(
    manifest_path: Path,
    module_id: str,
    review_id: Any,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Preserve reviewed renders so normal fixed-path rerenders cannot erase the baseline."""

    destination_dir = (
        cache_path(manifest_path).parent
        / "review-renders"
        / _safe_cache_segment(module_id)
        / _safe_cache_segment(review_id)
    )
    destination_dir.mkdir(parents=True, exist_ok=True)
    snapshots: list[dict[str, str]] = []
    for index, view in enumerate(evidence.get("views", [])):
        if not isinstance(view, dict) or not isinstance(view.get("viewId"), str):
            continue
        source_value = view.get("renderScreenshot")
        expected_hash = view.get("renderSha256")
        if not isinstance(source_value, str) or not isinstance(expected_hash, str):
            raise ValueError("reviewed render snapshot is missing its path or hash")
        source = Path(source_value).expanduser().resolve()
        if not source.is_file() or file_sha256(source) != expected_hash:
            raise ValueError("reviewed render changed before its immutable snapshot was stored")
        suffix = source.suffix.lower() if source.suffix.lower() in {".png", ".jpg", ".jpeg"} else ".img"
        destination = destination_dir / (
            f"{index + 1:02d}-{_safe_cache_segment(view['viewId'])}-{expected_hash[:12]}{suffix}"
        )
        if not destination.is_file() or file_sha256(destination) != expected_hash:
            temporary = destination.with_name(destination.name + ".tmp")
            shutil.copyfile(source, temporary)
            if file_sha256(temporary) != expected_hash:
                temporary.unlink(missing_ok=True)
                raise ValueError("immutable render snapshot hash does not match reviewed evidence")
            temporary.replace(destination)
        snapshots.append(
            {
                "viewId": view["viewId"],
                "renderScreenshot": str(destination),
                "renderSha256": expected_hash,
            }
        )
    if not snapshots:
        raise ValueError("refinement review has no render views to preserve")
    return {
        "artifactType": "threejs-sculpt-render-snapshot",
        "version": 1,
        "views": snapshots,
    }


def _verified_previous_render_evidence(attempt: dict[str, Any]) -> dict[str, Any]:
    snapshot = attempt.get("renderSnapshot")
    if isinstance(snapshot, dict):
        if snapshot.get("artifactType") != "threejs-sculpt-render-snapshot":
            raise ValueError("previous render snapshot artifact type is invalid")
        if snapshot.get("version") != 1:
            raise ValueError("previous render snapshot version is invalid")
        views = snapshot.get("views")
        if not isinstance(views, list) or not views:
            raise ValueError("previous render snapshot has no views")
        for view in views:
            if not isinstance(view, dict):
                raise ValueError("previous render snapshot contains an invalid view")
            path = Path(str(view.get("renderScreenshot") or "")).expanduser()
            expected_hash = view.get("renderSha256")
            if not path.is_file() or not isinstance(expected_hash, str) or file_sha256(path) != expected_hash:
                raise ValueError("previous immutable render snapshot is missing or changed")
        return snapshot

    # Compatibility for attempts recorded before immutable snapshots existed.
    previous_evidence_path = Path(str(attempt.get("evidenceManifest"))).expanduser()
    if (
        not previous_evidence_path.is_file()
        or attempt.get("evidenceSha256") != file_sha256(previous_evidence_path)
    ):
        raise ValueError("previous evidence artifact changed after its review")
    previous_evidence = read_object(previous_evidence_path, "previous visual evidence manifest")
    previous_integrity = visual_evidence_integrity_failures(previous_evidence)
    if previous_integrity:
        raise ValueError("; ".join(previous_integrity))
    return previous_evidence


def _render_pixel_delta(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, float]:
    from make_visual_comparison_sheet import resize_contain

    previous_views = {
        view.get("viewId"): view
        for view in previous.get("views", [])
        if isinstance(view, dict) and isinstance(view.get("viewId"), str)
    }
    current_views = {
        view.get("viewId"): view
        for view in current.get("views", [])
        if isinstance(view, dict) and isinstance(view.get("viewId"), str)
    }
    maximum_mean_delta = 0.0
    maximum_changed_fraction = 0.0
    compared = 0
    for view_id in sorted(set(previous_views) & set(current_views)):
        old_path = Path(str(previous_views[view_id].get("renderScreenshot"))).expanduser()
        new_path = Path(str(current_views[view_id].get("renderScreenshot"))).expanduser()
        old_w, old_h, old_pixels = load_image(old_path)
        new_w, new_h, new_pixels = load_image(new_path)
        old_panel = resize_contain(old_w, old_h, old_pixels, 128, 128)
        new_panel = resize_contain(new_w, new_h, new_pixels, 128, 128)
        absolute_sum = 0
        changed = 0
        for old_pixel, new_pixel in zip(old_panel, new_panel):
            channel_deltas = [abs(first - second) for first, second in zip(old_pixel, new_pixel)]
            absolute_sum += sum(channel_deltas)
            changed += int(max(channel_deltas) >= 8)
        sample_count = max(1, len(old_panel))
        maximum_mean_delta = max(
            maximum_mean_delta,
            absolute_sum / (sample_count * 3 * 255),
        )
        maximum_changed_fraction = max(maximum_changed_fraction, changed / sample_count)
        compared += 1
    return {
        "comparedViews": float(compared),
        "maximumMeanAbsoluteDelta": maximum_mean_delta,
        "maximumChangedPixelFraction": maximum_changed_fraction,
    }


def _reported_quality_improved(previous: dict[str, Any], verdict: dict[str, Any]) -> bool:
    old_overall = previous.get("overallScore")
    new_overall = verdict.get("overallScore")
    if _is_score(old_overall) and _is_score(new_overall) and float(new_overall) >= float(old_overall) + 0.01:
        return True
    old_layers = previous.get("layerScores") if isinstance(previous.get("layerScores"), dict) else {}
    new_layers = verdict.get("layerScores") if isinstance(verdict.get("layerScores"), dict) else {}
    return any(
        _is_score(old_layers.get(layer))
        and _is_score(value)
        and float(value) >= float(old_layers[layer]) + 0.01
        for layer, value in new_layers.items()
    )


def _normalize_lineage_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def _issue_lineage_keys(
    issues: Any,
    corrections: Any,
) -> set[str]:
    """Derive semantic defect identity independently of reviewer-chosen IDs/root keys."""

    correction_paths: dict[str, list[str]] = {}
    if isinstance(corrections, list):
        for correction in corrections:
            if not isinstance(correction, dict) or not isinstance(correction.get("issueId"), str):
                continue
            correction_paths.setdefault(correction["issueId"], []).append(
                _normalize_lineage_text(correction.get("parameterPath"))
            )
    keys: set[str] = set()
    if not isinstance(issues, list):
        return keys
    for issue in issues:
        if (
            not isinstance(issue, dict)
            or issue.get("status") != "open"
        ):
            continue
        issue_id = issue.get("id")
        payload = {
            "failureClass": _normalize_lineage_text(issue.get("failureClass")),
            "target": _normalize_lineage_text(issue.get("target")),
            "parameterPaths": sorted(
                path for path in correction_paths.get(str(issue_id), []) if path
            ),
        }
        keys.add(
            hashlib.sha256(
                repr(sorted(payload.items())).encode("utf-8")
            ).hexdigest()
        )
    return keys


def _latest_pending_refinement_attempt(
    attempts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for attempt in reversed(attempts):
        if not isinstance(attempt, dict):
            continue
        if attempt.get("accepted") is True or attempt.get("action") in {
            "stop",
            STRATEGY_RESET_ACTION,
        }:
            return None
        if attempt.get("action") == "request-input":
            continue
        if attempt.get("action") in REFINEMENT_ACTIONS:
            return attempt
    return None


def _active_refinement_cycle(attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active: list[dict[str, Any]] = []
    for attempt in reversed(attempts):
        if not isinstance(attempt, dict):
            continue
        if attempt.get("accepted") is True or attempt.get("action") in {
            "stop",
            STRATEGY_RESET_ACTION,
        }:
            break
        active.append(attempt)
    return list(reversed(active))


def _pending_strategy_reset(attempts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return a reset until one materially changed render consumes it."""

    for attempt in reversed(attempts):
        if not isinstance(attempt, dict):
            continue
        action = attempt.get("action")
        if attempt.get("accepted") is True or action in {"request-input", "stop"}:
            return None
        if action in REFINEMENT_ACTIONS:
            return None
        if action == STRATEGY_RESET_ACTION:
            return attempt
    return None


def _pending_request_input(attempts: list[dict[str, Any]]) -> dict[str, Any] | None:
    for attempt in reversed(attempts):
        if not isinstance(attempt, dict):
            continue
        action = attempt.get("action")
        if attempt.get("accepted") is True or action in {
            "stop",
            STRATEGY_RESET_ACTION,
            *REFINEMENT_ACTIONS,
        }:
            return None
        if action == "request-input":
            return attempt
    return None


def _attempt_correction_batch(attempt: dict[str, Any]) -> dict[str, Any]:
    batch = attempt.get("correctionBatch")
    if isinstance(batch, dict) and batch.get("correctionCount", 0) > 0:
        return batch
    return correction_batch_from_verdict(
        {
            "reviewId": attempt.get("reviewId"),
            "action": attempt.get("action"),
            "issues": attempt.get("issues", []),
            "corrections": attempt.get("corrections", []),
        }
    )


def _refinement_preflight_failures(
    attempts: list[dict[str, Any]],
    module_hash: str,
    representation_signature: str,
    implementation_semantic_files: dict[str, str],
    evidence: dict[str, Any],
) -> list[str]:
    pending_input = _pending_request_input(attempts)
    if pending_input is not None:
        requested_views = {
            item.get("missingViewId")
            for item in pending_input.get("requiredEvidence", [])
            if isinstance(item, dict) and isinstance(item.get("missingViewId"), str)
        }
        current_views = {
            view.get("viewId")
            for view in evidence.get("views", [])
            if isinstance(view, dict)
            and isinstance(view.get("viewId"), str)
            and isinstance(view.get("referenceProvenance"), dict)
            and view["referenceProvenance"].get("origin") == "observed"
        }
        missing_views = requested_views - current_views
        if missing_views:
            return [
                "request-input remains blocked by missing observed views: "
                + ", ".join(sorted(missing_views))
            ]
        # New observed evidence unlocks evaluation, but does not erase batch usage
        # or semantic lineage from the current strategy.
        return []
    previous = _latest_pending_refinement_attempt(attempts)
    strategy_reset = _pending_strategy_reset(attempts)
    if previous is None and strategy_reset is None:
        return []
    if previous is None and strategy_reset is not None:
        failures: list[str] = []
        if strategy_reset.get("representationSignature") == representation_signature:
            failures.append(
                "strategy-reset requires a different topology/geometry representation before rendering"
            )
        if strategy_reset.get("comparisonSha256") == evidence.get("comparisonSha256"):
            failures.append("strategy-reset reused the previous comparison image")
        if strategy_reset.get("renderSha256") == _render_hashes(evidence):
            failures.append("strategy-reset produced no new render artifact")
        return failures
    assert previous is not None
    batch = _attempt_correction_batch(previous)
    scopes = set(_strings(batch.get("scopes")))
    failures: list[str] = []
    if "spec" in scopes and previous.get("moduleHash") == module_hash:
        failures.append(
            "pending correction batch requires a module spec change before rendering"
        )
    if (
        "code" in scopes
        and previous.get("implementationSemanticFiles") == implementation_semantic_files
    ):
        failures.append(
            "pending correction batch requires an executable code change before rendering"
        )
    if previous.get("comparisonSha256") == evidence.get("comparisonSha256"):
        failures.append("pending correction batch reused the previous comparison image")
    if previous.get("renderSha256") == _render_hashes(evidence):
        failures.append("pending correction batch produced no new render artifact")
    try:
        previous_evidence = _verified_previous_render_evidence(previous)
        delta = _render_pixel_delta(previous_evidence, evidence)
    except (OSError, ValueError, TypeError) as exc:
        failures.append(f"refinement pixel delta could not be verified: {exc}")
    else:
        if delta["comparedViews"] < 1:
            failures.append("refinement has no matching before/after render views")
        elif (
            delta["maximumMeanAbsoluteDelta"] < 0.003
            and delta["maximumChangedPixelFraction"] < 0.01
        ):
            failures.append(
                "refinement render delta is below the perceptible-change floor "
                f"(mean={delta['maximumMeanAbsoluteDelta']:.5f}, "
                f"changed={delta['maximumChangedPixelFraction']:.5f})"
            )
    return list(dict.fromkeys(failures))


def _refinement_delta_failures(
    attempts: list[dict[str, Any]],
    verdict: dict[str, Any],
) -> list[str]:
    previous = _latest_pending_refinement_attempt(attempts)
    if previous is None:
        return []
    failures: list[str] = []
    if not _reported_quality_improved(previous, verdict):
        failures.append("refinement did not improve any independently reviewed quality score")
    previous_blockers = {
        issue.get("rootCauseKey")
        for attempt in _active_refinement_cycle(attempts)
        if isinstance(attempt, dict) and attempt.get("accepted") is not True
        for issue in attempt.get("issues", [])
        if isinstance(issue, dict)
        and issue.get("status") == "open"
        and issue.get("severity") in BLOCKING_SEVERITIES
        and isinstance(issue.get("rootCauseKey"), str)
    }
    resolved_root_causes = set(_strings(verdict.get("resolvedRootCauseKeys")))
    unresolved = previous_blockers - resolved_root_causes
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
        and issue.get("rootCauseKey") in resolved_root_causes
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
        and issue.get("severity") in BLOCKING_SEVERITIES
        and isinstance(issue.get("rootCauseKey"), str)
    }
    new_blockers = current_blockers - previous_blockers
    if new_blockers:
        failures.append(
            "a new blocking root cause cannot be introduced within the same strategy; "
            "record strategy-reset before changing defect identity: "
            + ", ".join(sorted(new_blockers))
        )
    previous_lineages: set[str] = set()
    for attempt in _active_refinement_cycle(attempts):
        if not isinstance(attempt, dict) or attempt.get("accepted") is True:
            continue
        stored = set(_strings(attempt.get("issueLineageKeys")))
        previous_lineages.update(
            stored
            or _issue_lineage_keys(
                attempt.get("issues", []),
                attempt.get("corrections", []),
            )
        )
    current_lineages = _issue_lineage_keys(
        verdict.get("issues", []),
        verdict.get("corrections", []),
    )
    repeated_lineages = previous_lineages & current_lineages
    if repeated_lineages:
        failures.append(
            "a blocking defect remains open under the same canonical issue lineage"
        )
    return failures


def _module_preflight_context(
    manifest_path: Path,
    module_id: str,
    evidence_path: Path,
    implementation_files: list[Path] | None = None,
    verify_evidence: bool = True,
) -> dict[str, Any]:
    path = manifest_path.expanduser().resolve()
    manifest = read_object(path, "manifest JSON")
    entries = entry_by_id(manifest)
    if module_id not in entries:
        raise ValueError(f"unknown module {module_id!r}")
    entry = entries[module_id]
    if entry.get("gateType") != "visual":
        raise ValueError("structural modules use `sculpt module accept`; visual modules use review")
    before = module_status(path, manifest)
    if before.get("currentModule") != module_id:
        raise ValueError(
            "only the current highest-risk ready module may be reviewed; "
            f"current={before.get('currentModule')!r}"
        )
    checked = check_module(path, module_id, strict_quality=True)
    if not checked["ok"]:
        raise ValueError("module check failed: " + "; ".join(checked["errors"]))
    resolved_evidence_path = evidence_path.expanduser().resolve()
    evidence = read_object(resolved_evidence_path, "visual evidence manifest")
    module = load_modules(path, manifest, [module_id])[module_id][1]
    gate = module.get("qualityGate") if isinstance(module.get("qualityGate"), dict) else {}
    required_views = set(_strings(gate.get("requiredViews")))
    diagnostic_views = set(_strings(gate.get("diagnosticViews")))
    failures: list[str] = []
    if verify_evidence:
        failures.extend(visual_evidence_integrity_failures(evidence))
        failures.extend(visual_evidence_authority_failures(evidence, required_views))
        if diagnostic_views:
            from sculpt_view_hypotheses import hypothesis_evidence_failures

            global_spec = manifest.get("globalSpec") if isinstance(manifest.get("globalSpec"), dict) else {}
            failures.extend(
                hypothesis_evidence_failures(
                    path,
                    global_spec,
                    evidence,
                    diagnostic_views,
                )
            )
    declared_implementation = implementation_contract_paths(path, module)
    if implementation_files is not None:
        root = path.parent.resolve()
        supplied = {
            (
                (root / item.expanduser()).resolve()
                if not item.expanduser().is_absolute()
                else item.expanduser().resolve()
            )
            for item in implementation_files
        }
        if supplied != set(declared_implementation):
            raise ValueError(
                "supplied implementation files must exactly match module contract.implementationFiles"
            )
    implementation_hashes = _implementation_hashes(declared_implementation)
    semantic_implementation_hashes = implementation_semantic_hashes(declared_implementation)
    cache = _load_cache(path)
    attempts_by_module = cache.get("reviewAttempts", {}) if isinstance(cache, dict) else {}
    attempts = (
        attempts_by_module.get(module_id, [])
        if isinstance(attempts_by_module, dict)
        else []
    )
    if not isinstance(attempts, list):
        attempts = []
    pending_attempt = _latest_pending_refinement_attempt(attempts)
    pending_batch = _attempt_correction_batch(pending_attempt) if pending_attempt else {}
    representation_signature = module_representation_signature(
        manifest,
        module_id,
        module,
    )
    if verify_evidence:
        failures.extend(
            _render_provenance_failures(
                evidence,
                manifest,
                module,
                module_id,
                str(checked.get("moduleHash") or ""),
                implementation_hashes,
                semantic_implementation_hashes,
                path,
            )
        )
        failures.extend(diagnostic_veto_failures(manifest, module, evidence))
        failures.extend(
            _refinement_preflight_failures(
                attempts,
                str(checked.get("moduleHash") or ""),
                representation_signature,
                semantic_implementation_hashes,
                evidence,
            )
        )
    return {
        "path": path,
        "manifest": manifest,
        "entry": entry,
        "checked": checked,
        "evidencePath": resolved_evidence_path,
        "evidence": evidence,
        "module": module,
        "gate": gate,
        "requiredViews": required_views,
        "implementationFiles": implementation_hashes,
        "implementationSemanticFiles": semantic_implementation_hashes,
        "representationSignature": representation_signature,
        "evidenceFiles": _evidence_file_snapshot(evidence),
        "pendingCorrectionBatch": pending_batch,
        "refinementBudget": refinement_budget(attempts),
        "failures": list(dict.fromkeys(failures)),
    }


def preflight_module_review(
    manifest_path: Path,
    module_id: str,
    evidence_path: Path,
    implementation_files: list[Path] | None = None,
) -> dict[str, Any]:
    """Return the cheap fail-closed result that must pass before spawning a reviewer."""
    context = _module_preflight_context(
        manifest_path,
        module_id,
        evidence_path,
        implementation_files,
    )
    ok = not context["failures"]
    now = datetime.now(timezone.utc).isoformat()
    cache = _load_cache(context["path"])
    cache["version"] = 2
    preflights = cache.setdefault("reviewPreflights", {})
    if not isinstance(preflights, dict):
        preflights = {}
        cache["reviewPreflights"] = preflights
    preflights[module_id] = {
        "artifactType": MODULE_PREFLIGHT_ARTIFACT_TYPE,
        "version": MODULE_PREFLIGHT_VERSION,
        "ok": ok,
        "moduleId": module_id,
        "moduleHash": context["checked"].get("moduleHash"),
        "evidenceManifest": str(context["evidencePath"]),
        "evidenceSha256": file_sha256(context["evidencePath"]),
        "comparisonSha256": context["evidence"].get("comparisonSha256"),
        "implementationFiles": context["implementationFiles"],
        "implementationSemanticFiles": context["implementationSemanticFiles"],
        "evidenceFiles": context["evidenceFiles"],
        "recordedAt": now,
        "failures": context["failures"],
    }
    cache["updatedAt"] = now
    write_spec_atomic(cache_path(context["path"]), cache)
    return {
        "ok": ok,
        "moduleId": module_id,
        "moduleHash": context["checked"].get("moduleHash"),
        "comparisonSha256": context["evidence"].get("comparisonSha256"),
        "pendingCorrectionBatch": context["pendingCorrectionBatch"],
        "refinementBudget": context["refinementBudget"],
        "failures": context["failures"],
    }


def _module_preflight_receipt(
    manifest_path: Path,
    module_id: str,
    evidence_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    path = manifest_path.expanduser().resolve()
    resolved_evidence = evidence_path.expanduser().resolve()
    cache = _load_cache(path)
    preflights = cache.get("reviewPreflights") if isinstance(cache, dict) else None
    receipt = preflights.get(module_id) if isinstance(preflights, dict) else None
    failures: list[str] = []
    if not isinstance(receipt, dict):
        return cache, {}, ["run a passing `sculpt module preflight` before creating/reusing a reviewer verdict"]
    if receipt.get("artifactType") != MODULE_PREFLIGHT_ARTIFACT_TYPE:
        failures.append("module preflight receipt artifact type is invalid")
    if receipt.get("version") != MODULE_PREFLIGHT_VERSION:
        failures.append(f"module preflight receipt version must be {MODULE_PREFLIGHT_VERSION}")
    if receipt.get("ok") is not True:
        failures.append("latest module preflight did not pass")
    if receipt.get("moduleId") != module_id:
        failures.append("module preflight receipt is bound to another module")
    if receipt.get("evidenceManifest") != str(resolved_evidence):
        failures.append("module preflight receipt is bound to another evidence manifest")
    if not resolved_evidence.is_file() or receipt.get("evidenceSha256") != file_sha256(resolved_evidence):
        failures.append("module evidence changed after preflight")
    return cache, receipt, list(dict.fromkeys(failures))


def review_module(
    manifest_path: Path,
    module_id: str,
    verdict_path: Path,
    evidence_path: Path,
    implementation_files: list[Path] | None = None,
) -> dict[str, Any]:
    cache, preflight_receipt, receipt_failures = _module_preflight_receipt(
        manifest_path,
        module_id,
        evidence_path,
    )
    if receipt_failures:
        raise ValueError("module review requires a current passing preflight receipt: " + "; ".join(receipt_failures))
    context = _module_preflight_context(
        manifest_path,
        module_id,
        evidence_path,
        implementation_files,
        verify_evidence=False,
    )
    if context["failures"]:
        raise ValueError("module review preflight failed: " + "; ".join(context["failures"]))
    current_receipt_contract = {
        "moduleHash": context["checked"].get("moduleHash"),
        "comparisonSha256": context["evidence"].get("comparisonSha256"),
        "implementationFiles": context["implementationFiles"],
        "implementationSemanticFiles": context["implementationSemanticFiles"],
        "evidenceFiles": context["evidenceFiles"],
    }
    stale_fields = [
        field
        for field, value in current_receipt_contract.items()
        if preflight_receipt.get(field) != value
    ]
    if stale_fields:
        raise ValueError(
            "module review requires a fresh preflight; changed fields: "
            + ", ".join(stale_fields)
        )
    path = context["path"]
    manifest = context["manifest"]
    entry = context["entry"]
    checked = context["checked"]
    resolved_evidence_path = context["evidencePath"]
    evidence = context["evidence"]
    module = context["module"]
    gate = context["gate"]
    required_views = context["requiredViews"]
    implementation_hashes = context["implementationFiles"]
    semantic_implementation_hashes = context["implementationSemanticFiles"]
    resolved_verdict_path = verdict_path.expanduser().resolve()
    verdict = read_object(resolved_verdict_path, "module review verdict")
    contract_failures = _review_contract_failures(verdict, evidence)
    if contract_failures:
        raise ValueError("invalid module review verdict: " + "; ".join(contract_failures))
    cache["version"] = 2
    attempts_by_module = cache.setdefault("reviewAttempts", {})
    attempts = attempts_by_module.setdefault(module_id, [])
    if not isinstance(attempts, list):
        attempts = []
        attempts_by_module[module_id] = attempts
    if any(attempt.get("reviewId") == verdict.get("reviewId") for attempt in attempts if isinstance(attempt, dict)):
        raise ValueError(f"reviewId {verdict.get('reviewId')!r} has already been recorded")

    action = str(verdict.get("action"))
    budget = refinement_budget(attempts)
    if action in REFINEMENT_ACTIONS and budget["exhausted"]:
        raise ValueError(
            "atomic refinement budget is exhausted; record one strategy-reset with a "
            "different representation before any further refinement"
        )
    if action == STRATEGY_RESET_ACTION:
        if budget.get("remainingStrategyResets", 0) < 1:
            raise ValueError(
                "strategy-reset budget is exhausted; only concrete missing evidence or a "
                "verified capability limit may pause the task"
            )
        active_root_causes = {
            issue.get("rootCauseKey")
            for attempt in _active_refinement_cycle(attempts)
            if isinstance(attempt, dict)
            for issue in attempt.get("issues", [])
            if isinstance(issue, dict)
            and issue.get("status") == "open"
            and issue.get("severity") in BLOCKING_SEVERITIES
            and isinstance(issue.get("rootCauseKey"), str)
        }
        declared_root_causes = set(_strings(verdict.get("rootCauseKeys")))
        if not active_root_causes:
            raise ValueError("strategy-reset requires a failed refinement cycle")
        if not declared_root_causes or not declared_root_causes <= active_root_causes:
            raise ValueError(
                "strategy-reset rootCauseKeys must reference blockers from the active failed cycle"
            )
    if action in REFINEMENT_ACTIONS and _latest_pending_refinement_attempt(attempts) is not None:
        progress_failures = _refinement_delta_failures(attempts, verdict)
        if progress_failures:
            raise ValueError(
                "another refinement batch requires independently measured progress and closure "
                "of prior blockers; complete the batch or use strategy-reset after budget exhaustion: "
                + "; ".join(progress_failures)
            )
    correction_batch = correction_batch_from_verdict(verdict)
    render_snapshot = (
        _snapshot_refinement_renders(
            path,
            module_id,
            verdict.get("reviewId"),
            evidence,
        )
        if action in REFINEMENT_ACTIONS
        else {}
    )
    quality_failures: list[str] = []
    if action == "continue":
        quality_failures.extend(
            _continue_gate_failures(
                manifest,
                module,
                entry,
                evidence,
                verdict,
                diagnostics_preflighted=True,
            )
        )
        quality_failures.extend(
            _refinement_delta_failures(
                attempts,
                verdict,
            )
        )
    accepted = action == "continue" and not quality_failures
    now = datetime.now(timezone.utc).isoformat()
    attempt = {
        "attempt": len(attempts) + 1,
        "reviewId": verdict.get("reviewId"),
        "action": action,
        "accepted": accepted,
        "recordedAt": now,
        "moduleHash": checked.get("moduleHash"),
        "implementationFiles": implementation_hashes,
        "implementationSemanticFiles": semantic_implementation_hashes,
        "representationSignature": context["representationSignature"],
        "reviewVerdict": str(resolved_verdict_path),
        "reviewVerdictSha256": file_sha256(resolved_verdict_path),
        "evidenceManifest": str(resolved_evidence_path),
        "evidenceSha256": file_sha256(resolved_evidence_path),
        "comparisonSha256": evidence.get("comparisonSha256"),
        "renderSha256": _render_hashes(evidence),
        "renderSnapshot": render_snapshot,
        "reviewer": verdict.get("reviewer"),
        "overallScore": verdict.get("overallScore"),
        "layerScores": verdict.get("layerScores", {}),
        "featureReviews": verdict.get("featureReviews", []),
        "issues": verdict.get("issues", []),
        "corrections": verdict.get("corrections", []),
        "issueLineageKeys": sorted(
            _issue_lineage_keys(
                verdict.get("issues", []),
                verdict.get("corrections", []),
            )
        ),
        "correctionBatch": correction_batch,
        "resolvedIssueIds": verdict.get("resolvedIssueIds", []),
        "resolvedRootCauseKeys": verdict.get("resolvedRootCauseKeys", []),
        "strategyId": verdict.get("strategyId"),
        "strategyChange": verdict.get("strategyChange"),
        "rootCauseKeys": verdict.get("rootCauseKeys", []),
        "falsifyingCheck": verdict.get("falsifyingCheck"),
        "requiredEvidence": verdict.get("requiredEvidence", []),
        "stopReason": verdict.get("stopReason"),
        "stopEvidence": verdict.get("stopEvidence", []),
        "summary": verdict.get("summary"),
        "failures": quality_failures,
    }
    attempts.append(attempt)
    preflights = cache.get("reviewPreflights")
    if isinstance(preflights, dict):
        preflights.pop(module_id, None)
    records = cache.setdefault("modules", {})
    if accepted:
        records[module_id] = {
            "moduleHash": checked.get("moduleHash"),
            "interfaceHash": interface_hash(module),
            "gateType": "visual",
            "score": verdict.get("overallScore"),
            "layerScores": verdict.get("layerScores", {}),
            "notes": verdict.get("summary", ""),
            "threshold": gate.get("minimumScore"),
            "evidenceManifest": str(resolved_evidence_path),
            "evidenceSha256": file_sha256(resolved_evidence_path),
            "comparisonSha256": evidence.get("comparisonSha256"),
            "reviewVerdict": str(resolved_verdict_path),
            "reviewVerdictSha256": file_sha256(resolved_verdict_path),
            "reviewerModel": verdict.get("reviewer", {}).get("model", ""),
            "reviewerContextId": verdict.get("reviewer", {}).get("contextId", ""),
            "builderContextId": verdict.get("builder", {}).get("contextId", ""),
            "implementationFiles": implementation_hashes,
            "implementationSemanticFiles": semantic_implementation_hashes,
            "requiredViews": sorted(required_views),
            "acceptedAt": now,
            "reviewId": verdict.get("reviewId"),
        }
    else:
        records.pop(module_id, None)
    cache["updatedAt"] = now
    write_spec_atomic(cache_path(path), cache)
    status = module_status(path, manifest)
    status.update(
        {
            "reviewAccepted": accepted,
            "reviewAction": action,
            "reviewFailures": quality_failures,
            "reviewAttempt": len(attempts),
            "pendingCorrectionBatch": correction_batch,
        }
    )
    return status
