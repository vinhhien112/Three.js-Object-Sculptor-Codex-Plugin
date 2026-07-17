---
name: object-to-threejs-procedural
description: Use when the user provides or references an object image and wants Codex to validate whether it can be reconstructed in Three.js, then author a composable procedural sculpt spec, generate geometry/material/lighting code, and validate the real render against the reference.
---

# Object to Three.js Procedural

Create an editable procedural Three.js approximation from reference images. Treat it as code-native reconstruction, not photogrammetry, exact mesh recovery, or physically exact PBR inversion.

## Required outcome

1. Inspect the real reference and decide `pass`, `conditional`, or `reject`.
2. Write one global contract, then one spec per semantic module as that module is built.
3. Build and validate the highest-risk ready module first.
4. Assemble only modules whose current content hash has passed its gate.
5. Run the existing blockout/form/lookdev/runtime/optimization gates on the assembled model.

Never claim hidden geometry as observed fact. Label approximations and ask for better evidence only when it can change the result.

## Inputs and quality choice

Require at least one inspectable image. If intended use is missing, use `browser-prop`; if complexity is unclear, start at `moderate` and revise after inspection.

Choose the quality profile explicitly. “Game quality”, realistic, sharp, hero asset, or close-reference requests require `reference-fidelity`. Use `game-prop` for rigid real-time assets and `static-render` for still-only work. Do not silently downgrade either choice.

## Command surface

From this skill directory run:

```bash
python3 ../../scripts/sculpt.py <command>
```

Primary commands are `init`, `views`, `module`, `validate`, `status`, `check`, `generate`, `compare`, `review`, `probe`, `pbr`, and `migrate`. Individual scripts and `--layout monolithic` remain compatibility paths only.

## Workflow

### 1. Inspect and write the global contract

Inspect silhouette, macro hierarchy, negative spaces, attachments, repeated systems, material families, occlusion, and intended behavior. Identify perceptually fragile or technically uncertain subsystems such as a face, interacting hand, thin structure, transparent surface, dense fibers, deformable fabric, or unusual joint.

Create the v4 root manifest:

```bash
python3 ../../scripts/sculpt.py init "Object Name" \
  --image <reference> \
  --complexity <simple|moderate|complex|ultra> \
  --intended-use <static-render|browser-prop|game-prop|animated|playable|destructible> \
  --quality-profile <balanced|reference-fidelity> \
  --out object-sculpt.json
```

Fill `globalSpec.preSpecAssessment`, silhouette, coordinate frame, quality contract, source observations, known risks, and `surfaceTopologyPlan` before creating geometry modules. Classify each visible system as `continuous-sculpt`, `assembled-solid`, `conforming-shell`, `surface-relief`, `fiber-strand`, or `material-only`. Semantic landmarks may share one mesh. A new visual module is refused until the plan is `planned` and contains a group whose `ownerModuleId` matches that module. The manifest initially contains only the global assembly root; this is intentional.

If `viewHypothesisPolicy.enabled` is true, use built-in ImageGen once per required named view at this point—not once per pass. Preserve identity and generate only the requested `three-quarter`, `side`, or `back` hypothesis without redesigning the object. Register the resulting files once; `views status` reuses them while the source hash and prompt version remain unchanged:

```bash
python3 ../../scripts/sculpt.py views register object-sculpt.json \
  --view three-quarter=review/three-quarter.png \
  --view side=review/side.png \
  [--view back=review/back.png]
python3 ../../scripts/sculpt.py views status object-sculpt.json
```

These images are cached planning/veto evidence. They can expose bad depth, silhouette, or stacked-blob geometry, but inferred color/roughness/detail is not material truth and cannot veto lookdev. They can never approve a module or pass. If ImageGen changes an existing cached view, increment the prompt version explicitly instead of silently replacing evidence.

### 2. Add semantic modules

Decompose by independently reviewable systems, not by arbitrary mesh count. Examples include `face-identity`, `gripping-hand`, `instrument`, `body-clothing`, `tail`, or `hard-surface-core`.

