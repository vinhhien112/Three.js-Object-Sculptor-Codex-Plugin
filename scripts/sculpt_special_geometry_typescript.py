#!/usr/bin/env python3
"""Conditional TypeScript helpers for opt-in static special geometry.

The helpers deliberately use only core Three.js APIs.  They construct one
``BufferGeometry`` per component and contain no renderer, simulation, or
external-addon dependency.  ``SPECIAL_GEOMETRY_HELPERS`` is insertion ordered:
callers should emit the selected snippets in this order so shared utilities
precede the geometry factories that use them.
"""

from __future__ import annotations


_SPECIAL_GEOMETRY_COMMON_TYPESCRIPT = r"""function createSpecialGeometryRandom(seed: number): () => number {
  let state = seed >>> 0;
  return (): number => {
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    return (state >>> 0) / 4294967296;
  };
}

function sampleSpecialGeometryNormal(random: () => number): number {
  const first = Math.max(Number.EPSILON, random());
  const second = random();
  return Math.sqrt(-2 * Math.log(first)) * Math.cos(Math.PI * 2 * second);
}

function finalizeSpecialGeometry(
  geometry: THREE.BufferGeometry,
  computeNormals = false,
): THREE.BufferGeometry {
  const position = geometry.getAttribute('position');
  if (position.count === 0) {
    throw new Error('special geometry emitted no positions');
  }
  if (computeNormals) geometry.computeVertexNormals();
  for (const [name, attribute] of Object.entries(geometry.attributes)) {
    const values = (attribute as THREE.BufferAttribute).array;
    for (let index = 0; index < values.length; index += 1) {
      if (!Number.isFinite(Number(values[index]))) {
        throw new Error(`special geometry emitted a non-finite ${name} value at ${index}`);
      }
    }
  }
  geometry.computeBoundingBox();
  geometry.computeBoundingSphere();
  if (geometry.boundingSphere && !Number.isFinite(geometry.boundingSphere.radius)) {
    throw new Error('special geometry emitted a non-finite bounding sphere');
  }
  return geometry;
}"""


_MODELING_COMMON_TYPESCRIPT = r"""type LoftSectionSpec = {
  position: [number, number, number];
  radii: [number, number];
  twist: number;
};

type LoftSectionFrame = {
  center: THREE.Vector3;
  radii: THREE.Vector2;
  tangent: THREE.Vector3;
  axisX: THREE.Vector3;
  axisZ: THREE.Vector3;
  twist: number;
};

function sampleLoftSectionValues(
  sections: LoftSectionSpec[],
  v: number,
): { center: THREE.Vector3; radii: THREE.Vector2; twist: number } {
  const scaled = THREE.MathUtils.clamp(v, 0, 1) * (sections.length - 1);
  const span = Math.min(sections.length - 2, Math.floor(scaled));
  const amount = scaled - span;
  const first = sections[span];
  const second = sections[span + 1];
  return {
    center: new THREE.Vector3(
      THREE.MathUtils.lerp(first.position[0], second.position[0], amount),
      THREE.MathUtils.lerp(first.position[1], second.position[1], amount),
      THREE.MathUtils.lerp(first.position[2], second.position[2], amount),
    ),
    radii: new THREE.Vector2(
      THREE.MathUtils.lerp(first.radii[0], second.radii[0], amount),
      THREE.MathUtils.lerp(first.radii[1], second.radii[1], amount),
    ),
    twist: THREE.MathUtils.lerp(first.twist, second.twist, amount),
  };
}

function sampleLoftSectionFrame(
  sections: LoftSectionSpec[],
  v: number,
): LoftSectionFrame {
  const values = sampleLoftSectionValues(sections, v);
  const epsilon = 0.5 / Math.max(1, sections.length - 1);
  const before = sampleLoftSectionValues(sections, Math.max(0, v - epsilon)).center;
  const after = sampleLoftSectionValues(sections, Math.min(1, v + epsilon)).center;
  const tangent = after.sub(before).normalize();
  if (tangent.lengthSq() <= Number.EPSILON) tangent.set(0, 1, 0);
  const reference = Math.abs(tangent.z) < 0.9
    ? new THREE.Vector3(0, 0, 1)
    : new THREE.Vector3(1, 0, 0);
  const axisX = new THREE.Vector3().crossVectors(tangent, reference).normalize();
  const axisZ = new THREE.Vector3().crossVectors(axisX, tangent).normalize();
  return {
    center: values.center,
    radii: values.radii,
    tangent,
    axisX,
    axisZ,
    twist: values.twist,
  };
}

function loftRadialNormal(frame: LoftSectionFrame, angle: number): THREE.Vector3 {
  return frame.axisX.clone()
    .multiplyScalar(Math.cos(angle) / frame.radii.x)
    .addScaledVector(frame.axisZ, Math.sin(angle) / frame.radii.y)
    .normalize();
}"""


_SECTION_LOFT_TYPESCRIPT = r"""type SectionLoftGeometrySpec = {
  sections: LoftSectionSpec[];
  radialSegments: number;
  segmentsPerSpan: number;
  capStart: boolean;
  capEnd: boolean;
};

function createSectionLoftGeometry(spec: SectionLoftGeometrySpec): THREE.BufferGeometry {
  const radialSegments = Math.max(3, spec.radialSegments);
  const longitudinalSegments = Math.max(
    1,
    (spec.sections.length - 1) * spec.segmentsPerSpan,
  );
  const stride = radialSegments + 1;
  const positions: number[] = [];
  const uvs: number[] = [];
  const indices: number[] = [];
  for (let ring = 0; ring <= longitudinalSegments; ring += 1) {
    const v = ring / longitudinalSegments;
    const frame = sampleLoftSectionFrame(spec.sections, v);
    for (let radial = 0; radial <= radialSegments; radial += 1) {
      const u = radial / radialSegments;
      const angle = u * Math.PI * 2 + frame.twist;
      const point = frame.center.clone()
        .addScaledVector(frame.axisX, Math.cos(angle) * frame.radii.x)
        .addScaledVector(frame.axisZ, Math.sin(angle) * frame.radii.y);
      positions.push(point.x, point.y, point.z);
      uvs.push(u, v);
    }
  }
  for (let ring = 0; ring < longitudinalSegments; ring += 1) {
    for (let radial = 0; radial < radialSegments; radial += 1) {
      const a = ring * stride + radial;
      const b = (ring + 1) * stride + radial;
      const c = b + 1;
      const d = a + 1;
      indices.push(a, b, d, b, c, d);
    }
  }
  if (spec.capStart) {
    const centerIndex = positions.length / 3;
    const center = sampleLoftSectionFrame(spec.sections, 0).center;
    positions.push(center.x, center.y, center.z);
    uvs.push(0.5, 0.5);
    for (let radial = 0; radial < radialSegments; radial += 1) {
      indices.push(centerIndex, radial, radial + 1);
    }
  }
  if (spec.capEnd) {
    const centerIndex = positions.length / 3;
    const center = sampleLoftSectionFrame(spec.sections, 1).center;
    positions.push(center.x, center.y, center.z);
    uvs.push(0.5, 0.5);
    const offset = longitudinalSegments * stride;
    for (let radial = 0; radial < radialSegments; radial += 1) {
      indices.push(centerIndex, offset + radial + 1, offset + radial);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  return finalizeSpecialGeometry(geometry, true);
}"""


