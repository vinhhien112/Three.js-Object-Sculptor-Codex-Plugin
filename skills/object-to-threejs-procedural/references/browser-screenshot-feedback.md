# Browser Screenshot Feedback

Use this reference when a procedural Three.js reconstruction has a browser-renderable preview.

## Capture Rule

Each visual build pass should produce at least one rendered screenshot from a named review viewpoint. Use the Codex in-app Browser screenshot tool first. Do not install or download Playwright/Chromium just for this skill; use Playwright or another browser automation path only when the user explicitly allows it or the project already depends on it. If the in-app Browser is unavailable, ask for a screenshot path or use browser tooling that is already present in the target project.

Create a side-by-side review image after capture:

```bash
python3 ../../scripts/sculpt.py compare \
  --reference reference.png \
  --render render.png \
  --out comparison.png \
  --manifest-out evidence.json \
  --json
```

For a v4 module, also pass `--sculpt-manifest object-sculpt.json --module-id <module-id>` so the evidence contains the required render receipt for the exact module and implementation snapshot.

The script aligns and packages evidence, verifies real image inputs, and hashes the exact artifacts. It must not calculate the acceptance score. A genuinely fresh Codex vision reviewer—not the builder with a renamed ID—must inspect `comparison.png`, and the verdict must bind its exact hash and both context IDs.

When `--diagnostics-dir` is used, inspect the silhouette overlay and manifest metrics to correct camera/framing before geometry. Red is reference-only, cyan is render-only, and white is overlap. Missing/empty masks and gross silhouette/framing/detail mismatch are hard vetoes; good diagnostics still cannot unlock a pass.

The layout uses contain/no-crop fitting. For multi-view passes, use `--pairs-json` and `--manifest-out`; all required views go into one contact sheet and one immutable manifest.

Every new evidence view records `referenceProvenance`. Real user/source references use `origin: observed` and `allowedUse: acceptance`. Registered ImageGen `three-quarter`/`side`/`back` hypotheses use `origin: synthetic-hypothesis` and `allowedUse: planning-veto`; they may veto cross-view silhouette/depth failures but their inferred material appearance is not truth. Required acceptance views remain observed evidence, while both module and assembled sheets may include synthetic diagnostic rows that never approve.

Use the same contact sheet to score critical semantic systems up to the configured policy cap. A normal feature is a subsystem such as a hull, cabin system, roof system, limb assembly, control panel, or sail-and-rigging system; it is not an individual mesh. Declared face and hand regions are the exception: each stays independent and requires its configured close-up view in that same contact sheet. Score up to three uncertain important features only when adaptive escalation is useful.

The starter spec contains generic review targets only as placeholders. Replace them with object-specific systems discovered during pre-spec assessment; otherwise strict quality validation should not pass a moderate or complex object.

## Compare By Layer

Review screenshot evidence in this order:

1. Silhouette and proportions: bounding shape, width/height/depth cues, taper, symmetry, negative space.
2. Component structure: parent/child placement, joints, contact points, repeated systems, floating or detached parts.
3. Form detail: bevels, chamfers, curvature, bends, dents, seams, raised ridges, holes, deformation scale.
4. Surface response: albedo zones, roughness variation, metalness, clearcoat, transmission, normal/bump/displacement, ambient occlusion.
5. Local features: scratches, chips, dirt accumulation, moss, stains, color patches, edge wear, contact wear.
6. Lighting/camera: exposure, shadow softness, contact shadows, color temperature, rim light, reflection readability.
7. Performance tradeoff: whether missing detail is intentional because of triangle, draw call, texture, or FPS budgets.

Action selection and root-cause rules live only in `self-correction-loop.md`. This file owns capture, evidence packaging, and visual scoring order.

## AI Vision Scorecard

Score each applicable layer from `0` to `1`, then assign one overall score based on the pass goal:

- `silhouetteProportion`: outer contour, mass distribution, negative space, camera-normalized proportions.
- `componentStructure`: hierarchy, placement, attachment, repeated systems, floating or disconnected parts.
- `formDetail`: taper, bend, bevel, deformation, secondary forms, local geometry.
- `materialSurface`: albedo, roughness, reflectance, normal/displacement, AO, local wear, tactile frequency.
- `lightingCamera`: camera match, exposure, key/fill/rim balance, shadow/contact response, background.

Do not hide a critical failed layer inside a high average. If a layer is essential to the current pass and remains visibly wrong, choose `refine-spec` or `refine-code` even when the arithmetic mean is above threshold; use one `refine-batch` when the complete fix spans both.

## Feature Tiers

- `critical`: identity-defining, user-prioritized, visually salient, or high-risk subsystem. It must be visible and pass independently; face/hand targets must also bind their dedicated `viewIds`.
- `important`: useful secondary subsystem. Review only suspicious items; the reviewed average must meet the configured threshold.
- `detail`: micro detail. Record mismatch notes and defer to refinement unless the user promotes it.

Repeated parts should be one target when they form one recognizable system. For example, review three cabins as `cabin-system`, not three separate cabin targets.

## Evidence Format

Record each item in `evidence.views` with:

- `viewId`: the required view name.
- `referenceImage`: source image, crop, or marked-up reference path.
- `renderScreenshot`: browser-rendered screenshot path.
- `comparisonImage`: side-by-side evidence image reviewed by AI vision.

Record review-level fields separately:

- `aiVisionScore`: overall score from `0` to `1`.
- `layerScores`: per-layer scores from the scorecard.
- `aiVisionNotes`: concrete matched features, mismatches, root causes, and next correction.
- `featureReviews`: feature ID, score, visibility in the contact sheet, focused notes, and `viewIds` for targets that require dedicated evidence.

Never use screenshots as decoration only. They are the ground truth for the self-correction loop.
