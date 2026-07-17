# Face And Hand Region Contracts

Use this reference only when the target contains a visible face or hand. These are perceptually sensitive regions: a small proportion, gaze, expression, digit, or contact error can make an otherwise acceptable model look wrong.

This contract does not add a build pass and does not claim medical anatomy or automatic rigging. It strengthens the existing pre-spec, component hierarchy, form/lookdev review, and optimization no-regression gate.

## Declare The Region

Set `preSpecAssessment.specializedRegions.status` to:

- `declared` when at least one face or hand is visible;
- `none` only after inspection, with a concrete reason;
- `unassessed` only while the pre-spec is unfinished.

Each declared region needs:

- one unique `id`, `kind`, descriptive `representation`, visibility, confidence, and occlusion handling;
- one named `assemblyRef` containing all region geometry;
- component and source-evidence refs;
- one or more dedicated close-up `reviewViewIds`;
- visible landmarks mapped to real geometry components;
- explicit proportion plus expression/pose constraints;
- one independent critical `featureTargetId` for every visible region.

If a region is partial or occluded, record the unknowns. Use `request-input` or `omit-hidden-detail` when the hidden structure cannot be bounded honestly. Do not invent hidden fingers or facial forms.

## Face Standard

A clear face must cover at least these landmark roles:

- `face-contour`: forehead/head mass, cheeks, jaw, or muzzle silhouette;
- `eye-system`: eye shape, spacing, vertical placement, pupils/gaze, and eyelid exposure;
- `nose-muzzle`: nose bridge or muzzle mass and its relation to the eyes and mouth;
- `mouth-expression`: mouth corners, lip/opening shape, teeth/tongue when visible, and expression.

Add `brow-expression`, `jaw-cheeks`, and `ears` when they carry identity. Map a clear face to at least four named landmark regions backed by executable geometry; multiple regions may reference one continuous sculpted host. Accessories such as glasses, masks, eyeballs, teeth, and true strands remain separate. Surface-continuous cheek fur or fleshy muzzle relief stays embedded in the host and cannot be replaced by floating clumps.

Face constraints must preserve proportion and expression. Do not mirror away observed asymmetry, and do not use material or decals to hide a wrong silhouette, gaze, or mouth opening.

## Hand Standard

Choose the articulation mode from the reference:

- `explicit-digits`: visible bare/gloved digits need named thumb and finger chains, segment counts, joint arcs, taper, curl, and pose criteria;
- `grouped-digits`: stylized paws, mittens, or grouped glove forms still need named wrist, palm, digit mass, and outer-contour landmarks;
- `silhouette-only`: allowed only for a partial or strongly obscured hand;
- `hidden`: allowed only for an occluded hand with an explicit hidden-detail policy.

Do not force five human fingers onto a stylized paw, and do not collapse a clearly articulated hand into a mitten. Follow the visible representation.

For a static or rigid asset, several hand landmarks and digit chains may map to one continuous sculpted host; semantic completeness does not imply separate meshes. Require separate articulatable geometry parts only for `animated`, `playable`, or `destructible` output, or where the reference shows a real seam. An action-ready digit part also needs a non-static `animationRole`, `transformChannels.rotate: true`, and a finite non-zero joint pivot; a detached but transform-locked mesh is not articulation.

When a hand touches an object, add `interaction` with:

- the contact type and target component;
- the hand components forming the contact;
- observable criteria for overlap, negative space, grip direction, penetration, and floating gaps.

An interacting hand target includes the structure pass when that pass exists, then remains independently reviewed in form, lookdev, and real-time optimization when active.

## Close-Up Review

Add matching source and render crops to `sculpt compare --pairs-json` using the region's exact `reviewViewIds`. The corresponding feature review records those IDs:

```json
{
  "id": "primary-face-identity",
  "score": 0.88,
  "visible": true,
  "viewIds": ["face-closeup"],
  "notes": "Eye spacing and smile match; lower muzzle still needs width correction."
}
```

The region score cannot be averaged away by the full-object score. A missing crop, an unbound review, a hidden critical region, or a score below its critical threshold blocks `continue`.

## Modeling Guidance

- Use a topology plan first. Prefer one `sculpted-surface` host when face contour, cheeks, muzzle, and jaw transition continuously; use separate ellipsoids, extrudes, curve sweeps, or parts only at real anatomical/accessory boundaries. There is intentionally no one-click `face` or `hand` primitive.
- Separate eyeballs, teeth, a true mouth cavity, and accessories when their boundary is visible. Keep fleshy muzzle/nose, eyelids, brows, lips, folds, and fur relief embedded when the topology plan identifies an uninterrupted host surface.
- Preserve wrist, palm, thumb/digit chains or grouped digit mass as semantic regions. Keep them on one sculpted host for continuous static anatomy; separate them only for real seams/accessories or action-ready articulation.
- Validate neutral form before relying on fur, skin, cloth, nail, eye, or accessory materials.
- Review the close-up and full object together so a locally accurate face or hand still fits the body proportions and pose.
