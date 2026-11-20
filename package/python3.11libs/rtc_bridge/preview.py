"""Live, display-only vertex-alpha interpretation for the Houdini viewport.

The Python SOP cooks a copy. Its render flag stays on the authored SOP, and the
bridge resolves a selected preview SOP back to its input before export.
"""
import hou

PREVIEW_TAG = 'rtc_alpha_preview'
CODE = "from rtc_bridge.preview import cook\ncook(hou.pwd())\n"
DECAL_SHADERS = {'RenderCoreMeshDecalMaterial', 'RenderCoreMeshDecalLayeredMaterial'}


def alpha_role(mat):
    from .bridge import VARIANTS
    if not mat or not mat.parm('rc_variant'):
        return 'unchanged'
    shader = VARIANTS.get(mat.evalParm('rc_variant'))
    if shader == 'RenderCoreLayeredMaterial':
        return 'darken'
    if shader in DECAL_SHADERS:
        return 'opacity'
    # The reference DRN alpha and foliage graphs use texture alpha only.
    return 'ignore'


def authored_sop(sop):
    if sop and sop.userData(PREVIEW_TAG) != '1' and any(n.userData(PREVIEW_TAG) == '1' for n in sop.inputAncestors()):
        raise ValueError('Move painting/modeling before RC_ALPHA_PREVIEW before exporting: ' + sop.path())
    seen = set()
    while sop and sop.userData(PREVIEW_TAG) == '1':
        if sop.path() in seen:
            raise ValueError('Cyclic RenderCore preview input')
        seen.add(sop.path())
        sop = sop.input(0)
    return sop


def _value(geo, vertex, name, default):
    for find, element in ((geo.findVertexAttrib, vertex),
                           (geo.findPointAttrib, vertex.point()),
                           (geo.findPrimAttrib, vertex.prim()),
                           (geo.findGlobalAttrib, geo)):
        attr = find(name)
        if attr:
            return element.attribValue(attr)
    return default


def cook(node):
    geo = node.geometry()
    obj = node.parent()
    fallback = obj.evalParm('shop_materialpath') if obj.parm('shop_materialpath') else ''
    assignments = geo.findPrimAttrib('shop_materialpath')
    cache, values = {}, []
    for prim in geo.prims():
        path = (prim.attribValue(assignments) if assignments else '') or fallback
        if path not in cache:
            mat = hou.node(path) if path.startswith('/') else obj.node(path) if path else None
            # The supplied 4-layer alpha-test graph multiplies vertex alpha
            # both in Mix_Float_Alpha and once more at the material output.
            power = 2 if mat and mat.parm('rc_variant') and mat.evalParm('rc_variant') == '300' else 1
            cache[path] = (alpha_role(mat), power)
        role, power = cache[path]
        for vertex in prim.vertices():
            alpha = float(_value(geo, vertex, 'Alpha', 1.0))
            color = tuple(_value(geo, vertex, 'Cd', (1.0, 1.0, 1.0)))
            if role == 'darken':
                a = max(0.0, min(1.0, alpha))
                color, alpha = (a, a, a), 1.0
            elif role == 'opacity':
                color, alpha = (1.0, 1.0, 1.0), max(0.0, min(1.0, alpha)) ** power
            elif role == 'ignore':
                color, alpha = (1.0, 1.0, 1.0), 1.0
            values.append((vertex, color, alpha))
    # Corner attributes preserve different alpha rules across shared points.
    # Only the display copy loses the original color/alpha attributes.
    for name in ('Cd', 'Alpha'):
        for find in (geo.findVertexAttrib, geo.findPointAttrib,
                      geo.findPrimAttrib, geo.findGlobalAttrib):
            attr = find(name)
            if attr:
                attr.destroy()
    cd = geo.addAttrib(hou.attribType.Vertex, 'Cd', (1.0, 1.0, 1.0))
    alpha = geo.addAttrib(hou.attribType.Vertex, 'Alpha', 1.0)
    for vertex, color, a in values:
        vertex.setAttribValue(cd, color)
        vertex.setAttribValue(alpha, a)


def attach(obj, sop=None):
    """Append/reuse one display-only SOP; keep the authored output for render/export."""
    displayed = obj.displayNode()
    if displayed and displayed.userData(PREVIEW_TAG) == '1' and (sop is None or sop == displayed):
        source = authored_sop(displayed)
        if source:
            source.setRenderFlag(True)
            displayed.setRenderFlag(False)
            return displayed
    source = authored_sop(sop or obj.renderNode() or displayed)
    if not source:
        return None
    nodes = [n for n in obj.children() if n.userData(PREVIEW_TAG) == '1']
    preview = nodes[0] if nodes else obj.createNode('python', 'RC_ALPHA_PREVIEW')
    if preview != displayed and any(n.input(0) == preview for n in obj.children()):
        # Do not silently modify a stream an artist has started to model after.
        raise ValueError('RC_ALPHA_PREVIEW has downstream nodes. Move painting/modeling before the preview SOP: ' + preview.path())
    preview.setUserData(PREVIEW_TAG, '1')
    preview.setInput(0, source)
    preview.parm('python').set(CODE)
    preview.setComment('Display only. Paint Alpha upstream. Render/export use the authored input.')
    preview.setGenericFlag(hou.nodeFlag.DisplayComment, True)
    preview.moveToGoodPosition()
    preview.setDisplayFlag(True)
    source.setRenderFlag(True)
    preview.setRenderFlag(False)
    return preview


def refresh(nodes=None):
    from .bridge import _selection, sync_preview
    if nodes:
        objects = _selection(nodes)
    else:
        objects = [(n, authored_sop(n.renderNode() or n.displayNode()))
                   for n in hou.node('/obj').allSubChildren()
                   if n.type().category() == hou.objNodeTypeCategory() and n.type().name() == 'geo']
    touched, materials = [], set()
    for obj, source in objects:
        if not source:
            continue
        geo = source.geometry()
        paths = set(geo.primStringAttribValues('shop_materialpath')) if geo.findPrimAttrib('shop_materialpath') else set()
        if obj.parm('shop_materialpath'):
            paths.add(obj.evalParm('shop_materialpath'))
        found = []
        for path in paths:
            mat = hou.node(path) if path.startswith('/') else obj.node(path) if path else None
            if mat and mat.parm('rc_variant'):
                found.append(mat)
        if not found:
            continue
        for mat in found:
            if mat.path() not in materials:
                sync_preview(mat)
                materials.add(mat.path())
        preview = attach(obj, source)
        if preview:
            preview.cook(force=True)
            if preview.errors():
                raise RuntimeError('Alpha preview failed: ' + str(preview.errors()))
            touched.append(preview.path())
    return touched