_CONFORMING_SHELL_TYPESCRIPT = r"""type ConformingShellOpeningSpec = {
  id: string;
  center: [number, number];
  radius: [number, number];
};

type ConformingShellFoldSpec = {
  direction: [number, number];
  amplitude: number;
  frequency: number;
  phase: number;
};

type ConformingShellGeometrySpec = {
  sections: LoftSectionSpec[];
  radialSegments: number;
  segmentsPerSpan: number;
  clearance: number;
  thickness: number;
  coverage: {
    vRange: [number, number];
    angleStart: number;
    angleLength: number;
  };
  openings: ConformingShellOpeningSpec[];
  folds: ConformingShellFoldSpec[];
};

function shellFoldDisplacement(
  folds: ConformingShellFoldSpec[],
  u: number,
  v: number,
): number {
  let displacement = 0;
  for (const fold of folds) {
    const coordinate = u * fold.direction[0] + v * fold.direction[1];
    displacement += fold.amplitude * Math.sin(
      coordinate * fold.frequency * Math.PI * 2 + fold.phase,
    );
  }
  return displacement;
}

function shellCellIsOpen(
  openings: ConformingShellOpeningSpec[],
  u: number,
  v: number,
): boolean {
  return openings.some((opening) => {
    const x = (u - opening.center[0]) / opening.radius[0];
    const y = (v - opening.center[1]) / opening.radius[1];
    return x * x + y * y <= 1;
  });
}

function createConformingShellGeometry(
  spec: ConformingShellGeometrySpec,
): THREE.BufferGeometry {
  const fullAngle = Math.abs(spec.coverage.angleLength - Math.PI * 2) <= 0.000001;
  const totalLongitudinal = Math.max(
    1,
    (spec.sections.length - 1) * spec.segmentsPerSpan,
  );
  const longitudinalSegments = Math.max(
    1,
    Math.ceil(totalLongitudinal * (spec.coverage.vRange[1] - spec.coverage.vRange[0])),
  );
  const angularSegments = Math.max(
    1,
    Math.ceil(spec.radialSegments * spec.coverage.angleLength / (Math.PI * 2)),
  );
  const columns = angularSegments + 1;
  const rows = longitudinalSegments + 1;
  const layerSize = rows * columns;
  const positions: number[] = [];
  const uvs: number[] = [];
  const indices: number[] = [];
  for (let layer = 0; layer < 2; layer += 1) {
    for (let row = 0; row <= longitudinalSegments; row += 1) {
      const localV = row / longitudinalSegments;
      const v = THREE.MathUtils.lerp(
        spec.coverage.vRange[0],
        spec.coverage.vRange[1],
        localV,
      );
      const frame = sampleLoftSectionFrame(spec.sections, v);
      for (let column = 0; column <= angularSegments; column += 1) {
        const localU = column / angularSegments;
        const angle = spec.coverage.angleStart
          + spec.coverage.angleLength * localU
          + frame.twist;
        const fold = shellFoldDisplacement(spec.folds, localU, localV);
        const shellThickness = Math.max(
          spec.thickness * 0.05,
          spec.thickness + fold,
        );
        const expansion = spec.clearance + (layer === 0 ? shellThickness : 0);
        const point = frame.center.clone()
          .addScaledVector(frame.axisX, Math.cos(angle) * (frame.radii.x + expansion))
          .addScaledVector(frame.axisZ, Math.sin(angle) * (frame.radii.y + expansion));
        positions.push(point.x, point.y, point.z);
        uvs.push(localU, localV);
      }
    }
  }
  const active: boolean[][] = [];
  for (let row = 0; row < longitudinalSegments; row += 1) {
    const cells: boolean[] = [];
    for (let column = 0; column < angularSegments; column += 1) {
      cells.push(!shellCellIsOpen(
        spec.openings,
        (column + 0.5) / angularSegments,
        (row + 0.5) / longitudinalSegments,
      ));
    }
    active.push(cells);
  }
  const outer = (row: number, column: number): number => row * columns + column;
  const inner = (row: number, column: number): number => layerSize + row * columns + column;
  const addWall = (first: [number, number], second: [number, number]): void => {
    const outerFirst = outer(first[0], first[1]);
    const outerSecond = outer(second[0], second[1]);
    const innerFirst = inner(first[0], first[1]);
    const innerSecond = inner(second[0], second[1]);
    indices.push(
      outerFirst, innerFirst, outerSecond,
      outerSecond, innerFirst, innerSecond,
      outerFirst, outerSecond, innerFirst,
      outerSecond, innerSecond, innerFirst,
    );
  };
  for (let row = 0; row < longitudinalSegments; row += 1) {
    for (let column = 0; column < angularSegments; column += 1) {
      if (!active[row][column]) continue;
      const a = outer(row, column);
      const b = outer(row + 1, column);
      const c = outer(row + 1, column + 1);
      const d = outer(row, column + 1);
      const ia = inner(row, column);
      const ib = inner(row + 1, column);
      const ic = inner(row + 1, column + 1);
      const id = inner(row, column + 1);
      indices.push(a, b, d, b, c, d);
      indices.push(ia, id, ib, ib, id, ic);
      if (row === 0 || !active[row - 1][column]) {
        addWall([row, column], [row, column + 1]);
      }
      if (row === longitudinalSegments - 1 || !active[row + 1][column]) {
        addWall([row + 1, column + 1], [row + 1, column]);
      }
      const leftActive = column > 0
        ? active[row][column - 1]
        : fullAngle && active[row][angularSegments - 1];
      if (!leftActive) addWall([row + 1, column], [row, column]);
      const rightActive = column < angularSegments - 1
        ? active[row][column + 1]
        : fullAngle && active[row][0];
      if (!rightActive) addWall([row, column + 1], [row + 1, column + 1]);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  return finalizeSpecialGeometry(geometry, true);
}"""


_SURFACE_SCATTER_TYPESCRIPT = r"""type SurfaceScatterMaskSpec = {
  uRange: [number, number];
  vRange: [number, number];
};

type SurfaceScatterLayoutSpec = {
  sections: LoftSectionSpec[];
  count: number;
  seed: number;
  uRange: [number, number];
  vRange: [number, number];
  excludeMasks: SurfaceScatterMaskSpec[];
  normalOffset: number;
  scaleRange: [number, number];
  baseScale: [number, number, number];
  spinRange: number;
  alignToNormal: boolean;
  baseRotation: [number, number, number];
};

function applySurfaceScatterLayout(
  mesh: THREE.InstancedMesh,
  spec: SurfaceScatterLayoutSpec,
): void {
  const random = createSpecialGeometryRandom(spec.seed);
  const pivot = new THREE.Object3D();
  const up = new THREE.Vector3(0, 1, 0);
  const maximumAttempts = Math.max(64, spec.count * 64);
  let attempts = 0;
  let writeIndex = 0;
  while (writeIndex < spec.count && attempts < maximumAttempts) {
    attempts += 1;
    const u = THREE.MathUtils.lerp(spec.uRange[0], spec.uRange[1], random());
    const v = THREE.MathUtils.lerp(spec.vRange[0], spec.vRange[1], random());
    const excluded = spec.excludeMasks.some((mask) => (
      u >= mask.uRange[0] && u <= mask.uRange[1]
      && v >= mask.vRange[0] && v <= mask.vRange[1]
    ));
    if (excluded) continue;
    const frame = sampleLoftSectionFrame(spec.sections, v);
    const angle = u * Math.PI * 2 + frame.twist;
    const normal = loftRadialNormal(frame, angle);
    const point = frame.center.clone()
      .addScaledVector(frame.axisX, Math.cos(angle) * frame.radii.x)
      .addScaledVector(frame.axisZ, Math.sin(angle) * frame.radii.y)
      .addScaledVector(normal, spec.normalOffset);
    const randomScale = THREE.MathUtils.lerp(
      spec.scaleRange[0],
      spec.scaleRange[1],
      random(),
    );
    const spin = THREE.MathUtils.lerp(-spec.spinRange, spec.spinRange, random());
    pivot.position.copy(point);
    pivot.rotation.set(...spec.baseRotation);
    let orientation: THREE.Quaternion | undefined;
    if (spec.alignToNormal) {
      orientation = new THREE.Quaternion().setFromUnitVectors(up, normal);
    }
    if (spin !== 0) {
      const spinQuaternion = new THREE.Quaternion().setFromAxisAngle(up, spin);
      orientation = orientation ? orientation.multiply(spinQuaternion) : spinQuaternion;
    }
    if (orientation) pivot.quaternion.premultiply(orientation);
    pivot.scale.set(
      spec.baseScale[0] * randomScale,
      spec.baseScale[1] * randomScale,
      spec.baseScale[2] * randomScale,
    );
    pivot.updateMatrix();
    mesh.setMatrixAt(writeIndex, pivot.matrix);
    writeIndex += 1;
  }
  if (writeIndex !== spec.count) {
    throw new Error('surface-scatter masks leave too little sampled surface');
  }
  mesh.count = writeIndex;
  mesh.instanceMatrix.needsUpdate = true;
}"""


