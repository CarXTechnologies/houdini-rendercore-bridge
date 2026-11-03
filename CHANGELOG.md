# Changelog

## 1.2.0
- Reuse existing RenderCore materials by case-insensitive Source material name.
- Preserve local shader settings on reimport; canonicalize legacy duplicates on the exported copy.
- Verify repeated Max / Houdini exchange without changing Max scripts.

## 1.1.0
- Add Max to Houdini import.
- Convert Y-up / Z-up axes and bake static world transforms; explicitly export meters.

## 1.0.0
- Initial Houdini to Max bridge using the existing Get from Blender protocol.
