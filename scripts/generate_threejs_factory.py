#!/usr/bin/env python3
"""Generate a TypeScript Three.js factory skeleton from an ObjectSculptSpec."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sculpt_contract import (
    component_type,
    generation_validation_hash,
    load_spec_file,
    pass_order,
    pipeline_status,
)
from sculpt_geometry import (
    GeometryEmission,
    GeometrySpecError,
    VALID_PRIMITIVES,  # compatibility re-export for existing script consumers
    _emit_prevalidated_geometry,
    emit_geometry,
    geometry_for,  # compatibility re-export for existing script consumers
)
from sculpt_pass_orchestrator import check_pass
from sculpt_special_geometry_typescript import SPECIAL_GEOMETRY_HELPERS
from sculpt_specialized_regions import specialized_regions_payload
from validate_sculpt_spec import validate_spec


PASS_LEVELS = {
    "blockout": {"macro"},
    "structure": {"macro", "meso"},
    "form": {"macro", "meso", "micro"},
    "lookdev": {"macro", "meso", "micro"},
    "interaction": {"macro", "meso", "micro"},
    "optimization": {"macro", "meso", "micro"},
    "structural-pass": {"macro", "meso"},
    "form-refinement": {"macro", "meso", "micro"},
    "material-pass": {"macro", "meso", "micro"},
    "surface-pass": {"macro", "meso", "micro"},
    "lighting-pass": {"macro", "meso", "micro"},
    "interaction-pass": {"macro", "meso", "micro"},
    "optimization-pass": {"macro", "meso", "micro"},
}


_GENERATION_VALIDATION_GUARD = object()


@dataclass(frozen=True)
class _GenerationValidationProof:
    pass_id: str
    spec_sha256: str
    guard: object


def _validate_generation_spec(
    spec: dict[str, Any],
    pass_id: str,
) -> tuple[list[str], list[str], _GenerationValidationProof | None]:
    """Run the full pass-aware gate and issue a non-serializable in-process receipt."""

    errors, warnings = validate_spec(spec, pass_id)
    proof = (
        None
        if errors
        else _GenerationValidationProof(
            pass_id=pass_id,
            spec_sha256=generation_validation_hash(spec, pass_id),
            guard=_GENERATION_VALIDATION_GUARD,
        )
    )
    return errors, warnings, proof


def _validation_proof_matches(
    proof: object,
    spec: dict[str, Any],
    pass_id: str,
) -> bool:
    return (
        isinstance(proof, _GenerationValidationProof)
        and proof.guard is _GENERATION_VALIDATION_GUARD
        and proof.pass_id == pass_id
        and proof.spec_sha256 == generation_validation_hash(spec, pass_id)
    )


def unlocked_pass(spec: dict[str, Any]) -> str:
    status = pipeline_status(spec)
    if status["currentPass"] == "complete":
        return str(status["lastCompletedPass"] or status["passOrder"][-1])
    return str(status["currentPass"])


def assert_pass_unlocked(
    spec: dict[str, Any],
    requested_pass: str,
    *,
    _geometry_prevalidated: bool = False,
) -> None:
    allowed, message, _ = check_pass(
        spec,
        requested_pass,
        _geometry_prevalidated=_geometry_prevalidated,
    )
    if not allowed:
        raise ValueError(message)


def component_refs_for_pass(spec: dict[str, Any], pass_id: str) -> set[str]:
    ids = pass_order(spec)
    if pass_id not in ids:
        return set()
    allowed_ids = set(ids[: ids.index(pass_id) + 1])
    refs: set[str] = set()
    for item in spec.get("buildPasses", []):
        if not isinstance(item, dict) or item.get("id") not in allowed_ids:
            continue
        component_refs = item.get("componentRefs", [])
        if isinstance(component_refs, list):
            refs.update(str(value) for value in component_refs if str(value).strip())
    return refs


def filter_components_for_pass(spec: dict[str, Any], components: list[dict[str, Any]], pass_id: str) -> list[dict[str, Any]]:
    allowed_levels = PASS_LEVELS.get(pass_id, {"macro"})
    explicit_refs = component_refs_for_pass(spec, pass_id)
    included: list[dict[str, Any]] = []
    included_ids: set[str] = set()
    component_by_id = {str(item.get("id")): item for item in components if item.get("id") is not None}

    def include_component(component: dict[str, Any]) -> None:
        component_id = str(component.get("id") or "")
        if not component_id or component_id in included_ids:
            return
        parent_id = component.get("parent")
        if parent_id is not None and str(parent_id) in component_by_id:
            include_component(component_by_id[str(parent_id)])
        included.append(component)
        included_ids.add(component_id)

    for component in components:
        component_id = str(component.get("id") or "")
        level = str(component.get("level") or "macro")
        tier = str(component.get("fidelityTier") or "")
        if component_id in explicit_refs or level in allowed_levels or tier == pass_id:
            include_component(component)
    if not included and components:
        included.append(components[0])
    return included


def pascal_case(value: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", value)
    return "".join(part[:1].upper() + part[1:] for part in parts) or "Object"


def const_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", value.strip())
    if not name:
        return "component"
    if name[0].isdigit():
        name = "_" + name
    return name


def local_var(prefix: str, value: str, index: int) -> str:
    return f"{prefix}_{const_name(value)}_{index}"


def hex_to_number(value: Any, fallback: str = "#8A7A5F") -> str:
    color = value if isinstance(value, str) else fallback
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        return "0x" + color[1:]
    if re.fullmatch(r"#[0-9A-Fa-f]{3}", color):
        return "0x" + "".join(ch * 2 for ch in color[1:])
    return "0x" + fallback[1:]


def material_base_value(value: Any, fallback: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, dict) and isinstance(value.get("base"), (int, float)):
        return float(value["base"])
    return fallback


def json_literal(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def generated_factory_contract_from_source(source: str) -> dict[str, Any]:
    """Read the machine-emitted factory contract without executing TypeScript."""

    fields: dict[str, Any] = {}
    for field in (
        "factoryId",
        "factoryExport",
        "specSha256",
        "passId",
        "expectedComponentIds",
        "expectedMeshComponentIds",
        "expectedPrimitives",
    ):
        match = re.search(rf"^  {field}: (.+),$", source, re.MULTILINE)
        if match is None:
            raise ValueError(f"generated source has no SCULPT_FACTORY_CONTRACT.{field}")
        fields[field] = json.loads(match.group(1))
    return fields


def vector(values: Any, fallback: list[float]) -> str:
    if (
        isinstance(values, list)
        and len(values) == 3
        and all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(float(item))
            for item in values
        )
    ):
        return ", ".join(str(float(item)) for item in values)
    return ", ".join(str(item) for item in fallback)


def scale_vector(component: dict[str, Any], transform: dict[str, Any]) -> str:
    desired_size = [1.0, 1.0, 1.0]
    primitive = str(component.get("primitive") or "box")
    dimensions = component.get("dimensions")
    if isinstance(dimensions, dict):
        radius = dimensions.get("radius")
        diameter = (
            float(radius) * 2
            if isinstance(radius, (int, float))
            and not isinstance(radius, bool)
            and math.isfinite(float(radius))
            else 1
        )
        width = dimensions.get("width", diameter)
        default_height = diameter if primitive in {"sphere", "ellipsoid"} else dimensions.get("length", 1)
        height = dimensions.get("height", default_height)
        depth = dimensions.get("depth", diameter)
        if all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(float(item))
            for item in (width, height, depth)
        ):
            desired_size = [float(width), float(height), float(depth)]
    native_extent = {
        "capsule": [0.5, 1.0, 0.5],
        "torus": [1.0, 1.0, 0.2],
    }.get(primitive, [1.0, 1.0, 1.0])
    transform_scale = transform.get("scale", [1, 1, 1])
    if not (
        isinstance(transform_scale, list)
        and len(transform_scale) == 3
        and all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(float(item))
            for item in transform_scale
        )
    ):
        transform_scale = [1, 1, 1]
    return vector(
        [
            desired_size[index] / native_extent[index] * float(transform_scale[index])
            for index in range(3)
        ],
        [1, 1, 1],
    )


def transform_scale_vector(transform: dict[str, Any]) -> str:
    """Emit only the authored transform multiplier for local-coordinate geometry."""

    values = transform.get("scale", [1, 1, 1])
    if not (
        isinstance(values, list)
        and len(values) == 3
        and all(
            isinstance(item, (int, float)) and not isinstance(item, bool)
            and math.isfinite(float(item))
            for item in values
        )
    ):
        values = [1, 1, 1]
    return vector([float(item) for item in values], [1, 1, 1])


_EXTRUDE_TYPESCRIPT = """type ExtrudeGeometrySpec = {
  outline: number[][];
  holes: number[][][];
  depth: number;
  steps: number;
  bevelEnabled: boolean;
  bevelThickness: number;
  bevelSize: number;
  bevelOffset: number;
  bevelSegments: number;
};

function createExtrudeGeometry(spec: ExtrudeGeometrySpec): THREE.ExtrudeGeometry {
  const shape = new THREE.Shape();
  spec.outline.forEach((point, index) => {
    if (index === 0) shape.moveTo(point[0], point[1]);
    else shape.lineTo(point[0], point[1]);
  });
  shape.closePath();
  for (const contour of spec.holes) {
    const hole = new THREE.Path();
    contour.forEach((point, index) => {
      if (index === 0) hole.moveTo(point[0], point[1]);
      else hole.lineTo(point[0], point[1]);
    });
    hole.closePath();
    shape.holes.push(hole);
  }
  return new THREE.ExtrudeGeometry(shape, {
    depth: spec.depth,
    steps: spec.steps,
    bevelEnabled: spec.bevelEnabled,
    bevelThickness: spec.bevelThickness,
    bevelSize: spec.bevelSize,
    bevelOffset: spec.bevelOffset,
    bevelSegments: spec.bevelSegments,
  });
}"""


_ROUNDED_BOX_TYPESCRIPT = """function createRoundedBoxGeometry(
  width: number,
  height: number,
  depth: number,
  radiusRatio: number,
  segments: number,
): THREE.BufferGeometry {
  const radius = Math.min(width, height, depth) * THREE.MathUtils.clamp(radiusRatio, 0, 0.5);
  const subdivisions = Math.max(1, Math.round(segments));
  const geometry = new THREE.BoxGeometry(
    width,
    height,
    depth,
    subdivisions * 2,
    subdivisions * 2,
    subdivisions * 2,
  );
  if (radius <= Number.EPSILON) return geometry;
  const position = geometry.getAttribute('position') as THREE.BufferAttribute;
  const normal = geometry.getAttribute('normal') as THREE.BufferAttribute;
  const core = new THREE.Vector3(
    Math.max(0, width * 0.5 - radius),
    Math.max(0, height * 0.5 - radius),
    Math.max(0, depth * 0.5 - radius),
  );
  const point = new THREE.Vector3();
  const closest = new THREE.Vector3();
  const direction = new THREE.Vector3();
  for (let index = 0; index < position.count; index += 1) {
    point.fromBufferAttribute(position, index);
    closest.set(
      THREE.MathUtils.clamp(point.x, -core.x, core.x),
      THREE.MathUtils.clamp(point.y, -core.y, core.y),
      THREE.MathUtils.clamp(point.z, -core.z, core.z),
    );
    direction.subVectors(point, closest);
    if (direction.lengthSq() <= Number.EPSILON) {
      direction.fromBufferAttribute(normal, index).normalize();
    } else {
      direction.normalize();
    }
    point.copy(closest).addScaledVector(direction, radius);
    position.setXYZ(index, point.x, point.y, point.z);
    normal.setXYZ(index, direction.x, direction.y, direction.z);
  }
  position.needsUpdate = true;
  normal.needsUpdate = true;
  geometry.computeBoundingBox();
  geometry.computeBoundingSphere();
  return geometry;
}"""


_CURVE_SWEEP_TYPESCRIPT = """type CurveSweepGeometrySpec = {
  path: number[][];
  profile: number[][];
  pathSegments: number;
  closedPath: boolean;
  closedProfile: boolean;
  curveType: 'centripetal' | 'chordal' | 'catmullrom';
  tension: number;
  twist: number;
  radii: number[];
};

function sampleSweepRadius(radii: number[], t: number): number {
  if (radii.length === 0) return 1;
  if (radii.length === 1) return radii[0];
  const scaled = Math.max(0, Math.min(1, t)) * (radii.length - 1);
  const lower = Math.min(radii.length - 2, Math.floor(scaled));
  return THREE.MathUtils.lerp(radii[lower], radii[lower + 1], scaled - lower);
}