_BRANCH_NETWORK_TYPESCRIPT = r"""type BranchNetworkNodeSpec = {
  id: string;
  position: [number, number, number];
  radius: number;
};

type BranchNetworkEdgeSpec = {
  from: string;
  to: string;
  controlPoints: [number, number, number][];
};

type BranchNetworkGeometrySpec = {
  nodes: BranchNetworkNodeSpec[];
  edges: BranchNetworkEdgeSpec[];
  radialSegments: number;
  segmentsPerEdge: number;
  junctionSegments: number;
  capEnds: boolean;
};

function createBranchNetworkGeometry(spec: BranchNetworkGeometrySpec): THREE.BufferGeometry {
  const positions: number[] = [];
  const uvs: number[] = [];
  const indices: number[] = [];
  const nodes = new Map(spec.nodes.map((node) => [node.id, node]));
  const indegree = new Map(spec.nodes.map((node) => [node.id, 0]));
  const outdegree = new Map(spec.nodes.map((node) => [node.id, 0]));
  for (const edge of spec.edges) {
    indegree.set(edge.to, (indegree.get(edge.to) ?? 0) + 1);
    outdegree.set(edge.from, (outdegree.get(edge.from) ?? 0) + 1);
  }
  const radialSegments = Math.max(3, spec.radialSegments);
  const stride = radialSegments + 1;
  for (const edge of spec.edges) {
    const start = nodes.get(edge.from);
    const end = nodes.get(edge.to);
    if (!start || !end) throw new Error('branch-network edge references an unknown node');
    const curvePoints = [
      new THREE.Vector3(...start.position),
      ...edge.controlPoints.map((point) => new THREE.Vector3(...point)),
      new THREE.Vector3(...end.position),
    ];
    const curve = new THREE.CatmullRomCurve3(curvePoints, false, 'centripetal');
    const frames = curve.computeFrenetFrames(spec.segmentsPerEdge, false);
    const vertexStart = positions.length / 3;
    for (let ring = 0; ring <= spec.segmentsPerEdge; ring += 1) {
      const t = ring / spec.segmentsPerEdge;
      const center = curve.getPointAt(t);
      const normal = frames.normals[ring];
      const binormal = frames.binormals[ring];
      const radius = THREE.MathUtils.lerp(start.radius, end.radius, t);
      for (let radial = 0; radial <= radialSegments; radial += 1) {
        const u = radial / radialSegments;
        const angle = u * Math.PI * 2;
        const point = center.clone()
          .addScaledVector(normal, Math.cos(angle) * radius)
          .addScaledVector(binormal, Math.sin(angle) * radius);
        positions.push(point.x, point.y, point.z);
        uvs.push(u, t);
      }
    }
    for (let ring = 0; ring < spec.segmentsPerEdge; ring += 1) {
      for (let radial = 0; radial < radialSegments; radial += 1) {
        const a = vertexStart + ring * stride + radial;
        const b = vertexStart + (ring + 1) * stride + radial;
        const c = b + 1;
        const d = a + 1;
        indices.push(a, d, b, b, d, c);
      }
    }
    if (spec.capEnds && (indegree.get(edge.from) ?? 0) === 0) {
      const centerIndex = positions.length / 3;
      positions.push(...start.position);
      uvs.push(0.5, 0.5);
      for (let radial = 0; radial < radialSegments; radial += 1) {
        indices.push(centerIndex, vertexStart + radial + 1, vertexStart + radial);
      }
    }
    if (spec.capEnds && (outdegree.get(edge.to) ?? 0) === 0) {
      const centerIndex = positions.length / 3;
      positions.push(...end.position);
      uvs.push(0.5, 0.5);
      const offset = vertexStart + spec.segmentsPerEdge * stride;
      for (let radial = 0; radial < radialSegments; radial += 1) {
        indices.push(centerIndex, offset + radial, offset + radial + 1);
      }
    }
  }
  const sphereSegments = Math.max(4, spec.junctionSegments);
  const sphereRows = Math.max(3, Math.floor(sphereSegments * 0.5));
  for (const node of spec.nodes) {
    const degree = (indegree.get(node.id) ?? 0) + (outdegree.get(node.id) ?? 0);
    if (degree <= 1) continue;
    const vertexStart = positions.length / 3;
    for (let row = 0; row <= sphereRows; row += 1) {
      const v = row / sphereRows;
      const phi = v * Math.PI;
      for (let column = 0; column <= sphereSegments; column += 1) {
        const u = column / sphereSegments;
        const theta = u * Math.PI * 2;
        positions.push(
          node.position[0] + Math.sin(phi) * Math.cos(theta) * node.radius,
          node.position[1] + Math.cos(phi) * node.radius,
          node.position[2] + Math.sin(phi) * Math.sin(theta) * node.radius,
        );
        uvs.push(u, v);
      }
    }
    const sphereStride = sphereSegments + 1;
    for (let row = 0; row < sphereRows; row += 1) {
      for (let column = 0; column < sphereSegments; column += 1) {
        const a = vertexStart + row * sphereStride + column;
        const b = a + sphereStride;
        const c = b + 1;
        const d = a + 1;
        if (row > 0) indices.push(a, d, b);
        if (row < sphereRows - 1) indices.push(b, d, c);
      }
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  return finalizeSpecialGeometry(geometry, true);
}"""


