# Self-Correction Loop Reference

Use this reference when a model construction pass has just finished.

## Review Order

1. Capture or collect a rendered screenshot for the current browser view.
2. Create a side-by-side reference/render sheet with `make_visual_comparison_sheet.py`.
3. Inspect the sheet with Codex AI vision and score the relevant visual layers.
4. Compare the rendered result to current `ObjectSculptSpec`.
5. Decide whether the mismatch is caused by the spec, the implementation, lighting/camera, missing evidence, or performance tradeoff.
6. Choose exactly one action:
   - `continue`
   - `refine-spec`
   - `refine-code`
   - `request-input`
   - `stop`
7. Record the screenshot paths, comparison image, overall score, layer scores, and AI critique in `reviewHistory`.

For visual passes, `continue` requires a rendered screenshot, a comparison image, and an AI vision score at or above the configured threshold. Without them, the review is not evidence-backed enough. Pixel comparison code is never the acceptance authority.

## Root Cause Guide

Use `refine-spec` when:

- a component is missing or invented incorrectly
- the primitive family is wrong
- proportions or coordinate frame are wrong
- material layer is under-specified
- local features are missing from the spec
- evidence refs are absent or contradict the image
- user expectation cannot be represented by current build passes

Use `refine-code` when:

- the spec is clear but generated geometry is wrong
- material parameters were not implemented
- local masks/noise/wear are missing in code
- hierarchy/pivots do not match the spec
- browser render has obvious artifacts
- performance can be improved without changing the spec

Use `request-input` when:

- the image hides essential geometry
- material cannot be inferred from the provided view
- exact branding/text/ornament is required
- the requested fidelity is incompatible with a single image

Use `stop` when:

- target fidelity is reached
- user accepted current approximation
- remaining issues require new references, manual modeling, or non-procedural assets

## Fidelity Estimate

Use a practical 0-1 scale:

- `0.2`: only rough primitive placeholder
- `0.4`: silhouette recognizable, structure incomplete
- `0.6`: macro and meso forms mostly correct, material/detail weak
- `0.75`: object reads correctly, local details approximate
- `0.85`: strong procedural match for real-time use
- `0.95`: near-reference, usually requires multiple views or manual art

Do not claim `0.9+` from a single ambiguous image unless the object is simple and symmetrical.