Do not turn every named region into a mesh. Preserve a single surface when the reference shows an uninterrupted material/form transition: head, cheeks, muzzle, jaw, muscle masses, rock bulges, and branch junctions normally belong to one continuous host. Separate only observed seams, sockets, articulation boundaries, accessories, shells, or real strands. Put silhouette-changing relief into the host surface; use material response for sub-silhouette microdetail.

```bash
python3 ../../scripts/sculpt.py module add object-sculpt.json <module-id> \
  --role "<semantic responsibility>" \
  --risk-score <0..100> \
  --gate-type <visual|structural> \
  [--covers <global-feature-group-id>] \
  [--depends-on <module-id>] \
  [--template foundation]
```

Keep dependencies minimal. A module may parent into `root`, or into a dependency only through that dependency’s exported connector. Component, material, repetition, feature-target, and specialized-region IDs must be globally unique. Use `structural` only for assembly/interface nodes and connectors; any module that owns visible geometry, materials, repetitions, evidence, or specialized regions must use `visual`.

`--template foundation` is only an editable visual scaffold. Replace its explicit placeholder geometry/material text with observed decisions; `--strict-quality` refuses an untouched scaffold. In every visual module, list the exact project-relative runtime sources/assets that create its render under `contract.implementationFiles`. At least one declared runtime source must contain the executable ownership marker `export const SCULPT_MODULE_ID = "<module-id>";`; a comment or another module's source is rejected. That marker is ownership metadata, not proof of what was rendered: the app must instantiate the generated module's stable `createSculptModel` export.

Use face/hand modules when visible, but keep the architecture general: any difficult subsystem can be isolated and reviewed first. Do not create one builder sub-agent per module by default. After a visual comparison exists, use one fresh reviewer sub-agent that did not build the module; give it only the raw reference, render/contact sheet, and contracts—not the builder’s proposed score or defense.

### 3. Build the hardest ready module first

```bash
python3 ../../scripts/sculpt.py module context object-sculpt.json
```

`module context` selects the highest-risk ready module and returns one hash-aware work packet: module/dependency/runtime paths, only files changed since the previous context call, directly readable relevant reference paths, required views/layers, and any pending correction batch. References are part of the hash-tracked `files`/`readFiles` set, so read only the listed `readFiles` paths in one parallel tool call. Do not reopen an unchanged file when `cacheHit=true`; use `module status` or individual reads only for diagnosis after a concrete failure. A structural module returns one `accept` action instead of the visual build/evaluate/review route; `module accept` runs the same strict module check internally.

Author the complete module or pending correction batch before running validation. Then strict-check, resolve, validate, and generate with one fail-fast command:

```bash
python3 ../../scripts/sculpt.py module build object-sculpt.json <module-id>
```

The default outputs are `.sculpt-preview/<module-id>.json`, `.sculpt-preview/<module-id>.build.json`, and `src/generated/<module-id>.generated.ts`. `module build` invokes the same strict module check, resolver, spec/pass validation, unlock check, and generator as the individual commands; it stops at the first failed stage and reports that stage without weakening any gate. The build receipt binds the current module/spec to the exact generated factory hash and factory ID.

Fix structural/schema failures before rendering. A visual module cannot pass with executable `fidelityTier: blockout` parts. Capture every required and diagnostic view and create one no-crop comparison manifest. Run deterministic preflight before creating a reviewer sub-agent; a failed preflight goes directly back to the builder and spends no reviewer call. Only after it passes, spawn one fresh reviewer sub-agent with no builder rationale or proposed scores. Give it the raw reference, render/contact sheet, evidence hash, and module contract. The reviewer returns the structured verdict described in `references/self-correction-loop.md`.

Use the smallest real geometry system that matches the form. Prefer `sculpted-surface` for one irregular connected mass with embedded bulges/ridges/creases, `section-loft` for continuous forms governed by ordered cross-sections, `conforming-shell` for fitted static layers, `branch-network` for tapered branching layouts whose overlapping junctions are acceptable, and `surface-scatter` for masked repeated details. A hero tree, horn junction, or organic branch that must be topologically fused uses `sculpted-surface`, not `branch-network`. A `sculpted-surface` combines sphere/ellipsoid/capsule sources plus local `inflate`, `pinch`, `ridge`, and `crease` operations into one welded indexed mesh; its single-surface connectivity and closed bounds are validated before generation. Linked shells/scatter must be identity-transform children of their `section-loft` host; put the host's final fitted form in its sections instead of adding a later deformation stack. Generic bend/taper/bulge/twist/noise modifiers remain available for standalone parts.

