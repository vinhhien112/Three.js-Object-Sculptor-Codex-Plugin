#!/usr/bin/env python3
"""Shared geometry capabilities, validation, and TypeScript emitters.

The validator and factory generator intentionally share this registry.  A
primitive is supported only when it has a real handler here; unknown names are
never converted to a box.  Descriptor coordinates are emitted in component
local space, while legacy/basic primitives retain their unit-geometry scaling
contract in ``generate_threejs_factory.scale_vector``.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable, Mapping

from sculpt_contract import COMPONENT_TYPES, component_type


MAX_GEOMETRY_SEGMENTS = 512
MAX_PATH_POINTS = 2048
MAX_PROFILE_POINTS = 1024
MAX_INSTANCE_COUNT = 10_000
MAX_GRID_AXIS_COUNT = 1_000
MAX_DEFORMABLE_SAMPLED_VERTICES = 65_536
MAX_DEFORMABLE_FOLDS = 16
MAX_DEFORMABLE_CONTROL_POINTS = 256
MAX_FIBER_GUIDES = 128
MAX_FIBER_GUIDE_POINTS = 2_048
MAX_FIBER_STRANDS = 2_048
MAX_FIBER_SAMPLES = 32
MAX_FIBER_QUADS = 65_536
MAX_IMPLICIT_SOURCES = 32
MAX_IMPLICIT_CELLS = 32_768
MAX_SCULPT_SOURCES = 48
MAX_SCULPT_MODIFIERS = 32
MAX_SCULPT_CELLS = 250_000
MAX_SCULPT_FIELD_EVALUATIONS = 2_000_000
MAX_VOLUME_PARTICLES = 2_048
MAX_VOLUME_SOURCES = 32
MAX_LOFT_SECTIONS = 64
MAX_LOFT_VERTICES = 65_536
MAX_SHELL_OPENINGS = 24
MAX_BRANCH_NODES = 256
MAX_BRANCH_EDGES = 512
MAX_BRANCH_CONTROL_POINTS = 16
MAX_GEOMETRY_MODIFIERS = 12
MAX_SPECIAL_PARAMETER_MAGNITUDE = 1_000_000.0
MIN_IMPLICIT_FIELD_SCALE = 1e-6

SURFACE_TOPOLOGY_STRATEGIES = frozenset(
    {
        "continuous-sculpt",
        "assembled-solid",
        "conforming-shell",
        "surface-relief",
        "fiber-strand",
        "material-only",
    }
)
DETACHED_LOCAL_FEATURE_TYPES = frozenset(
    {
        "seam",
        "seam-line",
        "raised-ridge",
        "fabric-stitch",
        "button",
        "rivet",
        "screw",
        "decal",
    }
)
CONTINUOUS_SURFACE_PRIMITIVES = frozenset(
    {"sculpted-surface", "section-loft", "deformable-surface"}
)


class GeometrySpecError(ValueError):
    """Raised when a component cannot be emitted by a registered handler."""


@dataclass(frozen=True)
class GeometryEmission:
    """Sanitized code-generation result for one geometry component."""

    primitive: str
    geometry_expression: str
    dimension_mode: str = "component"
    endpoint_aware: bool = False
    mesh_kind: str = "mesh"
    helpers: frozenset[str] = frozenset()
    instance_count: int = 0
    instance_layout: Mapping[str, Any] | None = None
    instance_layout_function: str = "applyInstanceLayout"
    instance_layout_type: str = "InstanceLayoutSpec"
    is_blockout_proxy: bool = False


ParameterValidator = Callable[[Mapping[str, Any], Mapping[str, Any]], list[str]]
Emitter = Callable[
    [Mapping[str, Any], Mapping[str, Any], Mapping[str, Mapping[str, Any]]],
    GeometryEmission,
]


@dataclass(frozen=True)
class GeometryHandler:
    """One registered primitive capability."""

    primitive: str
    emitter: Emitter
    validator: ParameterValidator
    endpoint_aware: bool = False
    dimension_mode: str = "component"
    instancable: bool = True


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise GeometrySpecError(f"geometry parameters are not finite JSON data: {exc}") from exc


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _number(
    value: Any,
    fallback: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    result = float(value) if _is_number(value) else float(fallback)
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _integer(
    value: Any,
    fallback: int,
    *,
    minimum: int = 1,
    maximum: int = MAX_GEOMETRY_SEGMENTS,
) -> int:
    if _is_number(value):
        result = int(float(value))
    else:
        result = fallback
    return max(minimum, min(maximum, result))


def _boolean(value: Any, fallback: bool = False) -> bool:
    return value if isinstance(value, bool) else fallback


def _vector(
    value: Any,
    length: int,
    fallback: Iterable[float],
) -> list[float]:
    if (
        isinstance(value, list)
        and len(value) == length
        and all(_is_number(item) for item in value)
    ):
        return [float(item) for item in value]
    return [float(item) for item in fallback]


def _point_list(value: Any, dimensions: int, limit: int) -> list[list[float]]:
    if not isinstance(value, list):
        return []
    result: list[list[float]] = []
    for item in value[:limit]:
        if (
            isinstance(item, list)
            and len(item) == dimensions
            and all(_is_number(part) for part in item)
        ):
            result.append([float(part) for part in item])
    return result


def _distance_squared(first: list[float], second: list[float]) -> float:
    return sum((first[index] - second[index]) ** 2 for index in range(len(first)))


def _has_zero_length_segment(points: list[list[float]]) -> bool:
    return any(
        _distance_squared(points[index - 1], points[index]) <= 1e-16
        for index in range(1, len(points))
    )


def _polygon_area(points: list[list[float]]) -> float:
    if len(points) < 3:
        return 0.0
    return abs(
        sum(
            points[index][0] * points[(index + 1) % len(points)][1]
            - points[(index + 1) % len(points)][0] * points[index][1]
            for index in range(len(points))
        )
        * 0.5
    )


def _unknown_field_errors(
    value: Mapping[str, Any],
    allowed: set[str] | frozenset[str],
    label: str,
) -> list[str]:
    return [
        f"{label}.{key} is not supported"
        for key in sorted(str(key) for key in value if key not in allowed)
    ]


def _finite_vector(value: Any, length: int) -> list[float] | None:
    if (
        isinstance(value, list)
        and len(value) == length
        and all(
            _is_number(item) and abs(float(item)) <= MAX_SPECIAL_PARAMETER_MAGNITUDE
            for item in value
        )
    ):
        return [float(item) for item in value]
    return None


def _is_bounded_number(value: Any) -> bool:
    return _is_number(value) and abs(float(value)) <= MAX_SPECIAL_PARAMETER_MAGNITUDE


def _vector_difference(first: list[float], second: list[float]) -> list[float]:
    return [first[index] - second[index] for index in range(3)]


def _cross_squared(first: list[float], second: list[float]) -> float:
    x = first[1] * second[2] - first[2] * second[1]
    y = first[2] * second[0] - first[0] * second[2]
    z = first[0] * second[1] - first[1] * second[0]
    return x * x + y * y + z * z


def _vector_length(value: list[float]) -> float:
    return math.sqrt(sum(item * item for item in value))


def _normalize_vector(value: list[float], fallback: list[float]) -> list[float]:
    length = _vector_length(value)
    if length <= 1e-12:
        return list(fallback)
    return [item / length for item in value]


def _validate_unit_range(
    value: Any,
    label: str,
    errors: list[str],
    *,
    strict: bool = False,
) -> list[float] | None:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(_is_number(item) for item in value)
    ):
        errors.append(f"{label} must contain two finite numbers")
        return None
    parsed = [float(item) for item in value]
    if not 0 <= parsed[0] <= parsed[1] <= 1 or (strict and parsed[0] >= parsed[1]):
        relation = "0 <= start < end <= 1" if strict else "0 <= start <= end <= 1"
        errors.append(f"{label} must satisfy {relation}")
        return None
    return parsed


def _masked_available_fraction(
    u_range: list[float],
    v_range: list[float],
    masks: list[tuple[list[float], list[float]]],
) -> float:
    width = u_range[1] - u_range[0]
    height = v_range[1] - v_range[0]
    total_area = width * height
    if total_area <= 0:
        return 0.0
    clipped: list[tuple[list[float], list[float]]] = []
    boundaries = {u_range[0], u_range[1]}
    for mask_u, mask_v in masks:
        clipped_u = [max(u_range[0], mask_u[0]), min(u_range[1], mask_u[1])]
        clipped_v = [max(v_range[0], mask_v[0]), min(v_range[1], mask_v[1])]
        if clipped_u[0] >= clipped_u[1] or clipped_v[0] >= clipped_v[1]:
            continue
        clipped.append((clipped_u, clipped_v))
        boundaries.update(clipped_u)
    excluded_area = 0.0
    ordered = sorted(boundaries)
    for index in range(len(ordered) - 1):
        start = ordered[index]
        end = ordered[index + 1]
        midpoint = (start + end) * 0.5
        intervals = sorted(
            (mask_v[0], mask_v[1])
            for mask_u, mask_v in clipped
            if mask_u[0] <= midpoint <= mask_u[1]
        )
        covered = 0.0
        current_start: float | None = None
        current_end: float | None = None
        for interval_start, interval_end in intervals:
            if current_start is None or current_end is None:
                current_start, current_end = interval_start, interval_end
            elif interval_start > current_end:
                covered += current_end - current_start
                current_start, current_end = interval_start, interval_end
            else:
                current_end = max(current_end, interval_end)
        if current_start is not None and current_end is not None:
            covered += current_end - current_start
        excluded_area += (end - start) * covered
    return max(0.0, min(1.0, 1 - excluded_area / total_area))


def _validate_loft_sections_value(
    value: Any,
    label: str,
    errors: list[str],
) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    if not isinstance(value, list) or not 2 <= len(value) <= MAX_LOFT_SECTIONS:
        errors.append(f"{label} must contain 2 to {MAX_LOFT_SECTIONS} sections")
        return parsed
    for index, section in enumerate(value):
        section_label = f"{label}[{index}]"
        if not isinstance(section, Mapping):
            errors.append(f"{section_label} must be an object")
            continue
        errors.extend(
            _unknown_field_errors(section, {"position", "radii", "twist"}, section_label)
        )
        position = _finite_vector(section.get("position"), 3)
        radii = _finite_vector(section.get("radii"), 2)
        twist = section.get("twist", 0)
        if position is None:
            errors.append(f"{section_label}.position must be 3 finite numbers")
        if radii is None or any(radius <= 0 for radius in radii):
            errors.append(f"{section_label}.radii must be 2 positive finite numbers")
        if not _is_bounded_number(twist):
            errors.append(f"{section_label}.twist must be a finite number")
        if position is not None and radii is not None and all(radius > 0 for radius in radii) and _is_bounded_number(twist):
            parsed.append(
                {
                    "position": position,
                    "radii": radii,
                    "twist": float(twist),
                }
            )
    if len(parsed) == len(value):
        centers = [item["position"] for item in parsed]
        if _has_zero_length_segment(centers):
            errors.append(f"{label} must not contain identical consecutive positions")
    return parsed


def _validate_special_integer(
    value: Any,
    label: str,
    errors: list[str],
    *,
    minimum: int,
    maximum: int,
) -> int | None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or value > maximum
    ):
        errors.append(f"{label} must be an integer from {minimum} to {maximum}")
        return None
    return value


def _validate_special_bounds(
    value: Any,
    label: str,
    errors: list[str],
) -> tuple[list[float], list[float]] | None:
    if not isinstance(value, Mapping):
        errors.append(f"{label} must be an object with min/max local vectors")
        return None
    errors.extend(_unknown_field_errors(value, {"min", "max"}, label))
    minimum = _finite_vector(value.get("min"), 3)
    maximum = _finite_vector(value.get("max"), 3)
    if minimum is None:
        errors.append(f"{label}.min must be 3 finite numbers")
    if maximum is None:
        errors.append(f"{label}.max must be 3 finite numbers")
    if minimum is None or maximum is None:
        return None
    if any(minimum[index] >= maximum[index] for index in range(3)):
        errors.append(f"{label}.min must be strictly less than max on every axis")
        return None
    return minimum, maximum


def _minimum_resolvable_field_scales(
    bounds: tuple[list[float], list[float]] | None,
    resolution: list[int] | None,
) -> list[float] | None:
    if bounds is None or resolution is None:
        return None
    minimum, maximum = bounds
    return [
        max(
            MIN_IMPLICIT_FIELD_SCALE,
            (maximum[axis] - minimum[axis]) / (resolution[axis] - 1) * 0.25,
        )
        for axis in range(3)
    ]


def _parameters(component: Mapping[str, Any]) -> Mapping[str, Any]:
    descriptor = component.get("geometryDescriptor")
    if not isinstance(descriptor, Mapping):
        return {}
    parameters = descriptor.get("parameters")
    return parameters if isinstance(parameters, Mapping) else {}


def _component_label(component: Mapping[str, Any]) -> str:
    component_id = component.get("id")
    return f"component {component_id!r}" if component_id is not None else "component"


def validate_surface_topology_plan(
    plan: Any,
    components: Any,
    materials: Any = None,
    *,
    resolve_references: bool = True,
) -> tuple[list[str], list[str]]:
    """Validate the pre-build decision that maps semantic regions to real surface topology."""

    errors: list[str] = []
    warnings: list[str] = []
    if plan is None:
        warnings.append(
            "missing surfaceTopologyPlan; legacy specs remain readable but have no construction-strategy gate"
        )
        return errors, warnings
    if not isinstance(plan, Mapping):
        return ["surfaceTopologyPlan must be an object"], warnings
    errors.extend(
        _unknown_field_errors(
            plan,
            {"status", "reason", "decisionRule", "groups"},
            "surfaceTopologyPlan",
        )
    )
    status = plan.get("status")
    if status not in {"unassessed", "planned", "not-required"}:
        errors.append("surfaceTopologyPlan.status must be unassessed, planned, or not-required")
        return errors, warnings
    groups = plan.get("groups", [])
    if not isinstance(groups, list):
        errors.append("surfaceTopologyPlan.groups must be an array")
        return errors, warnings
    if status == "unassessed":
        warnings.append(
            "quality: surfaceTopologyPlan is unassessed; classify visible systems before authoring modules"
        )
        return errors, warnings
    reason = plan.get("reason")
    if status == "not-required":
        if not isinstance(reason, str) or not reason.strip():
            errors.append("surfaceTopologyPlan.reason is required when status is not-required")
        if groups:
            errors.append("surfaceTopologyPlan.groups must be empty when status is not-required")
        if resolve_references and isinstance(components, list) and any(
            isinstance(item, Mapping) and component_type(item) == "part"
            for item in components
        ):
            errors.append(
                "surfaceTopologyPlan.status not-required cannot be used when executable geometry parts exist"
            )
        return errors, warnings
    if not groups:
        errors.append("surfaceTopologyPlan.groups must not be empty when status is planned")
        return errors, warnings
    if not isinstance(reason, str) or not reason.strip():
        errors.append("surfaceTopologyPlan.reason is required when status is planned")
    decision_rule = plan.get("decisionRule")
    if not isinstance(decision_rule, str) or not decision_rule.strip():
        errors.append("surfaceTopologyPlan.decisionRule is required when status is planned")

    component_rows = [item for item in components if isinstance(item, Mapping)] if isinstance(components, list) else []
    component_map = {
        str(item.get("id")): item
        for item in component_rows
        if isinstance(item.get("id"), str) and item.get("id")
    }
    material_rows = [item for item in materials or [] if isinstance(item, Mapping)]
    material_map = {
        str(item.get("id")): item
        for item in material_rows
        if isinstance(item.get("id"), str)
    }
    material_ids = {
        str(item.get("id"))
        for item in material_rows
        if isinstance(item.get("id"), str)
    }
    present_modules = {
        str(item.get("moduleId"))
        for item in component_rows
        if isinstance(item.get("moduleId"), str)
    }
    active_component_refs: set[str] = set()
    seen_groups: set[str] = set()

    def string_list(value: Any, label: str, *, required: bool = False) -> list[str]:
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            errors.append(f"{label} must contain non-empty strings")
            return []
        if required and not value:
            errors.append(f"{label} must not be empty")
        if len(set(value)) != len(value):
            errors.append(f"{label} contains duplicates")
        return [str(item) for item in value]

    def detached_local_feature_ids(component: Mapping[str, Any] | None) -> list[str]:
        features = component.get("localFeatures") if isinstance(component, Mapping) else None
        if not isinstance(features, list):
            return []
        return [
            str(feature.get("id") or feature.get("type") or "unnamed")
            for feature in features
            if isinstance(feature, Mapping)
            and feature.get("type") in DETACHED_LOCAL_FEATURE_TYPES
        ]

    for index, group in enumerate(groups):
        label = f"surfaceTopologyPlan.groups[{index}]"
        if not isinstance(group, Mapping):
            errors.append(f"{label} must be an object")
            continue
        errors.extend(
            _unknown_field_errors(
                group,
                {
                    "id",
                    "strategy",
                    "regions",
                    "ownerModuleId",
                    "componentRefs",
                    "materialRefs",
                    "hostComponentRef",
                    "requiredTopology",
                    "separationReason",
                    "rationale",
                    "evidenceRefs",
                    "confidence",
                },
                label,
            )
        )
        group_id = group.get("id")
        if not isinstance(group_id, str) or not group_id.strip():
            errors.append(f"{label}.id is required")
        elif group_id in seen_groups:
            errors.append(f"duplicate surface topology group id {group_id!r}")
        else:
            seen_groups.add(group_id)
        strategy = group.get("strategy")
        if strategy not in SURFACE_TOPOLOGY_STRATEGIES:
            errors.append(
                f"{label}.strategy must be one of: {', '.join(sorted(SURFACE_TOPOLOGY_STRATEGIES))}"
            )
        regions = string_list(group.get("regions"), f"{label}.regions", required=True)
        del regions
        component_refs = string_list(group.get("componentRefs", []), f"{label}.componentRefs")
        material_refs = string_list(group.get("materialRefs", []), f"{label}.materialRefs")
        evidence_refs = string_list(group.get("evidenceRefs", []), f"{label}.evidenceRefs", required=True)
        del evidence_refs
        rationale = group.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            errors.append(f"{label}.rationale is required")
        confidence = group.get("confidence")
        if not _is_number(confidence) or not 0 <= float(confidence) <= 1:
            errors.append(f"{label}.confidence must be a number from 0 to 1")
        owner = group.get("ownerModuleId")
        if owner is not None and (not isinstance(owner, str) or not owner.strip()):
            errors.append(f"{label}.ownerModuleId must be a non-empty string")
        active = not resolve_references or owner is None or not present_modules or owner in present_modules
        if not active:
            continue
        active_component_refs.update(component_refs)
        if resolve_references:
            missing_components = sorted(set(component_refs) - set(component_map))
            if missing_components:
                errors.append(f"{label}.componentRefs contains unknown ids: {', '.join(missing_components)}")
            missing_materials = sorted(set(material_refs) - material_ids)
            if missing_materials:
                errors.append(f"{label}.materialRefs contains unknown ids: {', '.join(missing_materials)}")
            if present_modules:
                for component_ref in component_refs:
                    component_owner = component_map.get(component_ref, {}).get("moduleId")
                    if isinstance(component_owner, str) and owner != component_owner:
                        errors.append(
                            f"{label}.ownerModuleId must be {component_owner!r} to classify "
                            f"component {component_ref!r}"
                        )
                for material_ref in material_refs:
                    material_owner = material_map.get(material_ref, {}).get("moduleId")
                    if isinstance(material_owner, str) and owner != material_owner:
                        errors.append(
                            f"{label}.ownerModuleId must be {material_owner!r} to classify "
                            f"material {material_ref!r}"
                        )
        host_ref = group.get("hostComponentRef")
        host = component_map.get(str(host_ref)) if isinstance(host_ref, str) else None
        topology = group.get("requiredTopology")

        if strategy == "continuous-sculpt":
            if topology != "single-connected-surface":
                errors.append(f"{label}.requiredTopology must be single-connected-surface")
            if not isinstance(host_ref, str) or component_refs != [host_ref]:
                errors.append(
                    f"{label} continuous sculpt must reference exactly its one host component"
                )
            if isinstance(host, Mapping) and host.get("primitive") not in CONTINUOUS_SURFACE_PRIMITIVES:
                errors.append(
                    f"{label} continuous host must use one of: "
                    + ", ".join(sorted(CONTINUOUS_SURFACE_PRIMITIVES))
                )
            if isinstance(host, Mapping) and host.get("primitive") == "sculpted-surface":
                if _parameters(host).get("connectivity") != "single-surface":
                    errors.append(
                        f"{label} sculpted-surface host must declare connectivity 'single-surface'"
                    )
            detached_features = detached_local_feature_ids(host)
            if detached_features:
                errors.append(
                    f"{label} continuous host has detached localFeatures: "
                    + ", ".join(detached_features)
                    + "; embed relief in the host field or model a real accessory as an assembled component"
                )
        elif strategy == "surface-relief":
            if topology != "embedded-in-host":
                errors.append(f"{label}.requiredTopology must be embedded-in-host")
            if not isinstance(host_ref, str) or component_refs != [host_ref]:
                errors.append(
                    f"{label} relief must reference only its host component; detached relief meshes are not allowed"
                )
            if isinstance(host, Mapping) and host.get("primitive") not in {
                "sculpted-surface",
                "deformable-surface",
            }:
                errors.append(
                    f"{label} relief host must support embedded surface deformation"
                )
            detached_features = detached_local_feature_ids(host)
            if detached_features:
                errors.append(
                    f"{label} embedded relief cannot use detached localFeatures: "
                    + ", ".join(detached_features)
                )
            if (
                isinstance(host, Mapping)
                and host.get("primitive") == "sculpted-surface"
                and not _parameters(host).get("surfaceModifiers")
            ):
                errors.append(
                    f"{label} sculpted-surface relief needs executable surfaceModifiers on its host"
                )
        elif strategy == "assembled-solid":
            if topology != "intentional-separate-parts":
                errors.append(f"{label}.requiredTopology must be intentional-separate-parts")
            if not component_refs:
                errors.append(f"{label}.componentRefs must identify the separate parts")
            separation = group.get("separationReason")
            if not isinstance(separation, str) or not separation.strip():
                errors.append(f"{label}.separationReason is required for assembled-solid")
        elif strategy == "conforming-shell":
            if topology != "host-conforming":
                errors.append(f"{label}.requiredTopology must be host-conforming")
            if not isinstance(host_ref, str) or (resolve_references and host is None):
                errors.append(f"{label}.hostComponentRef must reference the fitted host")
            if not component_refs:
                errors.append(f"{label}.componentRefs must identify the fitted shell")
            for component_ref in component_refs:
                shell = component_map.get(component_ref)
                if not isinstance(shell, Mapping):
                    continue
                if shell.get("primitive") != "conforming-shell":
                    errors.append(f"{label} component {component_ref!r} must use conforming-shell")
                elif _parameters(shell).get("bodyRef") != host_ref:
                    errors.append(f"{label} component {component_ref!r} must conform to {host_ref!r}")
        elif strategy == "fiber-strand":
            if topology != "host-bound-strands":
                errors.append(f"{label}.requiredTopology must be host-bound-strands")
            if not isinstance(host_ref, str) or (resolve_references and host is None):
                errors.append(f"{label}.hostComponentRef must reference the supporting surface")
            if not component_refs:
                errors.append(f"{label}.componentRefs must identify the bound strand system")
            allowed_fibers = {"fiber-system", "surface-scatter", "tube", "curve-sweep"}
            for component_ref in component_refs:
                strand = component_map.get(component_ref)
                if isinstance(strand, Mapping) and strand.get("primitive") not in allowed_fibers:
                    errors.append(
                        f"{label} component {component_ref!r} must use a strand/surface-bound primitive"
                    )
                    continue
                if not isinstance(strand, Mapping) or not isinstance(host_ref, str):
                    continue
                primitive = strand.get("primitive")
                if primitive == "surface-scatter":
                    if (
                        _parameters(strand).get("surfaceRef") != host_ref
                        or strand.get("parent") != host_ref
                    ):
                        errors.append(
                            f"{label} surface-scatter {component_ref!r} must bind surfaceRef and parent to {host_ref!r}"
                        )
                else:
                    if strand.get("parent") != host_ref:
                        errors.append(
                            f"{label} strand component {component_ref!r} must use runtime parent {host_ref!r}; "
                            "attachment metadata alone does not bind generated geometry"
                        )
                    transform = strand.get("transform")
                    if isinstance(transform, Mapping):
                        position = _finite_vector(transform.get("position", [0, 0, 0]), 3)
                        rotation = _finite_vector(transform.get("rotation", [0, 0, 0]), 3)
                        scale = _finite_vector(transform.get("scale", [1, 1, 1]), 3)
                        if (
                            position is None
                            or rotation is None
                            or scale is None
                            or any(abs(value) > 1e-9 for value in [*position, *rotation])
                            or any(abs(value - 1) > 1e-9 for value in scale)
                        ):
                            errors.append(
                                f"{label} strand component {component_ref!r} must use an identity "
                                "transform and encode host-local placement in its guides/path"
                            )
        elif strategy == "material-only":
            if topology != "no-geometry":
                errors.append(f"{label}.requiredTopology must be no-geometry")
            if component_refs:
                errors.append(f"{label}.componentRefs must be empty for material-only")
            if not material_refs:
                errors.append(f"{label}.materialRefs must identify the material treatment")

    visible_parts = {
        str(item.get("id"))
        for item in component_rows
        if component_type(item) == "part"
        and isinstance(item.get("id"), str)
        and (not present_modules or item.get("moduleId") in present_modules)
    }
    uncovered = sorted(visible_parts - active_component_refs)
    if resolve_references and uncovered:
        errors.append(
            "surfaceTopologyPlan does not classify geometry components: " + ", ".join(uncovered)
        )
    return list(dict.fromkeys(errors)), list(dict.fromkeys(warnings))


def _validate_parameter_object(
    component: Mapping[str, Any],
    errors: list[str],
) -> Mapping[str, Any]:
    descriptor = component.get("geometryDescriptor")
    if descriptor is not None and not isinstance(descriptor, Mapping):
        errors.append(f"{_component_label(component)} geometryDescriptor must be an object")
        return {}
    if not isinstance(descriptor, Mapping):
        return {}
    parameters = descriptor.get("parameters")
    if parameters is not None and not isinstance(parameters, Mapping):
        errors.append(
            f"{_component_label(component)} geometryDescriptor.parameters must be an object"
        )
        return {}
    return parameters if isinstance(parameters, Mapping) else {}


def _deformation_stack(component: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    descriptor = component.get("geometryDescriptor")
    if not isinstance(descriptor, Mapping):
        return []
    value = descriptor.get("deformationStack", [])
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _validate_deformation_stack(component: Mapping[str, Any]) -> list[str]:
    descriptor = component.get("geometryDescriptor")
    if not isinstance(descriptor, Mapping) or "deformationStack" not in descriptor:
        return []
    value = descriptor.get("deformationStack")
    label = f"{_component_label(component)} geometryDescriptor.deformationStack"
    if not isinstance(value, list):
        return [f"{label} must be an array"]
    if len(value) > MAX_GEOMETRY_MODIFIERS:
        return [f"{label} must contain at most {MAX_GEOMETRY_MODIFIERS} modifiers"]
    errors: list[str] = []
    if component.get("primitive") == "conforming-shell" and value:
        errors.append(
            f"{label} must be empty for conforming-shell; use parameters.folds so the inner fit remains attached"
        )
    for index, modifier in enumerate(value):
        item_label = f"{label}[{index}]"
        if not isinstance(modifier, Mapping):
            errors.append(f"{item_label} must be an object")
            continue
        modifier_type = modifier.get("type")
        if modifier_type not in {"bend", "taper", "bulge", "twist", "noise"}:
            errors.append(f"{item_label}.type must be bend, taper, bulge, twist, or noise")
            continue
        allowed = {"type", "axis", "amount", "start", "end", "power"}
        if modifier_type == "bend":
            allowed.add("direction")
        if modifier_type == "noise":
            allowed.update({"frequency", "seed"})
        errors.extend(_unknown_field_errors(modifier, allowed, item_label))
        if modifier.get("axis", "y") not in {"x", "y", "z"}:
            errors.append(f"{item_label}.axis must be x, y, or z")
        amount = modifier.get("amount")
        if not _is_bounded_number(amount):
            errors.append(f"{item_label}.amount must be a finite number")
        start = modifier.get("start", 0)
        end = modifier.get("end", 1)
        if not _is_number(start) or not _is_number(end) or not 0 <= float(start) < float(end) <= 1:
            errors.append(f"{item_label}.start/end must satisfy 0 <= start < end <= 1")
        power = modifier.get("power", 1)
        if not _is_bounded_number(power) or float(power) <= 0:
            errors.append(f"{item_label}.power must be a positive finite number")
        if modifier_type == "bend" and modifier.get("direction", "x") not in {"x", "y", "z"}:
            errors.append(f"{item_label}.direction must be x, y, or z")
        if modifier_type == "bend" and modifier.get("direction", "x") == modifier.get("axis", "y"):
            errors.append(f"{item_label}.direction must differ from axis")
        if modifier_type == "noise":
            frequency = modifier.get("frequency", 1)
            if not _is_bounded_number(frequency) or float(frequency) <= 0:
                errors.append(f"{item_label}.frequency must be a positive finite number")
            seed = modifier.get("seed", 1)
            if not isinstance(seed, int) or isinstance(seed, bool) or not 1 <= seed <= 2_147_483_647:
                errors.append(f"{item_label}.seed must be an integer from 1 to 2147483647")
    return errors


def _validate_number(
    parameters: Mapping[str, Any],
    key: str,
    errors: list[str],
    label: str,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> None:
    if key not in parameters:
        return
    value = parameters[key]
    if not _is_number(value):
        errors.append(f"{label}.{key} must be a finite number")
    elif positive and float(value) <= 0:
        errors.append(f"{label}.{key} must be positive")
    elif non_negative and float(value) < 0:
        errors.append(f"{label}.{key} must be non-negative")


def _validate_integer(
    parameters: Mapping[str, Any],
    key: str,
    errors: list[str],
    label: str,
    *,
    minimum: int = 1,
    maximum: int = MAX_GEOMETRY_SEGMENTS,
) -> None:
    if key not in parameters:
        return
    value = parameters[key]
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or value > maximum
    ):
        errors.append(
            f"{label}.{key} must be an integer from {minimum} to {maximum}"
        )


def _validate_boolean(
    parameters: Mapping[str, Any],
    key: str,
    errors: list[str],
    label: str,
) -> None:
    if key in parameters and not isinstance(parameters[key], bool):
        errors.append(f"{label}.{key} must be boolean")


def _validate_points(
    value: Any,
    dimensions: int,
    minimum_count: int,
    label: str,
    errors: list[str],
    *,
    maximum_count: int,
) -> None:
    if not isinstance(value, list) or len(value) < minimum_count:
        errors.append(
            f"{label} must contain at least {minimum_count} local {dimensions}D points"
        )
        return
    if len(value) > maximum_count:
        errors.append(f"{label} must contain at most {maximum_count} points")
    for index, point in enumerate(value):
        if (
            not isinstance(point, list)
            or len(point) != dimensions
            or not all(_is_number(item) for item in point)
        ):
            errors.append(f"{label}[{index}] must be {dimensions} finite numbers")


def _validate_basic(
    parameters: Mapping[str, Any],
    component: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    label = f"{_component_label(component)} geometryDescriptor.parameters"
    for key in (
        "widthSegments",
        "heightSegments",
        "depthSegments",
        "radialSegments",
        "tubularSegments",
        "capSegments",
        "curveSegments",
        "segments",
    ):
        _validate_integer(parameters, key, errors, label)
    for key in ("phiStart", "phiLength", "thetaStart", "thetaLength", "arc"):
        _validate_number(parameters, key, errors, label)
    _validate_boolean(parameters, "openEnded", errors, label)
    return errors


def _edge_treatment(component: Mapping[str, Any]) -> Mapping[str, Any]:
    descriptor = component.get("geometryDescriptor")
    if not isinstance(descriptor, Mapping):
        return {}
    treatment = descriptor.get("edgeTreatment")
    return treatment if isinstance(treatment, Mapping) else {}


def _validate_edge_treatment(component: Mapping[str, Any]) -> list[str]:
    descriptor = component.get("geometryDescriptor")
    if not isinstance(descriptor, Mapping) or "edgeTreatment" not in descriptor:
        return []
    treatment = descriptor.get("edgeTreatment")
    label = f"{_component_label(component)} geometryDescriptor.edgeTreatment"
    if not isinstance(treatment, Mapping):
        return [f"{label} must be an object"]
    treatment_type = treatment.get("type", "none")
    if treatment_type not in {"none", "bevel", "rounded"}:
        return [f"{label}.type must be none, bevel, or rounded"]
    errors: list[str] = []
    radius = treatment.get("radiusRatio", treatment.get("bevelRadius", 0))
    if not _is_number(radius) or not 0 <= float(radius) <= 0.5:
        errors.append(f"{label}.radiusRatio/bevelRadius must be from 0 to 0.5")
    segments = treatment.get("segments", 4)
    if (
        not isinstance(segments, int)
        or isinstance(segments, bool)
        or not 1 <= segments <= 16
    ):
        errors.append(f"{label}.segments must be an integer from 1 to 16")
    if treatment_type != "none" and component.get("primitive") != "box":
        errors.append(
            f"{label}.type {treatment_type!r} currently emits real geometry only for box; "
            "use extrude bevel parameters or an explicit rounded component for other primitives"
        )
    return errors


def _attachment_delta(component: Mapping[str, Any]) -> list[float] | None:
    attachment = component.get("attachment")
    if not isinstance(attachment, Mapping):
        return None
    start = _vector(attachment.get("localStart"), 3, [0, 0, 0])
    end = _vector(attachment.get("localEnd"), 3, [0, 1, 0])
    delta = [end[index] - start[index] for index in range(3)]
    if math.sqrt(sum(value * value for value in delta)) <= 1e-6:
        return None
    return delta


def _validate_tube(
    parameters: Mapping[str, Any],
    component: Mapping[str, Any],
) -> list[str]:
    errors = _validate_basic(parameters, component)
    label = f"{_component_label(component)} geometryDescriptor.parameters"
    path = parameters.get("path")
    if path is None:
        if _attachment_delta(component) is None:
            errors.append(f"{label}.path is required when no valid attachment endpoints exist")
    else:
        _validate_points(
            path,
            3,
            2,
            f"{label}.path",
            errors,
            maximum_count=MAX_PATH_POINTS,
        )
        parsed_path = _point_list(path, 3, MAX_PATH_POINTS)
        if (
            isinstance(path, list)
            and len(parsed_path) == len(path)
            and _has_zero_length_segment(parsed_path)
        ):
            errors.append(f"{label}.path must not contain identical consecutive points")
    _validate_number(parameters, "radius", errors, label, positive=True)
    _validate_number(parameters, "tension", errors, label, non_negative=True)
    _validate_boolean(parameters, "closed", errors, label)
    curve_type = parameters.get("curveType")
    if curve_type is not None and curve_type not in {"centripetal", "chordal", "catmullrom"}:
        errors.append(f"{label}.curveType must be centripetal, chordal, or catmullrom")
    return errors


def _validate_lathe(
    parameters: Mapping[str, Any],
    component: Mapping[str, Any],
) -> list[str]:
    errors = _validate_basic(parameters, component)
    label = f"{_component_label(component)} geometryDescriptor.parameters"
    profile = parameters.get("profile", parameters.get("points"))
    _validate_points(
        profile,
        2,
        2,
        f"{label}.profile",
        errors,
        maximum_count=MAX_PROFILE_POINTS,
    )
    parsed_profile = _point_list(profile, 2, MAX_PROFILE_POINTS)
    if isinstance(profile, list) and len(parsed_profile) == len(profile):
        if any(point[0] < 0 for point in parsed_profile):
            errors.append(f"{label}.profile radii must be non-negative")
        if not any(point[0] > 1e-8 for point in parsed_profile):
            errors.append(f"{label}.profile must contain a positive radius")
        if max((point[1] for point in parsed_profile), default=0) - min(
            (point[1] for point in parsed_profile), default=0
        ) <= 1e-8:
            errors.append(f"{label}.profile must span a non-zero local y range")
    return errors


def _outline(parameters: Mapping[str, Any]) -> Any:
    for key in ("outline", "shape", "contour"):
        if key in parameters:
            return parameters[key]
    return None


def _outline_key(parameters: Mapping[str, Any]) -> str:
    return next((key for key in ("outline", "shape", "contour") if key in parameters), "outline")


def _validate_extrude(
    parameters: Mapping[str, Any],
    component: Mapping[str, Any],
) -> list[str]:
    errors = _validate_basic(parameters, component)
    label = f"{_component_label(component)} geometryDescriptor.parameters"
    outline_key = _outline_key(parameters)
    _validate_points(
        _outline(parameters),
        2,
        3,
        f"{label}.{outline_key}",
        errors,
        maximum_count=MAX_PROFILE_POINTS,
    )
    parsed_outline = _point_list(_outline(parameters), 2, MAX_PROFILE_POINTS)
    if len(parsed_outline) >= 3 and _polygon_area(parsed_outline) <= 1e-12:
        errors.append(f"{label}.{outline_key} must enclose a non-zero area")
    holes = parameters.get("holes", [])
    if not isinstance(holes, list):
        errors.append(f"{label}.holes must be an array of local 2D contours")
    else:
        if len(holes) > 64:
            errors.append(f"{label}.holes must contain at most 64 contours")
        for index, hole in enumerate(holes):
            _validate_points(
                hole,
                2,
                3,
                f"{label}.holes[{index}]",
                errors,
                maximum_count=MAX_PROFILE_POINTS,
            )
            parsed_hole = _point_list(hole, 2, MAX_PROFILE_POINTS)
            if len(parsed_hole) >= 3 and _polygon_area(parsed_hole) <= 1e-12:
                errors.append(f"{label}.holes[{index}] must enclose a non-zero area")
    for key in ("depth", "bevelThickness", "bevelSize"):
        _validate_number(parameters, key, errors, label, non_negative=key != "depth", positive=key == "depth")
    _validate_number(parameters, "bevelOffset", errors, label)
    _validate_integer(parameters, "steps", errors, label)
    _validate_integer(parameters, "bevelSegments", errors, label)
    _validate_boolean(parameters, "bevelEnabled", errors, label)
    return errors


def _validate_curve_sweep(
    parameters: Mapping[str, Any],
    component: Mapping[str, Any],
) -> list[str]:
    errors = _validate_tube(parameters, component)
    label = f"{_component_label(component)} geometryDescriptor.parameters"
    _validate_points(
        parameters.get("profile"),
        2,
        3,
        f"{label}.profile",
        errors,
        maximum_count=MAX_PROFILE_POINTS,
    )
    parsed_profile = _point_list(parameters.get("profile"), 2, MAX_PROFILE_POINTS)
    if len(parsed_profile) >= 3:
        if _boolean(parameters.get("closedProfile"), True):
            if _polygon_area(parsed_profile) <= 1e-12:
                errors.append(f"{label}.profile must enclose a non-zero area")
        elif all(
            _distance_squared(parsed_profile[0], point) <= 1e-16
            for point in parsed_profile[1:]
        ):
            errors.append(f"{label}.profile must span a non-zero local extent")
    _validate_integer(parameters, "pathSegments", errors, label, minimum=2)
    _validate_boolean(parameters, "closedPath", errors, label)
    _validate_boolean(parameters, "closedProfile", errors, label)
    _validate_number(parameters, "twist", errors, label)
    radii = parameters.get("radii")
    if radii is not None:
        if not isinstance(radii, list) or not radii or not all(_is_number(value) and float(value) > 0 for value in radii):
            errors.append(f"{label}.radii must be a non-empty array of positive finite numbers")
        elif len(radii) > MAX_PATH_POINTS:
            errors.append(f"{label}.radii must contain at most {MAX_PATH_POINTS} values")
    return errors


def _validate_section_loft(
    parameters: Mapping[str, Any],
    component: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    label = f"{_component_label(component)} geometryDescriptor.parameters"
    allowed = {
        "representation",
        "sections",
        "radialSegments",
        "segmentsPerSpan",
        "capStart",
        "capEnd",
    }
    errors.extend(_unknown_field_errors(parameters, allowed, label))
    if parameters.get("representation") != "elliptical-sections":
        errors.append(f"{label}.representation must be 'elliptical-sections'")
    sections = _validate_loft_sections_value(parameters.get("sections"), f"{label}.sections", errors)
    radial = _validate_special_integer(
        parameters.get("radialSegments"),
        f"{label}.radialSegments",
        errors,
        minimum=3,
        maximum=128,
    )
    per_span = _validate_special_integer(
        parameters.get("segmentsPerSpan"),
        f"{label}.segmentsPerSpan",
        errors,
        minimum=1,
        maximum=32,
    )
    _validate_boolean(parameters, "capStart", errors, label)
    _validate_boolean(parameters, "capEnd", errors, label)
    if sections and radial is not None and per_span is not None:
        vertices = ((len(sections) - 1) * per_span + 1) * (radial + 1) + 2
        if vertices > MAX_LOFT_VERTICES:
            errors.append(
                f"{label} would emit {vertices} vertices; maximum is {MAX_LOFT_VERTICES}"
            )
    return errors


def _validate_shell_fold(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, Mapping):
        errors.append(f"{label} must be an object")
        return
    errors.extend(
        _unknown_field_errors(value, {"direction", "amplitude", "frequency", "phase"}, label)
    )
    direction = _finite_vector(value.get("direction"), 2)
    if direction is None or direction[0] * direction[0] + direction[1] * direction[1] <= 1e-16:
        errors.append(f"{label}.direction must be 2 finite numbers and non-zero")
    for field in ("amplitude", "frequency"):
        field_value = value.get(field)
        if not _is_bounded_number(field_value) or (field == "frequency" and float(field_value) <= 0):
            qualifier = "positive finite" if field == "frequency" else "finite"
            errors.append(f"{label}.{field} must be a {qualifier} number")
    if not _is_bounded_number(value.get("phase", 0)):
        errors.append(f"{label}.phase must be a finite number")


def _validate_conforming_shell(
    parameters: Mapping[str, Any],
    component: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    label = f"{_component_label(component)} geometryDescriptor.parameters"
    allowed = {
        "representation",
        "bodyRef",
        "clearance",
        "thickness",
        "radialSegments",
        "segmentsPerSpan",
        "coverage",
        "openings",
        "folds",
        "_hostSections",
    }
    errors.extend(_unknown_field_errors(parameters, allowed, label))
    if parameters.get("representation") != "loft-shell":
        errors.append(f"{label}.representation must be 'loft-shell'")
    body_ref = parameters.get("bodyRef")
    if not isinstance(body_ref, str) or not body_ref.strip():
        errors.append(f"{label}.bodyRef is required")
    host_sections = _validate_loft_sections_value(
        parameters.get("_hostSections"), f"{label}.bodyRef sections", errors
    )
    clearance = parameters.get("clearance")
    if not _is_bounded_number(clearance) or float(clearance) < 0:
        errors.append(f"{label}.clearance must be a non-negative finite bounded number")
    thickness = parameters.get("thickness")
    if not _is_bounded_number(thickness) or float(thickness) <= 0:
        errors.append(f"{label}.thickness must be a positive finite bounded number")
    radial = _validate_special_integer(
        parameters.get("radialSegments"),
        f"{label}.radialSegments",
        errors,
        minimum=3,
        maximum=128,
    )
    per_span = _validate_special_integer(
        parameters.get("segmentsPerSpan"),
        f"{label}.segmentsPerSpan",
        errors,
        minimum=1,
        maximum=32,
    )
    coverage = parameters.get("coverage")
    coverage_v: list[float] | None = None
    angle_length_value: float | None = None
    if not isinstance(coverage, Mapping):
        errors.append(f"{label}.coverage must be an object")
    else:
        errors.extend(
            _unknown_field_errors(
                coverage,
                {"vRange", "angleStart", "angleLength"},
                f"{label}.coverage",
            )
        )
        coverage_v = _validate_unit_range(
            coverage.get("vRange"),
            f"{label}.coverage.vRange",
            errors,
            strict=True,
        )
        angle_start = coverage.get("angleStart", 0)
        angle_length = coverage.get("angleLength", math.tau)
        if not _is_bounded_number(angle_start):
            errors.append(f"{label}.coverage.angleStart must be a finite number")
        if not _is_bounded_number(angle_length) or not 0 < float(angle_length) <= math.tau:
            errors.append(f"{label}.coverage.angleLength must be > 0 and <= 2π")
        else:
            angle_length_value = float(angle_length)
    openings = parameters.get("openings", [])
    valid_openings: list[tuple[str, list[float], list[float]]] = []
    if not isinstance(openings, list):
        errors.append(f"{label}.openings must be an array")
    elif len(openings) > MAX_SHELL_OPENINGS:
        errors.append(f"{label}.openings must contain at most {MAX_SHELL_OPENINGS} entries")
    else:
        ids: set[str] = set()
        for index, opening in enumerate(openings):
            item_label = f"{label}.openings[{index}]"
            if not isinstance(opening, Mapping):
                errors.append(f"{item_label} must be an object")
                continue
            errors.extend(_unknown_field_errors(opening, {"id", "center", "radius"}, item_label))
            opening_id = opening.get("id")
            if not isinstance(opening_id, str) or not opening_id.strip():
                errors.append(f"{item_label}.id is required")
            elif opening_id in ids:
                errors.append(f"duplicate conforming-shell opening id {opening_id!r}")
            else:
                ids.add(opening_id)
            center = _finite_vector(opening.get("center"), 2)
            radius = _finite_vector(opening.get("radius"), 2)
            if center is None or not all(0 <= item <= 1 for item in center):
                errors.append(f"{item_label}.center must be 2 values from 0 to 1")
            if radius is None or any(item <= 0 or item > 1 for item in radius):
                errors.append(f"{item_label}.radius must be 2 values greater than 0 and <= 1")
            if (
                isinstance(opening_id, str)
                and opening_id.strip()
                and center is not None
                and all(0 <= item <= 1 for item in center)
                and radius is not None
                and all(0 < item <= 1 for item in radius)
            ):
                valid_openings.append((opening_id, center, radius))
    folds = parameters.get("folds", [])
    if not isinstance(folds, list):
        errors.append(f"{label}.folds must be an array")
    elif len(folds) > MAX_DEFORMABLE_FOLDS:
        errors.append(f"{label}.folds must contain at most {MAX_DEFORMABLE_FOLDS} entries")
    else:
        for index, fold in enumerate(folds):
            _validate_shell_fold(fold, f"{label}.folds[{index}]", errors)
    if (
        host_sections
        and radial is not None
        and per_span is not None
        and coverage_v is not None
        and angle_length_value is not None
    ):
        longitudinal = max(
            1,
            math.ceil(
                (len(host_sections) - 1)
                * per_span
                * (coverage_v[1] - coverage_v[0])
            ),
        )
        angular = max(1, math.ceil(radial * angle_length_value / math.tau))
        vertices = 2 * (longitudinal + 1) * (angular + 1)
        if vertices > MAX_LOFT_VERTICES:
            errors.append(
                f"{label} would emit {vertices} vertices; maximum is {MAX_LOFT_VERTICES}"
            )
        cells = [
            ((column + 0.5) / angular, (row + 0.5) / longitudinal)
            for row in range(longitudinal)
            for column in range(angular)
        ]
        for opening_id, center, radius in valid_openings:
            if not any(
                ((u - center[0]) / radius[0]) ** 2
                + ((v - center[1]) / radius[1]) ** 2
                <= 1
                for u, v in cells
            ):
                errors.append(
                    f"conforming-shell opening {opening_id!r} is smaller than the emitted grid and would do nothing"
                )
        if valid_openings and not any(
            not any(
                ((u - center[0]) / radius[0]) ** 2
                + ((v - center[1]) / radius[1]) ** 2
                <= 1
                for _, center, radius in valid_openings
            )
            for u, v in cells
        ):
            errors.append(f"{label}.openings remove the entire shell surface")
    return errors


def _validate_branch_network(
    parameters: Mapping[str, Any],
    component: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    label = f"{_component_label(component)} geometryDescriptor.parameters"
    allowed = {
        "representation",
        "nodes",
        "edges",
        "radialSegments",
        "segmentsPerEdge",
        "junctionSegments",
        "capEnds",
    }
    errors.extend(_unknown_field_errors(parameters, allowed, label))
    if parameters.get("representation") != "branch-graph":
        errors.append(f"{label}.representation must be 'branch-graph'")
    nodes = parameters.get("nodes")
    node_ids: set[str] = set()
    node_positions: dict[str, list[float]] = {}
    if not isinstance(nodes, list) or not 2 <= len(nodes) <= MAX_BRANCH_NODES:
        errors.append(f"{label}.nodes must contain 2 to {MAX_BRANCH_NODES} nodes")
        nodes = []
    for index, node in enumerate(nodes):
        node_label = f"{label}.nodes[{index}]"
        if not isinstance(node, Mapping):
            errors.append(f"{node_label} must be an object")
            continue
        errors.extend(_unknown_field_errors(node, {"id", "position", "radius"}, node_label))
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id.strip():
            errors.append(f"{node_label}.id is required")
        elif node_id in node_ids:
            errors.append(f"duplicate branch node id {node_id!r}")
        else:
            node_ids.add(node_id)
        position = _finite_vector(node.get("position"), 3)
        if position is None:
            errors.append(f"{node_label}.position must be 3 finite numbers")
        elif isinstance(node_id, str):
            node_positions[node_id] = position
        radius = node.get("radius")
        if not _is_bounded_number(radius) or float(radius) <= 0:
            errors.append(f"{node_label}.radius must be a positive finite number")
    edges = parameters.get("edges")
    graph: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    indegree: dict[str, int] = {node_id: 0 for node_id in node_ids}
    edge_pairs: set[tuple[str, str]] = set()
    if not isinstance(edges, list) or not 1 <= len(edges) <= MAX_BRANCH_EDGES:
        errors.append(f"{label}.edges must contain 1 to {MAX_BRANCH_EDGES} edges")
        edges = []
    for index, edge in enumerate(edges):
        edge_label = f"{label}.edges[{index}]"
        if not isinstance(edge, Mapping):
            errors.append(f"{edge_label} must be an object")
            continue
        errors.extend(_unknown_field_errors(edge, {"from", "to", "controlPoints"}, edge_label))
        start = edge.get("from")
        end = edge.get("to")
        if not isinstance(start, str) or start not in node_ids:
            errors.append(f"{edge_label}.from must reference a branch node")
        if not isinstance(end, str) or end not in node_ids:
            errors.append(f"{edge_label}.to must reference a branch node")
        if isinstance(start, str) and isinstance(end, str) and start in node_ids and end in node_ids:
            if start == end:
                errors.append(f"{edge_label} cannot connect a node to itself")
            elif (start, end) in edge_pairs:
                errors.append(f"duplicate branch edge {start!r}->{end!r}")
            else:
                edge_pairs.add((start, end))
                graph[start].append(end)
                indegree[end] += 1
                if indegree[end] > 1:
                    errors.append(f"branch node {end!r} must not have multiple parents")
                if start in node_positions and end in node_positions and _distance_squared(node_positions[start], node_positions[end]) <= 1e-16:
                    errors.append(f"{edge_label} endpoints must not coincide")
        control_points = edge.get("controlPoints", [])
        if not isinstance(control_points, list) or len(control_points) > MAX_BRANCH_CONTROL_POINTS:
            errors.append(
                f"{edge_label}.controlPoints must contain at most {MAX_BRANCH_CONTROL_POINTS} points"
            )
        else:
            parsed_controls: list[list[float]] = []
            for point_index, point in enumerate(control_points):
                parsed_point = _finite_vector(point, 3)
                if parsed_point is None:
                    errors.append(f"{edge_label}.controlPoints[{point_index}] must be 3 finite numbers")
                else:
                    parsed_controls.append(parsed_point)
            if (
                isinstance(start, str)
                and isinstance(end, str)
                and start in node_positions
                and end in node_positions
                and len(parsed_controls) == len(control_points)
                and _has_zero_length_segment(
                    [node_positions[start], *parsed_controls, node_positions[end]]
                )
            ):
                errors.append(
                    f"{edge_label}.controlPoints must not repeat an endpoint or consecutive point"
                )
    visit_state: dict[str, int] = {}

    def visit(node_id: str) -> None:
        state = visit_state.get(node_id, 0)
        if state == 1:
            errors.append("branch graph must be acyclic")
            return
        if state == 2:
            return
        visit_state[node_id] = 1
        for child in graph.get(node_id, []):
            visit(child)
        visit_state[node_id] = 2

    for node_id in node_ids:
        visit(node_id)
    roots = [node_id for node_id in node_ids if indegree.get(node_id, 0) == 0]
    if node_ids and len(roots) != 1:
        errors.append(f"{label}.nodes must form one connected tree with exactly one root")
    elif roots:
        reachable: set[str] = set()
        pending = [roots[0]]
        while pending:
            current = pending.pop()
            if current in reachable:
                continue
            reachable.add(current)
            pending.extend(graph.get(current, []))
        if reachable != node_ids:
            errors.append(f"{label}.nodes must all be reachable from the single root")
    radial = _validate_special_integer(
        parameters.get("radialSegments"),
        f"{label}.radialSegments",
        errors,
        minimum=3,
        maximum=48,
    )
    per_edge = _validate_special_integer(
        parameters.get("segmentsPerEdge"),
        f"{label}.segmentsPerEdge",
        errors,
        minimum=2,
        maximum=64,
    )
    junction = _validate_special_integer(
        parameters.get("junctionSegments"),
        f"{label}.junctionSegments",
        errors,
        minimum=4,
        maximum=24,
    )
    _validate_boolean(parameters, "capEnds", errors, label)
    if (
        radial is not None
        and per_edge is not None
        and junction is not None
        and isinstance(edges, list)
    ):
        vertices = (
            len(edges) * (per_edge + 1) * (radial + 1)
            + len(nodes) * (junction + 1) * (junction // 2 + 1)
            + len(edges) * 2
        )
        if vertices > MAX_LOFT_VERTICES:
            errors.append(
                f"{label} would emit approximately {vertices} vertices; maximum is {MAX_LOFT_VERTICES}"
            )
    return list(dict.fromkeys(errors))


def _validate_surface_scatter(
    parameters: Mapping[str, Any],
    component: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    label = f"{_component_label(component)} geometryDescriptor.parameters"
    allowed = {
        "representation",
        "surfaceRef",
        "basePrimitive",
        "baseParameters",
        "count",
        "seed",
        "uRange",
        "vRange",
        "excludeMasks",
        "normalOffset",
        "scaleRange",
        "baseScale",
        "spinRange",
        "alignToNormal",
        "baseRotation",
        "_hostSections",
    }
    errors.extend(_unknown_field_errors(parameters, allowed, label))
    if parameters.get("representation") != "loft-surface":
        errors.append(f"{label}.representation must be 'loft-surface'")
    surface_ref = parameters.get("surfaceRef")
    if not isinstance(surface_ref, str) or not surface_ref.strip():
        errors.append(f"{label}.surfaceRef is required")
    _validate_loft_sections_value(parameters.get("_hostSections"), f"{label}.surfaceRef sections", errors)
    _validate_special_integer(
        parameters.get("count"),
        f"{label}.count",
        errors,
        minimum=1,
        maximum=MAX_INSTANCE_COUNT,
    )
    _validate_special_integer(
        parameters.get("seed", 1),
        f"{label}.seed",
        errors,
        minimum=1,
        maximum=2_147_483_647,
    )
    u_range = _validate_unit_range(
        parameters.get("uRange"), f"{label}.uRange", errors, strict=True
    )
    v_range = _validate_unit_range(
        parameters.get("vRange"), f"{label}.vRange", errors, strict=True
    )
    normal_offset = parameters.get("normalOffset", 0)
    if not _is_bounded_number(normal_offset):
        errors.append(f"{label}.normalOffset must be a finite bounded number")
    spin_range = parameters.get("spinRange", 0)
    if not _is_bounded_number(spin_range) or float(spin_range) < 0:
        errors.append(f"{label}.spinRange must be a non-negative finite bounded number")
    _validate_boolean(parameters, "alignToNormal", errors, label)
    base_rotation = parameters.get("baseRotation", [0, 0, 0])
    if _finite_vector(base_rotation, 3) is None:
        errors.append(f"{label}.baseRotation must be 3 finite numbers")
    base_scale = _finite_vector(parameters.get("baseScale"), 3)
    if base_scale is None or any(item <= 0 for item in base_scale):
        errors.append(f"{label}.baseScale must be 3 positive finite numbers")
    scale_range = parameters.get("scaleRange")
    if (
        not isinstance(scale_range, list)
        or len(scale_range) != 2
        or not all(_is_bounded_number(item) and float(item) > 0 for item in scale_range)
        or float(scale_range[0]) > float(scale_range[1])
    ):
        errors.append(f"{label}.scaleRange must be two ordered positive finite numbers")
    masks = parameters.get("excludeMasks", [])
    parsed_masks: list[tuple[list[float], list[float]]] = []
    if not isinstance(masks, list) or len(masks) > MAX_SHELL_OPENINGS:
        errors.append(f"{label}.excludeMasks must contain at most {MAX_SHELL_OPENINGS} entries")
    else:
        for index, mask in enumerate(masks):
            mask_label = f"{label}.excludeMasks[{index}]"
            if not isinstance(mask, Mapping):
                errors.append(f"{mask_label} must be an object")
                continue
            errors.extend(_unknown_field_errors(mask, {"uRange", "vRange"}, mask_label))
            mask_u = _validate_unit_range(mask.get("uRange"), f"{mask_label}.uRange", errors)
            mask_v = _validate_unit_range(mask.get("vRange"), f"{mask_label}.vRange", errors)
            if mask_u is not None and mask_v is not None:
                parsed_masks.append((mask_u, mask_v))
    if u_range is not None and v_range is not None:
        available = _masked_available_fraction(u_range, v_range, parsed_masks)
        if available <= 1e-9:
            errors.append(f"{label}.excludeMasks remove the entire requested scatter surface")
        elif available < 0.05:
            errors.append(
                f"{label}.excludeMasks must leave at least 5% of the requested scatter surface"
            )
    base_primitive = parameters.get("basePrimitive")
    if not isinstance(base_primitive, str) or not base_primitive:
        errors.append(f"{label}.basePrimitive is required")
    elif base_primitive in {"instanced-cluster", "surface-scatter"}:
        errors.append(f"{label}.basePrimitive cannot be {base_primitive!r}")
    elif base_primitive not in GEOMETRY_REGISTRY:
        errors.append(f"{label}.basePrimitive {base_primitive!r} is unsupported")
    elif not GEOMETRY_REGISTRY[base_primitive].instancable:
        errors.append(f"{label}.basePrimitive {base_primitive!r} is not instancable")
    if not isinstance(parameters.get("baseParameters"), Mapping):
        errors.append(f"{label}.baseParameters must be an object")
    return errors


def _validate_deformable_surface(
    parameters: Mapping[str, Any],
    component: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    label = f"{_component_label(component)} geometryDescriptor.parameters"
    allowed = {"representation", "controlGrid", "segments", "folds"}
    errors.extend(_unknown_field_errors(parameters, allowed, label))
    if parameters.get("representation") != "grid":
        errors.append(f"{label}.representation must be 'grid'")

    control_grid = parameters.get("controlGrid")
    parsed_grid: list[list[list[float]]] = []
    if not isinstance(control_grid, list) or not 2 <= len(control_grid) <= 16:
        errors.append(f"{label}.controlGrid must contain 2 to 16 rows")
    else:
        column_count: int | None = None
        for row_index, row in enumerate(control_grid):
            if not isinstance(row, list) or not 2 <= len(row) <= 16:
                errors.append(
                    f"{label}.controlGrid[{row_index}] must contain 2 to 16 local 3D points"
                )
                continue
            if column_count is None:
                column_count = len(row)
            elif len(row) != column_count:
                errors.append(f"{label}.controlGrid must be rectangular")
            parsed_row: list[list[float]] = []
            for column_index, point in enumerate(row):
                parsed = _finite_vector(point, 3)
                if parsed is None:
                    errors.append(
                        f"{label}.controlGrid[{row_index}][{column_index}] must be 3 finite numbers"
                    )
                else:
                    parsed_row.append(parsed)
            parsed_grid.append(parsed_row)
        point_count = sum(len(row) for row in control_grid if isinstance(row, list))
        if point_count > MAX_DEFORMABLE_CONTROL_POINTS:
            errors.append(
                f"{label}.controlGrid must contain at most "
                f"{MAX_DEFORMABLE_CONTROL_POINTS} points"
            )
        if (
            column_count is not None
            and len(parsed_grid) == len(control_grid)
            and all(len(row) == column_count for row in parsed_grid)
        ):
            for row_index in range(len(parsed_grid) - 1):
                for column_index in range(column_count - 1):
                    p00 = parsed_grid[row_index][column_index]
                    p10 = parsed_grid[row_index][column_index + 1]
                    p01 = parsed_grid[row_index + 1][column_index]
                    p11 = parsed_grid[row_index + 1][column_index + 1]
                    first_area = _cross_squared(
                        _vector_difference(p10, p00),
                        _vector_difference(p11, p00),
                    )
                    second_area = _cross_squared(
                        _vector_difference(p11, p00),
                        _vector_difference(p01, p00),
                    )
                    if first_area <= 1e-20 or second_area <= 1e-20:
                        errors.append(
                            f"{label}.controlGrid cell [{row_index},{column_index}] "
                            "must define two non-degenerate triangles"
                        )

    segments = parameters.get("segments")
    if not isinstance(segments, list) or len(segments) != 2:
        errors.append(f"{label}.segments must contain [u, v] integers")
    else:
        first = _validate_special_integer(
            segments[0], f"{label}.segments[0]", errors, minimum=2, maximum=256
        )
        second = _validate_special_integer(
            segments[1], f"{label}.segments[1]", errors, minimum=2, maximum=256
        )
        if first is not None and second is not None:
            sampled_vertices = (first + 1) * (second + 1)
            if sampled_vertices > MAX_DEFORMABLE_SAMPLED_VERTICES:
                errors.append(
                    f"{label}.segments would emit {sampled_vertices} vertices; "
                    f"maximum is {MAX_DEFORMABLE_SAMPLED_VERTICES}"
                )

    folds = parameters.get("folds", [])
    if not isinstance(folds, list):
        errors.append(f"{label}.folds must be an array")
    else:
        if len(folds) > MAX_DEFORMABLE_FOLDS:
            errors.append(f"{label}.folds must contain at most {MAX_DEFORMABLE_FOLDS} folds")
        for index, fold in enumerate(folds):
            fold_label = f"{label}.folds[{index}]"
            if not isinstance(fold, Mapping):
                errors.append(f"{fold_label} must be an object")
                continue
            errors.extend(
                _unknown_field_errors(
                    fold,
                    {"direction", "amplitude", "frequency", "phase", "edgeFade"},
                    fold_label,
                )
            )
            direction = _finite_vector(fold.get("direction"), 2)
            if direction is None:
                errors.append(f"{fold_label}.direction must be 2 finite numbers")
            elif direction[0] * direction[0] + direction[1] * direction[1] <= 1e-16:
                errors.append(f"{fold_label}.direction must be non-zero")
            amplitude = fold.get("amplitude")
            if not _is_bounded_number(amplitude) or abs(float(amplitude)) <= 1e-12:
                errors.append(f"{fold_label}.amplitude must be a non-zero finite number")
            frequency = fold.get("frequency")
            if not _is_bounded_number(frequency) or float(frequency) <= 0:
                errors.append(f"{fold_label}.frequency must be a positive finite number")
            phase = fold.get("phase", 0)
            if not _is_bounded_number(phase):
                errors.append(f"{fold_label}.phase must be a finite number")
            edge_fade = fold.get("edgeFade", 0)
            if not _is_bounded_number(edge_fade) or not 0 <= float(edge_fade) <= 0.49:
                errors.append(f"{fold_label}.edgeFade must be from 0 to 0.49")
    return errors


def _validate_fiber_system(
    parameters: Mapping[str, Any],
    component: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    label = f"{_component_label(component)} geometryDescriptor.parameters"
    allowed = {
        "representation",
        "guides",
        "strandsPerGuide",
        "samples",
        "rootWidth",
        "tipWidth",
        "spread",
        "clump",
        "curl",
        "cardPlanes",
        "seed",
        "lengthVariation",
        "widthVariation",
    }
    errors.extend(_unknown_field_errors(parameters, allowed, label))
    if parameters.get("representation") != "ribbon-cards":
        errors.append(f"{label}.representation must be 'ribbon-cards'")

    guides = parameters.get("guides")
    guide_count = 0
    total_guide_points = 0
    if not isinstance(guides, list) or not 1 <= len(guides) <= MAX_FIBER_GUIDES:
        errors.append(f"{label}.guides must contain 1 to {MAX_FIBER_GUIDES} guide paths")
    else:
        guide_count = len(guides)
        for guide_index, guide in enumerate(guides):
            guide_label = f"{label}.guides[{guide_index}]"
            if not isinstance(guide, list) or not 2 <= len(guide) <= 64:
                errors.append(f"{guide_label} must contain 2 to 64 local 3D points")
                continue
            total_guide_points += len(guide)
            parsed = [_finite_vector(point, 3) for point in guide]
            for point_index, point in enumerate(parsed):
                if point is None:
                    errors.append(f"{guide_label}[{point_index}] must be 3 finite numbers")
            if all(point is not None for point in parsed):
                parsed_points = [point for point in parsed if point is not None]
                if _has_zero_length_segment(parsed_points):
                    errors.append(
                        f"{guide_label} must not contain identical consecutive points"
                    )
        if total_guide_points > MAX_FIBER_GUIDE_POINTS:
            errors.append(
                f"{label}.guides must contain at most {MAX_FIBER_GUIDE_POINTS} total points"
            )

    strands_per_guide = _validate_special_integer(
        parameters.get("strandsPerGuide", 1),
        f"{label}.strandsPerGuide",
        errors,
        minimum=1,
        maximum=MAX_FIBER_STRANDS,
    )
    samples = _validate_special_integer(
        parameters.get("samples", 8),
        f"{label}.samples",
        errors,
        minimum=2,
        maximum=MAX_FIBER_SAMPLES,
    )
    card_planes = parameters.get("cardPlanes", 2)
    if not isinstance(card_planes, int) or isinstance(card_planes, bool) or card_planes not in {1, 2}:
        errors.append(f"{label}.cardPlanes must be 1 or 2")
        parsed_card_planes: int | None = None
    else:
        parsed_card_planes = card_planes

    root_width = parameters.get("rootWidth")
    if not _is_bounded_number(root_width) or float(root_width) <= 0:
        errors.append(f"{label}.rootWidth must be a positive finite number")
    tip_width = parameters.get("tipWidth", 0)
    if not _is_bounded_number(tip_width) or float(tip_width) < 0:
        errors.append(f"{label}.tipWidth must be a non-negative finite number")
    elif _is_bounded_number(root_width) and float(tip_width) > float(root_width):
        errors.append(f"{label}.tipWidth must not exceed rootWidth")
    spread = parameters.get("spread", 0)
    if not _is_bounded_number(spread) or float(spread) < 0:
        errors.append(f"{label}.spread must be a non-negative finite number")
    clump = parameters.get("clump", 1)
    if not _is_bounded_number(clump) or not 0 <= float(clump) <= 1:
        errors.append(f"{label}.clump must be a finite number from 0 to 1")
    length_variation = parameters.get("lengthVariation", 0)
    if not _is_bounded_number(length_variation) or not 0 <= float(length_variation) <= 0.5:
        errors.append(f"{label}.lengthVariation must be from 0 to 0.5")
    width_variation = parameters.get("widthVariation", 0)
    if not _is_bounded_number(width_variation) or not 0 <= float(width_variation) <= 0.95:
        errors.append(f"{label}.widthVariation must be from 0 to 0.95")

    curl = parameters.get("curl", {})
    if not isinstance(curl, Mapping):
        errors.append(f"{label}.curl must be an object")
    else:
        errors.extend(
            _unknown_field_errors(curl, {"amplitude", "frequency", "phase"}, f"{label}.curl")
        )
        for field in ("amplitude", "frequency"):
            value = curl.get(field, 0)
            if not _is_bounded_number(value) or float(value) < 0:
                errors.append(f"{label}.curl.{field} must be a non-negative finite number")
        if not _is_bounded_number(curl.get("phase", 0)):
            errors.append(f"{label}.curl.phase must be a finite number")

    seed = parameters.get("seed", 1)
    _validate_special_integer(
        seed,
        f"{label}.seed",
        errors,
        minimum=1,
        maximum=2_147_483_647,
    )
    if guide_count and strands_per_guide is not None:
        strand_count = guide_count * strands_per_guide
        if strand_count > MAX_FIBER_STRANDS:
            errors.append(
                f"{label} would emit {strand_count} strands; maximum is {MAX_FIBER_STRANDS}"
            )
        if samples is not None and parsed_card_planes is not None:
            quads = strand_count * samples * parsed_card_planes
            if quads > MAX_FIBER_QUADS:
                errors.append(
                    f"{label} would emit {quads} ribbon quads; maximum is {MAX_FIBER_QUADS}"
                )
    return errors


def _gaussian_source_value(
    position: list[float],
    sources: list[dict[str, Any]],
) -> float:
    value = 0.0
    for source in sources:
        shape = source.get("shape", "sphere")
        if shape == "ellipsoid":
            normalized_distance = sum(
                ((position[index] - source["position"][index]) / source["radii"][index]) ** 2
                for index in range(3)
            )
        elif shape == "capsule":
            start = source["start"]
            end = source["end"]
            segment = _vector_difference(end, start)
            length_squared = sum(item * item for item in segment)
            offset = _vector_difference(position, start)
            amount = max(0.0, min(1.0, sum(offset[index] * segment[index] for index in range(3)) / length_squared))
            closest = [start[index] + segment[index] * amount for index in range(3)]
            normalized_distance = _distance_squared(position, closest) / (source["radius"] ** 2)
        else:
            normalized_distance = _distance_squared(position, source["position"]) / (source["radius"] ** 2)
        falloff = float(source.get("falloff", 0.5))
        density = source["strength"] * math.exp(-normalized_distance * falloff)
        value += -density if source["operation"] == "subtract" else density
    return value


def _validate_implicit_surface(
    parameters: Mapping[str, Any],
    component: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    label = f"{_component_label(component)} geometryDescriptor.parameters"
    allowed = {
        "representation",
        "bounds",
        "resolution",
        "isoLevel",
        "sources",
        "uvProjection",
    }
    errors.extend(_unknown_field_errors(parameters, allowed, label))
    if parameters.get("representation") != "metaballs":
        errors.append(f"{label}.representation must be 'metaballs'")
    bounds = _validate_special_bounds(parameters.get("bounds"), f"{label}.bounds", errors)

    resolution = parameters.get("resolution")
    parsed_resolution: list[int] | None = None
    if not isinstance(resolution, list) or len(resolution) != 3:
        errors.append(f"{label}.resolution must contain 3 integers")
    else:
        parsed_axes = [
            _validate_special_integer(
                value,
                f"{label}.resolution[{index}]",
                errors,
                minimum=4,
                maximum=40,
            )
            for index, value in enumerate(resolution)
        ]
        if all(axis is not None for axis in parsed_axes):
            parsed_resolution = [int(axis) for axis in parsed_axes if axis is not None]
            cells = math.prod(axis - 1 for axis in parsed_resolution)
            if cells > MAX_IMPLICIT_CELLS:
                errors.append(
                    f"{label}.resolution would evaluate {cells} cells; "
                    f"maximum is {MAX_IMPLICIT_CELLS}"
                )
    minimum_field_scales = _minimum_resolvable_field_scales(bounds, parsed_resolution)

    iso_level = parameters.get("isoLevel")
    if not _is_bounded_number(iso_level) or float(iso_level) <= 0:
        errors.append(f"{label}.isoLevel must be a positive finite number")

    sources_value = parameters.get("sources")
    parsed_sources: list[dict[str, Any]] = []
    add_count = 0
    if (
        not isinstance(sources_value, list)
        or not 1 <= len(sources_value) <= MAX_IMPLICIT_SOURCES
    ):
        errors.append(f"{label}.sources must contain 1 to {MAX_IMPLICIT_SOURCES} sources")
    else:
        for index, source in enumerate(sources_value):
            source_label = f"{label}.sources[{index}]"
            if not isinstance(source, Mapping):
                errors.append(f"{source_label} must be an object")
                continue
            shape = source.get("shape", "sphere")
            if shape not in {"sphere", "ellipsoid", "capsule"}:
                errors.append(f"{source_label}.shape must be sphere, ellipsoid, or capsule")
            allowed_fields = {"shape", "strength", "operation"}
            allowed_fields.update(
                {"start", "end", "radius"}
                if shape == "capsule"
                else {"position", "radii"}
                if shape == "ellipsoid"
                else {"position", "radius"}
            )
            errors.extend(_unknown_field_errors(source, allowed_fields, source_label))
            parsed_source: dict[str, Any] = {"shape": shape}
            shape_valid = shape in {"sphere", "ellipsoid", "capsule"}
            if shape == "capsule":
                start = _finite_vector(source.get("start"), 3)
                end = _finite_vector(source.get("end"), 3)
                radius = source.get("radius")
                radius_resolvable = True
                if start is None:
                    errors.append(f"{source_label}.start must be 3 finite numbers")
                if end is None:
                    errors.append(f"{source_label}.end must be 3 finite numbers")
                if start is not None and end is not None and _distance_squared(start, end) <= 1e-16:
                    errors.append(f"{source_label}.start and end must not coincide")
                if not _is_bounded_number(radius) or float(radius) <= 0:
                    errors.append(f"{source_label}.radius must be a positive finite number")
                elif (
                    minimum_field_scales is not None
                    and float(radius) < max(minimum_field_scales)
                ):
                    radius_resolvable = False
                    errors.append(
                        f"{source_label}.radius is below the minimum resolvable field scale "
                        f"{max(minimum_field_scales):.6g} for its bounds/resolution"
                    )
                if bounds is not None:
                    for field, point in (("start", start), ("end", end)):
                        if point is not None and any(
                            point[axis] < bounds[0][axis] or point[axis] > bounds[1][axis]
                            for axis in range(3)
                        ):
                            errors.append(f"{source_label}.{field} must be inside bounds")
                if start is not None and end is not None and _distance_squared(start, end) > 1e-16 and _is_bounded_number(radius) and float(radius) > 0 and radius_resolvable:
                    parsed_source.update({"start": start, "end": end, "radius": float(radius)})
                else:
                    shape_valid = False
            elif shape == "ellipsoid":
                position = _finite_vector(source.get("position"), 3)
                radii = _finite_vector(source.get("radii"), 3)
                radii_resolvable = True
                if position is None:
                    errors.append(f"{source_label}.position must be 3 finite numbers")
                if radii is None or any(item <= 0 for item in radii):
                    errors.append(f"{source_label}.radii must be 3 positive finite numbers")
                elif minimum_field_scales is not None:
                    for axis, radius in enumerate(radii):
                        if radius < minimum_field_scales[axis]:
                            radii_resolvable = False
                            errors.append(
                                f"{source_label}.radii[{axis}] is below the minimum resolvable "
                                f"field scale {minimum_field_scales[axis]:.6g} for its bounds/resolution"
                            )
                if bounds is not None and position is not None and any(
                    position[axis] < bounds[0][axis] or position[axis] > bounds[1][axis]
                    for axis in range(3)
                ):
                    errors.append(f"{source_label}.position must be inside bounds")
                if position is not None and radii is not None and all(item > 0 for item in radii) and radii_resolvable:
                    parsed_source.update({"position": position, "radii": radii})
                else:
                    shape_valid = False
            else:
                position = _finite_vector(source.get("position"), 3)
                radius = source.get("radius")
                radius_resolvable = True
                if position is None:
                    errors.append(f"{source_label}.position must be 3 finite numbers")
                if not _is_bounded_number(radius) or float(radius) <= 0:
                    errors.append(f"{source_label}.radius must be a positive finite number")
                elif (
                    minimum_field_scales is not None
                    and float(radius) < max(minimum_field_scales)
                ):
                    radius_resolvable = False
                    errors.append(
                        f"{source_label}.radius is below the minimum resolvable field scale "
                        f"{max(minimum_field_scales):.6g} for its bounds/resolution"
                    )
                if bounds is not None and position is not None and any(
                    position[axis] < bounds[0][axis] or position[axis] > bounds[1][axis]
                    for axis in range(3)
                ):
                    errors.append(f"{source_label}.position must be inside bounds")
                if position is not None and _is_bounded_number(radius) and float(radius) > 0 and radius_resolvable:
                    parsed_source.update({"position": position, "radius": float(radius)})
                else:
                    shape_valid = False
            strength = source.get("strength")
            if not _is_bounded_number(strength) or float(strength) <= 0:
                errors.append(f"{source_label}.strength must be a positive finite number")
            operation = source.get("operation", "add")
            if operation not in {"add", "subtract"}:
                errors.append(f"{source_label}.operation must be add or subtract")
            elif operation == "add":
                add_count += 1
            if shape_valid and _is_bounded_number(strength) and float(strength) > 0 and operation in {"add", "subtract"}:
                parsed_source.update({"strength": float(strength), "operation": operation})
                parsed_sources.append(parsed_source)
        if add_count == 0:
            errors.append(f"{label}.sources must contain at least one additive source")

    uv_projection = parameters.get("uvProjection", "xz")
    if uv_projection not in {"xy", "xz", "yz"}:
        errors.append(f"{label}.uvProjection must be xy, xz, or yz")

    if (
        bounds is not None
        and parsed_resolution is not None
        and _is_bounded_number(iso_level)
        and float(iso_level) > 0
        and len(parsed_sources) == len(sources_value or [])
        and add_count > 0
    ):
        minimum, maximum = bounds
        sampled_minimum = math.inf
        sampled_maximum = -math.inf
        for z in range(parsed_resolution[2]):
            z_value = minimum[2] + (
                (maximum[2] - minimum[2]) * z / (parsed_resolution[2] - 1)
            )
            for y in range(parsed_resolution[1]):
                y_value = minimum[1] + (
                    (maximum[1] - minimum[1]) * y / (parsed_resolution[1] - 1)
                )
                for x in range(parsed_resolution[0]):
                    x_value = minimum[0] + (
                        (maximum[0] - minimum[0]) * x / (parsed_resolution[0] - 1)
                    )
                    value = _gaussian_source_value(
                        [x_value, y_value, z_value], parsed_sources
                    )
                    sampled_minimum = min(sampled_minimum, value)
                    sampled_maximum = max(sampled_maximum, value)
        if not sampled_minimum < float(iso_level) < sampled_maximum:
            errors.append(
                f"{label}.isoLevel must cross the sampled field "
                f"({sampled_minimum:.6g} < isoLevel < {sampled_maximum:.6g})"
            )
    return errors


def _validate_sculpted_surface(
    parameters: Mapping[str, Any],
    component: Mapping[str, Any],
) -> list[str]:
    """Validate one bounded implicit field whose terms are semantic sculpt operations."""

    errors: list[str] = []
    label = f"{_component_label(component)} geometryDescriptor.parameters"
    errors.extend(
        _unknown_field_errors(
            parameters,
            {
                "representation",
                "bounds",
                "resolution",
                "isoLevel",
                "sources",
                "surfaceModifiers",
                "connectivity",
                "uvProjection",
            },
            label,
        )
    )
    if parameters.get("representation") != "field-sculpt":
        errors.append(f"{label}.representation must be 'field-sculpt'")
    if parameters.get("connectivity") != "single-surface":
        errors.append(f"{label}.connectivity must be 'single-surface'")
    bounds = _validate_special_bounds(parameters.get("bounds"), f"{label}.bounds", errors)
    resolution_value = parameters.get("resolution")
    resolution: list[int] | None = None
    if not isinstance(resolution_value, list) or len(resolution_value) != 3:
        errors.append(f"{label}.resolution must contain 3 integers")
    else:
        axes = [
            _validate_special_integer(
                value,
                f"{label}.resolution[{index}]",
                errors,
                minimum=8,
                maximum=64,
            )
            for index, value in enumerate(resolution_value)
        ]
        if all(axis is not None for axis in axes):
            resolution = [int(axis) for axis in axes if axis is not None]
            cells = math.prod(axis - 1 for axis in resolution)
            if cells > MAX_SCULPT_CELLS:
                errors.append(
                    f"{label}.resolution would evaluate {cells} cells; maximum is {MAX_SCULPT_CELLS}"
                )
    iso_level = parameters.get("isoLevel")
    if not _is_bounded_number(iso_level) or float(iso_level) <= 0:
        errors.append(f"{label}.isoLevel must be a positive finite number")
    minimum_field_scales = _minimum_resolvable_field_scales(bounds, resolution)

    seen_ids: set[str] = set()
    parsed_sources: list[dict[str, Any]] = []

    def parse_source(source: Any, source_label: str, *, require_id: bool = True) -> dict[str, Any] | None:
        if not isinstance(source, Mapping):
            errors.append(f"{source_label} must be an object")
            return None
        source_id = source.get("id")
        if require_id:
            if not isinstance(source_id, str) or not source_id.strip():
                errors.append(f"{source_label}.id is required")
            elif source_id in seen_ids:
                errors.append(f"duplicate sculpt field id {source_id!r}")
            else:
                seen_ids.add(source_id)
        shape = source.get("shape", "sphere")
        if shape not in {"sphere", "ellipsoid", "capsule"}:
            errors.append(f"{source_label}.shape must be sphere, ellipsoid, or capsule")
            return None
        allowed = {"id", "shape", "strength", "operation", "falloff"}
        allowed.update(
            {"start", "end", "radius"}
            if shape == "capsule"
            else {"position", "radii"}
            if shape == "ellipsoid"
            else {"position", "radius"}
        )
        errors.extend(_unknown_field_errors(source, allowed, source_label))
        strength = source.get("strength")
        falloff = source.get("falloff", 0.5)
        operation = source.get("operation", "add")
        if not _is_bounded_number(strength) or float(strength) <= 0:
            errors.append(f"{source_label}.strength must be a positive finite number")
        if not _is_bounded_number(falloff) or not 0.05 <= float(falloff) <= 8:
            errors.append(f"{source_label}.falloff must be from 0.05 to 8")
        if operation not in {"add", "subtract"}:
            errors.append(f"{source_label}.operation must be add or subtract")
        parsed: dict[str, Any] = {
            "id": str(source_id or source_label),
            "shape": shape,
            "strength": float(strength) if _is_bounded_number(strength) else 0.0,
            "falloff": float(falloff) if _is_bounded_number(falloff) else 0.5,
            "operation": operation,
        }
        if shape == "capsule":
            start = _finite_vector(source.get("start"), 3)
            end = _finite_vector(source.get("end"), 3)
            radius = source.get("radius")
            if start is None:
                errors.append(f"{source_label}.start must be 3 finite numbers")
            if end is None:
                errors.append(f"{source_label}.end must be 3 finite numbers")
            if start is not None and end is not None and _distance_squared(start, end) <= 1e-16:
                errors.append(f"{source_label}.start and end must not coincide")
            if not _is_bounded_number(radius) or float(radius) <= 0:
                errors.append(f"{source_label}.radius must be a positive finite number")
            elif (
                minimum_field_scales is not None
                and float(radius) < max(minimum_field_scales)
            ):
                errors.append(
                    f"{source_label}.radius is below the minimum resolvable field scale "
                    f"{max(minimum_field_scales):.6g} for its bounds/resolution"
                )
                return None
            if start is None or end is None or not _is_bounded_number(radius) or float(radius) <= 0:
                return None
            parsed.update({"start": start, "end": end, "radius": float(radius)})
            points = (start, end)
        elif shape == "ellipsoid":
            position = _finite_vector(source.get("position"), 3)
            radii = _finite_vector(source.get("radii"), 3)
            if position is None:
                errors.append(f"{source_label}.position must be 3 finite numbers")
            if radii is None or any(value <= 0 for value in radii):
                errors.append(f"{source_label}.radii must be 3 positive finite numbers")
            elif minimum_field_scales is not None:
                unresolved_axes = [
                    axis
                    for axis, radius in enumerate(radii)
                    if radius < minimum_field_scales[axis]
                ]
                for axis in unresolved_axes:
                    errors.append(
                        f"{source_label}.radii[{axis}] is below the minimum resolvable field "
                        f"scale {minimum_field_scales[axis]:.6g} for its bounds/resolution"
                    )
                if unresolved_axes:
                    return None
            if position is None or radii is None or any(value <= 0 for value in radii):
                return None
            parsed.update({"position": position, "radii": radii})
            points = (position,)
        else:
            position = _finite_vector(source.get("position"), 3)
            radius = source.get("radius")
            if position is None:
                errors.append(f"{source_label}.position must be 3 finite numbers")
            if not _is_bounded_number(radius) or float(radius) <= 0:
                errors.append(f"{source_label}.radius must be a positive finite number")
            elif (
                minimum_field_scales is not None
                and float(radius) < max(minimum_field_scales)
            ):
                errors.append(
                    f"{source_label}.radius is below the minimum resolvable field scale "
                    f"{max(minimum_field_scales):.6g} for its bounds/resolution"
                )
                return None
            if position is None or not _is_bounded_number(radius) or float(radius) <= 0:
                return None
            parsed.update({"position": position, "radius": float(radius)})
            points = (position,)
        if bounds is not None:
            minimum, maximum = bounds
            for point in points:
                if any(point[axis] < minimum[axis] or point[axis] > maximum[axis] for axis in range(3)):
                    errors.append(f"{source_label} control points must stay inside bounds")
                    break
        return parsed

    sources_value = parameters.get("sources")
    if not isinstance(sources_value, list) or not 1 <= len(sources_value) <= MAX_SCULPT_SOURCES:
        errors.append(f"{label}.sources must contain 1 to {MAX_SCULPT_SOURCES} sources")
    else:
        for index, source in enumerate(sources_value):
            parsed = parse_source(source, f"{label}.sources[{index}]")
            if parsed is not None:
                parsed_sources.append(parsed)

    modifiers = parameters.get("surfaceModifiers", [])
    if not isinstance(modifiers, list) or len(modifiers) > MAX_SCULPT_MODIFIERS:
        errors.append(
            f"{label}.surfaceModifiers must be an array with at most {MAX_SCULPT_MODIFIERS} items"
        )
    else:
        for index, modifier in enumerate(modifiers):
            modifier_label = f"{label}.surfaceModifiers[{index}]"
            if not isinstance(modifier, Mapping):
                errors.append(f"{modifier_label} must be an object")
                continue
            modifier_type = modifier.get("type")
            if modifier_type not in {"inflate", "pinch", "ridge", "crease"}:
                errors.append(f"{modifier_label}.type must be inflate, pinch, ridge, or crease")
                continue
            common = {
                "id": modifier.get("id"),
                "strength": modifier.get("strength"),
                "falloff": modifier.get("falloff", 0.5),
                "operation": "add" if modifier_type in {"inflate", "ridge"} else "subtract",
            }
            if modifier_type in {"ridge", "crease"}:
                errors.extend(
                    _unknown_field_errors(
                        modifier,
                        {"id", "type", "start", "end", "radius", "strength", "falloff"},
                        modifier_label,
                    )
                )
                candidate = {
                    **common,
                    "shape": "capsule",
                    "start": modifier.get("start"),
                    "end": modifier.get("end"),
                    "radius": modifier.get("radius"),
                }
            else:
                errors.extend(
                    _unknown_field_errors(
                        modifier,
                        {"id", "type", "position", "radius", "radii", "strength", "falloff"},
                        modifier_label,
                    )
                )
                has_radius = "radius" in modifier
                has_radii = "radii" in modifier
                if has_radius == has_radii:
                    errors.append(f"{modifier_label} must provide exactly one of radius or radii")
                    continue
                candidate = {
                    **common,
                    "shape": "sphere" if has_radius else "ellipsoid",
                    "position": modifier.get("position"),
                    **(
                        {"radius": modifier.get("radius")}
                        if has_radius
                        else {"radii": modifier.get("radii")}
                    ),
                }
            parsed = parse_source(candidate, modifier_label)
            if parsed is not None:
                parsed_sources.append(parsed)

    if len(parsed_sources) > MAX_SCULPT_SOURCES + MAX_SCULPT_MODIFIERS:
        errors.append(f"{label} contains too many combined field terms")
    if not any(source.get("operation") == "add" for source in parsed_sources):
        errors.append(f"{label} needs at least one additive source or modifier")
    if (
        resolution is not None
        and parsed_sources
        and math.prod(resolution) * len(parsed_sources) > MAX_SCULPT_FIELD_EVALUATIONS
    ):
        errors.append(
            f"{label} field workload exceeds {MAX_SCULPT_FIELD_EVALUATIONS} sample-term evaluations; "
            "reduce resolution or consolidate overlapping sculpt terms"
        )
    uv_projection = parameters.get("uvProjection", "xz")
    if uv_projection not in {"xy", "xz", "yz"}:
        errors.append(f"{label}.uvProjection must be xy, xz, or yz")

    if (
        bounds is not None
        and resolution is not None
        and _is_bounded_number(iso_level)
        and float(iso_level) > 0
        and parsed_sources
        and not errors
    ):
        minimum, maximum = bounds
        active: set[int] = set()
        boundary_active = False
        total = resolution[0] * resolution[1] * resolution[2]
        sampled_values = [0.0] * total
        sampled_minimum = math.inf
        sampled_maximum = -math.inf
        for z in range(resolution[2]):
            position_z = minimum[2] + (maximum[2] - minimum[2]) * z / (resolution[2] - 1)
            for y in range(resolution[1]):
                position_y = minimum[1] + (maximum[1] - minimum[1]) * y / (resolution[1] - 1)
                for x in range(resolution[0]):
                    position_x = minimum[0] + (maximum[0] - minimum[0]) * x / (resolution[0] - 1)
                    value = _gaussian_source_value(
                        [position_x, position_y, position_z], parsed_sources
                    )
                    index = (z * resolution[1] + y) * resolution[0] + x
                    sampled_values[index] = value
                    sampled_minimum = min(sampled_minimum, value)
                    sampled_maximum = max(sampled_maximum, value)
                    if value < float(iso_level):
                        continue
                    active.add(index)
                    boundary_active = boundary_active or (
                        x in {0, resolution[0] - 1}
                        or y in {0, resolution[1] - 1}
                        or z in {0, resolution[2] - 1}
                    )
        strict_crossing = sampled_minimum < float(iso_level) < sampled_maximum
        if not strict_crossing:
            errors.append(
                f"{label}.isoLevel must lie strictly inside the sampled field range "
                f"({sampled_minimum:.6g}, {sampled_maximum:.6g})"
            )
        if not active or len(active) == total:
            errors.append(f"{label}.isoLevel must cross the sculpt field")
        if boundary_active:
            errors.append(f"{label} sculpt field reaches bounds and would emit an open/clipped surface")
        has_nondegenerate_crossing = False
        if strict_crossing:
            cube_offsets = (
                (0, 0, 0),
                (1, 0, 0),
                (1, 1, 0),
                (0, 1, 0),
                (0, 0, 1),
                (1, 0, 1),
                (1, 1, 1),
                (0, 1, 1),
            )
            tetrahedra = (
                (0, 5, 1, 6),
                (0, 1, 2, 6),
                (0, 2, 3, 6),
                (0, 3, 7, 6),
                (0, 7, 4, 6),
                (0, 4, 5, 6),
            )
            tetra_edges = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))
            for cell_z in range(resolution[2] - 1):
                if has_nondegenerate_crossing:
                    break
                for cell_y in range(resolution[1] - 1):
                    if has_nondegenerate_crossing:
                        break
                    for cell_x in range(resolution[0] - 1):
                        cube_values = [
                            sampled_values[
                                ((cell_z + offset[2]) * resolution[1] + cell_y + offset[1])
                                * resolution[0]
                                + cell_x
                                + offset[0]
                            ]
                            for offset in cube_offsets
                        ]
                        for tetrahedron in tetrahedra:
                            crossings: list[tuple[float, float, float]] = []
                            for first_edge, second_edge in tetra_edges:
                                first_index = tetrahedron[first_edge]
                                second_index = tetrahedron[second_edge]
                                first_value = cube_values[first_index]
                                second_value = cube_values[second_index]
                                if (first_value >= float(iso_level)) == (
                                    second_value >= float(iso_level)
                                ):
                                    continue
                                mix = (float(iso_level) - first_value) / (
                                    second_value - first_value
                                )
                                first_offset = cube_offsets[first_index]
                                second_offset = cube_offsets[second_index]
                                point = tuple(
                                    float(first_offset[axis])
                                    + (float(second_offset[axis]) - float(first_offset[axis])) * mix
                                    for axis in range(3)
                                )
                                if not any(
                                    sum(
                                        (point[axis] - existing[axis]) ** 2
                                        for axis in range(3)
                                    )
                                    <= 1e-20
                                    for existing in crossings
                                ):
                                    crossings.append(point)
                            for first in range(len(crossings) - 2):
                                if has_nondegenerate_crossing:
                                    break
                                for second in range(first + 1, len(crossings) - 1):
                                    a = tuple(
                                        crossings[second][axis] - crossings[first][axis]
                                        for axis in range(3)
                                    )
                                    for third in range(second + 1, len(crossings)):
                                        b = tuple(
                                            crossings[third][axis] - crossings[first][axis]
                                            for axis in range(3)
                                        )
                                        cross = (
                                            a[1] * b[2] - a[2] * b[1],
                                            a[2] * b[0] - a[0] * b[2],
                                            a[0] * b[1] - a[1] * b[0],
                                        )
                                        if sum(value * value for value in cross) > 1e-18:
                                            has_nondegenerate_crossing = True
                                            break
                            if has_nondegenerate_crossing:
                                break
                        if has_nondegenerate_crossing:
                            break
        if strict_crossing and not has_nondegenerate_crossing:
            errors.append(
                f"{label}.isoLevel does not produce a non-degenerate crossing tetrahedron"
            )
        if active:
            visited: set[int] = set()
            stack = [next(iter(active))]
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                x = current % resolution[0]
                yz = current // resolution[0]
                y = yz % resolution[1]
                z = yz // resolution[1]
                for next_x, next_y, next_z in (
                    (x - 1, y, z),
                    (x + 1, y, z),
                    (x, y - 1, z),
                    (x, y + 1, z),
                    (x, y, z - 1),
                    (x, y, z + 1),
                ):
                    if not (
                        0 <= next_x < resolution[0]
                        and 0 <= next_y < resolution[1]
                        and 0 <= next_z < resolution[2]
                    ):
                        continue
                    neighbor = (
                        (next_z * resolution[1] + next_y) * resolution[0] + next_x
                    )
                    if neighbor in active and neighbor not in visited:
                        stack.append(neighbor)
            if len(visited) != len(active):
                errors.append(
                    f"{label}.connectivity single-surface failed; field contains disconnected solid regions"
                )
            exterior: set[int] = set()
            exterior_stack: list[int] = []
            for index in range(total):
                if index in active:
                    continue
                x = index % resolution[0]
                yz = index // resolution[0]
                y = yz % resolution[1]
                z = yz // resolution[1]
                if (
                    x in {0, resolution[0] - 1}
                    or y in {0, resolution[1] - 1}
                    or z in {0, resolution[2] - 1}
                ):
                    exterior_stack.append(index)
            while exterior_stack:
                current = exterior_stack.pop()
                if current in exterior:
                    continue
                exterior.add(current)
                x = current % resolution[0]
                yz = current // resolution[0]
                y = yz % resolution[1]
                z = yz // resolution[1]
                for next_x, next_y, next_z in (
                    (x - 1, y, z),
                    (x + 1, y, z),
                    (x, y - 1, z),
                    (x, y + 1, z),
                    (x, y, z - 1),
                    (x, y, z + 1),
                ):
                    if not (
                        0 <= next_x < resolution[0]
                        and 0 <= next_y < resolution[1]
                        and 0 <= next_z < resolution[2]
                    ):
                        continue
                    neighbor = (
                        (next_z * resolution[1] + next_y) * resolution[0] + next_x
                    )
                    if neighbor not in active and neighbor not in exterior:
                        exterior_stack.append(neighbor)
            if len(active) + len(exterior) != total:
                errors.append(
                    f"{label}.connectivity single-surface failed; field contains an enclosed void that would emit a separate inner surface"
                )
    return list(dict.fromkeys(errors))


def _validate_volume_field(
    parameters: Mapping[str, Any],
    component: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    label = f"{_component_label(component)} geometryDescriptor.parameters"
    allowed = {
        "representation",
        "bounds",
        "sources",
        "particleCount",
        "cardPlanes",
        "cardSize",
        "seed",
    }
    errors.extend(_unknown_field_errors(parameters, allowed, label))
    if parameters.get("representation") != "crossed-cards":
        errors.append(f"{label}.representation must be 'crossed-cards'")
    bounds = _validate_special_bounds(parameters.get("bounds"), f"{label}.bounds", errors)

    sources = parameters.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_VOLUME_SOURCES:
        errors.append(f"{label}.sources must contain 1 to {MAX_VOLUME_SOURCES} sources")
    else:
        for index, source in enumerate(sources):
            source_label = f"{label}.sources[{index}]"
            if not isinstance(source, Mapping):
                errors.append(f"{source_label} must be an object")
                continue
            errors.extend(
                _unknown_field_errors(
                    source,
                    {"position", "radius", "density"},
                    source_label,
                )
            )
            position = _finite_vector(source.get("position"), 3)
            if position is None:
                errors.append(f"{source_label}.position must be 3 finite numbers")
            elif bounds is not None and any(
                position[axis] < bounds[0][axis] or position[axis] > bounds[1][axis]
                for axis in range(3)
            ):
                errors.append(f"{source_label}.position must be inside bounds")
            for field in ("radius", "density"):
                value = source.get(field)
                if not _is_bounded_number(value) or float(value) <= 0:
                    errors.append(f"{source_label}.{field} must be a positive finite number")

    _validate_special_integer(
        parameters.get("particleCount"),
        f"{label}.particleCount",
        errors,
        minimum=1,
        maximum=MAX_VOLUME_PARTICLES,
    )
    card_planes = parameters.get("cardPlanes")
    if not isinstance(card_planes, int) or isinstance(card_planes, bool) or card_planes not in {2, 3}:
        errors.append(f"{label}.cardPlanes must be 2 or 3")
    card_size = parameters.get("cardSize")
    if (
        not isinstance(card_size, list)
        or len(card_size) != 2
        or not all(_is_bounded_number(value) and float(value) > 0 for value in card_size)
    ):
        errors.append(f"{label}.cardSize must contain two positive finite numbers")
    elif float(card_size[0]) > float(card_size[1]):
        errors.append(f"{label}.cardSize minimum must not exceed maximum")
    _validate_special_integer(
        parameters.get("seed", 1),
        f"{label}.seed",
        errors,
        minimum=1,
        maximum=2_147_483_647,
    )
    return errors


def _repetition_map(
    repetition_systems: Iterable[Mapping[str, Any]] | None,
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for item in repetition_systems or []:
        if isinstance(item, Mapping) and isinstance(item.get("id"), str) and item["id"].strip():
            result[item["id"]] = item
    return result


def _system_payload(system: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(system)
    nested = system.get("parameters")
    if isinstance(nested, Mapping):
        payload.update(nested)
    return payload


def _repetition_ref(parameters: Mapping[str, Any]) -> str | None:
    for key in ("repetitionSystemRef", "repetitionRef", "systemRef"):
        value = parameters.get(key)
        if isinstance(value, str) and value.strip():
            return value
    value = parameters.get("repetitionSystem")
    return value if isinstance(value, str) and value.strip() else None


def _layout_mode(payload: Mapping[str, Any]) -> str:
    value = payload.get("type", payload.get("mode", payload.get("layout", "explicit")))
    aliases = {
        "alongPath": "along-path",
        "path": "along-path",
        "random": "scatter",
        "instances": "explicit",
    }
    return aliases.get(str(value), str(value))


def _validate_layout_vector(
    payload: Mapping[str, Any],
    key: str,
    label: str,
    errors: list[str],
    *,
    positive: bool = False,
    non_negative: bool = False,
    allow_scalar: bool = False,
    allow_two: bool = False,
) -> None:
    if key not in payload:
        return
    value = payload[key]
    if allow_scalar and _is_number(value):
        values = [float(value)]
    elif isinstance(value, list) and len(value) in ({2, 3} if allow_two else {3}) and all(
        _is_number(item) for item in value
    ):
        values = [float(item) for item in value]
    else:
        shape = "a finite number or 2/3 finite numbers" if allow_scalar else (
            "2 or 3 finite numbers" if allow_two else "3 finite numbers"
        )
        errors.append(f"{label}.{key} must be {shape}")
        return
    if positive and any(item <= 0 for item in values):
        errors.append(f"{label}.{key} values must be positive")
    if non_negative and any(item < 0 for item in values):
        errors.append(f"{label}.{key} values must be non-negative")


def _validate_layout_bool(
    payload: Mapping[str, Any],
    key: str,
    label: str,
    errors: list[str],
) -> None:
    if key in payload and not isinstance(payload[key], bool):
        errors.append(f"{label}.{key} must be boolean")


def _validate_repetition_payload(payload: Mapping[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    mode = _layout_mode(payload)
    if mode not in {"explicit", "grid", "radial", "along-path", "scatter"}:
        errors.append(f"{label}.type must be explicit, grid, radial, along-path, or scatter")
        return errors
    _validate_layout_vector(payload, "rotation", label, errors)
    _validate_layout_vector(payload, "scale", label, errors, positive=True)
    if mode == "explicit":
        instances = payload.get("instances", payload.get("transforms"))
        if not isinstance(instances, list) or not instances:
            errors.append(f"{label}.instances must be a non-empty array")
        else:
            if len(instances) > MAX_INSTANCE_COUNT:
                errors.append(
                    f"{label}.instances must contain at most {MAX_INSTANCE_COUNT} transforms"
                )
            for index, item in enumerate(instances):
                if not isinstance(item, Mapping):
                    errors.append(f"{label}.instances[{index}] must be an object")
                    continue
                for field in ("position", "rotation", "scale"):
                    value = item.get(field)
                    if value is not None and (
                        not isinstance(value, list)
                        or len(value) != 3
                        or not all(_is_number(part) for part in value)
                    ):
                        errors.append(f"{label}.instances[{index}].{field} must be 3 finite numbers")
                    elif field == "scale" and isinstance(value, list) and any(
                        float(part) <= 0 for part in value
                    ):
                        errors.append(f"{label}.instances[{index}].scale values must be positive")
    elif mode == "grid":
        counts = payload.get("counts", payload.get("gridCounts"))
        if counts is None and (
            "columns" in payload or "rows" in payload or "layers" in payload
        ):
            counts = [
                payload.get("columns", 1),
                payload.get("rows", 1),
                payload.get("layers", 1),
            ]
        if (
            not isinstance(counts, list)
            or len(counts) != 3
            or not all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in counts)
        ):
            errors.append(f"{label}.counts must be 3 positive integers")
        else:
            if any(value > MAX_GRID_AXIS_COUNT for value in counts):
                errors.append(
                    f"{label}.counts axes must not exceed {MAX_GRID_AXIS_COUNT}"
                )
            product = counts[0] * counts[1] * counts[2]
            if product > MAX_INSTANCE_COUNT:
                errors.append(
                    f"{label} grid product must not exceed {MAX_INSTANCE_COUNT} instances"
                )
        _validate_layout_vector(
            payload,
            "spacing",
            label,
            errors,
            non_negative=True,
            allow_scalar=True,
            allow_two=True,
        )
        _validate_layout_vector(payload, "origin", label, errors, allow_two=True)
        declared_count = payload.get("count")
        if declared_count is not None and (
            not isinstance(declared_count, int)
            or isinstance(declared_count, bool)
            or declared_count <= 0
            or declared_count > MAX_INSTANCE_COUNT
        ):
            errors.append(
                f"{label}.count must be an integer from 1 to {MAX_INSTANCE_COUNT}"
            )
        elif (
            declared_count is not None
            and isinstance(counts, list)
            and len(counts) == 3
            and all(isinstance(value, int) and not isinstance(value, bool) for value in counts)
            and declared_count != counts[0] * counts[1] * counts[2]
        ):
            errors.append(f"{label}.count must equal the grid counts product")
    elif mode == "radial":
        if (
            not isinstance(payload.get("count"), int)
            or isinstance(payload.get("count"), bool)
            or int(payload.get("count", 0)) <= 0
            or int(payload.get("count", 0)) > MAX_INSTANCE_COUNT
        ):
            errors.append(f"{label}.count must be an integer from 1 to {MAX_INSTANCE_COUNT}")
        if "radius" in payload and (not _is_number(payload["radius"]) or float(payload["radius"]) < 0):
            errors.append(f"{label}.radius must be a non-negative finite number")
        if payload.get("axis", "y") not in {"x", "y", "z"}:
            errors.append(f"{label}.axis must be x, y, or z")
        _validate_layout_vector(payload, "origin", label, errors)
        for key in ("startAngle", "arc"):
            if key in payload and not _is_number(payload[key]):
                errors.append(f"{label}.{key} must be a finite number")
        if "arc" in payload and _is_number(payload["arc"]) and float(payload["arc"]) <= 0:
            errors.append(f"{label}.arc must be positive")
        _validate_layout_bool(payload, "alignToRadius", label, errors)
    elif mode == "along-path":
        _validate_points(
            payload.get("path"),
            3,
            2,
            f"{label}.path",
            errors,
            maximum_count=MAX_PATH_POINTS,
        )
        if (
            not isinstance(payload.get("count"), int)
            or isinstance(payload.get("count"), bool)
            or int(payload.get("count", 0)) <= 0
            or int(payload.get("count", 0)) > MAX_INSTANCE_COUNT
        ):
            errors.append(f"{label}.count must be an integer from 1 to {MAX_INSTANCE_COUNT}")
        _validate_layout_bool(payload, "closed", label, errors)
        _validate_layout_bool(payload, "alignToPath", label, errors)
    elif mode == "scatter":
        if (
            not isinstance(payload.get("count"), int)
            or isinstance(payload.get("count"), bool)
            or int(payload.get("count", 0)) <= 0
            or int(payload.get("count", 0)) > MAX_INSTANCE_COUNT
        ):
            errors.append(f"{label}.count must be an integer from 1 to {MAX_INSTANCE_COUNT}")
        bounds = payload.get("bounds")
        if bounds is not None and not isinstance(bounds, Mapping):
            errors.append(f"{label}.bounds must be an object with local min/max vectors")
        elif isinstance(bounds, Mapping):
            parsed_bounds: dict[str, list[float]] = {}
            for field in ("min", "max"):
                value = bounds.get(field)
                if (
                    not isinstance(value, list)
                    or len(value) != 3
                    or not all(_is_number(part) for part in value)
                ):
                    errors.append(f"{label}.bounds.{field} must be 3 finite numbers")
                else:
                    parsed_bounds[field] = [float(part) for part in value]
            if "min" in parsed_bounds and "max" in parsed_bounds and any(
                parsed_bounds["min"][index] > parsed_bounds["max"][index]
                for index in range(3)
            ):
                errors.append(f"{label}.bounds min values must not exceed max values")
        else:
            _validate_layout_vector(payload, "size", label, errors, positive=True)
            _validate_layout_vector(payload, "origin", label, errors)
        seed = payload.get("seed")
        if seed is not None and (
            not isinstance(seed, int)
            or isinstance(seed, bool)
            or seed <= 0
            or seed > 2_147_483_647
        ):
            errors.append(f"{label}.seed must be an integer from 1 to 2147483647")
        _validate_layout_vector(
            payload,
            "rotationRange",
            label,
            errors,
            non_negative=True,
        )
        scale_range = payload.get("scaleRange")
        if scale_range is not None:
            if (
                not isinstance(scale_range, list)
                or len(scale_range) != 2
                or not all(_is_number(value) and float(value) > 0 for value in scale_range)
            ):
                errors.append(f"{label}.scaleRange must be 2 positive finite numbers")
            elif float(scale_range[0]) > float(scale_range[1]):
                errors.append(f"{label}.scaleRange minimum must not exceed maximum")
    return errors


def _validate_instanced(
    parameters: Mapping[str, Any],
    component: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    label = f"{_component_label(component)} geometryDescriptor.parameters"
    base_primitive = parameters.get("basePrimitive", parameters.get("sourcePrimitive"))
    if not isinstance(base_primitive, str) or not base_primitive:
        errors.append(f"{label}.basePrimitive is required")
    elif base_primitive == "instanced-cluster":
        errors.append(f"{label}.basePrimitive cannot recursively be instanced-cluster")
    elif base_primitive not in GEOMETRY_REGISTRY:
        errors.append(f"{label}.basePrimitive {base_primitive!r} has no registered handler")
    elif not GEOMETRY_REGISTRY[base_primitive].instancable:
        errors.append(f"{label}.basePrimitive {base_primitive!r} is not instancable")
    base_parameters = parameters.get("baseParameters", parameters.get("sourceParameters"))
    if base_parameters is not None and not isinstance(base_parameters, Mapping):
        errors.append(f"{label}.baseParameters must be an object")
    elif isinstance(base_parameters, Mapping):
        for field in ("radius", "width", "height", "depth", "length"):
            if field in base_parameters and (
                not _is_number(base_parameters[field])
                or float(base_parameters[field]) <= 0
            ):
                errors.append(f"{label}.baseParameters.{field} must be a positive finite number")
    ref = _repetition_ref(parameters)
    inline = parameters.get("instances")
    inline_system = parameters.get("layout")
    if ref is None and inline is None and not isinstance(inline_system, Mapping):
        errors.append(
            f"{label} requires repetitionSystemRef, instances, or an inline layout object"
        )
    return errors


def _basic_emitter(
    primitive: str,
    expression: Callable[[Mapping[str, Any]], str],
    *,
    endpoint_aware: bool = False,
) -> Emitter:
    def emit(
        component: Mapping[str, Any],
        parameters: Mapping[str, Any],
        systems: Mapping[str, Mapping[str, Any]],
    ) -> GeometryEmission:
        del component, systems
        return GeometryEmission(
            primitive=primitive,
            geometry_expression=expression(parameters),
            endpoint_aware=endpoint_aware,
        )

    return emit


def _box_expression(parameters: Mapping[str, Any]) -> str:
    return (
        "new THREE.BoxGeometry(1,1,1,"
        f"{_integer(parameters.get('widthSegments'), 12)},"
        f"{_integer(parameters.get('heightSegments'), 12)},"
        f"{_integer(parameters.get('depthSegments'), 12)})"
    )


def _box_emitter(
    component: Mapping[str, Any],
    parameters: Mapping[str, Any],
    systems: Mapping[str, Mapping[str, Any]],
) -> GeometryEmission:
    del systems
    treatment = _edge_treatment(component)
    treatment_type = str(treatment.get("type", "none"))
    radius = float(treatment.get("radiusRatio", treatment.get("bevelRadius", 0)) or 0)
    if treatment_type in {"bevel", "rounded"} and radius > 0:
        segments = int(treatment.get("segments", 4))
        return GeometryEmission(
            primitive="box",
            geometry_expression=(
                f"createRoundedBoxGeometry(1,1,1,{radius},{segments})"
            ),
            helpers=frozenset({"rounded-box"}),
        )
    return GeometryEmission(primitive="box", geometry_expression=_box_expression(parameters))


def _sphere_expression(parameters: Mapping[str, Any]) -> str:
    return (
        "new THREE.SphereGeometry(0.5,"
        f"{_integer(parameters.get('widthSegments'), 64, minimum=3)},"
        f"{_integer(parameters.get('heightSegments'), 40, minimum=2)},"
        f"{_number(parameters.get('phiStart'), 0)},"
        f"{_number(parameters.get('phiLength'), math.tau, minimum=0, maximum=math.tau)},"
        f"{_number(parameters.get('thetaStart'), 0)},"
        f"{_number(parameters.get('thetaLength'), math.pi, minimum=0, maximum=math.pi)})"
    )


def _cylinder_expression(parameters: Mapping[str, Any]) -> str:
    return (
        "new THREE.CylinderGeometry(0.5,0.5,1,"
        f"{_integer(parameters.get('radialSegments'), 48, minimum=3)},"
        f"{_integer(parameters.get('heightSegments'), 16)},"
        f"{str(_boolean(parameters.get('openEnded'))).lower()},"
        f"{_number(parameters.get('thetaStart'), 0)},"
        f"{_number(parameters.get('thetaLength'), math.tau, minimum=0, maximum=math.tau)})"
    )


def _cone_expression(parameters: Mapping[str, Any]) -> str:
    return (
        "new THREE.ConeGeometry(0.5,1,"
        f"{_integer(parameters.get('radialSegments'), 48, minimum=3)},"
        f"{_integer(parameters.get('heightSegments'), 16)},"
        f"{str(_boolean(parameters.get('openEnded'))).lower()},"
        f"{_number(parameters.get('thetaStart'), 0)},"
        f"{_number(parameters.get('thetaLength'), math.tau, minimum=0, maximum=math.tau)})"
    )


def _capsule_expression(parameters: Mapping[str, Any]) -> str:
    return (
        "new THREE.CapsuleGeometry(0.25,0.5,"
        f"{_integer(parameters.get('capSegments'), 16, minimum=1)},"
        f"{_integer(parameters.get('radialSegments'), 32, minimum=3)})"
    )


def _torus_expression(parameters: Mapping[str, Any]) -> str:
    return (
        "new THREE.TorusGeometry(0.4,0.1,"
        f"{_integer(parameters.get('radialSegments'), 24, minimum=3)},"
        f"{_integer(parameters.get('tubularSegments'), 96, minimum=3)},"
        f"{_number(parameters.get('arc'), math.tau, minimum=0, maximum=math.tau)})"
    )


def _plane_expression(parameters: Mapping[str, Any]) -> str:
    return (
        "new THREE.PlaneGeometry(1,1,"
        f"{_integer(parameters.get('widthSegments'), 24)},"
        f"{_integer(parameters.get('heightSegments'), 24)})"
    )


def _path_for_component(
    component: Mapping[str, Any],
    parameters: Mapping[str, Any],
) -> list[list[float]]:
    path = _point_list(parameters.get("path"), 3, MAX_PATH_POINTS)
    if path:
        return path
    delta = _attachment_delta(component)
    return [[0.0, 0.0, 0.0], delta] if delta is not None else []


def _tube_emitter(
    component: Mapping[str, Any],
    parameters: Mapping[str, Any],
    systems: Mapping[str, Mapping[str, Any]],
) -> GeometryEmission:
    del systems
    path = _path_for_component(component, parameters)
    curve_type = str(parameters.get("curveType", "centripetal"))
    if curve_type not in {"centripetal", "chordal", "catmullrom"}:
        curve_type = "centripetal"
    points = ",".join(f"new THREE.Vector3({point[0]},{point[1]},{point[2]})" for point in path)
    expression = (
        "new THREE.TubeGeometry("
        f"new THREE.CatmullRomCurve3([{points}],"
        f"{str(_boolean(parameters.get('closed'))).lower()},"
        f"{_json(curve_type)},"
        f"{_number(parameters.get('tension'), 0.5, minimum=0, maximum=1)}),"
        f"{_integer(parameters.get('tubularSegments'), max(16, len(path) * 12), minimum=2)},"
        f"{_number(parameters.get('radius'), 0.05, minimum=0.0001, maximum=1000000)},"
        f"{_integer(parameters.get('radialSegments'), 12, minimum=3)},"
        f"{str(_boolean(parameters.get('closed'))).lower()})"
    )
    return GeometryEmission(
        primitive="tube",
        geometry_expression=expression,
        dimension_mode="local",
        endpoint_aware=isinstance(component.get("attachment"), Mapping),
    )


def _lathe_emitter(
    component: Mapping[str, Any],
    parameters: Mapping[str, Any],
    systems: Mapping[str, Mapping[str, Any]],
) -> GeometryEmission:
    del component, systems
    profile = _point_list(parameters.get("profile", parameters.get("points")), 2, MAX_PROFILE_POINTS)
    points = ",".join(f"new THREE.Vector2({point[0]},{point[1]})" for point in profile)
    expression = (
        f"new THREE.LatheGeometry([{points}],"
        f"{_integer(parameters.get('segments'), parameters.get('radialSegments', 64), minimum=3)},"
        f"{_number(parameters.get('phiStart'), 0)},"
        f"{_number(parameters.get('phiLength'), math.tau, minimum=0, maximum=math.tau)})"
    )
    return GeometryEmission("lathe", expression, dimension_mode="local")


def _extrude_emitter(
    component: Mapping[str, Any],
    parameters: Mapping[str, Any],
    systems: Mapping[str, Mapping[str, Any]],
) -> GeometryEmission:
    del component, systems
    payload = {
        "outline": _point_list(_outline(parameters), 2, MAX_PROFILE_POINTS),
        "holes": [
            _point_list(hole, 2, MAX_PROFILE_POINTS)
            for hole in (parameters.get("holes") if isinstance(parameters.get("holes"), list) else [])[:64]
        ],
        "depth": _number(parameters.get("depth"), 0.2, minimum=0.0001, maximum=1000000),
        "steps": _integer(parameters.get("steps"), 1),
        "bevelEnabled": _boolean(parameters.get("bevelEnabled"), False),
        "bevelThickness": _number(parameters.get("bevelThickness"), 0.02, minimum=0, maximum=1000000),
        "bevelSize": _number(parameters.get("bevelSize"), 0.02, minimum=0, maximum=1000000),
        "bevelOffset": _number(parameters.get("bevelOffset"), 0, minimum=-1000000, maximum=1000000),
        "bevelSegments": _integer(parameters.get("bevelSegments"), 2, maximum=64),
    }
    return GeometryEmission(
        "extrude",
        f"createExtrudeGeometry({_json(payload)})",
        dimension_mode="local",
        helpers=frozenset({"extrude"}),
    )


def _curve_sweep_emitter(
    component: Mapping[str, Any],
    parameters: Mapping[str, Any],
    systems: Mapping[str, Mapping[str, Any]],
) -> GeometryEmission:
    del systems
    path = _path_for_component(component, parameters)
    payload = {
        "path": path,
        "profile": _point_list(parameters.get("profile"), 2, MAX_PROFILE_POINTS),
        "pathSegments": _integer(
            parameters.get("pathSegments"),
            max(12, (len(path) - 1) * 16),
            minimum=2,
        ),
        "closedPath": _boolean(parameters.get("closedPath", parameters.get("closed")), False),
        "closedProfile": _boolean(parameters.get("closedProfile"), True),
        "curveType": str(parameters.get("curveType", "centripetal")),
        "tension": _number(parameters.get("tension"), 0.5, minimum=0, maximum=1),
        "twist": _number(parameters.get("twist"), 0, minimum=-math.tau * 64, maximum=math.tau * 64),
        "radii": [
            _number(value, 1, minimum=0.0001, maximum=1000000)
            for value in (parameters.get("radii") if isinstance(parameters.get("radii"), list) else [])[:MAX_PATH_POINTS]
        ],
    }
    if payload["curveType"] not in {"centripetal", "chordal", "catmullrom"}:
        payload["curveType"] = "centripetal"
    return GeometryEmission(
        "curve-sweep",
        f"createCurveSweepGeometry({_json(payload)})",
        dimension_mode="local",
        endpoint_aware=isinstance(component.get("attachment"), Mapping),
        helpers=frozenset({"curve-sweep"}),
    )


def _loft_sections_payload(parameters: Mapping[str, Any], key: str = "sections") -> list[dict[str, Any]]:
    return [
        {
            "position": [float(value) for value in section["position"]],
            "radii": [float(value) for value in section["radii"]],
            "twist": float(section.get("twist", 0)),
        }
        for section in parameters[key]
    ]


def _section_loft_emitter(
    component: Mapping[str, Any],
    parameters: Mapping[str, Any],
    systems: Mapping[str, Mapping[str, Any]],
) -> GeometryEmission:
    del component, systems
    payload = {
        "sections": _loft_sections_payload(parameters),
        "radialSegments": int(parameters["radialSegments"]),
        "segmentsPerSpan": int(parameters["segmentsPerSpan"]),
        "capStart": bool(parameters.get("capStart", True)),
        "capEnd": bool(parameters.get("capEnd", True)),
    }
    return GeometryEmission(
        "section-loft",
        f"createSectionLoftGeometry({_json(payload)})",
        dimension_mode="local",
        helpers=frozenset({"special-common", "modeling-common", "section-loft"}),
    )


def _conforming_shell_emitter(
    component: Mapping[str, Any],
    parameters: Mapping[str, Any],
    systems: Mapping[str, Mapping[str, Any]],
) -> GeometryEmission:
    del component, systems
    coverage = parameters["coverage"]
    folds: list[dict[str, Any]] = []
    for fold in parameters.get("folds", []):
        direction = _normalize_vector([float(value) for value in fold["direction"]], [1, 0])
        folds.append(
            {
                "direction": direction,
                "amplitude": float(fold["amplitude"]),
                "frequency": float(fold["frequency"]),
                "phase": float(fold.get("phase", 0)),
            }
        )
    payload = {
        "sections": _loft_sections_payload(parameters, "_hostSections"),
        "radialSegments": int(parameters["radialSegments"]),
        "segmentsPerSpan": int(parameters["segmentsPerSpan"]),
        "clearance": float(parameters["clearance"]),
        "thickness": float(parameters["thickness"]),
        "coverage": {
            "vRange": [float(value) for value in coverage["vRange"]],
            "angleStart": float(coverage.get("angleStart", 0)),
            "angleLength": float(coverage.get("angleLength", math.tau)),
        },
        "openings": [
            {
                "id": str(opening["id"]),
                "center": [float(value) for value in opening["center"]],
                "radius": [float(value) for value in opening["radius"]],
            }
            for opening in parameters.get("openings", [])
        ],
        "folds": folds,
    }
    return GeometryEmission(
        "conforming-shell",
        f"createConformingShellGeometry({_json(payload)})",
        dimension_mode="local",
        helpers=frozenset({"special-common", "modeling-common", "conforming-shell"}),
    )


def _branch_network_emitter(
    component: Mapping[str, Any],
    parameters: Mapping[str, Any],
    systems: Mapping[str, Mapping[str, Any]],
) -> GeometryEmission:
    del component, systems
    payload = {
        "nodes": [
            {
                "id": str(node["id"]),
                "position": [float(value) for value in node["position"]],
                "radius": float(node["radius"]),
            }
            for node in parameters["nodes"]
        ],
        "edges": [
            {
                "from": str(edge["from"]),
                "to": str(edge["to"]),
                "controlPoints": [
                    [float(value) for value in point]
                    for point in edge.get("controlPoints", [])
                ],
            }
            for edge in parameters["edges"]
        ],
        "radialSegments": int(parameters["radialSegments"]),
        "segmentsPerEdge": int(parameters["segmentsPerEdge"]),
        "junctionSegments": int(parameters["junctionSegments"]),
        "capEnds": bool(parameters.get("capEnds", True)),
    }
    return GeometryEmission(
        "branch-network",
        f"createBranchNetworkGeometry({_json(payload)})",
        dimension_mode="local",
        helpers=frozenset({"special-common", "branch-network"}),
    )


def _surface_scatter_emitter(
    component: Mapping[str, Any],
    parameters: Mapping[str, Any],
    systems: Mapping[str, Mapping[str, Any]],
) -> GeometryEmission:
    base_primitive = str(parameters["basePrimitive"])
    base_component: dict[str, Any] = {
        "id": f"{component.get('id', 'scatter')}::base",
        "componentType": "part",
        "primitive": base_primitive,
        "geometryDescriptor": {"parameters": dict(parameters["baseParameters"])},
    }
    base_emission = _emit_registered(base_component, systems)
    if base_emission.mesh_kind != "mesh":
        raise GeometrySpecError("surface-scatter basePrimitive must emit a regular mesh geometry")
    count = int(parameters["count"])
    layout = {
        "sections": _loft_sections_payload(parameters, "_hostSections"),
        "count": count,
        "seed": int(parameters.get("seed", 1)),
        "uRange": [float(value) for value in parameters["uRange"]],
        "vRange": [float(value) for value in parameters["vRange"]],
        "excludeMasks": [
            {
                "uRange": [float(value) for value in mask["uRange"]],
                "vRange": [float(value) for value in mask["vRange"]],
            }
            for mask in parameters.get("excludeMasks", [])
        ],
        "normalOffset": float(parameters.get("normalOffset", 0)),
        "scaleRange": [float(value) for value in parameters["scaleRange"]],
        "baseScale": [float(value) for value in parameters["baseScale"]],
        "spinRange": float(parameters.get("spinRange", 0)),
        "alignToNormal": bool(parameters.get("alignToNormal", True)),
        "baseRotation": [
            float(value) for value in parameters.get("baseRotation", [0, 0, 0])
        ],
    }
    return GeometryEmission(
        "surface-scatter",
        base_emission.geometry_expression,
        dimension_mode="local",
        mesh_kind="instanced",
        helpers=base_emission.helpers
        | frozenset({"special-common", "modeling-common", "surface-scatter"}),
        instance_count=count,
        instance_layout=layout,
        instance_layout_function="applySurfaceScatterLayout",
        instance_layout_type="SurfaceScatterLayoutSpec",
    )


def _deformable_surface_emitter(
    component: Mapping[str, Any],
    parameters: Mapping[str, Any],
    systems: Mapping[str, Mapping[str, Any]],
) -> GeometryEmission:
    del component, systems
    control_grid = [
        [[float(value) for value in point] for point in row]
        for row in parameters["controlGrid"]
    ]
    folds: list[dict[str, Any]] = []
    for fold in parameters.get("folds", []):
        direction = [float(value) for value in fold["direction"]]
        direction_length = math.hypot(direction[0], direction[1])
        folds.append(
            {
                "direction": [
                    direction[0] / direction_length,
                    direction[1] / direction_length,
                ],
                "amplitude": float(fold["amplitude"]),
                "frequency": float(fold["frequency"]),
                "phase": float(fold.get("phase", 0)),
                "edgeFade": float(fold.get("edgeFade", 0)),
            }
        )
    payload = {
        "controlGrid": control_grid,
        "segments": [int(value) for value in parameters["segments"]],
        "folds": folds,
    }
    return GeometryEmission(
        "deformable-surface",
        f"createDeformableSurfaceGeometry({_json(payload)})",
        dimension_mode="local",
        helpers=frozenset({"special-common", "deformable-surface"}),
    )


def _fiber_system_emitter(
    component: Mapping[str, Any],
    parameters: Mapping[str, Any],
    systems: Mapping[str, Mapping[str, Any]],
) -> GeometryEmission:
    del component, systems
    curl = parameters.get("curl", {})
    payload = {
        "guides": [
            [[float(value) for value in point] for point in guide]
            for guide in parameters["guides"]
        ],
        "strandsPerGuide": int(parameters.get("strandsPerGuide", 1)),
        "samples": int(parameters.get("samples", 8)),
        "rootWidth": float(parameters["rootWidth"]),
        "tipWidth": float(parameters.get("tipWidth", 0)),
        "spread": float(parameters.get("spread", 0)),
        "clump": float(parameters.get("clump", 1)),
        "lengthVariation": float(parameters.get("lengthVariation", 0)),
        "widthVariation": float(parameters.get("widthVariation", 0)),
        "curl": {
            "amplitude": float(curl.get("amplitude", 0)),
            "frequency": float(curl.get("frequency", 0)),
            "phase": float(curl.get("phase", 0)),
        },
        "cardPlanes": int(parameters.get("cardPlanes", 2)),
        "seed": int(parameters.get("seed", 1)),
    }
    return GeometryEmission(
        "fiber-system",
        f"createFiberSystemGeometry({_json(payload)})",
        dimension_mode="local",
        helpers=frozenset({"special-common", "fiber-system"}),
    )


def _implicit_surface_emitter(
    component: Mapping[str, Any],
    parameters: Mapping[str, Any],
    systems: Mapping[str, Mapping[str, Any]],
) -> GeometryEmission:
    del component, systems
    bounds = parameters["bounds"]
    payload = {
        "bounds": {
            "min": [float(value) for value in bounds["min"]],
            "max": [float(value) for value in bounds["max"]],
        },
        "resolution": [int(value) for value in parameters["resolution"]],
        "isoLevel": float(parameters["isoLevel"]),
        "sources": [],
        "uvProjection": str(parameters.get("uvProjection", "xz")),
    }
    for source in parameters["sources"]:
        shape = str(source.get("shape", "sphere"))
        emitted: dict[str, Any] = {
            "shape": shape,
            "strength": float(source["strength"]),
            "operation": str(source.get("operation", "add")),
        }
        if shape == "capsule":
            emitted.update(
                {
                    "start": [float(value) for value in source["start"]],
                    "end": [float(value) for value in source["end"]],
                    "radius": float(source["radius"]),
                }
            )
        elif shape == "ellipsoid":
            emitted.update(
                {
                    "position": [float(value) for value in source["position"]],
                    "radii": [float(value) for value in source["radii"]],
                }
            )
        else:
            emitted.update(
                {
                    "position": [float(value) for value in source["position"]],
                    "radius": float(source["radius"]),
                }
            )
        payload["sources"].append(emitted)
    return GeometryEmission(
        "implicit-surface",
        f"createImplicitSurfaceGeometry({_json(payload)})",
        dimension_mode="local",
        helpers=frozenset({"special-common", "implicit-surface"}),
    )


def _sculpted_surface_emitter(
    component: Mapping[str, Any],
    parameters: Mapping[str, Any],
    systems: Mapping[str, Mapping[str, Any]],
) -> GeometryEmission:
    del component, systems
    bounds = parameters["bounds"]

    def emit_source(source: Mapping[str, Any]) -> dict[str, Any]:
        shape = str(source.get("shape", "sphere"))
        emitted: dict[str, Any] = {
            "id": str(source["id"]),
            "shape": shape,
            "strength": float(source["strength"]),
            "falloff": float(source.get("falloff", 0.5)),
            "operation": str(source.get("operation", "add")),
        }
        if shape == "capsule":
            emitted.update(
                {
                    "start": [float(value) for value in source["start"]],
                    "end": [float(value) for value in source["end"]],
                    "radius": float(source["radius"]),
                }
            )
        elif shape == "ellipsoid":
            emitted.update(
                {
                    "position": [float(value) for value in source["position"]],
                    "radii": [float(value) for value in source["radii"]],
                }
            )
        else:
            emitted.update(
                {
                    "position": [float(value) for value in source["position"]],
                    "radius": float(source["radius"]),
                }
            )
        return emitted

    payload = {
        "bounds": {
            "min": [float(value) for value in bounds["min"]],
            "max": [float(value) for value in bounds["max"]],
        },
        "resolution": [int(value) for value in parameters["resolution"]],
        "isoLevel": float(parameters["isoLevel"]),
        "sources": [emit_source(source) for source in parameters["sources"]],
        "surfaceModifiers": [
            {
                key: (
                    [float(value) for value in value]
                    if key in {"position", "radii", "start", "end"}
                    else float(value)
                    if key in {"radius", "strength", "falloff"}
                    else str(value)
                )
                for key, value in modifier.items()
            }
            for modifier in parameters.get("surfaceModifiers", [])
        ],
        "connectivity": "single-surface",
        "uvProjection": str(parameters.get("uvProjection", "xz")),
    }
    return GeometryEmission(
        "sculpted-surface",
        f"createSculptedSurfaceGeometry({_json(payload)})",
        dimension_mode="local",
        helpers=frozenset({"special-common", "implicit-surface"}),
    )


def _volume_field_emitter(
    component: Mapping[str, Any],
    parameters: Mapping[str, Any],
    systems: Mapping[str, Mapping[str, Any]],
) -> GeometryEmission:
    del component, systems
    bounds = parameters["bounds"]
    payload = {
        "bounds": {
            "min": [float(value) for value in bounds["min"]],
            "max": [float(value) for value in bounds["max"]],
        },
        "sources": [
            {
                "position": [float(value) for value in source["position"]],
                "radius": float(source["radius"]),
                "density": float(source["density"]),
            }
            for source in parameters["sources"]
        ],
        "particleCount": int(parameters["particleCount"]),
        "cardPlanes": int(parameters["cardPlanes"]),
        "cardSize": [float(value) for value in parameters["cardSize"]],
        "seed": int(parameters.get("seed", 1)),
    }
    return GeometryEmission(
        "volume-field",
        f"createVolumeFieldGeometry({_json(payload)})",
        dimension_mode="local",
        helpers=frozenset({"special-common", "volume-field"}),
    )


def _base_dimensions_scale(parameters: Mapping[str, Any]) -> list[float]:
    dimensions = parameters.get("baseDimensions")
    if dimensions is None:
        candidate = parameters.get("baseParameters", parameters.get("sourceParameters"))
        if isinstance(candidate, Mapping) and any(
            field in candidate
            for field in ("radius", "width", "height", "depth", "length")
        ):
            dimensions = candidate
    if isinstance(dimensions, list):
        return _vector(dimensions, 3, [1, 1, 1])
    if not isinstance(dimensions, Mapping):
        return [1.0, 1.0, 1.0]
    radius = _number(dimensions.get("radius"), 0.5, minimum=0.0001)
    diameter = radius * 2
    return [
        _number(dimensions.get("width"), diameter, minimum=0.0001),
        _number(dimensions.get("height", dimensions.get("length")), diameter, minimum=0.0001),
        _number(dimensions.get("depth"), diameter, minimum=0.0001),
    ]


def _normalized_instance(
    item: Mapping[str, Any],
    base_scale: list[float],
) -> dict[str, Any]:
    scale = _vector(item.get("scale"), 3, [1, 1, 1])
    return {
        "position": _vector(item.get("position"), 3, [0, 0, 0]),
        "rotation": _vector(item.get("rotation"), 3, [0, 0, 0]),
        "scale": [scale[index] * base_scale[index] for index in range(3)],
    }


def _grid_vector(value: Any, fallback: list[float]) -> list[float]:
    if _is_number(value):
        scalar = float(value)
        return [scalar, scalar, scalar]
    if isinstance(value, list) and len(value) == 2 and all(_is_number(item) for item in value):
        return [float(value[0]), float(value[1]), 0.0]
    return _vector(value, 3, fallback)


def _instance_layout(
    parameters: Mapping[str, Any],
    systems: Mapping[str, Mapping[str, Any]],
    base_scale: list[float],
) -> tuple[dict[str, Any], int]:
    ref = _repetition_ref(parameters)
    if ref is not None:
        if ref not in systems:
            raise GeometrySpecError(f"unknown repetitionSystemRef {ref!r}")
        payload = _system_payload(systems[ref])
    elif isinstance(parameters.get("layout"), Mapping):
        payload = _system_payload(parameters["layout"])
    else:
        payload = {"type": "explicit", "instances": parameters.get("instances", [])}
    mode = _layout_mode(payload)
    common_rotation = _vector(payload.get("rotation"), 3, [0, 0, 0])
    common_scale = _vector(payload.get("scale"), 3, [1, 1, 1])
    scaled = [common_scale[index] * base_scale[index] for index in range(3)]
    if mode == "explicit":
        items = payload.get("instances", payload.get("transforms", []))
        transforms = [
            _normalized_instance(item, base_scale)
            for item in (items if isinstance(items, list) else [])[:MAX_INSTANCE_COUNT]
            if isinstance(item, Mapping)
        ]
        return {"mode": "explicit", "transforms": transforms}, len(transforms)
    if mode == "grid":
        raw_counts = payload.get("counts", payload.get("gridCounts", [1, 1, 1]))
        if "counts" not in payload and "gridCounts" not in payload and (
            "columns" in payload or "rows" in payload or "layers" in payload
        ):
            raw_counts = [
                payload.get("columns", 1),
                payload.get("rows", 1),
                payload.get("layers", 1),
            ]
        counts = [
            _integer(value, 1, maximum=MAX_GRID_AXIS_COUNT)
            for value in (raw_counts if isinstance(raw_counts, list) and len(raw_counts) == 3 else [1, 1, 1])
        ]
        while counts[0] * counts[1] * counts[2] > MAX_INSTANCE_COUNT:
            largest = max(range(3), key=lambda index: counts[index])
            counts[largest] = max(1, counts[largest] - 1)
        return {
            "mode": "grid",
            "counts": counts,
            "spacing": _grid_vector(payload.get("spacing"), [1, 1, 1]),
            "origin": _grid_vector(payload.get("origin"), [0, 0, 0]),
            "rotation": common_rotation,
            "scale": scaled,
        }, counts[0] * counts[1] * counts[2]
    count = _integer(payload.get("count"), 1, maximum=MAX_INSTANCE_COUNT)
    if mode == "radial":
        axis = str(payload.get("axis", "y"))
        if axis not in {"x", "y", "z"}:
            axis = "y"
        return {
            "mode": "radial",
            "count": count,
            "radius": _number(payload.get("radius"), 1, minimum=0, maximum=1000000),
            "origin": _vector(payload.get("origin"), 3, [0, 0, 0]),
            "startAngle": _number(payload.get("startAngle"), 0, minimum=-math.tau * 64, maximum=math.tau * 64),
            "arc": _number(payload.get("arc"), math.tau, minimum=0, maximum=math.tau),
            "axis": axis,
            "alignToRadius": _boolean(payload.get("alignToRadius"), False),
            "rotation": common_rotation,
            "scale": scaled,
        }, count
    if mode == "along-path":
        return {
            "mode": "along-path",
            "count": count,
            "path": _point_list(payload.get("path"), 3, MAX_PATH_POINTS),
            "closed": _boolean(payload.get("closed"), False),
            "alignToPath": _boolean(payload.get("alignToPath"), True),
            "rotation": common_rotation,
            "scale": scaled,
        }, count
    bounds = payload.get("bounds") if isinstance(payload.get("bounds"), Mapping) else {}
    if bounds:
        minimum = _vector(bounds.get("min"), 3, [-0.5, -0.5, -0.5])
        maximum = _vector(bounds.get("max"), 3, [0.5, 0.5, 0.5])
    else:
        size = _vector(payload.get("size"), 3, [1, 1, 1])
        origin = _vector(payload.get("origin"), 3, [0, 0, 0])
        minimum = [origin[index] - size[index] * 0.5 for index in range(3)]
        maximum = [origin[index] + size[index] * 0.5 for index in range(3)]
    scale_range = payload.get("scaleRange")
    if isinstance(scale_range, list) and len(scale_range) == 2:
        normalized_scale_range = [
            _number(scale_range[0], 1, minimum=0.0001, maximum=1000000),
            _number(scale_range[1], 1, minimum=0.0001, maximum=1000000),
        ]
    else:
        normalized_scale_range = [1.0, 1.0]
    return {
        "mode": "scatter",
        "count": count,
        "min": minimum,
        "max": maximum,
        "seed": _integer(payload.get("seed"), 1, minimum=1, maximum=2_147_483_647),
        "rotationRange": _vector(payload.get("rotationRange"), 3, [0, 0, 0]),
        "scaleRange": normalized_scale_range,
        "rotation": common_rotation,
        "scale": scaled,
    }, count


def _instanced_emitter(
    component: Mapping[str, Any],
    parameters: Mapping[str, Any],
    systems: Mapping[str, Mapping[str, Any]],
) -> GeometryEmission:
    base_primitive = str(parameters.get("basePrimitive", parameters.get("sourcePrimitive")))
    base_parameters = parameters.get("baseParameters", parameters.get("sourceParameters"))
    base_component: dict[str, Any] = {
        "id": f"{component.get('id', 'cluster')}::base",
        "componentType": "part",
        "primitive": base_primitive,
        "geometryDescriptor": {
            "parameters": dict(base_parameters) if isinstance(base_parameters, Mapping) else {}
        },
    }
    base_emission = _emit_registered(base_component, systems)
    if base_emission.mesh_kind != "mesh":
        raise GeometrySpecError("instanced-cluster basePrimitive must emit a regular mesh geometry")
    base_scale = _base_dimensions_scale(parameters) if base_emission.dimension_mode == "component" else [1.0, 1.0, 1.0]
    layout, count = _instance_layout(parameters, systems, base_scale)
    return GeometryEmission(
        "instanced-cluster",
        base_emission.geometry_expression,
        dimension_mode="local",
        mesh_kind="instanced",
        helpers=base_emission.helpers | frozenset({"instances"}),
        instance_count=count,
        instance_layout=layout,
    )


GEOMETRY_REGISTRY: dict[str, GeometryHandler] = {
    "box": GeometryHandler("box", _box_emitter, _validate_basic),
    "sphere": GeometryHandler("sphere", _basic_emitter("sphere", _sphere_expression), _validate_basic),
    "ellipsoid": GeometryHandler("ellipsoid", _basic_emitter("ellipsoid", _sphere_expression), _validate_basic),
    "cylinder": GeometryHandler(
        "cylinder",
        _basic_emitter("cylinder", _cylinder_expression, endpoint_aware=True),
        _validate_basic,
        endpoint_aware=True,
    ),
    "cone": GeometryHandler(
        "cone",
        _basic_emitter("cone", _cone_expression, endpoint_aware=True),
        _validate_basic,
        endpoint_aware=True,
    ),
    "capsule": GeometryHandler(
        "capsule",
        _basic_emitter("capsule", _capsule_expression, endpoint_aware=True),
        _validate_basic,
        endpoint_aware=True,
    ),
    "torus": GeometryHandler("torus", _basic_emitter("torus", _torus_expression), _validate_basic),
    "plane-card": GeometryHandler("plane-card", _basic_emitter("plane-card", _plane_expression), _validate_basic),
    "tube": GeometryHandler(
        "tube",
        _tube_emitter,
        _validate_tube,
        endpoint_aware=True,
        dimension_mode="local",
    ),
    "lathe": GeometryHandler("lathe", _lathe_emitter, _validate_lathe, dimension_mode="local"),
    "extrude": GeometryHandler("extrude", _extrude_emitter, _validate_extrude, dimension_mode="local"),
    "curve-sweep": GeometryHandler(
        "curve-sweep",
        _curve_sweep_emitter,
        _validate_curve_sweep,
        endpoint_aware=True,
        dimension_mode="local",
    ),
    "section-loft": GeometryHandler(
        "section-loft",
        _section_loft_emitter,
        _validate_section_loft,
        dimension_mode="local",
    ),
    "conforming-shell": GeometryHandler(
        "conforming-shell",
        _conforming_shell_emitter,
        _validate_conforming_shell,
        dimension_mode="local",
        instancable=False,
    ),
    "branch-network": GeometryHandler(
        "branch-network",
        _branch_network_emitter,
        _validate_branch_network,
        dimension_mode="local",
    ),
    "deformable-surface": GeometryHandler(
        "deformable-surface",
        _deformable_surface_emitter,
        _validate_deformable_surface,
        dimension_mode="local",
    ),
    "fiber-system": GeometryHandler(
        "fiber-system",
        _fiber_system_emitter,
        _validate_fiber_system,
        dimension_mode="local",
    ),
    "implicit-surface": GeometryHandler(
        "implicit-surface",
        _implicit_surface_emitter,
        _validate_implicit_surface,
        dimension_mode="local",
    ),
    "sculpted-surface": GeometryHandler(
        "sculpted-surface",
        _sculpted_surface_emitter,
        _validate_sculpted_surface,
        dimension_mode="local",
        instancable=False,
    ),
    "volume-field": GeometryHandler(
        "volume-field",
        _volume_field_emitter,
        _validate_volume_field,
        dimension_mode="local",
    ),
    "instanced-cluster": GeometryHandler(
        "instanced-cluster",
        _instanced_emitter,
        _validate_instanced,
        dimension_mode="local",
        instancable=False,
    ),
    "surface-scatter": GeometryHandler(
        "surface-scatter",
        _surface_scatter_emitter,
        _validate_surface_scatter,
        dimension_mode="local",
        instancable=False,
    ),
}

VALID_PRIMITIVES = frozenset(GEOMETRY_REGISTRY)
ENDPOINT_PRIMITIVES = frozenset(
    primitive for primitive, handler in GEOMETRY_REGISTRY.items() if handler.endpoint_aware
)
BLOCKOUT_PROXY_PRIMITIVES = frozenset(
    {"box", "sphere", "ellipsoid", "cylinder", "cone", "capsule"}
)


def _resolve_geometry_references(
    component: Mapping[str, Any],
    component_lookup: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[Mapping[str, Any], list[str]]:
    primitive = component.get("primitive")
    if primitive not in {"conforming-shell", "surface-scatter"}:
        return component, []
    parameters = _parameters(component)
    ref_field = "bodyRef" if primitive == "conforming-shell" else "surfaceRef"
    ref_value = parameters.get(ref_field)
    label = f"{_component_label(component)} geometryDescriptor.parameters.{ref_field}"
    if not isinstance(ref_value, str) or not ref_value.strip():
        return component, [f"{label} is required"]
    if component_lookup is None:
        return component, [f"{label} cannot be resolved without the component tree"]
    host = component_lookup.get(ref_value)
    if not isinstance(host, Mapping):
        return component, [f"{label} references unknown component {ref_value!r}"]
    if host is component or host.get("id") == component.get("id"):
        return component, [f"{label} cannot reference the component itself"]
    if host.get("primitive") != "section-loft":
        return component, [f"{label} must reference a section-loft component"]
    host_parameters = _parameters(host)
    host_sections = host_parameters.get("sections")
    errors: list[str] = []
    if _deformation_stack(host):
        errors.append(
            f"{label} references a deformed host; encode the host's fitted form in "
            "section-loft sections so linked shells/scatter cannot drift from the final surface"
        )
    if component.get("parent") != ref_value:
        errors.append(
            f"{_component_label(component)} must parent to {ref_value!r} so its local surface matches {ref_field}"
        )
    transform = component.get("transform")
    if isinstance(transform, Mapping):
        position = _vector(transform.get("position"), 3, [0, 0, 0])
        rotation = _vector(transform.get("rotation"), 3, [0, 0, 0])
        scale = _vector(transform.get("scale"), 3, [1, 1, 1])
        if any(abs(item) > 1e-9 for item in [*position, *rotation]) or any(
            abs(item - 1) > 1e-9 for item in scale
        ):
            errors.append(
                f"{_component_label(component)} must use an identity transform when conforming to {ref_value!r}"
            )
    resolved_parameters = dict(parameters)
    resolved_parameters["_hostSections"] = host_sections
    if primitive == "conforming-shell":
        resolved_parameters.setdefault("radialSegments", host_parameters.get("radialSegments"))
        resolved_parameters.setdefault("segmentsPerSpan", host_parameters.get("segmentsPerSpan"))
    descriptor = component.get("geometryDescriptor")
    resolved_descriptor = dict(descriptor) if isinstance(descriptor, Mapping) else {}
    resolved_descriptor["parameters"] = resolved_parameters
    resolved_component = dict(component)
    resolved_component["geometryDescriptor"] = resolved_descriptor
    return resolved_component, errors


def get_geometry_handler(primitive: str) -> GeometryHandler:
    """Return the handler or fail explicitly; there is no geometry fallback."""

    try:
        return GEOMETRY_REGISTRY[primitive]
    except KeyError as exc:
        supported = ", ".join(sorted(GEOMETRY_REGISTRY))
        raise GeometrySpecError(
            f"unsupported primitive {primitive!r}; registered geometry handlers: {supported}"
        ) from exc


def validate_repetition_systems(repetition_systems: Any) -> list[str]:
    """Validate reusable instance layouts independently of component references."""

    if repetition_systems is None:
        return []
    if not isinstance(repetition_systems, list):
        return ["repetitionSystems must be an array"]
    errors: list[str] = []
    seen: set[str] = set()
    for index, system in enumerate(repetition_systems):
        label = f"repetitionSystems[{index}]"
        if not isinstance(system, Mapping):
            errors.append(f"{label} must be an object")
            continue
        system_id = system.get("id")
        if not isinstance(system_id, str) or not system_id.strip():
            errors.append(f"{label}.id is required")
        elif system_id in seen:
            errors.append(f"duplicate repetition system id {system_id!r}")
        else:
            seen.add(system_id)
        errors.extend(_validate_repetition_payload(_system_payload(system), label))
    return errors


def validate_geometry_component(
    component: Mapping[str, Any],
    repetition_systems: Iterable[Mapping[str, Any]] | None = None,
    component_lookup: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str]:
    """Return all registry/capability errors for one component."""

    errors: list[str] = []
    kind = component_type(component)
    if kind not in COMPONENT_TYPES:
        return [f"{_component_label(component)} componentType must be part or assembly"]
    if kind == "assembly":
        return errors
    resolved_component, reference_errors = _resolve_geometry_references(component, component_lookup)
    errors.extend(reference_errors)
    primitive = resolved_component.get("primitive")
    if not isinstance(primitive, str) or not primitive:
        return [f"{_component_label(component)} primitive is required for a part"]
    handler = GEOMETRY_REGISTRY.get(primitive)
    if handler is None:
        return [f"{_component_label(component)} uses unsupported primitive {primitive!r}"]
    parameters = _validate_parameter_object(resolved_component, errors)
    errors.extend(handler.validator(parameters, resolved_component))
    errors.extend(_validate_edge_treatment(resolved_component))
    errors.extend(_validate_deformation_stack(resolved_component))
    systems = _repetition_map(repetition_systems)
    if primitive == "instanced-cluster":
        ref = _repetition_ref(parameters)
        if ref is not None and ref not in systems:
            errors.append(f"{_component_label(component)} references unknown repetitionSystem {ref!r}")
        base_primitive = parameters.get("basePrimitive", parameters.get("sourcePrimitive"))
        if isinstance(base_primitive, str) and base_primitive in GEOMETRY_REGISTRY and base_primitive != "instanced-cluster":
            base_parameters = parameters.get("baseParameters", parameters.get("sourceParameters"))
            base_component = {
                "id": f"{component.get('id', 'cluster')}::base",
                "primitive": base_primitive,
                "geometryDescriptor": {
                    "parameters": dict(base_parameters) if isinstance(base_parameters, Mapping) else {}
                },
            }
            errors.extend(validate_geometry_component(base_component, repetition_systems))
        if ref is not None and ref in systems:
            errors.extend(
                _validate_repetition_payload(
                    _system_payload(systems[ref]),
                    f"repetitionSystem {ref!r}",
                )
            )
        elif isinstance(parameters.get("layout"), Mapping):
            errors.extend(
                _validate_repetition_payload(
                    _system_payload(parameters["layout"]),
                    f"{_component_label(component)} geometryDescriptor.parameters.layout",
                )
            )
        elif isinstance(parameters.get("instances"), list):
            errors.extend(
                _validate_repetition_payload(
                    {"type": "explicit", "instances": parameters["instances"]},
                    f"{_component_label(component)} geometryDescriptor.parameters",
                )
            )
    if primitive == "surface-scatter":
        base_primitive = parameters.get("basePrimitive")
        if isinstance(base_primitive, str) and base_primitive in GEOMETRY_REGISTRY:
            base_component = {
                "id": f"{component.get('id', 'scatter')}::base",
                "componentType": "part",
                "primitive": base_primitive,
                "geometryDescriptor": {
                    "parameters": dict(parameters.get("baseParameters", {}))
                    if isinstance(parameters.get("baseParameters"), Mapping)
                    else {}
                },
            }
            errors.extend(validate_geometry_component(base_component, repetition_systems))
    proxy = component.get("blockoutProxy")
    if proxy is not None:
        if not isinstance(proxy, Mapping):
            errors.append(f"{_component_label(component)} blockoutProxy must be an object")
        else:
            proxy_primitive = proxy.get("primitive")
            if proxy_primitive not in BLOCKOUT_PROXY_PRIMITIVES:
                errors.append(
                    f"{_component_label(component)} blockoutProxy.primitive must be one of: "
                    + ", ".join(sorted(BLOCKOUT_PROXY_PRIMITIVES))
                )
            proxy_parameters = proxy.get("parameters")
            if proxy_parameters is not None and not isinstance(proxy_parameters, Mapping):
                errors.append(f"{_component_label(component)} blockoutProxy.parameters must be an object")
    return errors


def _emit_registered(
    component: Mapping[str, Any],
    systems: Mapping[str, Mapping[str, Any]],
) -> GeometryEmission:
    primitive = component.get("primitive")
    if not isinstance(primitive, str):
        raise GeometrySpecError(f"{_component_label(component)} primitive is required for a part")
    handler = get_geometry_handler(primitive)
    parameters = _parameters(component)
    return handler.emitter(component, parameters, systems)


def _geometry_modifier_payload(component: Mapping[str, Any]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for modifier in _deformation_stack(component):
        modifier_type = str(modifier["type"])
        item: dict[str, Any] = {
            "type": modifier_type,
            "axis": str(modifier.get("axis", "y")),
            "amount": float(modifier["amount"]),
            "start": float(modifier.get("start", 0)),
            "end": float(modifier.get("end", 1)),
            "power": float(modifier.get("power", 1)),
        }
        if modifier_type == "bend":
            item["direction"] = str(modifier.get("direction", "x"))
        if modifier_type == "noise":
            item["frequency"] = float(modifier.get("frequency", 1))
            item["seed"] = int(modifier.get("seed", 1))
        payload.append(item)
    return payload


def _apply_geometry_modifiers(
    component: Mapping[str, Any],
    emission: GeometryEmission,
) -> GeometryEmission:
    modifiers = _geometry_modifier_payload(component)
    if not modifiers:
        return emission
    return replace(
        emission,
        geometry_expression=(
            f"applyGeometryModifiers({emission.geometry_expression},{_json(modifiers)})"
        ),
        helpers=emission.helpers | frozenset({"special-common", "geometry-modifiers"}),
    )


def _emit_geometry_impl(
    component: Mapping[str, Any],
    repetition_systems: Iterable[Mapping[str, Any]] | None = None,
    *,
    component_lookup: Mapping[str, Mapping[str, Any]] | None = None,
    use_blockout_proxy: bool = False,
    validate: bool,
) -> GeometryEmission:
    if component_type(component) != "part":
        raise GeometrySpecError(f"{_component_label(component)} is an assembly and has no geometry")
    if validate:
        errors = validate_geometry_component(component, repetition_systems, component_lookup)
        if errors:
            raise GeometrySpecError("; ".join(errors))
    resolved_component, _ = _resolve_geometry_references(component, component_lookup)
    systems = _repetition_map(repetition_systems)
    primitive = str(resolved_component.get("primitive"))
    # Resolve the real primitive first so even a declared proxy cannot hide an
    # unsupported capability.
    get_geometry_handler(primitive)
    proxy = component.get("blockoutProxy")
    if use_blockout_proxy and isinstance(proxy, Mapping):
        proxy_component = dict(component)
        proxy_component["primitive"] = str(proxy["primitive"])
        proxy_component["geometryDescriptor"] = {
            "parameters": dict(proxy.get("parameters", {}))
            if isinstance(proxy.get("parameters"), Mapping)
            else {}
        }
        if isinstance(proxy.get("dimensions"), Mapping):
            proxy_component["dimensions"] = dict(proxy["dimensions"])
        emission = _emit_registered(proxy_component, systems)
        return replace(emission, is_blockout_proxy=True)
    return _apply_geometry_modifiers(
        resolved_component,
        _emit_registered(resolved_component, systems),
    )


def emit_geometry(
    component: Mapping[str, Any],
    repetition_systems: Iterable[Mapping[str, Any]] | None = None,
    *,
    component_lookup: Mapping[str, Mapping[str, Any]] | None = None,
    use_blockout_proxy: bool = False,
) -> GeometryEmission:
    """Validate and emit one part, optionally honoring an explicit blockout proxy."""

    return _emit_geometry_impl(
        component,
        repetition_systems,
        component_lookup=component_lookup,
        use_blockout_proxy=use_blockout_proxy,
        validate=True,
    )


def _emit_prevalidated_geometry(
    component: Mapping[str, Any],
    repetition_systems: Iterable[Mapping[str, Any]] | None = None,
    *,
    component_lookup: Mapping[str, Mapping[str, Any]] | None = None,
    use_blockout_proxy: bool = False,
) -> GeometryEmission:
    """Emit after full spec validation has already covered this exact component graph."""

    return _emit_geometry_impl(
        component,
        repetition_systems,
        component_lookup=component_lookup,
        use_blockout_proxy=use_blockout_proxy,
        validate=False,
    )


def geometry_for(primitive: str, parameters: Mapping[str, Any] | None = None) -> str:
    """Backward-compatible expression lookup for simple callers and tests."""

    component = {
        "id": "geometry",
        "componentType": "part",
        "primitive": primitive,
        "geometryDescriptor": {"parameters": dict(parameters or {})},
    }
    return emit_geometry(component).geometry_expression


__all__ = [
    "BLOCKOUT_PROXY_PRIMITIVES",
    "ENDPOINT_PRIMITIVES",
    "GEOMETRY_REGISTRY",
    "MAX_BRANCH_CONTROL_POINTS",
    "MAX_BRANCH_EDGES",
    "MAX_BRANCH_NODES",
    "MAX_DEFORMABLE_CONTROL_POINTS",
    "MAX_DEFORMABLE_FOLDS",
    "MAX_DEFORMABLE_SAMPLED_VERTICES",
    "MAX_FIBER_GUIDE_POINTS",
    "MAX_FIBER_GUIDES",
    "MAX_FIBER_QUADS",
    "MAX_FIBER_SAMPLES",
    "MAX_FIBER_STRANDS",
    "MAX_GEOMETRY_SEGMENTS",
    "MAX_GEOMETRY_MODIFIERS",
    "MAX_IMPLICIT_CELLS",
    "MAX_IMPLICIT_SOURCES",
    "MAX_SCULPT_CELLS",
    "MAX_SCULPT_FIELD_EVALUATIONS",
    "MAX_SCULPT_MODIFIERS",
    "MAX_SCULPT_SOURCES",
    "MAX_INSTANCE_COUNT",
    "MAX_LOFT_SECTIONS",
    "MAX_LOFT_VERTICES",
    "MAX_PATH_POINTS",
    "MAX_PROFILE_POINTS",
    "MAX_SHELL_OPENINGS",
    "MAX_SPECIAL_PARAMETER_MAGNITUDE",
    "MAX_VOLUME_PARTICLES",
    "MAX_VOLUME_SOURCES",
    "GeometryEmission",
    "GeometryHandler",
    "GeometrySpecError",
    "VALID_PRIMITIVES",
    "emit_geometry",
    "geometry_for",
    "get_geometry_handler",
    "validate_geometry_component",
    "validate_repetition_systems",
    "validate_surface_topology_plan",
]