_GEOMETRY_MODIFIERS_TYPESCRIPT = r"""type ModelingAxis = 'x' | 'y' | 'z';

type GeometryModifierSpec = {
  type: 'bend' | 'taper' | 'bulge' | 'twist' | 'noise';
  axis: ModelingAxis;
  direction?: ModelingAxis;
  amount: number;
  start: number;
  end: number;
  power: number;
  frequency?: number;
  seed?: number;
};

function geometryAxisValue(point: THREE.Vector3, axis: ModelingAxis): number {
  return axis === 'x' ? point.x : axis === 'y' ? point.y : point.z;
}

function setGeometryAxisValue(
  point: THREE.Vector3,
  axis: ModelingAxis,
  value: number,
): void {
  if (axis === 'x') point.x = value;
  else if (axis === 'y') point.y = value;
  else point.z = value;
}

function otherGeometryAxes(axis: ModelingAxis): [ModelingAxis, ModelingAxis] {
  if (axis === 'x') return ['y', 'z'];
  if (axis === 'y') return ['x', 'z'];
  return ['x', 'y'];
}

function applyGeometryModifiers(
  source: THREE.BufferGeometry,
  modifiers: GeometryModifierSpec[],
): THREE.BufferGeometry {
  const geometry = source.clone();
  const point = new THREE.Vector3();
  const normal = new THREE.Vector3();
  for (const modifier of modifiers) {
    geometry.computeBoundingBox();
    const bounds = geometry.boundingBox;
    if (!bounds) throw new Error('geometry modifier requires finite bounds');
    const position = geometry.getAttribute('position') as THREE.BufferAttribute;
    const minimum = geometryAxisValue(bounds.min, modifier.axis);
    const maximum = geometryAxisValue(bounds.max, modifier.axis);
    const length = maximum - minimum;
    if (!(length > Number.EPSILON)) continue;
    const rangeStart = minimum + length * modifier.start;
    const rangeEnd = minimum + length * modifier.end;
    const rangeLength = rangeEnd - rangeStart;
    const perpendicular = otherGeometryAxes(modifier.axis);
    const firstCenter = (
      geometryAxisValue(bounds.min, perpendicular[0])
      + geometryAxisValue(bounds.max, perpendicular[0])
    ) * 0.5;
    const secondCenter = (
      geometryAxisValue(bounds.min, perpendicular[1])
      + geometryAxisValue(bounds.max, perpendicular[1])
    ) * 0.5;
    if (modifier.type === 'noise') {
      position.needsUpdate = true;
      geometry.computeVertexNormals();
    }
    const normalAttribute = geometry.getAttribute('normal') as THREE.BufferAttribute | undefined;
    for (let index = 0; index < position.count; index += 1) {
      point.fromBufferAttribute(position, index);
      const axisValue = geometryAxisValue(point, modifier.axis);
      if (axisValue < rangeStart) continue;
      const localT = THREE.MathUtils.clamp(
        (axisValue - rangeStart) / rangeLength,
        0,
        1,
      );
      const weight = Math.pow(localT, modifier.power);
      if (modifier.type === 'taper' || modifier.type === 'bulge') {
        const envelope = modifier.type === 'bulge'
          ? Math.pow(Math.sin(Math.PI * localT), modifier.power)
          : weight;
        const factor = Math.max(0.02, 1 + modifier.amount * envelope);
        setGeometryAxisValue(
          point,
          perpendicular[0],
          firstCenter + (geometryAxisValue(point, perpendicular[0]) - firstCenter) * factor,
        );
        setGeometryAxisValue(
          point,
          perpendicular[1],
          secondCenter + (geometryAxisValue(point, perpendicular[1]) - secondCenter) * factor,
        );
      } else if (modifier.type === 'twist') {
        const angle = modifier.amount * weight;
        const cosine = Math.cos(angle);
        const sine = Math.sin(angle);
        const first = geometryAxisValue(point, perpendicular[0]) - firstCenter;
        const second = geometryAxisValue(point, perpendicular[1]) - secondCenter;
        setGeometryAxisValue(point, perpendicular[0], firstCenter + first * cosine - second * sine);
        setGeometryAxisValue(point, perpendicular[1], secondCenter + first * sine + second * cosine);
      } else if (modifier.type === 'bend') {
        if (Math.abs(modifier.amount) <= Number.EPSILON) continue;
        const direction = modifier.direction ?? perpendicular[0];
        const directionCenter = (
          geometryAxisValue(bounds.min, direction)
          + geometryAxisValue(bounds.max, direction)
        ) * 0.5;
        const clampedAxis = Math.min(axisValue, rangeEnd);
        const clampedT = THREE.MathUtils.clamp(
          (clampedAxis - rangeStart) / rangeLength,
          0,
          1,
        );
        const angle = modifier.amount * Math.pow(clampedT, modifier.power);
        const radius = rangeLength / modifier.amount;
        let centerAxis = rangeStart + Math.sin(angle) * radius;
        let centerDirection = directionCenter + (1 - Math.cos(angle)) * radius;
        if (axisValue > rangeEnd) {
          const beyond = axisValue - rangeEnd;
          centerAxis += Math.cos(modifier.amount) * beyond;
          centerDirection += Math.sin(modifier.amount) * beyond;
        }
        const offset = geometryAxisValue(point, direction) - directionCenter;
        setGeometryAxisValue(point, modifier.axis, centerAxis - Math.sin(angle) * offset);
        setGeometryAxisValue(point, direction, centerDirection + Math.cos(angle) * offset);
      } else if (modifier.type === 'noise' && normalAttribute) {
        normal.fromBufferAttribute(normalAttribute, index).normalize();
        const frequency = modifier.frequency ?? 1;
        const seed = modifier.seed ?? 1;
        const phase = (
          point.x * 12.9898
          + point.y * 78.233
          + point.z * 37.719
        ) * frequency + seed * 0.12345;
        const raw = Math.sin(phase) * 43758.5453;
        const signed = (raw - Math.floor(raw)) * 2 - 1;
        point.addScaledVector(normal, signed * modifier.amount * weight);
      }
      position.setXYZ(index, point.x, point.y, point.z);
    }
    position.needsUpdate = true;
  }
  return finalizeSpecialGeometry(geometry, true);
}"""


_DEFORMABLE_SURFACE_TYPESCRIPT = r"""type DeformableSurfaceFoldSpec = {
  direction: [number, number];
  amplitude: number;
  frequency: number;
  phase: number;
  edgeFade: number;
};

type DeformableSurfaceGeometrySpec = {
  controlGrid: number[][][];
  segments: [number, number];
  folds: DeformableSurfaceFoldSpec[];
};

function sampleDeformableControlGrid(
  controlGrid: number[][][],
  u: number,
  v: number,
  target: THREE.Vector3,
): THREE.Vector3 {
  const rows = controlGrid.length;
  const columns = controlGrid[0].length;
  const scaledU = THREE.MathUtils.clamp(u, 0, 1) * (columns - 1);
  const scaledV = THREE.MathUtils.clamp(v, 0, 1) * (rows - 1);
  const column = Math.min(columns - 2, Math.floor(scaledU));
  const row = Math.min(rows - 2, Math.floor(scaledV));
  const localU = scaledU - column;
  const localV = scaledV - row;
  const p00 = controlGrid[row][column];
  const p10 = controlGrid[row][column + 1];
  const p01 = controlGrid[row + 1][column];
  const p11 = controlGrid[row + 1][column + 1];
  const topX = THREE.MathUtils.lerp(p00[0], p10[0], localU);
  const topY = THREE.MathUtils.lerp(p00[1], p10[1], localU);
  const topZ = THREE.MathUtils.lerp(p00[2], p10[2], localU);
  const bottomX = THREE.MathUtils.lerp(p01[0], p11[0], localU);
  const bottomY = THREE.MathUtils.lerp(p01[1], p11[1], localU);
  const bottomZ = THREE.MathUtils.lerp(p01[2], p11[2], localU);
  return target.set(
    THREE.MathUtils.lerp(topX, bottomX, localV),
    THREE.MathUtils.lerp(topY, bottomY, localV),
    THREE.MathUtils.lerp(topZ, bottomZ, localV),
  );
}

function createDeformableSurfaceGeometry(
  spec: DeformableSurfaceGeometrySpec,
): THREE.BufferGeometry {
  const [segmentsU, segmentsV] = spec.segments;
  const positions: number[] = [];
  const uvs: number[] = [];
  const indices: number[] = [];
  const base = new THREE.Vector3();
  const beforeU = new THREE.Vector3();
  const afterU = new THREE.Vector3();
  const beforeV = new THREE.Vector3();
  const afterV = new THREE.Vector3();
  const tangentU = new THREE.Vector3();
  const tangentV = new THREE.Vector3();
  const normal = new THREE.Vector3();
  const epsilonU = 1 / segmentsU;
  const epsilonV = 1 / segmentsV;
  for (let row = 0; row <= segmentsV; row += 1) {
    const v = row / segmentsV;
    for (let column = 0; column <= segmentsU; column += 1) {
      const u = column / segmentsU;
      sampleDeformableControlGrid(spec.controlGrid, u, v, base);
      sampleDeformableControlGrid(spec.controlGrid, u - epsilonU, v, beforeU);
      sampleDeformableControlGrid(spec.controlGrid, u + epsilonU, v, afterU);
      sampleDeformableControlGrid(spec.controlGrid, u, v - epsilonV, beforeV);
      sampleDeformableControlGrid(spec.controlGrid, u, v + epsilonV, afterV);
      tangentU.subVectors(afterU, beforeU);
      tangentV.subVectors(afterV, beforeV);
      normal.crossVectors(tangentU, tangentV).normalize();
      let displacement = 0;
      for (const fold of spec.folds) {
        const coordinate = u * fold.direction[0] + v * fold.direction[1];
        const edgeDistance = Math.min(u, 1 - u, v, 1 - v);
        const envelope = fold.edgeFade > 0
          ? THREE.MathUtils.smoothstep(edgeDistance, 0, fold.edgeFade)
          : 1;
        displacement += envelope * fold.amplitude * Math.sin(
          coordinate * fold.frequency * Math.PI * 2 + fold.phase,
        );
      }
      base.addScaledVector(normal, displacement);
      positions.push(base.x, base.y, base.z);
      uvs.push(u, v);
    }
  }
  const stride = segmentsU + 1;
  for (let row = 0; row < segmentsV; row += 1) {
    for (let column = 0; column < segmentsU; column += 1) {
      const a = row * stride + column;
      const b = a + 1;
      const c = a + stride;
      const d = c + 1;
      indices.push(a, c, b, b, c, d);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  return finalizeSpecialGeometry(geometry, true);
}"""