After the real app has instantiated `createSculptModel`, attach its root to the rendered `THREE.Scene`. At the same scene state used for screenshots, save the JSON returned by `window.__THREEJS_SCULPT_CAPTURE_RUNTIME__()` to `review/<module-id>-runtime.json`. Review-only ground/contact-shadow meshes may use `userData.reviewOnly = true`; any other intentional environment mesh must use `userData.sculptValidationRole = "environment"`. Do not hide the generated root or place an untracked substitute model in front of it.

After the real app has captured all required views and the runtime receipt, create the comparison and run deterministic preflight together:

```bash
python3 ../../scripts/sculpt.py module evaluate object-sculpt.json <module-id> \
  --pairs-json review/<module-id>-pairs.json \
  --runtime-receipt review/<module-id>-runtime.json

# Only when evaluate reports ok=true: spawn a fresh reviewer sub-agent.
python3 ../../scripts/sculpt.py module review object-sculpt.json <module-id> \
  --verdict-json review/<module-id>-verdict.json \
  --evidence-manifest review/<module-id>-evidence.json
```

Keep one command budget per module work cycle: one `context`, one complete edit/patch batch, one `build`, one application typecheck if the project build does not already include it, one render capture, one `evaluate`, then one independent `review`. Do not typecheck, build, render, or reread files between individual edits. Repeat a stage only after its inputs changed or its previous failure produced a new falsifiable fix. The individual `status`, `check`, `resolve`, `generate`, `compare`, and `preflight` commands remain debugging/compatibility paths, not the default workflow.

One `module review` call records either a rejected/refine attempt or a passing acceptance; do not add a second bookkeeping command. A refine verdict must enumerate every currently visible actionable issue and produces one atomic `pendingCorrectionBatch`. Apply the entire batch before rendering again—never render/review between individual corrections. Use `refine-batch` with per-correction `scope: spec|code` when both change. `module status.correctionBatchProgress` must report `readyToRender: true` before capture; otherwise keep editing the same batch. A passing preflight writes a hash-bound receipt, while an incomplete/no-op batch is rejected before spending a reviewer call. The workflow preserves the reviewed render baseline inside `.sculpt-cache`, so normal fixed output filenames may be overwritten safely. At most two atomic batches are allowed per strategy. If both fail, record one `strategy-reset` tied to the stable blocker `rootCauseKeys`, explain the different representation and its falsifying check, then make a material spec or executable change before one new render. Do not ask the user merely because the batch budget ended: `request-input` requires concrete missing evidence and the exact criterion it blocks; `stop` requires verified capability evidence. `continue` is refused when diagnostics are invalid, a required feature fails, a blocking issue remains open, or refinement lacks a perceptible improvement. Risk/profile score floors, required visual layers, and diagnostic veto floors cannot be lowered inside a module. For an assembly-only structural gate use `module accept`. Acceptance is fail-closed and stored in `.sculpt-cache`; visual records bind the exact generated factory/live scene, declared implementation snapshot, module, verdict, evidence, render receipt, and dependency interfaces by hash.

Do not use ImageGen output as reference truth or passing evidence. Registered synthetic views must remain `synthetic-hypothesis` + `planning-veto`; observed source/render evidence remains authoritative.

### 4. Assemble and run the normal quality passes

Final validation and generation remain locked until every required module has a current acceptance, every required feature group has exactly one explicit owner mode (`--covers` on one visual module or `coverageContract.assemblyFeatureGroups` with a matching critical assembled-pass target), and the fully resolved spec passes strict validation. Module acceptance means only “ready to assemble”, not “asset complete”.

```bash
python3 ../../scripts/sculpt.py validate object-sculpt.json \
  --for-pass <current-pass> --strict-quality

python3 ../../scripts/sculpt.py generate object-sculpt.json \
  --out src/generated/Object.generated.ts \
  --wrapper-out src/Object.ts
```

