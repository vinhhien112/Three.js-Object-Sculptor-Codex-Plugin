# Three.js Object Sculptor

Turn the object in an attached image into a quality-gated, animation-ready procedural Three.js model built entirely with code.

Three.js Object Sculptor is a Codex plugin for rebuilding the visible object in a user-provided attachment image as a code-only Three.js model. It does not try to do photogrammetry, download an art pack, or extract a perfect mesh from one image. Instead, it guides Codex through a sculpting workflow: validate the image, describe the object precisely, decompose it into geometry and material systems, build from blockout to detail, wire an animation-friendly hierarchy, then compare the browser render against the original reference.

## Demo

### Tower Ship

[Open the live tower ship demo](https://3dship.harrysoftware.com)

![Procedural Three.js tower ship demo generated from an attached reference image](assets/tower-ship-demo.png)

This tower ship study shows the intended output shape: a browser-rendered, code-sculpted Three.js object rebuilt from an attached reference image, with procedural geometry, articulated parts, material work, and interactive controls.

### Ancient Autumn Tree

[Open the live ancient autumn tree demo](https://tree.harrysoftware.com/)

![Procedural Three.js ancient autumn tree reconstructed from an attached reference image](assets/ancient-autumn-tree-demo.png)

This botanical study reconstructs a complex ancient tree with procedural curves, deterministic branching, layered bark materials, dense autumn foliage, and an animation-ready hierarchy.

## At A Glance

- **Name:** Three.js Object Sculptor
- **Category:** Codex plugin for image-to-procedural-3D workflows
- **Input:** an attached object image, reference screenshot, or local image path
- **Output:** a code-only procedural Three.js object factory, backed by an `ObjectSculptSpec`
- **Primary goal:** recreate the target object's silhouette, component structure, materials, lighting response, and action-ready hierarchy in browser-friendly Three.js code
- **Best for:** animation-ready real-time props, game objects, scene dressing, destructible objects, product-style objects, botanical objects, mechanical parts, and stylized reference reconstructions
- **Not for:** photogrammetry, exact mesh extraction, scanned assets, downloaded art packs, or guaranteed production-perfect geometry from one image

## What It Does

- Validates whether an image is suitable for procedural 3D reconstruction.
- Integrates the pre-spec complexity assessment into the main `ObjectSculptSpec` before code generation.
- Writes an `ObjectSculptSpec` with component hierarchy, materials, lighting, pivots, sockets, animation anchors, destruction anchors, and quality targets.
- New specs use a v4 root manifest plus independently authored module specs; the highest-risk ready module is validated first and accepted modules are reused by content/interface hash.
- New visual modules require a global surface-topology decision first, so continuous forms, real assemblies, fitted shells, relief, fibers, and material-only detail are not confused with arbitrary mesh count.
- Supports compound objects through nested `assembly` groups plus geometry-bearing `part` nodes.
- Uses one geometry registry for validation and generation, including tube, lathe, extrude, curve sweep, section lofts, fitted shells, branch networks, masked surface scatter, modifiers, and bounded instancing; unsupported geometry is rejected instead of becoming a box silently.
- Supports bounded `sculpted-surface` fields that fuse irregular masses and embedded ridges/creases into one connectivity-checked welded mesh.
- Adds opt-in static approximations for organic bodies, fitted cloth/layers, trees/roots/horns, hair/fur, smooth merged forms, glass/liquid, and soft volumes through bounded special geometry and material profiles; physics simulation and raymarched volumes remain out of scope.
- Uses an adaptive pipeline: blockout, form, and lookdev always run; structure, interaction, and optimization run only when the object's complexity or intended use requires them.
- Provides one `scripts/sculpt.py` command surface while keeping the older individual scripts compatible.
- Generates a code-only Three.js factory skeleton from the current unlocked sculpt pass.
- Designs the generated object as an action-ready hierarchy, so later animation, transformation, physics, or destruction requests have real pivots and attachment points to use.
- Packages reference/render screenshots into one comparison sheet for AI vision review.
- Adds diagnostic-only silhouette IoU, framing deltas, contour overlays, and camera-first correction hints; AI vision remains the acceptance authority.
- Records self-correction reviews with overall, layer, and critical feature scores.
- Supports reference-derived procedural PBR evidence: albedo, roughness estimate, height, normal, and AO maps.
- Emits bounded rounded-box edge treatment and supported local seam/ridge/stitch/button/rivet/decal geometry instead of leaving those details as metadata.

## Use Cases

- Convert an attached object image into a procedural Three.js model generated entirely with TypeScript and geometry code.
- Build animation-ready Three.js props with meaningful pivots, sockets, parent-child hierarchy, and transform anchors.
- Recreate reference objects as browser-friendly procedural assets without relying on downloaded meshes or external art packs.
- Generate a structured object spec before implementation, so Codex understands geometry, materials, lighting, local surface features, and interaction readiness.
- Create destructible or transformable objects by planning detachable parts, fracture seams, colliders, and effect emitters before the model is coded.
- Compare the rendered model against the original attachment with AI vision and block progress when critical features do not match.
- Produce reusable procedural object factories for Three.js games, WebGPU demos, interactive prototypes, and visual experiments.

## Why This Exists

Procedural 3D generation can fail in a very specific way: the silhouette is "kind of right", but the object loses the details that make it recognizable. This plugin is designed to slow Codex down at the right moments:

- First understand what object class and complexity tier it is dealing with.
- Define what "good enough" means for this specific object.
- Build from coarse structure to fine surface response.
- Fail a pass if an identity-defining feature is wrong, even when the overall score looks acceptable.

The result is less "one-shot generated mesh" and more "Codex as a procedural sculptor with checkpoints": block out the form, attach the moving parts correctly, layer the materials, then keep refining until the model reads like the object in the attachment.

## Requirements

- Codex with local plugin support.
- Python 3.10 or newer.
- A browser project using Three.js when you want to implement the generated factory.
- For visual acceptance: a screenshot from the rendered model and an AI vision reviewer.

The helper scripts use Python standard-library modules and shell image tooling when available. They do not require Playwright or a downloaded Chromium bundle.

## Install For Codex

Clone the plugin source into your local plugin folder. Replace `REPOSITORY_URL` with the Git URL for your copy of this repository:

```bash
mkdir -p ~/plugins
git clone REPOSITORY_URL ~/plugins/threejs-object-sculptor
```

Make sure your local Codex marketplace has an entry for the plugin. If you already have `~/.agents/plugins/marketplace.json`, add this object to its `plugins` array:

```json
{
  "name": "threejs-object-sculptor",
  "source": {
    "source": "local",
    "path": "./plugins/threejs-object-sculptor"
  },
  "policy": {
    "installation": "AVAILABLE",
    "authentication": "ON_INSTALL"
  },
  "category": "Productivity"
}
```

If you do not have a local marketplace file yet, create `~/.agents/plugins/marketplace.json` with:

```json
{
  "name": "local",
  "interface": {
    "displayName": "Local Plugins"
  },
  "plugins": [
    {
      "name": "threejs-object-sculptor",
      "source": {
        "source": "local",
        "path": "./plugins/threejs-object-sculptor"
      },
      "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL"
      },
      "category": "Productivity"
    }
  ]
}
```

Install it in Codex:

```bash
codex plugin add threejs-object-sculptor@local
```

Start a new Codex thread after installation so the plugin skill is loaded.

## Quick Start

In Codex, attach an object image and ask:

```text
Use Three.js Object Sculptor to turn the object in this attachment into a procedural Three.js model built entirely with code.
```

![Codex prompt example using an attached object image with Three.js Object Sculptor and Browser](assets/codex-prompt-example.png)

For best results, include the intended use:

```text
Make it a real-time browser prop, action-ready for animation, transformation, physics, and destruction.
```

The plugin will guide Codex through:

1. Image suitability check.
2. Pre-spec complexity and quality contract.
3. Risk-ranked semantic module specs.
4. Module-level validation and cache.
5. Final assembly and normal build passes.
6. Browser screenshot review.
7. AI vision comparison and self-correction.

## Adaptive Workflow

New work uses one root manifest and one command entry point. Module files are created only when their block is ready to build:

```bash
python3 scripts/sculpt.py init "Ancient Autumn Oak" \
  --image ./reference/oak-tree.png \
  --complexity complex \
  --intended-use browser-prop \
  --quality-profile balanced \
  --out object-sculpt-spec.json

python3 scripts/sculpt.py views register object-sculpt-spec.json \
  --view three-quarter=review/oak-three-quarter.png \
  --view side=review/oak-side.png \
  --view back=review/oak-back.png

python3 scripts/sculpt.py status object-sculpt-spec.json

# Before adding a visual module, edit globalSpec.surfaceTopologyPlan:
# set status="planned" and add a complete group owned by "rigid-core".
# The plugin intentionally refuses visual modules until this construction
# decision distinguishes continuous form, real seams, shells, relief, and fibers.
python3 scripts/sculpt.py module add object-sculpt-spec.json rigid-core \
  --role "visible trunk and branch foundation" \
  --risk-score 60 \
  --gate-type visual \
  --template foundation

# Read the returned changed-file list once, then edit the complete module batch.
python3 scripts/sculpt.py module context object-sculpt-spec.json

# Set contract.implementationFiles and put
# `export const SCULPT_MODULE_ID = "rigid-core";` in its runtime source.
python3 scripts/sculpt.py module build object-sculpt-spec.json rigid-core
# After capturing all required render views:
python3 scripts/sculpt.py module evaluate object-sculpt-spec.json rigid-core \
  --pairs-json review/rigid-core-pairs.json \
  --runtime-receipt review/rigid-core-runtime.json
python3 scripts/sculpt.py module review object-sculpt-spec.json rigid-core \
  --verdict-json review/rigid-core-verdict.json \
  --evidence-manifest review/rigid-core-evidence.json

python3 scripts/sculpt.py generate object-sculpt-spec.json \
  --out src/AncientOak.generated.ts \
  --wrapper-out src/AncientOak.ts
```

Pre-spec has not been removed; it lives in `globalSpec.preSpecAssessment`. Final generation stays locked until required modules have current hash-bound acceptance records in the ignored `.sculpt-cache` directory. Every active visual pass still requires an original/render comparison and AI review.

ImageGen unseen views are registered once per source/prompt and reused from `.sculpt-cache`; they are veto-only and can never approve a gate. The default fast path uses one hash-aware `module context`, one fail-fast `module build` (strict check + resolve + validate + generate), and one `module evaluate` (compare + deterministic preflight); the individual commands remain available for diagnosis. The app must render the generated `createSculptModel` root and save `window.__THREEJS_SCULPT_CAPTURE_RUNTIME__()` beside its screenshots; evaluate binds that live scene receipt to the current generated factory and rejects hidden roots or untracked substitute meshes before reviewer creation. Review corrections are returned as one atomic batch: all known spec/code fixes are applied before a single rerender and rereview. Reviewed renders are snapshotted automatically, so fixed output filenames may be reused safely. Incomplete, comment-only, reused, or imperceptible batches fail before the reviewer call; a strategy gets two atomic batches, then one bounded `strategy-reset` must materially change representation. `request-input` requires concrete missing evidence rather than exhausted budget. Stable root-cause keys prevent relabeling the same visible defect as “resolved.” Diagnostics are recomputed from pixels instead of trusted from JSON. Modular assembled visual passes use the same preflight-first rule and require a hash-bound independent `--verdict-json`; manual builder scores cannot approve them. Non-lowerable floors still reject weak scores/diagnostics, blockout fidelity, failed critical features, incomplete ownership, stale code, and shallow assembly. `module accept` remains only for assembly/interface-only structural modules.

Legacy schema 2.0/3.0 specs remain readable. Upgrade deliberately with `python3 scripts/sculpt.py migrate <spec> --in-place`; existing review evidence is retained for audit but is not rewritten to manufacture a passing review.

Run `python3 scripts/sculpt.py --help` for the `compare`, `review`, `probe`, and `pbr` commands. The individual scripts below remain available for compatibility.

## Compatibility / Individual Scripts

The unified `sculpt.py` flow above is recommended. The individual scripts remain available for existing schema 3.1 workflows.

Probe a reference image:

```bash
python3 scripts/probe_reference_image.py ./reference/oak-tree.png
```

Create a pre-spec assessment:

```bash
python3 scripts/new_pre_spec_assessment.py "Ancient Autumn Oak" \
  --image ./reference/oak-tree.png \
  --complexity complex \
  --intended-use browser-prop \
  --quality-profile balanced \
  --out assessment.json
```

Create a starter sculpt spec:

```bash
python3 scripts/new_sculpt_spec.py "Ancient Autumn Oak" \
  --image ./reference/oak-tree.png \
  --assessment assessment.json \
  --intended-use browser-prop \
  --quality-profile balanced \
  --layout monolithic \
  --out object-sculpt-spec.json
```

Validate the spec:

```bash
python3 scripts/validate_sculpt_spec.py object-sculpt-spec.json \
  --for-pass blockout \
  --strict-quality
```

Check which sculpt pass is unlocked:

```bash
python3 scripts/sculpt_pass_orchestrator.py status object-sculpt-spec.json
```

Generate the current pass:

```bash
python3 scripts/generate_threejs_factory.py object-sculpt-spec.json \
  --out src/AncientOak.generated.ts \
  --wrapper-out src/AncientOak.ts
```

Create a comparison sheet after rendering the model:

```bash
python3 scripts/make_visual_comparison_sheet.py \
  --reference ./reference/oak-tree.png \
  --render ./screenshots/oak-render.png \
  --out ./screenshots/oak-comparison.png \
  --manifest-out ./screenshots/oak-evidence.json \
  --diagnostics-dir ./screenshots/oak-diagnostics \
  --json
```

The diagnostic overlay uses red for reference-only silhouette, cyan for render-only silhouette, and white for overlap. These metrics help fix camera/framing and visible detail, can veto an obvious mismatch, but cannot approve a pass.

Record an AI vision review:

```bash
python3 scripts/append_sculpt_review.py object-sculpt-spec.json \
  --pass-id blockout \
  --fidelity 0.82 \
  --action continue \
  --summary "Blockout silhouette and primary trunk fork are acceptable." \
  --evidence-set-json ./screenshots/oak-evidence.json \
  --ai-vision-score 0.82 \
  --reviewer-model example-vision-model \
  --layer-scores-json '{"silhouette":0.79}' \
  --feature-reviews-json ./reviews/blockout-features.json \
  --ai-vision-notes "Main proportions pass; canopy microstructure remains deferred." \
  --in-place
```

Sync the pass state:

```bash
python3 scripts/sculpt_pass_orchestrator.py sync object-sculpt-spec.json --in-place
```

## PBR Extraction

The plugin can extract reference-derived procedural PBR evidence from image pixels:

```bash
python3 scripts/extract_reference_pbr.py ./reference/oak-bark.png \
  --material-crop-confirmed \
  --mask ./reference/oak-bark-mask.png \
  --out-dir ./generated/pbr/oak-bark \
  --material-id bark \
  --target-threshold 0.75 \
  --report ./generated/pbr/oak-bark/report.json
```

This produces useful material evidence such as palette, albedo, roughness estimate, height, normal, and AO maps. It uses broad/meso/micro de-lighting and tile-safe border blending; `--mask` is optional but useful when the crop still contains other materials. It is not exact inverse rendering from a single image. An unconfirmed full screenshot remains diagnostic and cannot unlock lookdev, even when its extraction score is high.

## Quality Gates

The plugin uses two levels of visual acceptance:

- Overall match: silhouette, proportions, camera/view, material read, and lighting.
- Semantic feature match: selected critical object features scored from the same full reference/render comparison image.

Examples of critical feature targets:

- Hull shape, cabin blocks, sail rigging, and rails for a boat.
- Trunk fork, major branch sockets, canopy mass, bark material, and root flare for a tree.
- Body shell, wheels, windshield, grille, and headlight clusters for a vehicle.
- Face identity/expression and each visible hand or hand-to-object contact for a character; these use dedicated close-up views and cannot be hidden inside the overall score.

If a critical feature fails its threshold, the pass fails even if the global score is high.

New `reference-fidelity` specs use an overall/critical target of `0.85`, stronger form/material layer gates, hash-bound review evidence, and the same adaptive pass count. Both profiles require an explicit selection; real-time optimization also requires a fresh no-regression visual review.

## FAQ

### Is this photogrammetry?

No. Three.js Object Sculptor does not reconstruct a scanned mesh from pixels. It helps Codex infer a procedural model plan and generate Three.js code that approximates the visible object.

### Does it generate a GLB file?

Not by default. The main output is a code-only Three.js factory and an `ObjectSculptSpec`. You can add export tooling in the target Three.js project if you later need GLB output.

### Can the generated model be animated?

Yes. Animation readiness is a core goal. The spec asks for pivots, sockets, parent-child hierarchy, transform channels, collider proxies, and detachable or breakable component roles where relevant.

### Does it use downloaded assets or art packs?

No. The workflow is designed around generated geometry, procedural materials, local image evidence, and code-native Three.js construction.

### Can one image create an exact production model?

No. One image can be enough for a useful procedural reconstruction, but hidden sides, exact dimensions, and fine material behavior may need assumptions, extra reference views, or a lower-fidelity target.

### How does the plugin decide whether the model is good enough?

It uses a quality contract, adaptive build passes, browser screenshots, one reference/render comparison sheet, and AI vision review. Critical features can fail a pass even when the global visual score looks acceptable.

## Project Layout

```text
.codex-plugin/plugin.json
skills/object-to-threejs-procedural/SKILL.md
skills/object-to-threejs-procedural/references/
scripts/
```

Important scripts:

- `sculpt.py`: unified command entry point for new work.
- `sculpt_contract.py`: shared adaptive pass, evidence, and state rules.
- `sculpt_manifest.py`, `sculpt_module_contract.py`, `sculpt_module_state.py`, and `sculpt_module_review.py`: v4 composition, contracts, risk scheduling, independent review attempts, and cache validity.
- `sculpt_geometry.py`: shared geometry handlers, parameter checks, repetition limits, and TypeScript emitters.
- `sculpt_specialized_regions.py`: face/hand landmark, hierarchy, occlusion, contact, and close-up review contracts.
- `migrate_sculpt_spec.py`: explicit additive migration to schema 3.1.
- `probe_reference_image.py`: technical image metadata probe.
- `new_pre_spec_assessment.py`: compatibility wrapper for the integrated pre-spec.
- `new_sculpt_spec.py`: starter `ObjectSculptSpec` with integrated pre-spec.
- `validate_sculpt_spec.py`: structural and strict quality validation.
- `sculpt_pass_orchestrator.py`: pass locking and pipeline sync.
- `generate_threejs_factory.py`: current-pass Three.js factory generator.
- `make_visual_comparison_sheet.py`: full reference/render comparison image.
- `append_sculpt_review.py`: self-correction review recorder.
- `extract_reference_pbr.py`: reference-derived PBR evidence extraction.
- `sculpt_image_io.py`: shared dependency-free image codec used by comparison and PBR extraction.

## Limitations

- A single image cannot reveal hidden sides or guarantee exact geometry.
- Transparent glass, smoke, liquid, fur, fine cloth, and exact likeness tasks may require extra references or a lower-fidelity target.
- The generated factory is a starting point for procedural construction, not a finished asset pipeline replacement.
- AI vision review is expected for acceptance; the scripts package evidence but do not magically judge visual quality by themselves.

## Development Notes

After changing the plugin, update the cachebuster and reinstall. If you have Codex's `plugin-creator` skill installed, use its `update_plugin_cachebuster.py` helper:

```bash
python3 /path/to/plugin-creator/scripts/update_plugin_cachebuster.py ~/plugins/threejs-object-sculptor
codex plugin add threejs-object-sculptor@local
```

Then open a new Codex thread to pick up the updated skill and scripts.

## Support This Project

If Three.js Object Sculptor helps you, you can support its continued development:

<a href="https://ko-fi.com/harrynguyen112">
  <img height="36" src="https://storage.ko-fi.com/cdn/kofi6.png?v=6" alt="Buy Me a Coffee on Ko-fi">
</a>

## License

MIT