_FIBER_SYSTEM_TYPESCRIPT = r"""type FiberCurlSpec = {
  amplitude: number;
  frequency: number;
  phase: number;
};

type FiberSystemGeometrySpec = {
  guides: number[][][];
  strandsPerGuide: number;
  samples: number;
  rootWidth: number;
  tipWidth: number;
  spread: number;
  clump: number;
  lengthVariation: number;
  widthVariation: number;
  curl: FiberCurlSpec;
  cardPlanes: 1 | 2;
  seed: number;
};

function createFiberSystemGeometry(spec: FiberSystemGeometrySpec): THREE.BufferGeometry {
  const positions: number[] = [];
  const normals: number[] = [];
  const tangents: number[] = [];
  const uvs: number[] = [];
  const indices: number[] = [];
  const random = createSpecialGeometryRandom(spec.seed);
  for (const guideSpec of spec.guides) {
    const guidePoints = guideSpec.map((point) => new THREE.Vector3(point[0], point[1], point[2]));
    const guide = new THREE.CatmullRomCurve3(guidePoints, false, 'centripetal');
    const frames = guide.computeFrenetFrames(spec.samples, false);
    for (let strand = 0; strand < spec.strandsPerGuide; strand += 1) {
      const strandAngle = random() * Math.PI * 2;
      const spreadRadius = spec.spread * Math.sqrt(random());
      const curlPhase = spec.curl.phase + random() * Math.PI * 2;
      const strandLength = 1 - random() * spec.lengthVariation;
      const strandWidth = 1 + (random() * 2 - 1) * spec.widthVariation;
      for (let plane = 0; plane < spec.cardPlanes; plane += 1) {
        const planeStart = positions.length / 3;
        const planeAngle = strandAngle + plane * Math.PI * 0.5;
        for (let sample = 0; sample <= spec.samples; sample += 1) {
          const t = sample / spec.samples;
          const sampleT = t * strandLength;
          const frameIndex = Math.min(spec.samples, Math.round(sampleT * spec.samples));
          const center = guide.getPointAt(sampleT);
          const tangent = guide.getTangentAt(sampleT).normalize();
          const frameNormal = frames.normals[frameIndex];
          const frameBinormal = frames.binormals[frameIndex];
          const offsetScale = spreadRadius * (1 - spec.clump * t);
          center.addScaledVector(frameNormal, Math.cos(strandAngle) * offsetScale);
          center.addScaledVector(frameBinormal, Math.sin(strandAngle) * offsetScale);
          const curlAngle = spec.curl.frequency * Math.PI * 2 * t + curlPhase;
          center.addScaledVector(
            frameNormal,
            Math.cos(curlAngle) * spec.curl.amplitude,
          );
          center.addScaledVector(
            frameBinormal,
            Math.sin(curlAngle) * spec.curl.amplitude,
          );
          const widthDirection = frameNormal.clone()
            .multiplyScalar(Math.cos(planeAngle))
            .addScaledVector(frameBinormal, Math.sin(planeAngle))
            .normalize();
          const halfWidth = THREE.MathUtils.lerp(spec.rootWidth, spec.tipWidth, t) * strandWidth * 0.5;
          const left = center.clone().addScaledVector(widthDirection, -halfWidth);
          const right = center.clone().addScaledVector(widthDirection, halfWidth);
          const surfaceNormal = new THREE.Vector3().crossVectors(tangent, widthDirection).normalize();
          positions.push(left.x, left.y, left.z, right.x, right.y, right.z);
          normals.push(
            surfaceNormal.x, surfaceNormal.y, surfaceNormal.z,
            surfaceNormal.x, surfaceNormal.y, surfaceNormal.z,
          );
          tangents.push(
            tangent.x, tangent.y, tangent.z, 1,
            tangent.x, tangent.y, tangent.z, 1,
          );
          uvs.push(0, t, 1, t);
        }
        for (let sample = 0; sample < spec.samples; sample += 1) {
          const a = planeStart + sample * 2;
          const b = a + 1;
          const c = a + 2;
          const d = a + 3;
          indices.push(a, c, b, b, c, d);
        }
      }
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
  geometry.setAttribute('tangent', new THREE.Float32BufferAttribute(tangents, 4));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  return finalizeSpecialGeometry(geometry);
}"""