function createCurveSweepGeometry(spec: CurveSweepGeometrySpec): THREE.BufferGeometry {
  const points = spec.path.map((point) => new THREE.Vector3(point[0], point[1], point[2]));
  const curve = new THREE.CatmullRomCurve3(points, spec.closedPath, spec.curveType, spec.tension);
  const segments = Math.max(2, spec.pathSegments);
  const frames = curve.computeFrenetFrames(segments, spec.closedPath);
  const positions: number[] = [];
  const uvs: number[] = [];
  const indices: number[] = [];
  const profileCount = spec.profile.length;
  for (let ring = 0; ring <= segments; ring += 1) {
    const t = ring / segments;
    const center = curve.getPointAt(t);
    const normal = frames.normals[ring];
    const binormal = frames.binormals[ring];
    const radius = sampleSweepRadius(spec.radii, t);
    const angle = spec.twist * t;
    const cosine = Math.cos(angle);
    const sine = Math.sin(angle);
    for (let profileIndex = 0; profileIndex < profileCount; profileIndex += 1) {
      const profile = spec.profile[profileIndex];
      const localX = (profile[0] * cosine - profile[1] * sine) * radius;
      const localY = (profile[0] * sine + profile[1] * cosine) * radius;
      const vertex = center.clone()
        .addScaledVector(normal, localX)
        .addScaledVector(binormal, localY);
      positions.push(vertex.x, vertex.y, vertex.z);
      uvs.push(profileIndex / Math.max(1, profileCount - 1), t);
    }
  }
  const profileEdges = spec.closedProfile ? profileCount : profileCount - 1;
  for (let ring = 0; ring < segments; ring += 1) {
    for (let edge = 0; edge < profileEdges; edge += 1) {
      const nextEdge = (edge + 1) % profileCount;
      const a = ring * profileCount + edge;
      const b = (ring + 1) * profileCount + edge;
      const c = (ring + 1) * profileCount + nextEdge;
      const d = ring * profileCount + nextEdge;
      indices.push(a, d, b, b, d, c);
    }
  }
  if (!spec.closedPath && spec.closedProfile) {
    const startCenter = positions.length / 3;
    const start = curve.getPointAt(0);
    positions.push(start.x, start.y, start.z);
    uvs.push(0.5, 0.5);
    const endCenter = positions.length / 3;
    const end = curve.getPointAt(1);
    positions.push(end.x, end.y, end.z);
    uvs.push(0.5, 0.5);
    const endOffset = segments * profileCount;
    for (let edge = 0; edge < profileCount; edge += 1) {
      const nextEdge = (edge + 1) % profileCount;
      indices.push(startCenter, nextEdge, edge);
      indices.push(endCenter, endOffset + edge, endOffset + nextEdge);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  geometry.computeBoundingBox();
  geometry.computeBoundingSphere();
  return geometry;
}"""


_INSTANCES_TYPESCRIPT = """type InstanceTransformSpec = {
  position: number[];
  rotation: number[];
  scale: number[];
  normal?: number[];
  spin?: number;
};

type InstanceLayoutSpec = {
  mode: 'explicit' | 'grid' | 'radial' | 'along-path' | 'scatter';
  transforms?: InstanceTransformSpec[];
  counts?: number[];
  spacing?: number[];
  origin?: number[];
  rotation?: number[];
  scale?: number[];
  count?: number;
  radius?: number;
  startAngle?: number;
  arc?: number;
  axis?: 'x' | 'y' | 'z';
  alignToRadius?: boolean;
  path?: number[][];
  closed?: boolean;
  alignToPath?: boolean;
  min?: number[];
  max?: number[];
  seed?: number;
  rotationRange?: number[];
  scaleRange?: number[];
};

function applyInstanceLayout(mesh: THREE.InstancedMesh, layout: InstanceLayoutSpec): void {
  const pivot = new THREE.Object3D();
  const up = new THREE.Vector3(0, 1, 0);
  let writeIndex = 0;
  const write = (
    position: number[],
    rotation: number[],
    scale: number[],
    orientation?: THREE.Quaternion,
  ): void => {
    pivot.position.set(position[0], position[1], position[2]);
    pivot.rotation.set(rotation[0], rotation[1], rotation[2]);
    if (orientation) pivot.quaternion.premultiply(orientation);
    pivot.scale.set(scale[0], scale[1], scale[2]);
    pivot.updateMatrix();
    mesh.setMatrixAt(writeIndex, pivot.matrix);
    writeIndex += 1;
  };
  const rotation = layout.rotation ?? [0, 0, 0];
  const scale = layout.scale ?? [1, 1, 1];
  if (layout.mode === 'explicit') {
    for (const transform of layout.transforms ?? []) {
      let orientation: THREE.Quaternion | undefined;
      if (transform.normal && transform.normal.length === 3) {
        const normal = new THREE.Vector3(
          transform.normal[0],
          transform.normal[1],
          transform.normal[2],
        ).normalize();
        orientation = new THREE.Quaternion().setFromUnitVectors(up, normal);
        if (transform.spin) {
          orientation.multiply(
            new THREE.Quaternion().setFromAxisAngle(up, transform.spin),
          );
        }
      } else if (transform.spin) {
        orientation = new THREE.Quaternion().setFromAxisAngle(up, transform.spin);
      }
      write(transform.position, transform.rotation, transform.scale, orientation);
    }
  } else if (layout.mode === 'grid') {
    const counts = layout.counts ?? [1, 1, 1];
    const spacing = layout.spacing ?? [1, 1, 1];
    const origin = layout.origin ?? [0, 0, 0];
    for (let x = 0; x < counts[0]; x += 1) {
      for (let y = 0; y < counts[1]; y += 1) {
        for (let z = 0; z < counts[2]; z += 1) {
          write([
            origin[0] + (x - (counts[0] - 1) * 0.5) * spacing[0],
            origin[1] + (y - (counts[1] - 1) * 0.5) * spacing[1],
            origin[2] + (z - (counts[2] - 1) * 0.5) * spacing[2],
          ], rotation, scale);
        }
      }
    }
  } else if (layout.mode === 'radial') {
    const count = layout.count ?? 1;
    const origin = layout.origin ?? [0, 0, 0];
    const axis = layout.axis ?? 'y';
    for (let index = 0; index < count; index += 1) {
      const arc = layout.arc ?? Math.PI * 2;
      const denominator = Math.abs(arc - Math.PI * 2) < 0.000001 ? count : Math.max(1, count - 1);
      const angle = (layout.startAngle ?? 0) + arc * index / denominator;
      const radius = layout.radius ?? 1;
      const position = axis === 'x'
        ? [origin[0], origin[1] + Math.cos(angle) * radius, origin[2] + Math.sin(angle) * radius]
        : axis === 'z'
          ? [origin[0] + Math.cos(angle) * radius, origin[1] + Math.sin(angle) * radius, origin[2]]
          : [origin[0] + Math.cos(angle) * radius, origin[1], origin[2] + Math.sin(angle) * radius];
      const localRotation = [...rotation];
      if (layout.alignToRadius) {
        if (axis === 'x') localRotation[0] += angle;
        else if (axis === 'z') localRotation[2] += angle;
        else localRotation[1] += -angle + Math.PI * 0.5;
      }
      write(position, localRotation, scale);
    }
  } else if (layout.mode === 'along-path') {
    const points = (layout.path ?? []).map((point) => new THREE.Vector3(point[0], point[1], point[2]));
    const curve = new THREE.CatmullRomCurve3(points, layout.closed ?? false, 'centripetal');
    const count = layout.count ?? 1;
    for (let index = 0; index < count; index += 1) {
      const t = count === 1 ? 0.5 : index / ((layout.closed ?? false) ? count : count - 1);
      const position = curve.getPointAt(t);
      const orientation = layout.alignToPath === false
        ? undefined
        : new THREE.Quaternion().setFromUnitVectors(up, curve.getTangentAt(t).normalize());
      write(position.toArray(), rotation, scale, orientation);
    }
  } else {
    let state = (layout.seed ?? 1) >>> 0;
    const random = (): number => {
      state ^= state << 13;
      state ^= state >>> 17;
      state ^= state << 5;
      return (state >>> 0) / 4294967295;
    };
    const minimum = layout.min ?? [-0.5, -0.5, -0.5];
    const maximum = layout.max ?? [0.5, 0.5, 0.5];
    const rotationRange = layout.rotationRange ?? [0, 0, 0];
    const scaleRange = layout.scaleRange ?? [1, 1];
    for (let index = 0; index < (layout.count ?? 1); index += 1) {
      const randomScale = THREE.MathUtils.lerp(scaleRange[0], scaleRange[1], random());
      write([
        THREE.MathUtils.lerp(minimum[0], maximum[0], random()),
        THREE.MathUtils.lerp(minimum[1], maximum[1], random()),
        THREE.MathUtils.lerp(minimum[2], maximum[2], random()),
      ], [
        rotation[0] + (random() * 2 - 1) * rotationRange[0],
        rotation[1] + (random() * 2 - 1) * rotationRange[1],
        rotation[2] + (random() * 2 - 1) * rotationRange[2],
      ], scale.map((value) => value * randomScale));
    }
  }
  mesh.count = writeIndex;
  mesh.instanceMatrix.needsUpdate = true;
}"""


def typescript_geometry_helpers(required: set[str]) -> list[str]:
    snippets = {
        "rounded-box": _ROUNDED_BOX_TYPESCRIPT,
        "extrude": _EXTRUDE_TYPESCRIPT,
        "curve-sweep": _CURVE_SWEEP_TYPESCRIPT,
        "instances": _INSTANCES_TYPESCRIPT,
        **SPECIAL_GEOMETRY_HELPERS,
    }
    lines: list[str] = []
    for name, snippet in snippets.items():
        if name in required:
            lines.extend(["", *snippet.splitlines(), ""])
    return lines


LOCAL_PATH_FEATURES = {"seam", "seam-line", "raised-ridge", "fabric-stitch"}
LOCAL_POINT_FEATURES = {"button", "rivet", "screw"}


def local_feature_geometry(feature: dict[str, Any]) -> str | None:
    """Emit only bounded local details that have an honest geometry representation."""
    feature_type = str(feature.get("type") or "")
    if feature_type in LOCAL_PATH_FEATURES:
        path = feature.get("path")
        if not (
            isinstance(path, list)
            and len(path) >= 2
            and all(
                isinstance(point, list)
                and len(point) == 3
                and all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                    for value in point
                )
                for point in path
            )
        ):
            return None
        points = ",".join(
            f"new THREE.Vector3({float(point[0])},{float(point[1])},{float(point[2])})"
            for point in path
        )
        radius = feature.get("radius", 0.006)
        radius = float(radius) if isinstance(radius, (int, float)) and not isinstance(radius, bool) else 0.006
        segments = feature.get("segments", max(12, len(path) * 8))
        segments = int(segments) if isinstance(segments, int) and not isinstance(segments, bool) else max(12, len(path) * 8)
        return (
            "new THREE.TubeGeometry("
            f"new THREE.CatmullRomCurve3([{points}],false,'centripetal'),"
            f"{segments},{radius},8,false)"
        )
    if feature_type in LOCAL_POINT_FEATURES:
        radius = feature.get("radius", 0.012)
        radius = float(radius) if isinstance(radius, (int, float)) and not isinstance(radius, bool) else 0.012
        return f"new THREE.SphereGeometry({radius},24,14)"
    if feature_type == "decal":
        size = feature.get("size", [0.1, 0.1])
        if not (
            isinstance(size, list)
            and len(size) == 2
            and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in size)
        ):
            size = [0.1, 0.1]
        return f"new THREE.PlaneGeometry({float(size[0])},{float(size[1])},1,1)"
    return None


def generate(
    spec: dict[str, Any],
    pass_id: str,
    *,
    _geometry_prevalidated: bool = False,
) -> str:
    target = str(spec.get("targetName") or "Procedural Object")
    type_name = pascal_case(target)
    function_name = f"create{type_name}Model"
    spec_sha256 = generation_validation_hash(spec, pass_id)
    factory_id = hashlib.sha256(
        f"threejs-sculpt-factory-v1:{function_name}:{pass_id}:{spec_sha256}".encode("utf-8")
    ).hexdigest()
    all_materials = {
        str(material.get("id") or f"material{index}"): material
        for index, material in enumerate(spec.get("materials", []))
        if isinstance(material, dict)
    }
    all_components = [item for item in spec.get("componentTree", []) if isinstance(item, dict)]
    component_lookup = {
        str(item["id"]): item
        for item in all_components
        if isinstance(item.get("id"), str) and item["id"].strip()
    }
    components = filter_components_for_pass(spec, all_components, pass_id)
    repetition_systems = [
        item for item in spec.get("repetitionSystems", []) if isinstance(item, dict)
    ]
    component_emissions: dict[int, GeometryEmission] = {}
    geometry_helpers: set[str] = set()
    geometry_emitter = _emit_prevalidated_geometry if _geometry_prevalidated else emit_geometry
    for index, component in enumerate(components):
        kind = component_type(component)
        if kind == "assembly":
            continue
        if kind != "part":
            raise GeometrySpecError(
                f"component {component.get('id')!r} has invalid componentType {kind!r}"
            )
        emission = geometry_emitter(
            component,
            repetition_systems,
            component_lookup=component_lookup,
            use_blockout_proxy=pass_id == "blockout",
        )
        component_emissions[index] = emission
        geometry_helpers.update(emission.helpers)
    used_material_ids = {
        str(component.get("material"))
        for component in components
        if component.get("material") is not None
    }
    materials = {
        material_id: material
        for material_id, material in all_materials.items()
        if material_id in used_material_ids or not used_material_ids
    }
    if not materials and all_materials:
        first_id = next(iter(all_materials))
        materials[first_id] = all_materials[first_id]
    detailed_materials = pass_id not in {"blockout", "structure", "structural-pass"}
    emit_local_details = detailed_materials
    quality_first = spec.get("qualityProfile") == "reference-fidelity"
    expected_component_ids = sorted(
        str(component.get("id"))
        for component in components
        if isinstance(component.get("id"), str) and component.get("id")
    )
    expected_mesh_ids = sorted(
        str(components[index].get("id"))
        for index in component_emissions
        if isinstance(components[index].get("id"), str) and components[index].get("id")
    )
    expected_primitives = {
        str(components[index].get("id")): emission.primitive
        for index, emission in component_emissions.items()
        if isinstance(components[index].get("id"), str) and components[index].get("id")
    }

    lines: list[str] = [
        "import * as THREE from 'three';",
        "",
        "export type ProceduralModelOptions = {",
        "  wireframe?: boolean;",
        "  castShadow?: boolean;",
        "  receiveShadow?: boolean;",
        "  textureSize?: number;",
        "  textureAnisotropy?: number;",
        "  qualityPriority?: 'reference-fidelity' | 'balanced';",
        "};",
        "",
        "export type ProceduralModelRuntime = {",
        "  nodes: Record<string, THREE.Object3D>;",
        "  meshes: Record<string, THREE.Mesh>;",
        "  instances: Record<string, THREE.InstancedMesh>;",
        "  sockets: Record<string, THREE.Object3D>;",
        "  colliders: Record<string, unknown>;",
        "  destructionGroups: Record<string, THREE.Object3D[]>;",
        "  dispose: () => void;",
        "};",
        "",
        "export type SculptRuntimeReceipt = {",
        "  artifactType: 'threejs-sculpt-runtime-receipt';",
        "  version: 1;",
        "  factoryId: string;",
        "  factoryExport: 'createSculptModel';",
        "  specSha256: string;",
        "  passId: string;",
        "  rootName: string;",
        "  rootAttachedToScene: boolean;",
        "  rootEffectivelyVisible: boolean;",
        "  componentIds: string[];",
        "  meshComponentIds: string[];",
        "  componentPrimitives: Record<string, string>;",
        "  missingComponentIds: string[];",
        "  missingMeshComponentIds: string[];",
        "  hiddenMeshComponentIds: string[];",
        "  unexpectedGeneratedDescendantMeshes: string[];",
        "  unexpectedVisibleMeshes: string[];",
        "  initialGeometryFingerprint: string[];",
        "  geometryFingerprint: string[];",
        "  geometryChangedComponentIds: string[];",
        "};",
        "",
        "export const SCULPT_FACTORY_CONTRACT = {",
        "  artifactType: 'threejs-sculpt-factory-contract',",
        "  version: 1,",
        f"  factoryId: {json.dumps(factory_id)},",
        "  factoryExport: \"createSculptModel\",",
        f"  generatedFunction: {json.dumps(function_name)},",
        f"  specSha256: {json.dumps(spec_sha256)},",
        f"  passId: {json.dumps(pass_id)},",
        f"  expectedComponentIds: {json_literal(expected_component_ids)},",
        f"  expectedMeshComponentIds: {json_literal(expected_mesh_ids)},",
        f"  expectedPrimitives: {json_literal(expected_primitives)},",
        "} as const;",
        "",
        "const sculptFactoryRoots = new Set<THREE.Group>();",
        "const sculptFactoryInitialGeometry = new Map<THREE.Group, string[]>();",
        "const sculptFactoryGeometryObjects = new Map<THREE.Group, Record<string, THREE.BufferGeometry>>();",
        "",
        "function sculptRootScene(root: THREE.Object3D): THREE.Scene | null {",
        "  let cursor: THREE.Object3D | null = root;",
        "  while (cursor) {",
        "    if (cursor instanceof THREE.Scene) return cursor;",
        "    cursor = cursor.parent;",
        "  }",
        "  return null;",
        "}",
        "",
        "function sculptObjectVisible(object: THREE.Object3D): boolean {",
        "  let cursor: THREE.Object3D | null = object;",
        "  while (cursor) {",
        "    if (!cursor.visible) return false;",
        "    cursor = cursor.parent;",
        "  }",
        "  return true;",
        "}",
        "",
        "function sculptDescendsFrom(object: THREE.Object3D, root: THREE.Object3D): boolean {",
        "  let cursor: THREE.Object3D | null = object;",
        "  while (cursor) {",
        "    if (cursor === root) return true;",
        "    cursor = cursor.parent;",
        "  }",
        "  return false;",
        "}",
        "",
        "function sculptGeometryFingerprint(",
        "  renderables: Record<string, THREE.Mesh | THREE.InstancedMesh>,",
        "): string[] {",
        "  return Object.entries(renderables)",
        "    .sort(([a], [b]) => a.localeCompare(b))",
        "    .map(([id, mesh]) => {",
        "      const position = mesh.geometry.getAttribute('position');",
        "      const positionArray = position?.array;",
        "      let checksum = 2166136261;",
        "      if (positionArray) {",
        "        for (let index = 0; index < positionArray.length; index += 1) {",
        "          checksum ^= Math.round(Number(positionArray[index]) * 1_000_000);",
        "          checksum = Math.imul(checksum, 16777619);",
        "        }",
        "      }",
        "      return `${id}:${mesh.geometry.type}:${position?.count ?? 0}:${mesh.geometry.index?.count ?? 0}:${checksum >>> 0}`;",
        "    });",
        "}",
        "",
        "export function captureSculptRuntimeReceipt(root: THREE.Group): SculptRuntimeReceipt {",
        "  if (!sculptFactoryRoots.has(root)) {",
        "    throw new Error('runtime receipt requires a root created by createSculptModel');",
        "  }",
        "  const runtime = root.userData.sculptRuntime as ProceduralModelRuntime | undefined;",
        "  if (!runtime) throw new Error('generated sculpt root has no runtime inventory');",
        "  const scene = sculptRootScene(root);",
        "  const componentIds = Object.keys(runtime.nodes).filter((id) => id !== '$root').sort();",
        "  const renderables: Record<string, THREE.Mesh | THREE.InstancedMesh> = { ...runtime.meshes, ...runtime.instances };",
        "  const meshComponentIds = Object.keys(renderables).sort();",
        "  const componentPrimitives: Record<string, string> = {};",
        "  const geometryFingerprint = sculptGeometryFingerprint(renderables);",
        "  const initialGeometryFingerprint = sculptFactoryInitialGeometry.get(root) ?? [];",
        "  const initialGeometryObjects = sculptFactoryGeometryObjects.get(root) ?? {};",
        "  const initialFingerprintById = new Map(initialGeometryFingerprint.map((value) => [value.split(':', 1)[0], value]));",
        "  const currentFingerprintById = new Map(geometryFingerprint.map((value) => [value.split(':', 1)[0], value]));",
        "  const geometryChangedComponentIds = meshComponentIds.filter((id) =>",
        "    initialGeometryObjects[id] !== renderables[id]?.geometry",
        "    || initialFingerprintById.get(id) !== currentFingerprintById.get(id)",
        "  );",
        "  for (const [id, mesh] of Object.entries(renderables).sort(([a], [b]) => a.localeCompare(b))) {",
        "    const primitive = mesh.userData.sculptPrimitive;",
        "    if (typeof primitive === 'string') componentPrimitives[id] = primitive;",
        "  }",
        "  const unexpectedVisibleMeshes: string[] = [];",
        "  const unexpectedGeneratedDescendantMeshes: string[] = [];",
        "  const knownRenderables = new Set<THREE.Object3D>(Object.values(renderables));",
        "  root.traverse((object) => {",
        "    if (object instanceof THREE.Mesh && !knownRenderables.has(object) && sculptObjectVisible(object)) {",
        "      unexpectedGeneratedDescendantMeshes.push(object.name || object.uuid);",
        "    }",
        "  });",
        "  scene?.traverse((object) => {",
        "    if (!(object instanceof THREE.Mesh) || sculptDescendsFrom(object, root)) return;",
        "    if (object.userData.reviewOnly === true || object.userData.sculptValidationRole === 'environment') return;",
        "    if (sculptObjectVisible(object)) unexpectedVisibleMeshes.push(object.name || object.uuid);",
        "  });",
        "  return {",
        "    artifactType: 'threejs-sculpt-runtime-receipt',",
        "    version: 1,",
        "    factoryId: SCULPT_FACTORY_CONTRACT.factoryId,",
        "    factoryExport: SCULPT_FACTORY_CONTRACT.factoryExport,",
        "    specSha256: SCULPT_FACTORY_CONTRACT.specSha256,",
        "    passId: SCULPT_FACTORY_CONTRACT.passId,",
        "    rootName: root.name,",
        "    rootAttachedToScene: scene !== null,",
        "    rootEffectivelyVisible: sculptObjectVisible(root),",
        "    componentIds,",
        "    meshComponentIds,",
        "    componentPrimitives,",
        "    missingComponentIds: SCULPT_FACTORY_CONTRACT.expectedComponentIds.filter((id) => !componentIds.includes(id)),",
        "    missingMeshComponentIds: SCULPT_FACTORY_CONTRACT.expectedMeshComponentIds.filter((id) => !meshComponentIds.includes(id)),",
        "    hiddenMeshComponentIds: SCULPT_FACTORY_CONTRACT.expectedMeshComponentIds.filter((id) => {",
        "      const mesh = renderables[id];",
        "      return mesh !== undefined && !sculptObjectVisible(mesh);",
        "    }),",
        "    unexpectedGeneratedDescendantMeshes: unexpectedGeneratedDescendantMeshes.sort(),",
        "    unexpectedVisibleMeshes: unexpectedVisibleMeshes.sort(),",
        "    initialGeometryFingerprint: [...initialGeometryFingerprint],",
        "    geometryFingerprint,",
        "    geometryChangedComponentIds,",
        "  };",
        "}",
        "",
        "function installSculptRuntimeCapture(): void {",
        "  type CaptureHost = typeof globalThis & {",
        "    __THREEJS_SCULPT_RUNTIME_FACTORIES__?: Record<string, () => SculptRuntimeReceipt[]>;",
        "    __THREEJS_SCULPT_CAPTURE_RUNTIME__?: () => SculptRuntimeReceipt[];",
        "  };",
        "  const host = globalThis as CaptureHost;",
        "  const registry = host.__THREEJS_SCULPT_RUNTIME_FACTORIES__ ?? {};",
        "  host.__THREEJS_SCULPT_RUNTIME_FACTORIES__ = registry;",
        "  registry[SCULPT_FACTORY_CONTRACT.factoryId] = () =>",
        "    Array.from(sculptFactoryRoots).map((root) => captureSculptRuntimeReceipt(root));",
        "  host.__THREEJS_SCULPT_CAPTURE_RUNTIME__ = () =>",
        "    Object.values(registry).flatMap((capture) => capture());",
        "}",
        "",
        "type MaterialLayer = Record<string, unknown>;",
        "type SculptMaterialProfile = 'standard' | 'cloth' | 'fiber' | 'glass' | 'liquid' | 'volume';",
        "type SculptMaterialSpec = Record<string, unknown> & {",
        "  albedo?: MaterialLayer;",
        "  alpha?: unknown;",
        "  alphaHash?: unknown;",
        "  ambientOcclusion?: unknown;",
        "  anisotropy?: unknown;",
        "  anisotropyRotation?: unknown;",
        "  attenuationColor?: unknown;",
        "  attenuationDistance?: unknown;",
        "  baseColor?: unknown;",
        "  bump?: unknown;",
        "  clearcoat?: unknown;",
        "  clearcoatRoughness?: unknown;",
        "  color?: unknown;",
        "  colorVariation?: MaterialLayer;",
        "  depthWrite?: unknown;",
        "  displacement?: unknown;",
        "  dirt?: MaterialLayer;",
        "  dispersion?: unknown;",
        "  doubleSided?: unknown;",
        "  emissive?: unknown;",
        "  emissiveIntensity?: unknown;",
        "  envMapIntensity?: unknown;",
        "  forceSinglePass?: unknown;",
        "  ior?: unknown;",
        "  localOverrides?: unknown;",
        "  materialProfile?: unknown;",
        "  metalness?: unknown;",
        "  normal?: unknown;",
        "  opacity?: unknown;",
        "  referencePbr?: MaterialLayer;",
        "  roughness?: unknown;",
        "  sheen?: unknown;",
        "  sheenColor?: unknown;",
        "  sheenRoughness?: unknown;",
        "  specularColor?: unknown;",
        "  specularIntensity?: unknown;",
        "  surfaceFrequencyBands?: unknown;",
        "  textureProjection?: MaterialLayer;",
        "  textureResolution?: unknown;",
        "  thickness?: unknown;",
        "  transmission?: unknown;",
        "  wear?: MaterialLayer;",
        "};",
        "",
        "function hashString(value: string): number {",
        "  let hash = 2166136261;",
        "  for (let index = 0; index < value.length; index += 1) {",
        "    hash ^= value.charCodeAt(index);",
        "    hash = Math.imul(hash, 16777619);",
        "  }",
        "  return hash >>> 0;",
        "}",
        "",
        "function readLayerNumber(value: unknown, keys: string[], fallback: number): number {",
        "  if (typeof value === 'number') return value;",
        "  if (value && typeof value === 'object') {",
        "    const record = value as Record<string, unknown>;",
        "    for (const key of keys) {",
        "      if (typeof record[key] === 'number') return record[key] as number;",
        "    }",
        "  }",
        "  return fallback;",
        "}",
        "",
        "function readMaterialProfile(value: unknown): SculptMaterialProfile {",
        "  if (value === undefined) return 'standard';",
        "  if (value === 'standard' || value === 'cloth' || value === 'fiber'",
        "      || value === 'glass' || value === 'liquid' || value === 'volume') {",
        "    return value;",
        "  }",
        "  throw new Error(`unsupported materialProfile ${String(value)}`);",
        "}",
        "",
        "function hexToRgb(hex: string): [number, number, number] {",
        "  const normalized = /^#[0-9a-f]{3}$/i.test(hex)",
        "    ? '#' + hex.slice(1).split('').map((part) => part + part).join('')",
        "    : hex;",
        "  const value = /^#[0-9a-f]{6}$/i.test(normalized) ? Number.parseInt(normalized.slice(1), 16) : 0x8a7a5f;",
        "  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];",
        "}",
        "",
        "function materialPalette(spec: SculptMaterialSpec): string[] {",
        "  const palette = spec.colorVariation?.palette;",
        "  if (Array.isArray(palette) && palette.length > 0) return palette.filter((value) => typeof value === 'string');",
        "  const secondary = spec.albedo?.secondary;",
        "  const colors = [spec.baseColor ?? spec.color ?? spec.albedo?.dominant, ...(Array.isArray(secondary) ? secondary : [])];",
        "  return colors.filter((value): value is string => typeof value === 'string' && value.startsWith('#'));",
        "}",
        "",
        "function clamp01(value: number): number {",
        "  return Math.max(0, Math.min(1, value));",
        "}",
        "",
        "function smoothCurve(value: number): number {",
        "  return value * value * (3 - 2 * value);",
        "}",
        "",
        "function periodicHash(x: number, y: number, seed: number, periodX: number, periodY: number): number {",
        "  const wrappedX = ((x % periodX) + periodX) % periodX;",
        "  const wrappedY = ((y % periodY) + periodY) % periodY;",
        "  let value = Math.imul(wrappedX + seed * 17, 374761393) ^ Math.imul(wrappedY + seed * 31, 668265263);",
        "  value = Math.imul(value ^ (value >>> 13), 1274126177);",
        "  return ((value ^ (value >>> 16)) >>> 0) / 4294967295;",
        "}",
        "",
        "function periodicValueNoise(u: number, v: number, seed: number, periodX: number, periodY: number): number {",
        "  const x = u * periodX;",
        "  const y = v * periodY;",
        "  const x0 = Math.floor(x);",
        "  const y0 = Math.floor(y);",
        "  const tx = smoothCurve(x - x0);",
        "  const ty = smoothCurve(y - y0);",
        "  const a = periodicHash(x0, y0, seed, periodX, periodY);",
        "  const b = periodicHash(x0 + 1, y0, seed, periodX, periodY);",
        "  const c = periodicHash(x0, y0 + 1, seed, periodX, periodY);",
        "  const d = periodicHash(x0 + 1, y0 + 1, seed, periodX, periodY);",
        "  return THREE.MathUtils.lerp(THREE.MathUtils.lerp(a, b, tx), THREE.MathUtils.lerp(c, d, tx), ty);",
        "}",
        "",
        "type SurfaceBand = {",
        "  frequency: number;",
        "  amplitude: number;",
        "  stretchX: number;",
        "  stretchY: number;",
        "  ridge: boolean;",
        "};",
        "",
        "function surfaceBands(spec: SculptMaterialSpec): SurfaceBand[] {",
        "  const source = Array.isArray(spec.surfaceFrequencyBands) ? spec.surfaceFrequencyBands : [];",
        "  const parsed = source.flatMap((item: unknown) => {",
        "    if (!item || typeof item !== 'object') return [];",
        "    const band = item as Record<string, unknown>;",
        "    const frequency = typeof band.frequency === 'number' ? band.frequency : 0;",
        "    const amplitude = typeof band.amplitude === 'number' ? band.amplitude : 0;",
        "    if (frequency <= 0 || amplitude <= 0) return [];",
        "    const stretch = Array.isArray(band.stretch) ? band.stretch : [1, 1];",
        "    const description = `${String(band.pattern ?? '')} ${String(band.role ?? '')}`.toLowerCase();",
        "    return [{",
        "      frequency,",
        "      amplitude,",
        "      stretchX: typeof stretch[0] === 'number' ? Math.max(0.1, stretch[0]) : 1,",
        "      stretchY: typeof stretch[1] === 'number' ? Math.max(0.1, stretch[1]) : 1,",
        "      ridge: /(ridge|groove|grain|fiber|striated|crack)/.test(description),",
        "    }];",
        "  });",
        "  return parsed.length > 0 ? parsed : [",
        "    { frequency: 2, amplitude: 0.42, stretchX: 1, stretchY: 1, ridge: false },",
        "    { frequency: 12, amplitude: 0.22, stretchX: 1, stretchY: 1, ridge: false },",
        "    { frequency: 56, amplitude: 0.08, stretchX: 1, stretchY: 1, ridge: false },",
        "  ];",
        "}",
        "",
        "function sampleSurface(u: number, v: number, bands: SurfaceBand[], seed: number): number {",
        "  let value = 0;",
        "  let weight = 0;",
        "  for (let index = 0; index < bands.length; index += 1) {",
        "    const band = bands[index];",
        "    const periodX = Math.max(1, Math.round(band.frequency * band.stretchX));",
        "    const periodY = Math.max(1, Math.round(band.frequency * band.stretchY));",
        "    let sample = periodicValueNoise(u, v, seed + index * 1013, periodX, periodY);",
        "    if (band.ridge) sample = 1 - Math.abs(sample * 2 - 1);",
        "    value += sample * band.amplitude;",
        "    weight += band.amplitude;",
        "  }",
        "  return weight > 0 ? clamp01(value / weight) : 0.5;",
        "}",
        "",
        "type LocalMaterialLayer = {",
        "  type: string;",
        "  amount: number;",
        "  color: [number, number, number];",
        "  roughnessDelta: number;",
        "  metalnessDelta: number;",
        "  heightDelta: number;",
        "  pattern: string;",
        "  frequency: number;",
        "  threshold: number;",
        "  contrast: number;",
        "  cavityBias: number;",
        "  edgeBias: number;",
        "  verticalBias: number;",
        "  regional: boolean;",
        "  uvCenter: [number, number];",
        "  uvScale: [number, number];",
        "  feather: number;",
        "  seed: number;",
        "};",
        "",
        "function materialLocalLayers(spec: SculptMaterialSpec): LocalMaterialLayer[] {",
        "  const result: LocalMaterialLayer[] = [];",
        "  const append = (source: Record<string, unknown>, fallbackType: string, fallbackPattern: string): void => {",
        "    const type = typeof source.type === 'string' ? source.type : fallbackType;",
        "    if (type === 'material-map-evidence') return;",
        "    const amount = clamp01(readLayerNumber(source.amount, ['base', 'amount'], typeof source.amount === 'number' ? source.amount : 0));",
        "    if (amount <= 0) return;",
        "    const mask = source.mask && typeof source.mask === 'object' ? source.mask as Record<string, unknown> : {};",
        "    const uvCenter = Array.isArray(mask.uvCenter) ? mask.uvCenter : null;",
        "    const uvScale = Array.isArray(mask.uvScale) ? mask.uvScale : null;",
        "    const wet = type === 'wetness';",
        "    const worn = type === 'wear' || type === 'fade';",
        "    const defaultColor = wet ? '#1B2024' : (worn ? '#B8AE9B' : '#302B25');",
        "    const color = hexToRgb(typeof source.color === 'string' ? source.color : defaultColor);",
        "    result.push({",
        "      type,",
        "      amount,",
        "      color,",
        "      roughnessDelta: Math.max(-1, Math.min(1, readLayerNumber(source.roughnessDelta, ['base', 'amount'], wet || worn ? -0.22 : 0.18))),",
        "      metalnessDelta: Math.max(-1, Math.min(1, readLayerNumber(source.metalnessDelta, ['base', 'amount'], 0))),",
        "      heightDelta: Math.max(-0.25, Math.min(0.25, readLayerNumber(source.heightDelta, ['base', 'amount'], worn ? -0.025 : 0.006))),",
        "      pattern: typeof mask.pattern === 'string' ? mask.pattern : fallbackPattern,",
        "      frequency: Math.max(1, readLayerNumber(mask.frequency, ['base', 'amount'], 18)),",
        "      threshold: clamp01(readLayerNumber(mask.threshold, ['base', 'amount'], 0.52)),",
        "      contrast: Math.max(0.1, readLayerNumber(mask.contrast, ['base', 'amount'], 3.2)),",
        "      cavityBias: clamp01(readLayerNumber(mask.cavityBias, ['base', 'amount'], readLayerNumber(source.cavityBias, ['base', 'amount'], fallbackPattern === 'cavity' ? 0.8 : 0))),",
        "      edgeBias: clamp01(readLayerNumber(mask.edgeBias, ['base', 'amount'], fallbackPattern === 'edge' ? 0.8 : 0)),",
        "      verticalBias: Math.max(-1, Math.min(1, readLayerNumber(mask.verticalBias, ['base', 'amount'], 0))),",
        "      regional: Boolean(uvCenter && uvCenter.length === 2 && uvScale && uvScale.length === 2),",
        "      uvCenter: [",
        "        uvCenter && typeof uvCenter[0] === 'number' ? clamp01(uvCenter[0]) : 0.5,",
        "        uvCenter && typeof uvCenter[1] === 'number' ? clamp01(uvCenter[1]) : 0.5,",
        "      ],",
        "      uvScale: [",
        "        uvScale && typeof uvScale[0] === 'number' ? Math.max(0.001, uvScale[0]) : 1,",
        "        uvScale && typeof uvScale[1] === 'number' ? Math.max(0.001, uvScale[1]) : 1,",
        "      ],",
        "      feather: Math.max(0.001, clamp01(readLayerNumber(mask.feather, ['base', 'amount'], 0.25))),",
        "      seed: Math.round(readLayerNumber(mask.seed, ['base', 'amount'], result.length * 4099 + 97)),",
        "    });",
        "  };",
        "  if (spec.dirt && typeof spec.dirt === 'object') append(spec.dirt, 'dirt', 'cavity');",
        "  if (spec.wear && typeof spec.wear === 'object') {",
        "    const edgeWear = readLayerNumber(spec.wear.edgeWear, ['base', 'amount'], 0);",
        "    if (edgeWear > 0) append({ ...spec.wear, amount: edgeWear, type: 'wear' }, 'wear', 'edge');",
        "  }",
        "  if (Array.isArray(spec.localOverrides)) {",
        "    for (const item of spec.localOverrides) {",
        "      if (item && typeof item === 'object') append(item as Record<string, unknown>, 'stain', 'noise');",
        "    }",
        "  }",
        "  if (result.length > 16) throw new Error('material local layer limit exceeded (16)');",
        "  return result;",
        "}",
        "",
        "function sampleLocalLayerMask(",
        "  layer: LocalMaterialLayer,",
        "  u: number,",
        "  v: number,",
        "  cavity: number,",
        "  edge: number,",
        "  seed: number,",
        "): number {",
        "  const period = Math.max(1, Math.round(layer.frequency));",
        "  const noise = periodicValueNoise(u, v, seed + layer.seed, period, period);",
        "  let mask = smoothCurve(clamp01((noise - layer.threshold) * layer.contrast + 0.5));",
        "  if (layer.pattern === 'speckle') mask *= mask;",
        "  if (layer.pattern === 'streak') {",
        "    const streak = clamp01(0.5 + Math.sin((u * period + noise * 0.7) * Math.PI * 2) * 0.5);",
        "    mask = smoothCurve(mask * streak);",
        "  }",
        "  if (layer.pattern === 'cavity') mask = Math.max(mask * 0.35, cavity);",
        "  if (layer.pattern === 'edge') mask = Math.max(mask * 0.35, edge);",
        "  const periodicVertical = 0.5 - Math.cos(v * Math.PI * 2) * 0.5;",
        "  if (layer.pattern === 'vertical') mask *= periodicVertical;",
        "  if (layer.cavityBias > 0) mask = THREE.MathUtils.lerp(mask, Math.max(mask, cavity), layer.cavityBias);",
        "  if (layer.edgeBias > 0) mask = THREE.MathUtils.lerp(mask, Math.max(mask, edge), layer.edgeBias);",
        "  if (layer.verticalBias !== 0) {",
        "    const vertical = layer.verticalBias > 0 ? periodicVertical : 1 - periodicVertical;",
        "    mask = THREE.MathUtils.lerp(mask, mask * vertical, Math.abs(layer.verticalBias));",
        "  }",
        "  if (layer.regional) {",
        "    const rawU = Math.abs(u - layer.uvCenter[0]);",
        "    const rawV = Math.abs(v - layer.uvCenter[1]);",
        "    const du = Math.min(rawU, 1 - rawU) / layer.uvScale[0];",
        "    const dv = Math.min(rawV, 1 - rawV) / layer.uvScale[1];",
        "    const distance = Math.sqrt(du * du + dv * dv);",
        "    const regionMask = smoothCurve(clamp01((1 - distance) / layer.feather));",
        "    mask *= regionMask;",
        "  }",
        "  return clamp01(mask);",
        "}",
        "",
        "function applyProfileSurface(profile: SculptMaterialProfile, u: number, v: number, base: number): number {",
        "  if (profile === 'cloth') {",
        "    const warp = Math.sin(u * Math.PI * 128);",
        "    const weft = Math.sin(v * Math.PI * 128 + Math.PI * 0.5);",
        "    const weave = clamp01(0.5 + warp * weft * 0.5);",
        "    return clamp01(base * 0.68 + weave * 0.32);",
        "  }",
        "  if (profile === 'fiber') {",
        "    const grain = clamp01(0.5 + Math.sin(v * Math.PI * 192 + u * Math.PI * 7) * 0.5);",
        "    return clamp01(base * 0.74 + grain * 0.26);",
        "  }",
        "  return base;",
        "}",
        "",
        "function mixPalette(colors: [number, number, number][], value: number): [number, number, number] {",
        "  if (colors.length === 1) return colors[0];",
        "  const scaled = clamp01(value) * (colors.length - 1);",
        "  const index = Math.min(colors.length - 2, Math.floor(scaled));",
        "  const mix = scaled - index;",
        "  const a = colors[index];",
        "  const b = colors[index + 1];",
        "  return [",
        "    Math.round(THREE.MathUtils.lerp(a[0], b[0], mix)),",
        "    Math.round(THREE.MathUtils.lerp(a[1], b[1], mix)),",
        "    Math.round(THREE.MathUtils.lerp(a[2], b[2], mix)),",
        "  ];",
        "}",
        "",
        "function writePixel(data: Uint8ClampedArray, offset: number, red: number, green: number, blue: number, alpha = 255): void {",
        "  data[offset] = Math.max(0, Math.min(255, Math.round(red)));",
        "  data[offset + 1] = Math.max(0, Math.min(255, Math.round(green)));",
        "  data[offset + 2] = Math.max(0, Math.min(255, Math.round(blue)));",
        "  data[offset + 3] = Math.max(0, Math.min(255, Math.round(alpha)));",
        "}",
        "",
        "function makeCanvas(size: number): HTMLCanvasElement {",
        "  const canvas = document.createElement('canvas');",
        "  canvas.width = size;",
        "  canvas.height = size;",
        "  return canvas;",
        "}",
        "",
        "function createMapTexture(",
        "  canvas: HTMLCanvasElement,",
        "  colorSpace: THREE.ColorSpace,",
        "  spec: SculptMaterialSpec,",
        "  options: ProceduralModelOptions,",
        "): THREE.CanvasTexture {",
        "  const texture = new THREE.CanvasTexture(canvas);",
        "  const projection = spec.textureProjection && typeof spec.textureProjection === 'object' ? spec.textureProjection : {};",
        "  const repeat = Array.isArray(projection.repeat) ? projection.repeat : [2, 2];",
        "  texture.colorSpace = colorSpace;",
        "  texture.wrapS = THREE.RepeatWrapping;",
        "  texture.wrapT = THREE.RepeatWrapping;",
        "  texture.repeat.set(",
        "    typeof repeat[0] === 'number' ? repeat[0] : 2,",
        "    typeof repeat[1] === 'number' ? repeat[1] : 2,",
        "  );",
        "  const requestedAnisotropy = options.textureAnisotropy ?? (typeof projection.anisotropy === 'number' ? projection.anisotropy : 8);",
        "  texture.anisotropy = Math.max(1, Math.round(requestedAnisotropy));",
        "  texture.needsUpdate = true;",
        "  return texture;",
        "}",
        "",
        "type ProceduralTextureSet = {",
        "  albedo: THREE.Texture;",
        "  roughness: THREE.Texture;",
        "  metalness?: THREE.Texture;",
        "  height: THREE.Texture;",
        "  normal: THREE.Texture;",
        "  ao: THREE.Texture;",
        "  source: 'reference-pixel-extraction' | 'procedural';",
        "};",
        "",
        "function referenceMapUrl(spec: SculptMaterialSpec, channel: string): string | null {",
        "  const reference = spec.referencePbr;",
        "  if (!reference || typeof reference !== 'object') return null;",
        "  if (reference.usable !== true || reference.materialCropConfirmed !== true) return null;",
        "  const confidence = typeof reference.extractionSuitability === 'number'",
        "    ? reference.extractionSuitability",
        "    : (typeof reference.confidence === 'number'",
        "      ? reference.confidence",
        "      : (typeof reference.estimatedFidelity === 'number' ? reference.estimatedFidelity : 0));",
        "  const threshold = typeof reference.targetThreshold === 'number' ? reference.targetThreshold : 0.7;",
        "  if (confidence < threshold) return null;",
        "  const maps = reference.maps;",
        "  if (!maps || typeof maps !== 'object') return null;",
        "  const map = (maps as Record<string, unknown>)[channel];",
        "  if (!map || typeof map !== 'object') return null;",
        "  const record = map as Record<string, unknown>;",
        "  return typeof record.url === 'string' && record.url.trim() ? record.url : null;",
        "}",
        "",
        "function createLoadedMapTexture(",
        "  url: string,",
        "  colorSpace: THREE.ColorSpace,",
        "  spec: SculptMaterialSpec,",
        "  options: ProceduralModelOptions,",
        "): THREE.Texture {",
        "  const texture = new THREE.TextureLoader().load(url);",
        "  const projection = spec.textureProjection && typeof spec.textureProjection === 'object' ? spec.textureProjection : {};",
        "  const repeat = Array.isArray(projection.repeat) ? projection.repeat : [1, 1];",
        "  texture.colorSpace = colorSpace;",
        "  texture.wrapS = THREE.RepeatWrapping;",
        "  texture.wrapT = THREE.RepeatWrapping;",
        "  texture.repeat.set(",
        "    typeof repeat[0] === 'number' ? repeat[0] : 1,",
        "    typeof repeat[1] === 'number' ? repeat[1] : 1,",
        "  );",
        "  const requestedAnisotropy = options.textureAnisotropy ?? (typeof projection.anisotropy === 'number' ? projection.anisotropy : 8);",
        "  texture.anisotropy = Math.max(1, Math.round(requestedAnisotropy));",
        "  texture.needsUpdate = true;",
        "  return texture;",
        "}",
        "",
        "function makeReferenceTextureSet(spec: SculptMaterialSpec, options: ProceduralModelOptions): ProceduralTextureSet | null {",
        "  const albedo = referenceMapUrl(spec, 'albedo');",
        "  const roughness = referenceMapUrl(spec, 'roughness');",
        "  const height = referenceMapUrl(spec, 'height');",
        "  const normal = referenceMapUrl(spec, 'normal');",
        "  const ao = referenceMapUrl(spec, 'ao');",
        "  const metalness = referenceMapUrl(spec, 'metalness');",
        "  if (!albedo || !roughness || !height || !normal || !ao) return null;",
        "  return {",
        "    albedo: createLoadedMapTexture(albedo, THREE.SRGBColorSpace, spec, options),",
        "    roughness: createLoadedMapTexture(roughness, THREE.NoColorSpace, spec, options),",
        "    metalness: metalness ? createLoadedMapTexture(metalness, THREE.NoColorSpace, spec, options) : undefined,",
        "    height: createLoadedMapTexture(height, THREE.NoColorSpace, spec, options),",
        "    normal: createLoadedMapTexture(normal, THREE.NoColorSpace, spec, options),",
        "    ao: createLoadedMapTexture(ao, THREE.NoColorSpace, spec, options),",
        "    source: 'reference-pixel-extraction',",
        "  };",
        "}",
        "",
        "function makeProceduralTextureSet(",
        "  id: string,",
        "  spec: SculptMaterialSpec,",
        "  options: ProceduralModelOptions,",
        "  profile: SculptMaterialProfile,",
        "): ProceduralTextureSet | null {",
        "  if (typeof document === 'undefined') return null;",
        f"  const qualityFirst = {str(spec.get('qualityProfile') == 'reference-fidelity').lower()} || options.qualityPriority === 'reference-fidelity';",
        "  const requested = options.textureSize ?? spec.textureResolution;",
        "  const requestedSize = typeof requested === 'number' && Number.isFinite(requested)",
        "    ? requested",
        "    : (qualityFirst ? 1024 : 512);",
        "  // Large reference maps are authored offline; runtime procedural fallback stays bounded.",
        "  const minimumRuntimeSize = qualityFirst ? 1024 : 256;",
        "  const size = Math.max(minimumRuntimeSize, Math.min(1024, 2 ** Math.round(Math.log2(requestedSize))));",
        "  const canvases = {",
        "    albedo: makeCanvas(size),",
        "    roughness: makeCanvas(size),",
        "    metalness: makeCanvas(size),",
        "    height: makeCanvas(size),",
        "    normal: makeCanvas(size),",
        "    ao: makeCanvas(size),",
        "  };",
        "  const contexts = {",
        "    albedo: canvases.albedo.getContext('2d'),",
        "    roughness: canvases.roughness.getContext('2d'),",
        "    metalness: canvases.metalness.getContext('2d'),",
        "    height: canvases.height.getContext('2d'),",
        "    normal: canvases.normal.getContext('2d'),",
        "    ao: canvases.ao.getContext('2d'),",
        "  };",
        "  if (!contexts.albedo || !contexts.roughness || !contexts.metalness || !contexts.height || !contexts.normal || !contexts.ao) return null;",
        "  const images = {",
        "    albedo: contexts.albedo.createImageData(size, size),",
        "    roughness: contexts.roughness.createImageData(size, size),",
        "    metalness: contexts.metalness.createImageData(size, size),",
        "    height: contexts.height.createImageData(size, size),",
        "    normal: contexts.normal.createImageData(size, size),",
        "    ao: contexts.ao.createImageData(size, size),",
        "  };",
        "  const seed = hashString(id);",
        "  const bands = surfaceBands(spec);",
        "  const heightField = new Float32Array(size * size);",
        "  const heightDeltaField = new Float32Array(size * size);",
        "  const roughnessField = new Float32Array(size * size);",
        "  const metalnessField = new Float32Array(size * size);",
        "  const localLayers = materialLocalLayers(spec);",
        "  const palette = materialPalette(spec);",
        "  const fallback = typeof spec.baseColor === 'string' ? spec.baseColor : '#8A7A5F';",
        "  const colors = (palette.length >= 2 ? palette : [fallback, '#6E614B', '#A08F70']).map(hexToRgb);",
        "  const baseRoughness = clamp01(readLayerNumber(spec.roughness, ['base'], 0.76));",
        "  const baseMetalness = clamp01(readLayerNumber(spec.metalness, ['base'], 0));",
        "  const roughnessVariation = clamp01(readLayerNumber(spec.roughness, ['variation'], 0.18));",
        "  const colorAmplitude = clamp01(readLayerNumber(spec.colorVariation, ['amplitude', 'variation'], 0.18));",
        "  const heightCorrelation = clamp01(readLayerNumber(spec.colorVariation, ['heightCorrelation'], 0.3));",
        "  for (let y = 0; y < size; y += 1) {",
        "    const v = y / size;",
        "    for (let x = 0; x < size; x += 1) {",
        "      const u = x / size;",
        "      const index = y * size + x;",
        "      const height = applyProfileSurface(profile, u, v, sampleSurface(u, v, bands, seed + 101));",
        "      const roughNoise = sampleSurface(u, v, bands, seed + 7001);",
        "      const colorNoise = sampleSurface(u, v, bands, seed + 15013);",
        "      heightField[index] = height;",
        "      roughnessField[index] = clamp01(baseRoughness + (roughNoise - 0.5) * roughnessVariation * 2);",
        "      metalnessField[index] = baseMetalness;",
        "      const paletteValue = clamp01(",
        "        0.5 + (colorNoise - 0.5) * colorAmplitude * 2 + (height - 0.5) * heightCorrelation",
        "      );",
        "      const color = mixPalette(colors, paletteValue);",
        "      let surfaceAlpha = 255;",
        "      if (profile === 'volume') {",
        "        surfaceAlpha = clamp01((height * 0.64 + colorNoise * 0.36 - 0.24) / 0.76)",
        "          * clamp01(Math.min(u, 1 - u, v, 1 - v) * 8) * 255;",
        "      } else if (profile === 'fiber') {",
        "        const lateralFade = Math.pow(clamp01(1 - Math.abs(u * 2 - 1)), 0.45);",
        "        const tipFade = clamp01((1 - v) * 14);",
        "        surfaceAlpha = lateralFade * tipFade * 255;",
        "      }",
        "      writePixel(images.albedo.data, index * 4, color[0], color[1], color[2], surfaceAlpha);",
        "    }",
        "  }",
        "  // Apply evidence-backed local layers before deriving normal/AO so all PBR channels agree.",
        "  if (localLayers.length > 0) {",
        "  for (let y = 0; y < size; y += 1) {",
        "    const up = ((y - 1 + size) % size) * size;",
        "    const down = ((y + 1) % size) * size;",
        "    const v = y / size;",
        "    for (let x = 0; x < size; x += 1) {",
        "      const left = (x - 1 + size) % size;",
        "      const right = (x + 1) % size;",
        "      const u = x / size;",
        "      const index = y * size + x;",
        "      const center = heightField[index];",
        "      const neighbors = [",
        "        heightField[y * size + left], heightField[y * size + right],",
        "        heightField[up + x], heightField[down + x],",
        "      ];",
        "      const neighborAverage = neighbors.reduce((sum, value) => sum + value, 0) * 0.25;",
        "      const cavity = clamp01(Math.max(0, neighborAverage - center) * 18 + (1 - center) * 0.08);",
        "      const edge = clamp01(Math.max(...neighbors.map((value) => Math.abs(value - center))) * 14);",
        "      const offset = index * 4;",
        "      for (let layerIndex = 0; layerIndex < localLayers.length; layerIndex += 1) {",
        "        const layer = localLayers[layerIndex];",
        "        const weight = layer.amount * sampleLocalLayerMask(layer, u, v, cavity, edge, seed + layerIndex * 7919);",
        "        if (weight <= 0) continue;",
        "        images.albedo.data[offset] = THREE.MathUtils.lerp(images.albedo.data[offset], layer.color[0], weight);",
        "        images.albedo.data[offset + 1] = THREE.MathUtils.lerp(images.albedo.data[offset + 1], layer.color[1], weight);",
        "        images.albedo.data[offset + 2] = THREE.MathUtils.lerp(images.albedo.data[offset + 2], layer.color[2], weight);",
        "        roughnessField[index] = clamp01(roughnessField[index] + layer.roughnessDelta * weight);",
        "        metalnessField[index] = clamp01(metalnessField[index] + layer.metalnessDelta * weight);",
        "        heightDeltaField[index] += layer.heightDelta * weight;",
        "      }",
        "    }",
        "  }",
        "  for (let index = 0; index < heightField.length; index += 1) {",
        "    heightField[index] = clamp01(heightField[index] + heightDeltaField[index]);",
        "  }",
        "  }",
        "  const normalStrength = Math.max(0.05, readLayerNumber(spec.normal, ['strength', 'amplitude'], 0.35));",
        "  const aoStrength = clamp01(readLayerNumber(spec.ambientOcclusion, ['cavityStrength', 'strength'], 0.35));",
        "  for (let y = 0; y < size; y += 1) {",
        "    const up = ((y - 1 + size) % size) * size;",
        "    const down = ((y + 1) % size) * size;",
        "    for (let x = 0; x < size; x += 1) {",
        "      const left = (x - 1 + size) % size;",
        "      const right = (x + 1) % size;",
        "      const index = y * size + x;",
        "      const center = heightField[index];",
        "      const dx = (heightField[y * size + right] - heightField[y * size + left]) * normalStrength * 6;",
        "      const dy = (heightField[down + x] - heightField[up + x]) * normalStrength * 6;",
        "      const inverseLength = 1 / Math.sqrt(dx * dx + dy * dy + 1);",
        "      const normalX = -dx * inverseLength;",
        "      const normalY = -dy * inverseLength;",
        "      const normalZ = inverseLength;",
        "      const neighborAverage = (",
        "        heightField[y * size + left] + heightField[y * size + right]",
        "        + heightField[up + x] + heightField[down + x]",
        "      ) * 0.25;",
        "      const cavity = Math.max(0, neighborAverage - center);",
        "      const ao = clamp01(1 - aoStrength * (cavity * 12 + (1 - center) * 0.16));",
        "      const offset = index * 4;",
        "      const heightByte = center * 255;",
        "      const roughnessByte = roughnessField[index] * 255;",
        "      const metalnessByte = metalnessField[index] * 255;",
        "      writePixel(images.height.data, offset, heightByte, heightByte, heightByte);",
        "      writePixel(images.roughness.data, offset, roughnessByte, roughnessByte, roughnessByte);",
        "      writePixel(images.metalness.data, offset, metalnessByte, metalnessByte, metalnessByte);",
        "      writePixel(",
        "        images.normal.data, offset,",
        "        (normalX * 0.5 + 0.5) * 255,",
        "        (normalY * 0.5 + 0.5) * 255,",
        "        (normalZ * 0.5 + 0.5) * 255,",
        "      );",
        "      writePixel(images.ao.data, offset, ao * 255, ao * 255, ao * 255);",
        "    }",
        "  }",
        "  contexts.albedo.putImageData(images.albedo, 0, 0);",
        "  contexts.roughness.putImageData(images.roughness, 0, 0);",
        "  contexts.metalness.putImageData(images.metalness, 0, 0);",
        "  contexts.height.putImageData(images.height, 0, 0);",
        "  contexts.normal.putImageData(images.normal, 0, 0);",
        "  contexts.ao.putImageData(images.ao, 0, 0);",
        "  return {",
        "    albedo: createMapTexture(canvases.albedo, THREE.SRGBColorSpace, spec, options),",
        "    roughness: createMapTexture(canvases.roughness, THREE.NoColorSpace, spec, options),",
        "    metalness: createMapTexture(canvases.metalness, THREE.NoColorSpace, spec, options),",
        "    height: createMapTexture(canvases.height, THREE.NoColorSpace, spec, options),",
        "    normal: createMapTexture(canvases.normal, THREE.NoColorSpace, spec, options),",
        "    ao: createMapTexture(canvases.ao, THREE.NoColorSpace, spec, options),",
        "    source: 'procedural',",
        "  };",
        "}",
        "",
        "function createSculptMaterial(id: string, spec: SculptMaterialSpec, options: ProceduralModelOptions): THREE.MeshPhysicalMaterial {",
        "  const profile = readMaterialProfile(spec.materialProfile);",
        "  const textures = makeReferenceTextureSet(spec, options) ?? makeProceduralTextureSet(id, spec, options, profile);",
        "  const material = new THREE.MeshPhysicalMaterial({",
        "    color: textures ? 0xffffff : new THREE.Color(typeof spec.baseColor === 'string' ? spec.baseColor : '#8A7A5F'),",
        "    roughness: textures ? 1 : clamp01(readLayerNumber(spec.roughness, ['base'], 0.76)),",
        "    metalness: textures?.metalness ? 1 : clamp01(readLayerNumber(spec.metalness, ['base'], 0.0)),",
        "    clearcoat: clamp01(readLayerNumber(spec.clearcoat, ['base', 'amount'], 0)),",
        "    clearcoatRoughness: clamp01(readLayerNumber(spec.clearcoatRoughness, ['base'], 0.25)),",
        "    transmission: clamp01(readLayerNumber(spec.transmission, ['base', 'amount'], 0)),",
        "    opacity: clamp01(readLayerNumber(spec.opacity, ['base', 'amount'], 1)),",
        "    transparent: readLayerNumber(spec.transmission, ['base', 'amount'], 0) > 0 || readLayerNumber(spec.opacity, ['base', 'amount'], 1) < 1,",
        "    alphaTest: Math.max(0, readLayerNumber(spec.alpha, ['cutoff', 'alphaTest'], 0)),",
        "    wireframe: options.wireframe ?? false,",
        "    side: spec.doubleSided === true ? THREE.DoubleSide : THREE.FrontSide,",
        "  });",
        "  if (profile === 'cloth') {",
        "    material.sheen = clamp01(readLayerNumber(spec.sheen, ['base', 'amount'], 0.55));",
        "    material.sheenColor.set(typeof spec.sheenColor === 'string' ? spec.sheenColor : '#ffffff');",
        "    material.sheenRoughness = clamp01(readLayerNumber(spec.sheenRoughness, ['base', 'amount'], 0.86));",
        "    material.side = spec.doubleSided === false ? THREE.FrontSide : THREE.DoubleSide;",
        "  }",
        "  if (profile === 'fiber') {",
        "    material.anisotropy = clamp01(readLayerNumber(spec.anisotropy, ['base', 'amount'], 0.72));",
        "    material.anisotropyRotation = readLayerNumber(spec.anisotropyRotation, ['base', 'angle'], 0);",
        "    material.side = spec.doubleSided === false ? THREE.FrontSide : THREE.DoubleSide;",
        "  }",
        "  if (profile === 'glass' || profile === 'liquid') {",
        "    const defaultTransmission = profile === 'glass' ? 0.98 : 0.9;",
        "    const defaultIor = profile === 'glass' ? 1.5 : 1.333;",
        "    const defaultThickness = profile === 'glass' ? 0.1 : 0.5;",
        "    material.transmission = clamp01(readLayerNumber(spec.transmission, ['base', 'amount'], defaultTransmission));",
        "    material.ior = Math.max(1, Math.min(2.333, readLayerNumber(spec.ior, ['base'], defaultIor)));",
        "    material.thickness = Math.max(0, readLayerNumber(spec.thickness, ['base', 'amount'], defaultThickness));",
        "    material.attenuationColor.set(typeof spec.attenuationColor === 'string' ? spec.attenuationColor : '#ffffff');",
        "    material.attenuationDistance = Math.max(0.0001, readLayerNumber(spec.attenuationDistance, ['base'], Number.POSITIVE_INFINITY));",
        "    material.dispersion = Math.max(0, readLayerNumber(spec.dispersion, ['base', 'amount'], 0));",
        "    material.transparent = material.transmission > 0 || material.opacity < 1;",
        "  }",
        "  if (profile === 'volume') {",
        "    material.opacity = clamp01(readLayerNumber(spec.opacity, ['base', 'amount'], 0.72));",
        "    material.alphaHash = typeof spec.alphaHash === 'boolean' ? spec.alphaHash : true;",
        "    material.depthWrite = typeof spec.depthWrite === 'boolean' ? spec.depthWrite : false;",
        "    material.forceSinglePass = typeof spec.forceSinglePass === 'boolean' ? spec.forceSinglePass : true;",
        "    material.side = THREE.DoubleSide;",
        "    material.transparent = material.opacity < 1 && !material.alphaHash;",
        "  } else if (profile === 'fiber') {",
        "    material.alphaHash = typeof spec.alphaHash === 'boolean' ? spec.alphaHash : true;",
        "    material.depthWrite = typeof spec.depthWrite === 'boolean' ? spec.depthWrite : material.depthWrite;",
        "    material.forceSinglePass = typeof spec.forceSinglePass === 'boolean' ? spec.forceSinglePass : true;",
        "    material.transparent = material.opacity < 1 && !material.alphaHash;",
        "  } else if (profile !== 'standard') {",
        "    if (typeof spec.alphaHash === 'boolean') material.alphaHash = spec.alphaHash;",
        "    if (typeof spec.depthWrite === 'boolean') material.depthWrite = spec.depthWrite;",
        "    if (typeof spec.forceSinglePass === 'boolean') material.forceSinglePass = spec.forceSinglePass;",
        "  }",
        "  if (profile !== 'standard') {",
        "    if (typeof spec.emissive === 'string') material.emissive.set(spec.emissive);",
        "    material.emissiveIntensity = Math.max(0, readLayerNumber(spec.emissiveIntensity, ['base', 'amount'], material.emissiveIntensity));",
        "  }",
        "  const defaultSpecularIntensity = profile === 'glass' || profile === 'liquid' ? 1 : 0.5;",
        "  material.specularIntensity = clamp01(readLayerNumber(spec.specularIntensity, ['base', 'amount'], defaultSpecularIntensity));",
        "  material.specularColor.set(typeof spec.specularColor === 'string' ? spec.specularColor : '#ffffff');",
        "  if (textures) {",
        "    material.map = textures.albedo;",
        "    material.roughnessMap = textures.roughness;",
        "    if (textures.metalness) material.metalnessMap = textures.metalness;",
        "    material.normalMap = textures.normal;",
        "    material.normalScale.setScalar(Math.max(0.05, readLayerNumber(spec.normal, ['strength', 'amplitude'], 0.35)));",
        "    material.aoMap = textures.ao;",
        "    material.aoMap.channel = 0;",
        "    material.aoMapIntensity = readLayerNumber(spec.ambientOcclusion, ['cavityStrength', 'strength'], 0.35);",
        "    const bumpScale = Math.max(0, readLayerNumber(spec.bump, ['amplitude', 'strength'], 0));",
        "    if (bumpScale > 0) {",
        "      material.bumpMap = textures.height;",
        "      material.bumpScale = bumpScale;",
        "    }",
        "    const displacementScale = Math.max(0, readLayerNumber(spec.displacement, ['amplitude', 'strength'], 0));",
        "    if (displacementScale > 0) {",
        "      material.displacementMap = textures.height;",
        "      material.displacementScale = displacementScale;",
        "      material.displacementBias = -displacementScale * 0.5;",
        "    }",
        "  }",
        "  material.envMapIntensity = Math.max(0, readLayerNumber(spec.envMapIntensity, ['base', 'amount'], 0.8));",
        "  material.userData.sculptMaterial = spec;",
        "  material.userData.proceduralMapsIndependent = true;",
        "  material.userData.pbrTextureSource = textures?.source ?? 'flat-fallback';",
        "  material.userData.localMaterialLayerCount = materialLocalLayers(spec).length;",
        "  material.userData.referencePbr = spec.referencePbr ?? null;",
        "  material.userData.heightMap = textures?.height ?? null;",
        "  material.userData.materialProfile = profile;",
        "  material.needsUpdate = true;",
        "  return material;",
        "}",
        "",
        "function componentSurfaceMaterial(",
        "  base: THREE.Material,",
        "  detail: unknown,",
        "  cache: Record<string, THREE.Material>,",
        "  cacheKey: string,",
        "): THREE.Material {",
        "  if (!(base instanceof THREE.MeshStandardMaterial) || !detail || typeof detail !== 'object') return base;",
        "  const record = detail as Record<string, unknown>;",
        "  const macro = Math.max(0, readLayerNumber(record.macroRoughness, ['base', 'amount', 'value'], 0));",
        "  const micro = Math.max(0, readLayerNumber(record.microRoughness, ['base', 'amount', 'value'], 0));",
        "  const bump = Math.max(0, readLayerNumber(record.bumpAmplitude, ['base', 'amount', 'value'], 0));",
        "  if (macro <= 0 && micro <= 0 && bump <= 0) return base;",
        "  if (cache[cacheKey]) return cache[cacheKey];",
        "  const material = base.clone();",
        "  material.roughness = clamp01(material.roughness + macro * 0.15);",
        "  if (material.normalMap) material.normalScale.multiplyScalar(1 + micro * 0.5);",
        "  const heightMap = base.userData.heightMap;",
        "  if (bump > 0 && heightMap instanceof THREE.Texture) {",
        "    material.bumpMap = heightMap;",
        "    material.bumpScale = Math.max(material.bumpScale, bump);",
        "  }",
        "  material.userData = { ...base.userData, componentSurfaceDetail: detail, materialVariant: cacheKey };",
        "  material.needsUpdate = true;",
        "  cache[cacheKey] = material;",
        "  return material;",
        "}",
        "",
        "type AttachmentEndpoint = {",
        "  start: THREE.Vector3;",
        "  midpoint: THREE.Vector3;",
        "  quaternion: THREE.Quaternion;",
        "  length: number;",
        "  baseRadius: number;",
        "  endRadius: number;",
        "};",
        "",
        "function readVector3(value: unknown, fallback: [number, number, number]): THREE.Vector3 {",
        "  if (Array.isArray(value) && value.length === 3 && value.every((item) => typeof item === 'number')) {",
        "    return new THREE.Vector3(value[0], value[1], value[2]);",
        "  }",
        "  return new THREE.Vector3(fallback[0], fallback[1], fallback[2]);",
        "}",
        "",
        "function readNumber(value: unknown, fallback: number): number {",
        "  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;",
        "}",
        "",
        "function makeAttachmentEndpoint(attachment: unknown): AttachmentEndpoint | null {",
        "  if (!attachment || typeof attachment !== 'object') return null;",
        "  const record = attachment as Record<string, unknown>;",
        "  const start = readVector3(record.localStart, [0, 0, 0]);",
        "  const end = readVector3(record.localEnd, [0, 1, 0]);",
        "  const delta = end.clone().sub(start);",
        "  const length = delta.length();",
        "  if (length <= 0.0001) return null;",
        "  const direction = delta.clone().normalize();",
        "  const quaternion = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction);",
        "  const baseRadius = Math.max(0.005, readNumber(record.baseRadius, 0.06));",
        "  const endRadius = Math.max(0.003, readNumber(record.endRadius, baseRadius * 0.55));",
        "  return {",
        "    start,",
        "    midpoint: delta.multiplyScalar(0.5),",
        "    quaternion,",
        "    length,",
        "    baseRadius,",
        "    endRadius,",
        "  };",
        "}",
        "",
        *typescript_geometry_helpers(geometry_helpers),
        "// @generated by threejs-object-sculptor; edit a wrapper file, not this file.",
        "// Generator contract: ObjectSculptSpec 3.1 + hash-bound visual evidence v1.",
        f"// Generated from ObjectSculptSpec target: {target}",
        f"// Sculpt build pass: {pass_id}",
        "// This factory is intentionally pass-gated. Finish browser screenshot review before unlocking deeper passes.",
        f"export function {function_name}(options: ProceduralModelOptions = {{}}): THREE.Group {{",
        "  const root = new THREE.Group();",
        f"  root.name = {json.dumps(target)};",
        "  root.userData.generatorContract = 'object-sculpt-3.1/evidence-v1';",
        "",
            "  const materialMap: Record<string, THREE.Material> = {};",
    ]
    for material_id, material in materials.items():
        if detailed_materials:
            lines.extend(
                [
                    f"  materialMap[{json.dumps(material_id)}] = createSculptMaterial(",
                    f"    {json.dumps(material_id)},",
                    f"    {json_literal(material)},",
                    "    options",
                    "  );",
                ]
            )
        else:
            lines.append(
                f"  materialMap[{json.dumps(material_id)}] = new THREE.MeshStandardMaterial({{ "
                f"color: {hex_to_number(material.get('baseColor') or material.get('color'))}, "
                "roughness: 0.82, metalness: 0, wireframe: options.wireframe ?? false });"
            )
    lines.extend(
        [
            "",
            "  const nodes: Record<string, THREE.Object3D> = { '$root': root };",
            "  const meshes: Record<string, THREE.Mesh> = {};",
            "  const instances: Record<string, THREE.InstancedMesh> = {};",
            "  const sockets: Record<string, THREE.Object3D> = {};",
            "  const colliders: Record<string, unknown> = {};",
            "  const destructionGroups: Record<string, THREE.Object3D[]> = {};",
            "  const componentMaterialVariants: Record<string, THREE.Material> = {};",
        ]
    )

    for index, component in enumerate(components):
        component_id = str(component.get("id") or f"component-{index}")
        component_var = local_var("mesh", component_id, index)
        component_material_var = local_var("material", component_id, index)
        node_var = local_var("node", component_id, index)
        kind = component_type(component)
        emission = component_emissions.get(index)
        primitive = emission.primitive if emission is not None else "assembly"
        transform = component.get("transform", {}) if isinstance(component.get("transform"), dict) else {}
        action_profile = component.get("actionProfile") if isinstance(component.get("actionProfile"), dict) else {}
        sockets_spec = action_profile.get("sockets", []) if isinstance(action_profile.get("sockets"), list) else []
        collider_spec = action_profile.get("collider") if isinstance(action_profile.get("collider"), dict) else None
        destruction = action_profile.get("destruction") if isinstance(action_profile.get("destruction"), dict) else {}
        fracture_group = destruction.get("fractureGroup") if isinstance(destruction, dict) else None
        attachment = component.get("attachment") if isinstance(component.get("attachment"), dict) else None
        endpoint_attachment = attachment if emission is not None and emission.endpoint_aware else None
        attachment_var = local_var("attachment", component_id, index)
        endpoint_var = local_var("endpoint", component_id, index)
        material_id = str(component.get("material") or next(iter(materials.keys()), "base"))
        parent = "$root" if component.get("parent") is None else str(component.get("parent"))
        name = str(component.get("name") or component_id)
        lines.extend(
            [
                "",
                f"  const {node_var} = new THREE.Group();",
                f"  {node_var}.name = {json.dumps(name + '__pivot')};",
            ]
        )
        if endpoint_attachment is not None:
            lines.extend(
                [
                    f"  const {attachment_var} = {json_literal(endpoint_attachment)};",
                    f"  const {endpoint_var} = makeAttachmentEndpoint({attachment_var});",
                    f"  if ({endpoint_var}) {{",
                    f"    {node_var}.position.copy({endpoint_var}.start);",
                    f"    {node_var}.rotation.set(0, 0, 0);",
                    f"    {node_var}.scale.set({transform_scale_vector(transform)});",
                    "  } else {",
                    f"    {node_var}.position.set({vector(transform.get('position'), [0, 0, 0])});",
                    f"    {node_var}.rotation.set({vector(transform.get('rotation'), [0, 0, 0])});",
                    f"    {node_var}.scale.set({transform_scale_vector(transform) if emission is not None and emission.dimension_mode == 'local' else scale_vector(component, transform)});",
                    "  }",
                ]
            )
        else:
            node_scale = (
                transform_scale_vector(transform)
                if kind == "assembly" or (emission is not None and emission.dimension_mode == "local")
                else scale_vector(component, transform)
            )
            lines.extend(
                [
                    f"  {node_var}.position.set({vector(transform.get('position'), [0, 0, 0])});",
                    f"  {node_var}.rotation.set({vector(transform.get('rotation'), [0, 0, 0])});",
                    f"  {node_var}.scale.set({node_scale});",
                ]
            )
        lines.extend(
            [
                f"  {node_var}.userData.sculptComponent = {json_literal(component)};",
                f"  {node_var}.userData.actionProfile = {json_literal(action_profile)};",
                f"  (nodes[{json.dumps(str(parent))}] ?? root).add({node_var});",
                f"  nodes[{json.dumps(component_id)}] = {node_var};",
            ]
        )
        if emission is not None:
            endpoint_geometry = (
                endpoint_attachment is not None and emission.dimension_mode == "component"
            )
            geometry_expression = emission.geometry_expression
            if endpoint_geometry:
                geometry_expression = (
                    f"{endpoint_var} ? new THREE.CylinderGeometry("
                    f"{endpoint_var}.endRadius, {endpoint_var}.baseRadius, "
                    f"{endpoint_var}.length, 32, 12) : {geometry_expression}"
                )
            lines.append(f"  const {component_var}Geometry = {geometry_expression};")
            material_expression = (
                f"materialMap[{json.dumps(material_id)}] ?? "
                "new THREE.MeshStandardMaterial({ color: 0x888888 })"
            )
            surface_detail = component.get("surfaceDetail") if isinstance(component.get("surfaceDetail"), dict) else {}
            surface_signature = hashlib.sha256(
                json.dumps(
                    surface_detail,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()[:12]
            lines.append(
                f"  const {component_material_var} = componentSurfaceMaterial("
                f"{material_expression}, {json_literal(surface_detail)}, componentMaterialVariants, "
                f"{json.dumps(material_id + '::' + surface_signature)});"
            )
            if emission.mesh_kind == "instanced":
                lines.extend(
                    [
                        f"  const {component_var} = new THREE.InstancedMesh(",
                        f"    {component_var}Geometry,",
                        f"    {component_material_var},",
                        f"    {emission.instance_count}",
                        "  );",
                        f"  {emission.instance_layout_function}({component_var}, "
                        f"{json_literal(dict(emission.instance_layout or {}))} "
                        f"as {emission.instance_layout_type});",
                        f"  instances[{json.dumps(component_id)}] = {component_var};",
                    ]
                )
            else:
                lines.extend(
                    [
                        f"  const {component_var} = new THREE.Mesh(",
                        f"    {component_var}Geometry,",
                        f"    {component_material_var}",
                        "  );",
                    ]
                )
            lines.append(f"  {component_var}.name = {json.dumps(name)};")
            if endpoint_geometry:
                lines.extend(
                    [
                        f"  if ({endpoint_var}) {{",
                        f"    {component_var}.position.copy({endpoint_var}.midpoint);",
                        f"    {component_var}.quaternion.copy({endpoint_var}.quaternion);",
                        "  }",
                    ]
                )
            lines.extend(
                [
                    f"  {component_var}.castShadow = "
                    + ("true;" if quality_first else "options.castShadow ?? true;"),
                    f"  {component_var}.receiveShadow = "
                    + ("true;" if quality_first else "options.receiveShadow ?? true;"),
                    f"  {component_var}.userData.sculptComponentId = {json.dumps(component_id)};",
                    f"  {component_var}.userData.sculptPrimitive = {json.dumps(primitive)};",
                    f"  {component_var}.userData.blockoutProxy = {str(emission.is_blockout_proxy).lower()};",
                    f"  {node_var}.add({component_var});",
                    f"  meshes[{json.dumps(component_id)}] = {component_var};",
                ]
            )
        if emit_local_details and emission is not None:
            local_features = component.get("localFeatures")
            if isinstance(local_features, list):
                for feature_index, feature in enumerate(local_features):
                    if not isinstance(feature, dict):
                        continue
                    feature_geometry = local_feature_geometry(feature)
                    if feature_geometry is None:
                        continue
                    feature_id = str(feature.get("id") or f"feature-{feature_index}")
                    feature_key = f"{component_id}::{feature_id}"
                    feature_var = local_var("feature", feature_key, feature_index)
                    feature_material_id = str(feature.get("material") or material_id)
                    feature_position = feature.get("position", [0, 0, 0])
                    feature_rotation = feature.get("rotation", [0, 0, 0])
                    feature_scale = feature.get("scale", [1, 1, 1])
                    lines.extend(
                        [
                            f"  const {feature_var} = new THREE.Mesh(",
                            f"    {feature_geometry},",
                            f"    materialMap[{json.dumps(feature_material_id)}] ?? {material_expression}",
                            "  );",
                            f"  {feature_var}.name = {json.dumps(str(feature.get('name') or feature_id))};",
                            f"  {feature_var}.position.set({vector(feature_position, [0, 0, 0])});",
                            f"  {feature_var}.rotation.set({vector(feature_rotation, [0, 0, 0])});",
                            f"  {feature_var}.scale.set({vector(feature_scale, [1, 1, 1])});",
                            f"  {feature_var}.castShadow = "
                            + ("true;" if quality_first else "options.castShadow ?? true;"),
                            f"  {feature_var}.receiveShadow = "
                            + ("true;" if quality_first else "options.receiveShadow ?? true;"),
                            f"  {feature_var}.userData.sculptLocalFeature = {json_literal(feature)};",
                            f"  {node_var}.add({feature_var});",
                            f"  meshes[{json.dumps(feature_key)}] = {feature_var};",
                        ]
                    )
        if collider_spec is not None:
            lines.append(
                f"  colliders[{json.dumps(component_id)}] = {json_literal(collider_spec)};"
            )
        if isinstance(fracture_group, str) and fracture_group:
            lines.extend(
                [
                    f"  destructionGroups[{json.dumps(fracture_group)}] ??= [];",
                    f"  destructionGroups[{json.dumps(fracture_group)}].push({node_var});",
                ]
            )
        for socket_index, socket in enumerate(sockets_spec):
            if not isinstance(socket, dict):
                continue
            socket_id = str(socket.get("id") or f"socket-{socket_index}")
            socket_var = local_var("socket", f"{component_id}_{socket_id}", socket_index)
            local_position = socket.get("localPosition", socket.get("position"))
            local_rotation = socket.get("localRotation", socket.get("rotation"))
            socket_key = f"{component_id}:{socket_id}"
            lines.extend(
                [
                    f"  const {socket_var} = new THREE.Object3D();",
                    f"  {socket_var}.name = {json.dumps(socket_id)};",
                    f"  {socket_var}.position.set({vector(local_position, [0, 0, 0])});",
                    f"  {socket_var}.rotation.set({vector(local_rotation, [0, 0, 0])});",
                    f"  {socket_var}.userData.socket = {json_literal(socket)};",
                    f"  {node_var}.add({socket_var});",
                    f"  sockets[{json.dumps(socket_key)}] = {socket_var};",
                ]
            )
    look_dev_targets = spec.get("lookDevTargets", {})
    lighting_from_photo = spec.get("lightingFromPhoto", [])
    specialized_regions = specialized_regions_payload(spec)
    shadow_map_size = 4096 if spec.get("qualityProfile") == "reference-fidelity" else 2048
    lines.extend(
        [
            "",
            "  const dispose = (): void => {",
            "    sculptFactoryRoots.delete(root);",
            "    sculptFactoryInitialGeometry.delete(root);",
            "    sculptFactoryGeometryObjects.delete(root);",
            "    const disposedGeometries = new Set<THREE.BufferGeometry>();",
            "    const disposedMaterials = new Set<THREE.Material>();",
            "    const disposedTextures = new Set<THREE.Texture>();",
            "    const disposeMaterial = (material: THREE.Material): void => {",
            "      if (disposedMaterials.has(material)) return;",
            "      for (const value of Object.values(material as unknown as Record<string, unknown>)) {",
            "        if (value instanceof THREE.Texture && !disposedTextures.has(value)) {",
            "          value.dispose();",
            "          disposedTextures.add(value);",
            "        }",
            "      }",
            "      const heightMap = material.userData.heightMap;",
            "      if (heightMap instanceof THREE.Texture && !disposedTextures.has(heightMap)) {",
            "        heightMap.dispose();",
            "        disposedTextures.add(heightMap);",
            "      }",
            "      material.dispose();",
            "      disposedMaterials.add(material);",
            "    };",
            "    for (const mesh of Object.values(meshes)) {",
            "      if (!disposedGeometries.has(mesh.geometry)) {",
            "        mesh.geometry.dispose();",
            "        disposedGeometries.add(mesh.geometry);",
            "      }",
            "      const meshMaterials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];",
            "      for (const material of meshMaterials) disposeMaterial(material);",
            "    }",
            "    const disposedInstances = new Set<THREE.InstancedMesh>();",
            "    for (const instance of Object.values(instances)) {",
            "      if (disposedInstances.has(instance)) continue;",
            "      instance.dispose();",
            "      disposedInstances.add(instance);",
            "    }",
            "    for (const material of Object.values(materialMap)) disposeMaterial(material);",
            "  };",
            "  root.userData.sculptRuntime = { nodes, meshes, instances, sockets, colliders, destructionGroups, dispose } satisfies ProceduralModelRuntime;",
            f"  root.userData.lookDevTargets = {json_literal(look_dev_targets)};",
            f"  root.userData.specializedRegions = {json_literal(specialized_regions)};",
            "  root.userData.actionReadiness = {",
            "    note: 'Use root.userData.sculptRuntime nodes/instances/sockets for transforms and attachments; call dispose when removing the model.',",
            "  };",
            "  sculptFactoryRoots.add(root);",
            "  sculptFactoryInitialGeometry.set(",
            "    root,",
            "    sculptGeometryFingerprint({ ...meshes, ...instances }),",
            "  );",
            "  sculptFactoryGeometryObjects.set(",
            "    root,",
            "    Object.fromEntries(Object.entries({ ...meshes, ...instances }).map(([id, mesh]) => [id, mesh.geometry])),",
            "  );",
            "  installSculptRuntimeCapture();",
            "  return root;",
            "}",
            "",
            f"export const createSculptModel = {function_name};",
            "",
            f"export function create{type_name}LookDevLights(",
            "  mode: 'neutral' | 'grazing' | 'reference' = 'neutral',",
            "): THREE.Group {",
            "  const lights = new THREE.Group();",
            f"  lights.name = {json.dumps(target + ' look-dev lights')};",
            "  const hemi = new THREE.HemisphereLight(",
            "    mode === 'reference' ? 0xfff0d6 : 0xf2f4ff,",
            "    0x363b42,",
            "    mode === 'grazing' ? 0.28 : mode === 'reference' ? 0.72 : 0.85,",
            "  );",
            "  lights.add(hemi);",
            "  const key = new THREE.DirectionalLight(",
            "    mode === 'reference' ? 0xffcf8a : 0xfff4e8,",
            "    mode === 'grazing' ? 4.2 : mode === 'reference' ? 2.6 : 2.15,",
            "  );",
            "  if (mode === 'grazing') key.position.set(7.5, 1.1, 4.0);",
            "  else if (mode === 'reference') key.position.set(-4.5, 7.5, 5.0);",
            "  else key.position.set(-4.0, 6.0, 5.5);",
            "  key.castShadow = true;",
            f"  key.shadow.mapSize.set({shadow_map_size}, {shadow_map_size});",
            "  key.shadow.bias = -0.00025;",
            "  key.shadow.normalBias = 0.018;",
            "  lights.add(key);",
            "  const fill = new THREE.DirectionalLight(0xa8c4ff, mode === 'grazing' ? 0.12 : 0.42);",
            "  fill.position.set(4.0, 3.0, 3.5);",
            "  lights.add(fill);",
            "  const rim = new THREE.DirectionalLight(0xfff1c4, mode === 'grazing' ? 0.28 : 0.85);",
            "  rim.position.set(0.5, 4.5, -6.0);",
            "  lights.add(rim);",
            "  lights.userData.reviewMode = mode;",
            f"  lights.userData.lightingFromPhoto = {json_literal(lighting_from_photo)};",
            f"  lights.userData.lookDevTargets = {json_literal(look_dev_targets)};",
            "  return lights;",
            "}",
            "",
            f"export function configure{type_name}LookDevRenderer(",
            "  renderer: THREE.WebGLRenderer,",
            "  mode: 'neutral' | 'grazing' | 'reference' = 'neutral',",
            "  pixelRatio: number = typeof window === 'undefined' ? 1 : window.devicePixelRatio,",
            "): THREE.WebGLRenderer {",
            "  renderer.outputColorSpace = THREE.SRGBColorSpace;",
            "  renderer.toneMapping = THREE.ACESFilmicToneMapping;",
            "  renderer.toneMappingExposure = mode === 'grazing' ? 0.9 : mode === 'reference' ? 1.0 : 1.05;",
            "  renderer.shadowMap.enabled = true;",
            "  renderer.shadowMap.type = THREE.PCFSoftShadowMap;",
            "  renderer.setPixelRatio(Math.max(1, Math.min(2, pixelRatio)));",
            "  return renderer;",
            "}",
            "",
            f"export function frame{type_name}ForReview(",
            "  camera: THREE.PerspectiveCamera | THREE.OrthographicCamera,",
            "  model: THREE.Object3D,",
            "  padding = 1.18,",
            "): void {",
            "  const bounds = new THREE.Box3().setFromObject(model);",
            "  if (bounds.isEmpty()) return;",
            "  const center = bounds.getCenter(new THREE.Vector3());",
            "  const size = bounds.getSize(new THREE.Vector3());",
            "  const radius = Math.max(0.001, size.length() * 0.5);",
            "  const direction = camera.position.clone().sub(center);",
            "  if (direction.lengthSq() <= Number.EPSILON) direction.set(0, 0, 1);",
            "  direction.normalize();",
            "  if (camera instanceof THREE.PerspectiveCamera) {",
            "    const halfFov = THREE.MathUtils.degToRad(camera.fov * 0.5);",
            "    const distance = radius * padding / Math.max(0.01, Math.tan(halfFov));",
            "    camera.position.copy(center).addScaledVector(direction, distance);",
            "    camera.near = Math.max(0.001, distance - radius * 2.5);",
            "    camera.far = Math.max(camera.near + 1, distance + radius * 4);",
            "  } else {",
            "    const aspect = Math.max(0.1, (camera.right - camera.left) / Math.max(0.001, camera.top - camera.bottom));",
            "    const halfHeight = radius * padding;",
            "    const halfWidth = halfHeight * aspect;",
            "    camera.left = -halfWidth;",
            "    camera.right = halfWidth;",
            "    camera.top = halfHeight;",
            "    camera.bottom = -halfHeight;",
            "    camera.position.copy(center).addScaledVector(direction, radius * 3);",
            "  }",
            "  camera.lookAt(center);",
            "  camera.updateProjectionMatrix();",
            "}",
            "",
            f"export function create{type_name}ContactShadow(",
            "  model: THREE.Object3D,",
            "  padding = 1.4,",
            "): THREE.Mesh<THREE.PlaneGeometry, THREE.ShadowMaterial> {",
            "  const bounds = new THREE.Box3().setFromObject(model);",
            "  const size = bounds.getSize(new THREE.Vector3());",
            "  const groundSize = Math.max(1, size.x, size.z) * padding;",
            "  const material = new THREE.ShadowMaterial({ color: 0x000000, opacity: 0.28 });",
            "  const ground = new THREE.Mesh(new THREE.PlaneGeometry(groundSize, groundSize), material);",
            "  ground.name = 'sculpt-lookdev-contact-shadow';",
            "  ground.rotation.x = -Math.PI * 0.5;",
            "  ground.position.y = bounds.isEmpty() ? 0 : bounds.min.y - Math.max(0.0005, size.y * 0.001);",
            "  ground.receiveShadow = true;",
            "  ground.userData.reviewOnly = true;",
            "  return ground;",
            "}",
            "",
        ]
    )
    return "\n".join(lines)


def write_generated_spec(
    spec: dict[str, Any],
    output: Path,
    *,
    pass_id: str | None = None,
    wrapper_out: Path | None = None,
    force: bool = False,
    _validation_proof: _GenerationValidationProof | None = None,
) -> dict[str, Any]:
    """Validate and write one generated factory; shared by legacy and fast-path CLIs."""

    selected_pass = pass_id or unlocked_pass(spec)
    topology_plan = spec.get("surfaceTopologyPlan")
    component_items = spec.get("componentTree")
    has_geometry_parts = isinstance(component_items, list) and any(
        isinstance(item, dict) and item.get("componentType", "part") == "part"
        for item in component_items
    )
    if (
        has_geometry_parts
        and isinstance(topology_plan, dict)
        and topology_plan.get("status") != "planned"
    ):
        raise ValueError(
            "surfaceTopologyPlan must be planned before generating executable geometry"
        )
    if _validation_proof is not None:
        if not _validation_proof_matches(_validation_proof, spec, selected_pass):
            raise ValueError(
                "generation validation proof does not match the exact spec and pass"
            )
    else:
        errors, _, _ = _validate_generation_spec(spec, selected_pass)
        if errors:
            raise ValueError("spec validation failed: " + "; ".join(errors))
    try:
        assert_pass_unlocked(
            spec,
            selected_pass,
            _geometry_prevalidated=True,
        )
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    resolved_output = output.expanduser().resolve()
    if resolved_output.exists() and not force:
        raise ValueError(f"{resolved_output} already exists; use --force to overwrite")
    if resolved_output.exists() and force:
        existing = resolved_output.read_text(encoding="utf-8")
        if "@generated by threejs-object-sculptor" not in existing[:300]:
            raise ValueError(
                f"refusing to overwrite user-owned file {resolved_output}; generate to a *.generated.ts file"
            )
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved_output.with_name(f".{resolved_output.name}.{os.getpid()}.tmp")
    generated_source = generate(spec, selected_pass, _geometry_prevalidated=True)
    generated_contract = generated_factory_contract_from_source(generated_source)
    temporary.write_text(generated_source, encoding="utf-8")
    temporary.replace(resolved_output)
    wrapper_status = ""
    if wrapper_out:
        wrapper = wrapper_out.expanduser().resolve()
        if wrapper.exists():
            wrapper_status = "kept"
        else:
            wrapper.parent.mkdir(parents=True, exist_ok=True)
            import_path = os.path.relpath(resolved_output.with_suffix(""), wrapper.parent).replace(os.sep, "/")
            if not import_path.startswith("."):
                import_path = f"./{import_path}"
            wrapper.write_text(
                "// User-owned integration point. This file is never overwritten by the generator.\n"
                f"export * from {json.dumps(import_path)};\n",
                encoding="utf-8",
            )
            wrapper_status = "created"
    return {
        "output": str(resolved_output),
        "passId": selected_pass,
        **generated_contract,
        "wrapper": str(wrapper_out.expanduser().resolve()) if wrapper_out else "",
        "wrapperStatus": wrapper_status,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--pass-id",
        help="Build pass to generate. Defaults to the current unlocked sculptPipeline pass.",
    )
    parser.add_argument(
        "--wrapper-out",
        type=Path,
        help="Optional user-owned .ts wrapper; created once and never overwritten.",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    spec_path = args.spec.expanduser().resolve()
    from sculpt_modules import is_module_manifest, module_status, read_raw_spec

    raw_spec = read_raw_spec(spec_path)
    if is_module_manifest(raw_spec):
        modular_status = module_status(spec_path, raw_spec)
        if not modular_status["assemblyReady"]:
            current = modular_status.get("currentModule") or "none"
            parser.error(
                "modular assembly is locked until every required module passes its hash-bound gate; "
                f"current module: {current}"
            )
    spec = load_spec_file(spec_path)
    try:
        result = write_generated_spec(
            spec,
            args.out,
            pass_id=args.pass_id,
            wrapper_out=args.wrapper_out,
            force=args.force,
        )
    except ValueError as exc:
        parser.error(str(exc))
    if result["wrapperStatus"] == "kept":
        print(f"wrapper kept unchanged: {result['wrapper']}", file=sys.stderr)
    if not Path(result["output"]).name.endswith(".generated.ts"):
        print(
            "warning: use a *.generated.ts output so hand-written wrapper code remains safe",
            file=sys.stderr,
        )
    print(result["output"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