Once modules are accepted, `status` includes the assembled pass workflow. Run the real application and `compare`, then run deterministic preflight:

```bash
python3 ../../scripts/sculpt.py review object-sculpt.json \
  --pass-id <current-pass> \
  --evidence-set-json review/<pass>-evidence.json \
  --preflight-only
```

Only when it reports `ok: true`, spawn one fresh reviewer sub-agent and record its verdict unchanged:

```bash
python3 ../../scripts/sculpt.py review object-sculpt.json \
  --pass-id <current-pass> \
  --evidence-set-json review/<pass>-evidence.json \
  --verdict-json review/<pass>-verdict.json \
  --in-place
```

For modular visual passes, manual `--ai-vision-score` or `--reviewer-model` input is not acceptance authority. The pass verdict must bind the current pass/spec/comparison hashes, a current preflight receipt, and different builder/reviewer context IDs. The receipt is consumed after the verdict is recorded, so a new attempt needs a new preflight. Keep `*.generated.ts` generator-owned and hand-written integration in the wrapper.

The adaptive pass plan remains:

- `blockout`: silhouette, framing, masses, proportions;
- `structure`: only for complex/ultra hierarchy and contacts;
- `form`: recognizable geometry and local form;
- `lookdev`: material, special surface, lighting, and contact shadow;
- `interaction`: only for animated/playable/destructible use;
- `optimization`: only for real-time use, with measured metrics and a fresh visual no-regression review.

Do not add empty passes to simulate rigor. The useful gates are module, assembly, and final visual/runtime quality.

## Non-negotiable rules

- Use named assemblies and geometry-bearing parts; keep one acyclic global root.
- Use geometry for silhouette-changing forms. Unsupported primitives or modes must fail, never silently become boxes.
- Bind appendages with parent/socket/endpoints/contact/overlap/gap data and inspect hidden joints from useful angles.
- Keep albedo, roughness, normal/height, and AO independent. A material crop can provide inferred PBR evidence, not physical truth.
- Model visible face and hand landmarks as real named geometry regions with independent close-up gates; do not accept a generic face sphere or hand blob.
- Treat landmark/region names as semantic review handles, not automatic mesh boundaries. Enforce every planned `continuous-sculpt` group as one connected host and every `surface-relief` group as embedded in that host.
- Treat static cloth, fibers, glass, liquid, and volume as explicit bounded approximations; do not imply simulation, strand grooming, caustics, or raymarched scattering.
- Keep fitted shells and surface scatter tied to an undeformed final `section-loft`; never accept a visually detached layer or stacked-blob substitute.
- Diagnostics may veto obvious framing/silhouette/detail failures but cannot approve a visual gate.
- Run deterministic preflight before spawning a reviewer; do not spend independent review on evidence that already fails hashes, provenance, required views, or pixel vetoes.
- A builder must not author, rewrite, rescore, or override the independent verdict used by `module review` or modular assembled `review`.
- `continue` requires current, hash-bound evidence and every applicable critical feature threshold.

## Completion gate

Do not claim completion until all modules are accepted, the assembled spec validates, generated TypeScript compiles with `three`, the real app loads without relevant errors, every selected pass is complete, all visual evidence is bound to the reviewed artifact, and runtime/metric passes contain real proof.

If any check cannot run, state that limitation instead of implying success.

## Reference routing

For module work, follow the `module context.references` list and load those files together; do not preload all references. Outside a module, read only what the current work requires:

- suitability, complexity, and global quality contract: `references/pre-spec-assessment.md`;
- geometry and representation patterns: `references/procedural-patterns.md`;
- face and hand contracts: `references/anatomical-regions.md`;
- attachment correctness: `references/attachment-joint-correctness.md`;
- material, PBR, and lighting: `references/material-lighting-realism.md`;
- interaction/physics/destruction: `references/action-ready-models.md`;
- screenshot evidence and score layers: `references/browser-screenshot-feedback.md`;
- root-cause and next-action choice: `references/self-correction-loop.md`;
- terminology: `references/3d-graphics-terminology.md`.