_IMPLICIT_SURFACE_TYPESCRIPT = r"""type ImplicitSurfaceSourceSpec = {
  id?: string;
  shape: 'sphere' | 'ellipsoid' | 'capsule';
  position?: [number, number, number];
  radii?: [number, number, number];
  start?: [number, number, number];
  end?: [number, number, number];
  radius?: number;
  strength: number;
  falloff?: number;
  operation: 'add' | 'subtract';
};

type ImplicitSurfaceGeometrySpec = {
  bounds: { min: [number, number, number]; max: [number, number, number] };
  resolution: [number, number, number];
  isoLevel: number;
  sources: ImplicitSurfaceSourceSpec[];
  uvProjection: 'xy' | 'xz' | 'yz';
};

type SculptSurfaceModifierSpec = {
  id: string;
  type: 'inflate' | 'pinch' | 'ridge' | 'crease';
  position?: [number, number, number];
  radii?: [number, number, number];
  start?: [number, number, number];
  end?: [number, number, number];
  radius?: number;
  strength: number;
  falloff: number;
};

type SculptedSurfaceGeometrySpec = ImplicitSurfaceGeometrySpec & {
  surfaceModifiers: SculptSurfaceModifierSpec[];
  connectivity: 'single-surface';
};

function implicitSourceNormalizedDistance(
  source: ImplicitSurfaceSourceSpec,
  x: number,
  y: number,
  z: number,
): number {
  if (source.shape === 'ellipsoid' && source.position && source.radii) {
    const dx = (x - source.position[0]) / source.radii[0];
    const dy = (y - source.position[1]) / source.radii[1];
    const dz = (z - source.position[2]) / source.radii[2];
    return dx * dx + dy * dy + dz * dz;
  }
  if (source.shape === 'capsule' && source.start && source.end && source.radius) {
    const segmentX = source.end[0] - source.start[0];
    const segmentY = source.end[1] - source.start[1];
    const segmentZ = source.end[2] - source.start[2];
    const offsetX = x - source.start[0];
    const offsetY = y - source.start[1];
    const offsetZ = z - source.start[2];
    const amount = THREE.MathUtils.clamp(
      (offsetX * segmentX + offsetY * segmentY + offsetZ * segmentZ)
        / (segmentX * segmentX + segmentY * segmentY + segmentZ * segmentZ),
      0,
      1,
    );
    const dx = offsetX - segmentX * amount;
    const dy = offsetY - segmentY * amount;
    const dz = offsetZ - segmentZ * amount;
    return (dx * dx + dy * dy + dz * dz) / (source.radius * source.radius);
  }
  if (!source.position || !source.radius) {
    throw new Error('implicit-surface source is missing shape parameters');
  }
  const dx = x - source.position[0];
  const dy = y - source.position[1];
  const dz = z - source.position[2];
  return (dx * dx + dy * dy + dz * dz) / (source.radius * source.radius);
}

function sampleImplicitSurfaceField(
  spec: ImplicitSurfaceGeometrySpec,
  x: number,
  y: number,
  z: number,
): number {
  let value = 0;
  for (const source of spec.sources) {
    const density = source.strength * Math.exp(
      -implicitSourceNormalizedDistance(source, x, y, z) * (source.falloff ?? 0.5),
    );
    value += source.operation === 'subtract' ? -density : density;
  }
  return value;
}

function implicitSurfaceNormal(
  spec: ImplicitSurfaceGeometrySpec,
  point: THREE.Vector3,
): THREE.Vector3 {
  const gradient = new THREE.Vector3();
  for (const source of spec.sources) {
    let dx = 0;
    let dy = 0;
    let dz = 0;
    let divisorX = 1;
    let divisorY = 1;
    let divisorZ = 1;
    if (source.shape === 'ellipsoid' && source.position && source.radii) {
      dx = point.x - source.position[0];
      dy = point.y - source.position[1];
      dz = point.z - source.position[2];
      divisorX = source.radii[0] * source.radii[0];
      divisorY = source.radii[1] * source.radii[1];
      divisorZ = source.radii[2] * source.radii[2];
    } else if (source.shape === 'capsule' && source.start && source.end && source.radius) {
      const segmentX = source.end[0] - source.start[0];
      const segmentY = source.end[1] - source.start[1];
      const segmentZ = source.end[2] - source.start[2];
      const offsetX = point.x - source.start[0];
      const offsetY = point.y - source.start[1];
      const offsetZ = point.z - source.start[2];
      const amount = THREE.MathUtils.clamp(
        (offsetX * segmentX + offsetY * segmentY + offsetZ * segmentZ)
          / (segmentX * segmentX + segmentY * segmentY + segmentZ * segmentZ),
        0,
        1,
      );
      dx = offsetX - segmentX * amount;
      dy = offsetY - segmentY * amount;
      dz = offsetZ - segmentZ * amount;
      divisorX = source.radius * source.radius;
      divisorY = divisorX;
      divisorZ = divisorX;
    } else if (source.position && source.radius) {
      dx = point.x - source.position[0];
      dy = point.y - source.position[1];
      dz = point.z - source.position[2];
      divisorX = source.radius * source.radius;
      divisorY = divisorX;
      divisorZ = divisorX;
    } else {
      continue;
    }
    const normalizedDistance = dx * dx / divisorX
      + dy * dy / divisorY
      + dz * dz / divisorZ;
    const sign = source.operation === 'subtract' ? -1 : 1;
    const falloff = source.falloff ?? 0.5;
    const density = sign * source.strength * Math.exp(-normalizedDistance * falloff);
    gradient.x += -dx / divisorX * density * falloff;
    gradient.y += -dy / divisorY * density * falloff;
    gradient.z += -dz / divisorZ * density * falloff;
  }
  const gradientLengthSquared = gradient.lengthSq();
  if (!Number.isFinite(gradientLengthSquared) || gradientLengthSquared <= Number.MIN_VALUE) {
    return new THREE.Vector3(0, 1, 0);
  }
  return gradient.multiplyScalar(-1).normalize();
}

function implicitSurfaceUv(
  spec: ImplicitSurfaceGeometrySpec,
  point: THREE.Vector3,
): [number, number] {
  const minimum = spec.bounds.min;
  const maximum = spec.bounds.max;
  if (spec.uvProjection === 'xy') {
    return [
      (point.x - minimum[0]) / (maximum[0] - minimum[0]),
      (point.y - minimum[1]) / (maximum[1] - minimum[1]),
    ];
  }
  if (spec.uvProjection === 'yz') {
    return [
      (point.y - minimum[1]) / (maximum[1] - minimum[1]),
      (point.z - minimum[2]) / (maximum[2] - minimum[2]),
    ];
  }
  return [
    (point.x - minimum[0]) / (maximum[0] - minimum[0]),
    (point.z - minimum[2]) / (maximum[2] - minimum[2]),
  ];
}

function createWeldedImplicitGeometry(
  positions: number[],
  normals: number[],
  uvs: number[],
): THREE.BufferGeometry {
  const precision = 1e7;
  const lookup = new Map<string, number>();
  const weldedPositions: number[] = [];
  const weldedNormals: number[] = [];
  const weldedUvs: number[] = [];
  const indices: number[] = [];
  for (let vertex = 0; vertex < positions.length / 3; vertex += 1) {
    const positionOffset = vertex * 3;
    const uvOffset = vertex * 2;
    const key = [
      Math.round(positions[positionOffset] * precision),
      Math.round(positions[positionOffset + 1] * precision),
      Math.round(positions[positionOffset + 2] * precision),
    ].join(':');
    let index = lookup.get(key);
    if (index === undefined) {
      index = weldedPositions.length / 3;
      lookup.set(key, index);
      weldedPositions.push(
        positions[positionOffset],
        positions[positionOffset + 1],
        positions[positionOffset + 2],
      );
      weldedNormals.push(
        normals[positionOffset],
        normals[positionOffset + 1],
        normals[positionOffset + 2],
      );
      weldedUvs.push(uvs[uvOffset], uvs[uvOffset + 1]);
    } else {
      const normalOffset = index * 3;
      weldedNormals[normalOffset] += normals[positionOffset];
      weldedNormals[normalOffset + 1] += normals[positionOffset + 1];
      weldedNormals[normalOffset + 2] += normals[positionOffset + 2];
    }
    indices.push(index);
  }
  for (let index = 0; index < weldedNormals.length; index += 3) {
    const normal = new THREE.Vector3(
      weldedNormals[index],
      weldedNormals[index + 1],
      weldedNormals[index + 2],
    ).normalize();
    weldedNormals[index] = normal.x;
    weldedNormals[index + 1] = normal.y;
    weldedNormals[index + 2] = normal.z;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(weldedPositions, 3));
  geometry.setAttribute('normal', new THREE.Float32BufferAttribute(weldedNormals, 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(weldedUvs, 2));
  geometry.setIndex(indices);
  return geometry;
}

function createImplicitSurfaceGeometry(spec: ImplicitSurfaceGeometrySpec): THREE.BufferGeometry {
  const [resolutionX, resolutionY, resolutionZ] = spec.resolution;
  const minimum = spec.bounds.min;
  const maximum = spec.bounds.max;
  const step = [
    (maximum[0] - minimum[0]) / (resolutionX - 1),
    (maximum[1] - minimum[1]) / (resolutionY - 1),
    (maximum[2] - minimum[2]) / (resolutionZ - 1),
  ];
  const gridIndex = (x: number, y: number, z: number): number => (
    (z * resolutionY + y) * resolutionX + x
  );
  // Keep the sampled field in the same double precision used by Python validation.
  // Float32 can collapse a valid narrow iso range into one constant value.
  const values = new Float64Array(resolutionX * resolutionY * resolutionZ);
  for (let z = 0; z < resolutionZ; z += 1) {
    for (let y = 0; y < resolutionY; y += 1) {
      for (let x = 0; x < resolutionX; x += 1) {
        values[gridIndex(x, y, z)] = sampleImplicitSurfaceField(
          spec,
          minimum[0] + x * step[0],
          minimum[1] + y * step[1],
          minimum[2] + z * step[2],
        );
      }
    }
  }
  const cubeOffsets = [
    [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
    [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
  ];
  const tetrahedra = [
    [0, 5, 1, 6], [0, 1, 2, 6], [0, 2, 3, 6],
    [0, 3, 7, 6], [0, 7, 4, 6], [0, 4, 5, 6],
  ];
  const tetraEdges = [[0, 1], [1, 2], [2, 0], [0, 3], [1, 3], [2, 3]];
  const positions: number[] = [];
  const normals: number[] = [];
  const uvs: number[] = [];
  for (let z = 0; z < resolutionZ - 1; z += 1) {
    for (let y = 0; y < resolutionY - 1; y += 1) {
      for (let x = 0; x < resolutionX - 1; x += 1) {
        const cubePoints = cubeOffsets.map((offset) => new THREE.Vector3(
          minimum[0] + (x + offset[0]) * step[0],
          minimum[1] + (y + offset[1]) * step[1],
          minimum[2] + (z + offset[2]) * step[2],
        ));
        const cubeValues = cubeOffsets.map((offset) => values[
          gridIndex(x + offset[0], y + offset[1], z + offset[2])
        ]);
        for (const tetrahedron of tetrahedra) {
          const crossings: { point: THREE.Vector3; normal: THREE.Vector3 }[] = [];
          for (const edge of tetraEdges) {
            const firstIndex = tetrahedron[edge[0]];
            const secondIndex = tetrahedron[edge[1]];
            const firstValue = cubeValues[firstIndex];
            const secondValue = cubeValues[secondIndex];
            if ((firstValue >= spec.isoLevel) === (secondValue >= spec.isoLevel)) continue;
            const denominator = secondValue - firstValue;
            const mix = Math.abs(denominator) <= Number.EPSILON
              ? 0.5
              : THREE.MathUtils.clamp((spec.isoLevel - firstValue) / denominator, 0, 1);
            const point = cubePoints[firstIndex].clone().lerp(cubePoints[secondIndex], mix);
            if (!crossings.some((crossing) => crossing.point.distanceToSquared(point) <= 1e-20)) {
              crossings.push({ point, normal: implicitSurfaceNormal(spec, point) });
            }
          }
          if (crossings.length < 3) continue;
          const centroid = crossings.reduce(
            (result, crossing) => result.add(crossing.point),
            new THREE.Vector3(),
          ).multiplyScalar(1 / crossings.length);
          const averageNormal = crossings.reduce(
            (result, crossing) => result.add(crossing.normal),
            new THREE.Vector3(),
          );
          if (averageNormal.lengthSq() <= Number.EPSILON) {
            averageNormal.copy(crossings[0].normal);
          }
          averageNormal.normalize();
          const reference = Math.abs(averageNormal.x) < 0.8
            ? new THREE.Vector3(1, 0, 0)
            : new THREE.Vector3(0, 1, 0);
          const axisU = new THREE.Vector3().crossVectors(reference, averageNormal).normalize();
          const axisV = new THREE.Vector3().crossVectors(averageNormal, axisU).normalize();
          crossings.sort((first, second) => {
            const firstDelta = first.point.clone().sub(centroid);
            const secondDelta = second.point.clone().sub(centroid);
            const firstAngle = Math.atan2(firstDelta.dot(axisV), firstDelta.dot(axisU));
            const secondAngle = Math.atan2(secondDelta.dot(axisV), secondDelta.dot(axisU));
            return firstAngle - secondAngle;
          });
          for (let triangle = 1; triangle < crossings.length - 1; triangle += 1) {
            const ordered = [crossings[0], crossings[triangle], crossings[triangle + 1]];
            const faceNormal = new THREE.Vector3().crossVectors(
              ordered[1].point.clone().sub(ordered[0].point),
              ordered[2].point.clone().sub(ordered[0].point),
            );
            if (faceNormal.dot(averageNormal) < 0) [ordered[1], ordered[2]] = [ordered[2], ordered[1]];
            for (const vertex of ordered) {
              positions.push(vertex.point.x, vertex.point.y, vertex.point.z);
              normals.push(vertex.normal.x, vertex.normal.y, vertex.normal.z);
              const uv = implicitSurfaceUv(spec, vertex.point);
              uvs.push(uv[0], uv[1]);
            }
          }
        }
      }
    }
  }
  const geometry = createWeldedImplicitGeometry(positions, normals, uvs);
  return finalizeSpecialGeometry(geometry);
}

function sculptModifierSource(modifier: SculptSurfaceModifierSpec): ImplicitSurfaceSourceSpec {
  const operation = modifier.type === 'inflate' || modifier.type === 'ridge'
    ? 'add'
    : 'subtract';
  if (modifier.type === 'ridge' || modifier.type === 'crease') {
    if (!modifier.start || !modifier.end || !modifier.radius) {
      throw new Error(`sculpt modifier ${modifier.id} is missing capsule parameters`);
    }
    return {
      id: modifier.id,
      shape: 'capsule',
      start: modifier.start,
      end: modifier.end,
      radius: modifier.radius,
      strength: modifier.strength,
      falloff: modifier.falloff,
      operation,
    };
  }
  if (!modifier.position || (!modifier.radius && !modifier.radii)) {
    throw new Error(`sculpt modifier ${modifier.id} is missing radial parameters`);
  }
  return {
    id: modifier.id,
    shape: modifier.radii ? 'ellipsoid' : 'sphere',
    position: modifier.position,
    radii: modifier.radii,
    radius: modifier.radius,
    strength: modifier.strength,
    falloff: modifier.falloff,
    operation,
  };
}

function assertSingleClosedSurfaceGeometry(geometry: THREE.BufferGeometry): void {
  const index = geometry.getIndex();
  const positions = geometry.getAttribute('position');
  if (!index || positions.count === 0 || index.count === 0 || index.count % 3 !== 0) {
    throw new Error('sculpted-surface did not emit valid indexed triangles');
  }
  const parents = new Int32Array(positions.count);
  for (let vertex = 0; vertex < parents.length; vertex += 1) parents[vertex] = vertex;
  const vertexLinkEdges: [number, number][][] = Array.from(
    { length: positions.count },
    () => [],
  );
  const find = (value: number): number => {
    let root = value;
    while (parents[root] !== root) root = parents[root];
    while (parents[value] !== value) {
      const parent = parents[value];
      parents[value] = root;
      value = parent;
    }
    return root;
  };
  const join = (first: number, second: number): void => {
    const firstRoot = find(first);
    const secondRoot = find(second);
    if (firstRoot !== secondRoot) parents[secondRoot] = firstRoot;
  };
  const edgeCounts = new Map<number, number>();
  const countEdge = (first: number, second: number): void => {
    const low = Math.min(first, second);
    const high = Math.max(first, second);
    const key = low * positions.count + high;
    edgeCounts.set(key, (edgeCounts.get(key) ?? 0) + 1);
  };
  for (let triangle = 0; triangle < index.count; triangle += 3) {
    const a = index.getX(triangle);
    const b = index.getX(triangle + 1);
    const c = index.getX(triangle + 2);
    if (a === b || b === c || c === a) {
      throw new Error('sculpted-surface emitted a degenerate indexed triangle');
    }
    join(a, b);
    join(b, c);
    vertexLinkEdges[a].push([b, c]);
    vertexLinkEdges[b].push([c, a]);
    vertexLinkEdges[c].push([a, b]);
    countEdge(a, b);
    countEdge(b, c);
    countEdge(c, a);
  }
  const root = find(index.getX(0));
  for (let vertex = 0; vertex < positions.count; vertex += 1) {
    if (find(vertex) !== root) {
      throw new Error('sculpted-surface emitted disconnected mesh islands');
    }
  }
  for (const count of edgeCounts.values()) {
    if (count !== 2) {
      throw new Error('sculpted-surface emitted an open or non-manifold boundary');
    }
  }
  for (let vertex = 0; vertex < vertexLinkEdges.length; vertex += 1) {
    const linkAdjacency = new Map<number, Set<number>>();
    for (const [first, second] of vertexLinkEdges[vertex]) {
      if (!linkAdjacency.has(first)) linkAdjacency.set(first, new Set());
      if (!linkAdjacency.has(second)) linkAdjacency.set(second, new Set());
      linkAdjacency.get(first)?.add(second);
      linkAdjacency.get(second)?.add(first);
    }
    if (
      linkAdjacency.size < 3
      || [...linkAdjacency.values()].some((neighbors) => neighbors.size !== 2)
    ) {
      throw new Error('sculpted-surface emitted a non-manifold vertex link');
    }
    const visited = new Set<number>();
    const stack = [linkAdjacency.keys().next().value as number];
    while (stack.length) {
      const current = stack.pop() as number;
      if (visited.has(current)) continue;
      visited.add(current);
      for (const neighbor of linkAdjacency.get(current) ?? []) {
        if (!visited.has(neighbor)) stack.push(neighbor);
      }
    }
    if (visited.size !== linkAdjacency.size) {
      throw new Error('sculpted-surface emitted disconnected fans at one welded vertex');
    }
  }
}

function createSculptedSurfaceGeometry(spec: SculptedSurfaceGeometrySpec): THREE.BufferGeometry {
  if (spec.connectivity !== 'single-surface') {
    throw new Error('sculpted-surface requires single-surface connectivity');
  }
  const geometry = createImplicitSurfaceGeometry({
    bounds: spec.bounds,
    resolution: spec.resolution,
    isoLevel: spec.isoLevel,
    sources: [...spec.sources, ...spec.surfaceModifiers.map(sculptModifierSource)],
    uvProjection: spec.uvProjection,
  });
  assertSingleClosedSurfaceGeometry(geometry);
  return geometry;
}"""


