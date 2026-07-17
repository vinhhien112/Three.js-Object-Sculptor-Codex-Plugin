"""Schema and interface checks for composable sculpt modules."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any


MANIFEST_SCHEMA_VERSION = "4.0"
MODULE_SCHEMA_VERSION = "4.0-module"
MODULE_BUILD_RECEIPT_ARTIFACT_TYPE = "threejs-sculpt-module-build-receipt"
MODULE_BUILD_RECEIPT_VERSION = 1
MODULE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
RISK_TIERS = {"low", "medium", "high", "critical"}
GATE_TYPES = {"visual", "structural"}
DIAGNOSTIC_THRESHOLD_FIELDS = {
    "minimumSilhouetteIou",
    "maximumCentroidDelta",
    "maximumAspectRatioDelta",
    "minimumDetailEnergyRatio",
    "minimumEdgeDensityRatio",
    "minimumHistogramIntersection",
    "maximumMeanColorDelta",
    "minimumHighlightCoverageRatio",
    "minimumHighlightEnergyRatio",
}
SEGMENTED_FIELDS = (
    "componentTree",
    "materials",
    "repetitionSystems",
    "featureReviewTargets",
    "viewEvidence",
)


def module_build_receipt_path(manifest_path: Path, module_id: str) -> Path:
    """Canonical build receipt consumed by render/runtime attestation."""

    if MODULE_ID_PATTERN.fullmatch(module_id) is None:
        raise ValueError(f"module id must match {MODULE_ID_PATTERN.pattern}")
    return manifest_path.expanduser().resolve().parent / ".sculpt-preview" / f"{module_id}.build.json"


def module_path(manifest_path: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("module path must be a non-empty relative path")
    relative = Path(value)
    if relative.is_absolute():
        raise ValueError("module path must be relative to the manifest")
    root = manifest_path.parent.resolve()
    resolved = (root / relative).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"module path escapes the manifest directory: {value!r}")
    return resolved


def ids(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {
        str(item.get("id"))
        for item in value
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def manifest_errors(
    manifest_path: Path,
    manifest: dict[str, Any],
    *,
    require_files: bool = False,
) -> list[str]:
    errors: list[str] = []
    if manifest.get("schemaVersion") != MANIFEST_SCHEMA_VERSION:
        errors.append(f"manifest schemaVersion must be {MANIFEST_SCHEMA_VERSION!r}")
    if not isinstance(manifest.get("globalSpec"), dict):
        errors.append("manifest globalSpec must be an object")
    if not isinstance(manifest.get("assemblyContract"), dict):
        errors.append("manifest assemblyContract must be an object")
    global_spec = manifest.get("globalSpec")
    if isinstance(global_spec, dict):
        if not isinstance(global_spec.get("surfaceTopologyPlan"), dict):
            errors.append("manifest globalSpec.surfaceTopologyPlan must be an object")
        root_component_id = (
            manifest.get("assemblyContract", {}).get("rootComponentId")
            if isinstance(manifest.get("assemblyContract"), dict)
            else None
        )
        global_components = global_spec.get("componentTree", [])
        if (
            not isinstance(global_components, list)
            or len(global_components) != 1
            or not isinstance(global_components[0], dict)
            or global_components[0].get("id") != root_component_id
            or global_components[0].get("componentType") != "assembly"
            or isinstance(global_components[0].get("geometryDescriptor"), dict)
            or global_components[0].get("material") not in (None, "")
        ):
            errors.append(
                "manifest globalSpec.componentTree may contain only the geometry-free assembly root"
            )
        for field in ("materials", "repetitionSystems"):
            if global_spec.get(field) not in (None, []):
                errors.append(
                    f"manifest globalSpec.{field} must stay empty; visible payload belongs to visual modules"
                )
    quality_contract = global_spec.get("qualityContract") if isinstance(global_spec, dict) else None
    feature_groups = quality_contract.get("featureGroups") if isinstance(quality_contract, dict) else []
    known_feature_groups = ids(feature_groups)
    coverage_contract = manifest.get("coverageContract")
    assembly_feature_groups: list[str] = []
    if coverage_contract is not None and not isinstance(coverage_contract, dict):
        errors.append("manifest coverageContract must be an object")
    elif isinstance(coverage_contract, dict):
        raw_assembly_groups = coverage_contract.get("assemblyFeatureGroups", [])
        if not isinstance(raw_assembly_groups, list) or not all(
            isinstance(item, str) and item for item in raw_assembly_groups
        ):
            errors.append("manifest coverageContract.assemblyFeatureGroups must contain feature-group ids")
        else:
            if len(set(raw_assembly_groups)) != len(raw_assembly_groups):
                errors.append("manifest coverageContract.assemblyFeatureGroups contains duplicates")
            assembly_feature_groups = raw_assembly_groups
            unknown_assembly_groups = sorted(set(assembly_feature_groups) - known_feature_groups)
            if unknown_assembly_groups:
                errors.append(
                    "manifest assembly coverage contains unknown feature groups: "
                    + ", ".join(unknown_assembly_groups)
                )
            feature_targets = {
                item.get("id"): item
                for item in global_spec.get("featureReviewTargets", [])
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            } if isinstance(global_spec, dict) else {}
            for feature_id in assembly_feature_groups:
                target = feature_targets.get(feature_id)
                if not isinstance(target, dict) or not (
                    target.get("tier") == "critical" or target.get("mustPass") is True
                ):
                    errors.append(
                        f"assembly feature group {feature_id!r} needs a matching critical global featureReviewTarget"
                    )
    modules = manifest.get("modules")
    if not isinstance(modules, list):
        return errors + ["manifest modules must be an array"]
    topology_groups = (
        global_spec.get("surfaceTopologyPlan", {}).get("groups", [])
        if isinstance(global_spec, dict)
        and isinstance(global_spec.get("surfaceTopologyPlan"), dict)
        else []
    )
    topology_owners = (
        {
            group.get("ownerModuleId")
            for group in topology_groups
            if isinstance(group, dict) and isinstance(group.get("ownerModuleId"), str)
        }
        if isinstance(topology_groups, list)
        else set()
    )
    module_ids: set[str] = set()
    dependency_map: dict[str, list[str]] = {}
    for index, entry in enumerate(modules):
        label = f"modules[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        module_id = entry.get("id")
        if not isinstance(module_id, str) or MODULE_ID_PATTERN.fullmatch(module_id) is None:
            errors.append(f"{label}.id must match {MODULE_ID_PATTERN.pattern}")
            continue
        if module_id in module_ids:
            errors.append(f"duplicate module id {module_id!r}")
        module_ids.add(module_id)
        try:
            path = module_path(manifest_path, entry.get("path"))
            if require_files and entry.get("required", True) is True and not path.is_file():
                errors.append(f"required module {module_id!r} is missing: {path}")
        except ValueError as exc:
            errors.append(f"{label}: {exc}")
        score = entry.get("riskScore")
        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not math.isfinite(float(score))
            or not 0 <= float(score) <= 100
        ):
            errors.append(f"{label}.riskScore must be a finite number from 0 to 100")
        if entry.get("riskTier") not in RISK_TIERS:
            errors.append(f"{label}.riskTier must be one of: {', '.join(sorted(RISK_TIERS))}")
        if entry.get("gateType") not in GATE_TYPES:
            errors.append(f"{label}.gateType must be visual or structural")
        elif entry.get("gateType") == "visual" and module_id not in topology_owners:
            errors.append(
                f"visual module {module_id!r} must own at least one surfaceTopologyPlan group"
            )
        covers = entry.get("covers", [])
        if not isinstance(covers, list) or not all(isinstance(item, str) and item for item in covers):
            errors.append(f"{label}.covers must be an array of global feature-group ids")
        else:
            if len(set(covers)) != len(covers):
                errors.append(f"{label}.covers contains duplicates")
            unknown_coverage = sorted(set(covers) - known_feature_groups)
            if unknown_coverage:
                errors.append(
                    f"{label}.covers contains unknown global feature groups: "
                    + ", ".join(unknown_coverage)
                )
            if covers and entry.get("gateType") != "visual":
                errors.append(f"{label}.covers requires a visual module gate")
        dependencies = entry.get("dependsOn", [])
        if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
            errors.append(f"{label}.dependsOn must be an array of module ids")
            dependencies = []
        dependency_map[module_id] = list(dependencies)
    for module_id, dependencies in dependency_map.items():
        for dependency in dependencies:
            if dependency not in module_ids:
                errors.append(f"module {module_id!r} depends on unknown module {dependency!r}")
    visited: set[str] = set()
    active: list[str] = []

    def visit(module_id: str) -> None:
        if module_id in active:
            cycle = active[active.index(module_id) :] + [module_id]
            errors.append("module dependency cycle: " + " -> ".join(cycle))
            return
        if module_id in visited:
            return
        active.append(module_id)
        for dependency in dependency_map.get(module_id, []):
            visit(dependency)
        active.pop()
        visited.add(module_id)

    for module_id in dependency_map:
        visit(module_id)
    return list(dict.fromkeys(errors))


def module_document_errors(
    module_id: str,
    module: dict[str, Any],
    entry: dict[str, Any],
    dependencies: dict[str, dict[str, Any]],
) -> list[str]:
    """Validate ownership and only the cross-module interface, not implementation style."""
    errors: list[str] = []
    if module.get("moduleId") != module_id:
        errors.append(f"module {module_id!r} declares a different moduleId")
    if module.get("dependsOn", []) != entry.get("dependsOn", []):
        errors.append(f"module {module_id!r} dependsOn differs from its manifest entry")
    risk = module.get("risk")
    if not isinstance(risk, dict):
        errors.append(f"module {module_id!r} risk must be an object")
    else:
        if risk.get("tier") != entry.get("riskTier"):
            errors.append(f"module {module_id!r} risk tier differs from its manifest entry")
        if risk.get("score") != entry.get("riskScore"):
            errors.append(f"module {module_id!r} risk score differs from its manifest entry")
    gate = module.get("qualityGate")
    if not isinstance(gate, dict):
        errors.append(f"module {module_id!r} qualityGate must be an object")
    else:
        if gate.get("type") != entry.get("gateType"):
            errors.append(f"module {module_id!r} gate type differs from its manifest entry")
        threshold = gate.get("minimumScore")
        if (
            not isinstance(threshold, (int, float))
            or isinstance(threshold, bool)
            or not 0 <= float(threshold) <= 1
        ):
            errors.append(f"module {module_id!r} qualityGate.minimumScore must be from 0 to 1")
        views = gate.get("requiredViews")
        if not isinstance(views, list) or not all(isinstance(item, str) and item for item in views):
            errors.append(f"module {module_id!r} qualityGate.requiredViews must contain view ids")
        diagnostic_views = gate.get("diagnosticViews", [])
        if not isinstance(diagnostic_views, list) or not all(
            isinstance(item, str) and item for item in diagnostic_views
        ):
            errors.append(f"module {module_id!r} qualityGate.diagnosticViews must contain view ids")
        required_scores = gate.get("requiredLayerScores")
        if not isinstance(required_scores, dict):
            errors.append(f"module {module_id!r} qualityGate.requiredLayerScores must be an object")
        else:
            if entry.get("gateType") == "visual" and not required_scores:
                errors.append(f"visual module {module_id!r} must declare required layer scores")
            for layer, minimum in required_scores.items():
                if not isinstance(layer, str) or not layer.strip() or not isinstance(
                    minimum, (int, float)
                ) or isinstance(minimum, bool) or not 0 <= float(minimum) <= 1:
                    errors.append(
                        f"module {module_id!r} qualityGate.requiredLayerScores entries "
                        "must map layer names to thresholds from 0 to 1"
                    )
        diagnostic_thresholds = gate.get("diagnosticThresholds", {})
        if not isinstance(diagnostic_thresholds, dict):
            errors.append(f"module {module_id!r} qualityGate.diagnosticThresholds must be an object")
        else:
            unknown_thresholds = sorted(set(diagnostic_thresholds) - DIAGNOSTIC_THRESHOLD_FIELDS)
            if unknown_thresholds:
                errors.append(
                    f"module {module_id!r} qualityGate.diagnosticThresholds has unknown fields: "
                    + ", ".join(unknown_thresholds)
                )
            for field, value in diagnostic_thresholds.items():
                if (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not math.isfinite(float(value))
                    or not 0 <= float(value) <= 1
                ):
                    errors.append(
                        f"module {module_id!r} qualityGate.diagnosticThresholds.{field} "
                        "must be from 0 to 1"
                    )
    payload = module.get("payload")
    contract = module.get("contract")
    if not isinstance(payload, dict):
        return errors + [f"module {module_id!r} payload must be an object"]
    if not isinstance(contract, dict):
        return errors + [f"module {module_id!r} contract must be an object"]
    implementation_files = contract.get("implementationFiles", [])
    if not isinstance(implementation_files, list) or not all(
        isinstance(item, str)
        and item.strip()
        and not Path(item).is_absolute()
        and ".." not in Path(item).parts
        for item in implementation_files
    ):
        errors.append(
            f"module {module_id!r} contract.implementationFiles must contain safe project-relative paths"
        )
    owns = contract.get("owns")
    if not isinstance(owns, dict):
        errors.append(f"module {module_id!r} contract.owns must be an object")
    else:
        field_map = {
            "components": "componentTree",
            "materials": "materials",
            "repetitionSystems": "repetitionSystems",
            "featureTargets": "featureReviewTargets",
            "specializedRegions": "specializedRegions",
        }
        for ownership_field, payload_field in field_map.items():
            declared = owns.get(ownership_field)
            if not isinstance(declared, list) or not all(isinstance(item, str) for item in declared):
                errors.append(f"module {module_id!r} contract.owns.{ownership_field} must contain ids")
                continue
            if set(declared) != ids(payload.get(payload_field, [])):
                errors.append(
                    f"module {module_id!r} contract.owns.{ownership_field} must exactly match "
                    f"payload.{payload_field} ids"
                )
    component_ids = ids(payload.get("componentTree", []))
    connector_ids: set[str] = set()
    connectors = contract.get("connectors", [])
    if not isinstance(connectors, list):
        errors.append(f"module {module_id!r} contract.connectors must be an array")
        connectors = []
    for index, connector in enumerate(connectors):
        label = f"module {module_id!r} connector[{index}]"
        if not isinstance(connector, dict):
            errors.append(f"{label} must be an object")
            continue
        connector_id = connector.get("id")
        if not isinstance(connector_id, str) or not connector_id.strip():
            errors.append(f"{label}.id is required")
        elif connector_id in connector_ids:
            errors.append(f"module {module_id!r} has duplicate connector {connector_id!r}")
        else:
            connector_ids.add(connector_id)
        if connector.get("componentRef") not in component_ids:
            errors.append(f"{label}.componentRef must be owned by this module")
        for field in ("position", "rotation"):
            vector = connector.get(field)
            if not isinstance(vector, list) or len(vector) != 3 or not all(
                isinstance(item, (int, float))
                and not isinstance(item, bool)
                and math.isfinite(float(item))
                for item in vector
            ):
                errors.append(f"{label}.{field} must contain three finite numbers")
    dependency_components: dict[str, str] = {}
    exported_connectors: dict[str, tuple[str, str]] = {}
    for dependency_id, dependency in dependencies.items():
        dependency_payload = dependency.get("payload", {})
        for component_id in ids(dependency_payload.get("componentTree", [])):
            dependency_components[component_id] = dependency_id
        dependency_contract = dependency.get("contract", {})
        dependency_connectors = (
            dependency_contract.get("connectors", [])
            if isinstance(dependency_contract, dict)
            else []
        )
        for connector in dependency_connectors:
            if isinstance(connector, dict) and isinstance(connector.get("id"), str):
                exported_connectors[str(connector["id"])] = (
                    dependency_id,
                    str(connector.get("componentRef") or ""),
                )
    for component in payload.get("componentTree", []):
        if not isinstance(component, dict):
            continue
        parent = component.get("parent")
        if not isinstance(parent, str) or parent in component_ids or parent == "root":
            continue
        if parent not in dependency_components:
            errors.append(
                f"module {module_id!r} component {component.get('id')!r} parents outside its dependencies"
            )
            continue
        attachment = component.get("attachment")
        socket = attachment.get("parentSocket") if isinstance(attachment, dict) else None
        exported = exported_connectors.get(str(socket))
        if exported != (dependency_components[parent], parent):
            errors.append(
                f"module {module_id!r} component {component.get('id')!r} must bind parent "
                f"{parent!r} through that dependency's exported connector"
            )
    return list(dict.fromkeys(errors))
