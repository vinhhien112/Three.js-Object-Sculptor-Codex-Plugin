# Browser Screenshot Feedback

Use this reference when a procedural Three.js reconstruction has a browser-renderable preview.

## Capture Rule

Each visual build pass should produce at least one rendered screenshot from a named review viewpoint. Use the Codex in-app Browser screenshot tool first. Do not install or download Playwright/Chromium just for this skill; use Playwright or another browser automation path only when the user explicitly allows it or the project already depends on it. If the in-app Browser is unavailable, ask for a screenshot path or use browser tooling that is already present in the target project.

## Compare By Layer

Review screenshot evidence in this order:

1. Silhouette and proportions: bounding shape, width/height/depth cues, taper, symmetry, negative space.
2. Component structure: parent/child placement, joints, contact points, repeated systems, floating or detached parts.
3. Form detail: bevels, chamfers, curvature, bends, dents, seams, raised ridges, holes, deformation scale.
4. Surface response: albedo zones, roughness variation, metalness, clearcoat, transmission, normal/bump/displacement, ambient occlusion.
5. Local features: scratches, chips, dirt accumulation, moss, stains, color patches, edge wear, contact wear.
6. Lighting/camera: exposure, shadow softness, contact shadows, color temperature, rim light, reflection readability.
7. Performance tradeoff: whether missing detail is intentional because of triangle, draw call, texture, or FPS budgets.

## Decision Matrix

- If the screenshot reveals a missing or wrong component, choose `refine-spec`.
- If the spec describes the component but the render does not match it, choose `refine-code`.
- If the screenshot is too dark, too close, too far, or from the wrong viewpoint, choose `refine-code` for camera/lighting before judging model fidelity.
- If the source image does not reveal enough geometry or material information, choose `request-input`.
- If the screenshot matches the pass acceptance criteria and does not hide future risk, choose `continue`.

## Evidence Format

Record screenshot evidence with:

- `referenceScreenshot`: source image, crop, or marked-up reference path.
- `renderScreenshot`: browser-rendered screenshot path.
- `cameraView`: named viewpoint such as `front`, `three-quarter`, `side`, `top`, or `close-up-material`.
- `notes`: concise mismatch summary using 3D graphics terms.

Never use screenshots as decoration only. They are the ground truth for the self-correction loop.
