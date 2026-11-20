# Changelog

## 1.3.0
- Add material NavMesh type (block/walk/road) using the updated reference bridge JSON contract.
- Preserve local NavMesh on material reuse and default old scenes/sidecars to block.
- Add live display-only Vertex Alpha preview: decal opacity, opaque layered darkening and reference variant 300 Alpha squared.
- Preserve authored Cd/Alpha and render/export outputs; reject export from modeling after the preview SOP.
- Warn when the detected Max addon cannot read NavMesh; do not alter Max scripts.
- Verify synthetic transport/material/viewport behavior and the six-mesh Liza fixture.

## 1.2.0
- Reuse existing RenderCore materials by case-insensitive Source material name.
- Preserve local shader settings on reimport; canonicalize legacy duplicates on the exported copy.
- Verify repeated Max / Houdini exchange without changing Max scripts.

## 1.1.0
- Add Max to Houdini import.
- Convert Y-up / Z-up axes and bake static world transforms; explicitly export meters.

## 1.0.0
- Initial Houdini to Max bridge using the existing Get from Blender protocol.