_VOLUME_FIELD_TYPESCRIPT = r"""type VolumeFieldSourceSpec = {
  position: [number, number, number];
  radius: number;
  density: number;
};

type VolumeFieldGeometrySpec = {
  bounds: { min: [number, number, number]; max: [number, number, number] };
  sources: VolumeFieldSourceSpec[];
  particleCount: number;
  cardPlanes: 2 | 3;
  cardSize: [number, number];
  seed: number;
};

function createVolumeFieldGeometry(spec: VolumeFieldGeometrySpec): THREE.BufferGeometry {
  const positions: number[] = [];
  const normals: number[] = [];
  const uvs: number[] = [];
  const indices: number[] = [];
  const random = createSpecialGeometryRandom(spec.seed);
  const totalDensity = spec.sources.reduce((total, source) => total + source.density, 0);
  const chooseSource = (): VolumeFieldSourceSpec => {
    let threshold = random() * totalDensity;
    for (const source of spec.sources) {
      threshold -= source.density;
      if (threshold <= 0) return source;
    }
    return spec.sources[spec.sources.length - 1];
  };
  const withinBounds = (point: THREE.Vector3): boolean => (
    point.x >= spec.bounds.min[0] && point.x <= spec.bounds.max[0]
    && point.y >= spec.bounds.min[1] && point.y <= spec.bounds.max[1]
    && point.z >= spec.bounds.min[2] && point.z <= spec.bounds.max[2]
  );
  const baseAxes = [
    [new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 1)],
    [new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 0, 1), new THREE.Vector3(0, -1, 0)],
    [new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 1), new THREE.Vector3(1, 0, 0)],
  ];
  for (let particle = 0; particle < spec.particleCount; particle += 1) {
    const source = chooseSource();
    const center = new THREE.Vector3(source.position[0], source.position[1], source.position[2]);
    for (let attempt = 0; attempt < 32; attempt += 1) {
      const candidate = new THREE.Vector3(
        source.position[0] + sampleSpecialGeometryNormal(random) * source.radius,
        source.position[1] + sampleSpecialGeometryNormal(random) * source.radius,
        source.position[2] + sampleSpecialGeometryNormal(random) * source.radius,
      );
      if (withinBounds(candidate)) {
        center.copy(candidate);
        break;
      }
    }
    const size = THREE.MathUtils.lerp(spec.cardSize[0], spec.cardSize[1], random());
    const halfSize = size * 0.5;
    const quaternion = new THREE.Quaternion().setFromEuler(new THREE.Euler(
      random() * Math.PI * 2,
      random() * Math.PI * 2,
      random() * Math.PI * 2,
    ));
    for (let plane = 0; plane < spec.cardPlanes; plane += 1) {
      const vertexStart = positions.length / 3;
      const widthAxis = baseAxes[plane][0].clone().applyQuaternion(quaternion);
      const heightAxis = baseAxes[plane][1].clone().applyQuaternion(quaternion);
      const normal = baseAxes[plane][2].clone().applyQuaternion(quaternion).normalize();
      const corners = [[-1, -1], [1, -1], [1, 1], [-1, 1]];
      for (const corner of corners) {
        const vertex = center.clone()
          .addScaledVector(widthAxis, corner[0] * halfSize)
          .addScaledVector(heightAxis, corner[1] * halfSize);
        positions.push(vertex.x, vertex.y, vertex.z);
        normals.push(normal.x, normal.y, normal.z);
      }
      uvs.push(0, 0, 1, 0, 1, 1, 0, 1);
      indices.push(
        vertexStart, vertexStart + 1, vertexStart + 2,
        vertexStart, vertexStart + 2, vertexStart + 3,
      );
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  return finalizeSpecialGeometry(geometry);
}"""


SPECIAL_GEOMETRY_HELPERS: dict[str, str] = {
    "special-common": _SPECIAL_GEOMETRY_COMMON_TYPESCRIPT,
    "modeling-common": _MODELING_COMMON_TYPESCRIPT,
    "section-loft": _SECTION_LOFT_TYPESCRIPT,
    "conforming-shell": _CONFORMING_SHELL_TYPESCRIPT,
    "surface-scatter": _SURFACE_SCATTER_TYPESCRIPT,
    "branch-network": _BRANCH_NETWORK_TYPESCRIPT,
    "geometry-modifiers": _GEOMETRY_MODIFIERS_TYPESCRIPT,
    "deformable-surface": _DEFORMABLE_SURFACE_TYPESCRIPT,
    "fiber-system": _FIBER_SYSTEM_TYPESCRIPT,
    "implicit-surface": _IMPLICIT_SURFACE_TYPESCRIPT,
    "volume-field": _VOLUME_FIELD_TYPESCRIPT,
}


__all__ = ["SPECIAL_GEOMETRY_HELPERS"]
