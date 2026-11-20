import configparser
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

import hou

SCHEMA = json.loads(Path(__file__).with_name('schema.json').read_text(encoding='utf-8'))
SOCKETS = [s[0] for s in SCHEMA['swap_list']]
SHADERS = sorted({v[2] for v in SCHEMA['shader_variants']})
VARIANTS = {v[0]: v[2] for v in SCHEMA['shader_variants']}
NAVMESH_TYPES = ('block', 'walk', 'road')


def normalize_navmesh(value):
    """Match the reference Blender transport, including legacy numeric values."""
    if isinstance(value, bool):
        return 'block'
    value = str(value).strip().lower()
    if value in NAVMESH_TYPES:
        return value
    return NAVMESH_TYPES[int(value)] if value in ('0', '1', '2') else 'block'


def _navmesh_template(value='block'):
    parm = hou.StringParmTemplate('rc_navmesh_type', 'NavMesh type', 1,
        default_value=(normalize_navmesh(value),), menu_items=NAVMESH_TYPES,
        menu_labels=('Block (0)', 'Walk (1)', 'Road (2)'))
    parm.setHelp('NavMesh classification when collision is enabled. Requires the updated Max CarXBridge addon.')
    parm.setConditional(hou.parmCondType.HideWhen, '{ rc_use_collision == 0 }')
    return parm


def ensure_material_options(mat):
    """Upgrade old HIP materials in place, retaining all existing RC values."""
    group = mat.parmTemplateGroup()
    changed = False
    if not mat.parm('rc_navmesh_type'):
        group.insertAfter('rc_use_collision', _navmesh_template())
        changed = True
    variant = group.find('rc_variant')
    callback = "import rtc_bridge.bridge as b; b.sync_preview(kwargs['node'])"
    if variant and variant.scriptCallback() != callback:
        variant.setScriptCallback(callback)
        variant.setScriptCallbackLanguage(hou.scriptLanguage.Python)
        group.replace('rc_variant', variant)
        changed = True
    if changed:
        mat.setParmTemplateGroup(group)


def material_key(mat):
    """RenderCore identity survives Houdini's automatic node-name suffixes."""
    name=mat.evalParm('rc_source_name') if mat.parm('rc_source_name') else mat.name()
    return (name or mat.name()).casefold()


def scene_materials_by_name():
    """Choose one existing bridge material per Max name; never edit its settings.

    Prefer the original unsuffixed node, then the shortest stable path. Only
    bridge materials qualify: an arbitrary Principled material is not RenderCore.
    """
    candidates=[]
    for path in ('/mat','/shop','/obj'):
        root=hou.node(path)
        if root:
            candidates.extend(n for n in root.allSubChildren(recurse_in_locked_nodes=False) if n.parm('rc_variant'))
    def rank(mat):
        source=mat.evalParm('rc_source_name') if mat.parm('rc_source_name') else mat.name()
        return (mat.name()!=safe_name(source or mat.name()),len(mat.path()),mat.path())
    result={}
    for mat in sorted(candidates,key=rank):result.setdefault(material_key(mat),mat)
    return result


def bake_geometry(geo,matrix):
    """Transform N as normals even when a custom SOP created it as a plain tuple."""
    values=[]
    for a in (geo.findPointAttrib('N'),geo.findVertexAttrib('N'),geo.findPrimAttrib('N')):
        if not a:continue
        if a.size()!=3:raise ValueError('N must be a three-component normal')
        elements=list(geo.points() if a.type()==hou.attribType.Point else
                      geo.prims() if a.type()==hou.attribType.Prim else
                      (v for p in geo.prims() for v in p.vertices()))
        normals=[hou.Vector3(e.attribValue(a)) for e in elements]
        if not a.isTransformedAsNormal():
            owner=a.type();a.destroy()
            a=geo.addAttrib(owner,'N',(0.,0.,0.),transform_as_normal=True,create_local_variable=False)
        values.append((a,elements,normals))
    t=matrix.asTuple()
    normal_matrix=hou.Matrix3(t[:3]+t[4:7]+t[8:11]).inverted().transposed() if values else None
    geo.transform(matrix)
    for a,elements,normals in values:
        for element,n in zip(elements,normals):
            n=n*normal_matrix
            element.setAttribValue(a,tuple(n.normalized()) if n.lengthSquared()>0 else (0.,0.,0.))


def safe_name(name):
    clean = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    if clean and clean[0].isdigit(): clean='rtc_'+clean
    if clean != name:
        clean += '_' + hashlib.sha256(name.encode('utf-8')).hexdigest()[:8]
    return clean or 'material'


def create_material(name='RenderCore', data=None):
    """Create an editable material. RenderCore parameters are the export source of truth."""
    data = data or dict(name=name, variant='11', collision='none', textures=[])
    mat = hou.node('/mat').createNode('principledshader::2.0', safe_name(name))
    group = mat.parmTemplateGroup()
    folder = hou.FolderParmTemplate('rtc', 'RenderCore → 3ds Max')
    folder.addParmTemplate(hou.StringParmTemplate('rc_source_name', 'Source material name', 1, default_value=(name,)))
    variants = SCHEMA['shader_variants']
    folder.addParmTemplate(hou.StringParmTemplate('rc_variant', 'Shader / variant', 1,
        default_value=(str(data['variant']),), menu_items=tuple(v[0] for v in variants),
        menu_labels=tuple(v[1] for v in variants)))
    collision = SCHEMA['collision_types']
    folder.addParmTemplate(hou.StringParmTemplate('rc_collision', 'Collision surface', 1,
        default_value=(data.get('collision', 'none'),), menu_items=tuple(c[0] for c in collision),
        menu_labels=tuple(c[1] for c in collision)))
    folder.addParmTemplate(hou.ToggleParmTemplate('rc_use_collision','Enable collision',
        default_value=bool(data.get('use_collision',data.get('collision','none')!='none'))))
    folder.addParmTemplate(_navmesh_template(data.get('navmesh_type', 'block')))
    tex = {t['name']: t for t in data.get('textures', [])}
    for layer in range(1, 5):
        f = hou.FolderParmTemplate('rc_layer'+str(layer), 'Layer '+str(layer))
        suffix = '' if layer == 1 else 'Layer'+str(layer)
        scale = next((t['scale'] for t in tex.values() if ('Layer'+str(layer) in t['name'] if layer > 1 else 'Layer' not in t['name'])), 1.0)
        f.addParmTemplate(hou.FloatParmTemplate('rc_scale'+str(layer), 'Tiling', 1, default_value=(float(scale),), min=0.00001))
        for socket in SOCKETS:
            if (layer == 1 and 'Layer' not in socket) or (layer > 1 and socket.endswith(suffix)):
                p = hou.StringParmTemplate('rc_'+socket, socket, 1,
                    default_value=(tex.get(socket, {}).get('path', '').replace('\\','/'),),
                    string_type=hou.stringParmType.FileReference)
                p.setScriptCallback("import rtc_bridge.bridge as b; b.sync_preview(kwargs['node'])")
                p.setScriptCallbackLanguage(hou.scriptLanguage.Python)
                f.addParmTemplate(p)
        folder.addParmTemplate(f)
    group.insertBefore(group.entries()[0], folder)
    mat.setParmTemplateGroup(group)
    mat.setUserData('rtc_bridge_material', '1')
    sync_preview(mat)
    return mat


def sync_preview(mat):
    """Principled preview is approximate; RenderCore runs in Max, not Houdini."""
    ensure_material_options(mat)
    # Display-only SOP replaces layer-weight Cd with the appropriate alpha tint.
    # Raw Alpha/Cd on the authored/render stream remain unchanged.
    mat.parm('basecolor_usePointColor').set(1)
    from .preview import DECAL_SHADERS
    shader = VARIANTS.get(mat.evalParm('rc_variant'))
    texture_alpha = str(mat.evalParm('rc_variant')) in ('523', '1035', '8389121', '8389633', '8389643')
    decal = shader in DECAL_SHADERS
    mat.parm('opacpointalpha').set(1 if decal else 0)
    mat.parm('alphablendmode').set('mask' if texture_alpha and str(mat.evalParm('rc_variant')) in ('1035','8389633','8389643') else 'blend' if decal or texture_alpha else 'opaque')
    mat.parm('opaccolor_texture').setExpression('chs("rc_AlphaMap")', hou.exprLanguage.Hscript)
    mat.parm('opaccolor_useTexture').setExpression('strlen(chs("rc_AlphaMap")) > 0' if decal or texture_alpha else '0', hou.exprLanguage.Hscript)
    mat.parmTuple('basecolor').set((0.8,0.8,0.8))
    mapping = [('baseMap','basecolor_texture','basecolor_useTexture'),
               ('roughnessMap','rough_texture','rough_useTexture'),
               ('metalnessMap','metallic_texture','metallic_useTexture'),
               ('emissionMap','emitcolor_texture','emitcolor_useTexture')]
    for socket, target, toggle in mapping:
        if mat.parm(target): mat.parm(target).setExpression('chs("rc_'+socket+'")', hou.exprLanguage.Hscript)
        if mat.parm(toggle): mat.parm(toggle).setExpression('strlen(chs("rc_'+socket+'")) > 0', hou.exprLanguage.Hscript)
    if mat.parm('baseNormal_texture'):
        mat.parm('baseNormal_texture').setExpression('chs("rc_normalMap")', hou.exprLanguage.Hscript)
        mat.parm('baseBumpAndNormal_enable').setExpression('strlen(chs("rc_normalMap")) > 0', hou.exprLanguage.Hscript)
    configure_viewer()


def material_data(mat, check_files=True):
    if not mat or not mat.parm('rc_variant'):
        raise ValueError('Material is not a bridge RenderCore material: '+str(mat))
    variant = mat.evalParm('rc_variant')
    if variant not in VARIANTS: raise ValueError('Unsupported RenderCore variant: '+variant)
    collision = mat.evalParm('rc_collision')
    if collision not in {c[0] for c in SCHEMA['collision_types']}:
        raise ValueError('Unknown collision surface: '+collision)
    textures=[]
    for socket in SOCKETS:
        value = mat.evalParm('rc_'+socket).strip()
        if not value: continue
        if not os.path.isabs(value):
            raise ValueError('Use an absolute texture path or $HIP: '+value)
        path = os.path.normpath(value)
        if check_files and not Path(path).is_file(): raise ValueError('Missing texture: '+path)
        layer = int(socket[-1]) if re.search(r'Layer[234]$', socket) else 1
        scale = float(mat.evalParm('rc_scale'+str(layer)))
        if not math.isfinite(scale) or scale <= 0: raise ValueError('Tiling must be positive and finite')
        if VARIANTS[variant]!='RenderCoreLayeredMaterial' and scale!=1:
            raise ValueError('Stock Max reads tiling only for RenderCoreLayeredMaterial. Use a UV Transform SOP for this shader.')
        textures.append(dict(name=socket, path=path, scale=scale))
    return dict(name=mat.evalParm('rc_source_name') or mat.name(), shader=VARIANTS[variant], variant=variant,
                collision=collision, use_collision=bool(mat.evalParm('rc_use_collision')) if mat.parm('rc_use_collision') else collision != 'none',
                navmesh_type=normalize_navmesh(mat.evalParm('rc_navmesh_type') if mat.parm('rc_navmesh_type') else 'block'), textures=textures)


def exchange_paths(scale_section='Import'):
    """Read Max INI without writing it. Materials/meta use hardcoded TEMP in stock Max."""
    default = Path(tempfile.gettempdir())/'CarXBridge'
    candidates = sorted((Path(os.environ['LOCALAPPDATA'])/'Autodesk'/'3dsMax'/'2023 - 64bit').glob('*/scripts/CarXBridge/settings.ini'))
    candidates = [p for p in candidates if '-recover-' not in str(p)]
    ini = Path(os.environ['RTC_MAX_BRIDGE_INI']) if os.environ.get('RTC_MAX_BRIDGE_INI') else (candidates[0] if len(candidates)==1 else None)
    fbx_dir = default
    if ini and ini.is_file():
        raw=ini.read_bytes()
        encoding='utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig'
        cfg=configparser.ConfigParser(); cfg.read_string(raw.decode(encoding))
        fbx_dir = Path(cfg.get('Path','BRIDGE',fallback=str(default)))
        if cfg.getfloat(scale_section,'GlobalScale',fallback=1) != 1:
            raise ValueError('Max '+scale_section+' GlobalScale must be 1 for meter-accurate transfer')
    elif len(candidates)>1:
        raise ValueError('Several Max profiles: set RTC_MAX_BRIDGE_INI to the active settings.ini')
    return fbx_dir, default


def _selection(nodes):
    from .preview import authored_sop
    result=[]
    for n in nodes:
        if n.type().category()==hou.sopNodeTypeCategory():
            n=authored_sop(n)
            if n is None: raise ValueError('Preview SOP has no authored input')
            obj=n.parent()
            while obj and obj.type().category()!=hou.objNodeTypeCategory(): obj=obj.parent()
            if not obj or obj.type().name()!='geo': raise ValueError('SOP must belong to a Geometry object')
            result.append((obj,n))
        elif n.type().category()==hou.objNodeTypeCategory():
            if n.type().name()=='geo': result.append((n,n.renderNode() or n.displayNode()))
            elif n.type().name()=='subnet':
                for c in n.allSubChildren():
                    if c.type().category()==hou.objNodeTypeCategory() and c.type().name()=='geo':
                        result.append((c,c.renderNode() or c.displayNode()))
            else: raise ValueError('Select polygon Geometry objects or SOP outputs: '+n.path())
        else: raise ValueError('Unsupported selection: '+n.path())
    unique={}
    for obj,sop in result:
        sop=authored_sop(sop)
        if obj.path() in unique and unique[obj.path()][1] != sop:
            raise ValueError('Select one SOP output per Geometry object')
        unique[obj.path()]=(obj,sop)
    if not unique: raise ValueError('Select Geometry objects or SOP outputs to export')
    return list(unique.values())


def export_nodes(nodes, destination=None, check_files=True):
    """Export cooked static geometry, face assignments, UVs, normals and Cd/Alpha.

    Source nodes are never modified. Static object transforms are baked in a temporary network.
    destination is for isolated tests; shelf export resolves the existing Max configuration.
    """
    if hou.applicationVersion()[:2] != (20,5): raise RuntimeError('This package targets Houdini 20.5')
    if hou.licenseCategory()==hou.licenseCategoryType.Apprentice:
        raise RuntimeError('FBX export requires a Houdini license that supports FBX')
    selected=_selection(nodes)
    names=[o.evalParm('rc_export_name') if o.parm('rc_export_name') else o.name() for o,_ in selected]
    if len(set(n.casefold() for n in names)) != len(names): raise ValueError('Object names must be unique')
    if any(re.search(r'\.\d{3}$',n) for n in names): raise ValueError('Avoid .001 names: stock Max replacement matches base names')
    materials={}; prepared=[]; by_path={}; aliases={}
    existing=scene_materials_by_name()
    for (obj,sop),export_name in zip(selected,names):
        if not sop: raise ValueError('No output SOP: '+obj.path())
        if obj.worldTransform().determinant()<0:
            raise ValueError('Negative object scale: apply reflection in SOPs and correct polygon winding before export: '+obj.path())
        geo=hou.Geometry(); geo.merge(sop.geometry())
        if not geo.prims(): raise ValueError('Empty geometry: '+sop.path())
        if any(p.type()!=hou.primType.Polygon for p in geo.prims()):
            raise ValueError('Convert packed/curve/volume geometry to polygons before export: '+sop.path())
        attr=geo.findPrimAttrib('shop_materialpath')
        fallback=obj.evalParm('shop_materialpath') if obj.parm('shop_materialpath') else ''
        if not attr: attr=geo.addAttrib(hou.attribType.Prim,'shop_materialpath','')
        assigned=[]
        for prim in geo.prims():
            path=prim.attribValue(attr) or fallback
            mat=(hou.node(path) if path.startswith('/') else (obj.node(path) or sop.node(path))) if path else None
            if not mat: raise ValueError('Unassigned/unresolved material on '+obj.path()+' face '+str(prim.number()))
            original=mat
            mat=existing.get(material_key(mat),mat)
            if mat!=original:aliases[original.path()]=mat.path()
            if mat.path() not in by_path: by_path[mat.path()]=material_data(mat, check_files)
            data=by_path[mat.path()]
            key=data['name']
            if key in materials and materials[key]!=data: raise ValueError('Conflicting material names: '+key)
            materials[key]=data; assigned.append((prim,mat.path()))
        for prim,path in assigned: prim.setAttribValue(attr,path)
        used={by_path[path]['shader'] for _,path in assigned}
        if 'cxColliderMaterial' in used and len({path for _,path in assigned})>1:
            raise ValueError('Stock Max cannot import collider inside a multi-material; use a separate collider object')
        # Bake Y-up -> Z-up into geometry, keeping meters. Letting the FBX
        # exporter perform this creates a +90 X PreRotation on every Max node.
        bake_geometry(geo,obj.worldTransform()*hou.hmath.buildRotate(90,0,0))
        prepared.append((export_name,geo))
    for name in names+list(materials):
        if not name or any(c in name for c in ('"','\\','\n','\r',':','/')):
            raise ValueError('Unsupported FBX/Max name: '+repr(name))
    if len({n.casefold() for n in materials})!=len(materials): raise ValueError('Material names must be unique ignoring case')
    fbx_dir,meta_dir = (Path(destination),Path(destination)) if destination else exchange_paths()
    fbx_dir.mkdir(parents=True,exist_ok=True); meta_dir.mkdir(parents=True,exist_ok=True)
    lock=meta_dir/'.houdini_export.lock'
    try: fd=os.open(str(lock),os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError: raise RuntimeError('Another export is in progress; inspect '+str(lock))
    os.close(fd)
    stage=None; rop=None
    work=Path(tempfile.mkdtemp(prefix='rtc_houdini_'))
    try:
        stage=hou.node('/obj').createNode('subnet','__rtc_export_'+uuid.uuid4().hex[:8])
        for name,geo in prepared:
            obj=stage.createNode('geo',safe_name(name))
            sop=obj.createNode('stash','OUT'); sop.parm('stash').set(geo)
            sop.setDisplayFlag(True); sop.setRenderFlag(True)
        rop=hou.node('/out').createNode('filmboxfbx','rtc_bridge_'+uuid.uuid4().hex[:8])
        rop.parm('startnode').set(stage.path()); rop.parm('createsubnetroot').set(0)
        rop.parm('sopoutput').set(str(work/'TMP_BLENDER.fbx'))
        rop.parm('trange').set(0); rop.parm('sdkversion').set('FBX | FBX201400')
        rop.parm('axissystem').set('zupright'); rop.parm('convertaxis').set(0)
        rop.parm('convertunits').set(0); rop.parm('invisobj').set('fullnodes')
        rop.render()
        if not (work/'TMP_BLENDER.fbx').is_file() or rop.errors():
            raise RuntimeError('FBX export failed: '+str(rop.errors()))
        # Houdini disallows digit-leading and punctuation node names. Restore FBX labels
        # by exact typed identifiers; connections use numeric IDs and remain unchanged.
        fbx=work/'TMP_BLENDER.fbx'
        text=fbx.read_text(encoding='utf-8')
        # With convertunits disabled Houdini writes unchanged coordinates but
        # declares FBX's default centimeters. Declare our actual meter units
        # explicitly, avoiding a residual 0.01 scale on imported Max objects.
        text,count=re.subn(r'(P:\s*"(?:Original)?UnitScaleFactor",\s*"double",\s*"Number",\s*"",)\s*[^\r\n]+',
                           r'\g<1>100',text)
        if count!=2:raise RuntimeError('Unexpected Houdini FBX unit metadata; export not published')
        for kind,mapping in [('Model',{safe_name(n):n for n in names}),
                             ('Material',{hou.node(p).name():d['name'] for p,d in by_path.items()})]:
            for old,new in mapping.items():
                if old!=new: text=text.replace('"'+kind+'::'+old+'"','"'+kind+'::'+new+'"')
        fbx.write_text(text,encoding='utf-8')
        mats=work/'MaterialsBlender'; mats.mkdir()
        for i,data in enumerate(materials.values()):
            (mats/(str(i)+'.json')).write_text(json.dumps(data,ensure_ascii=True,indent=2),encoding='ascii')
        (work/'TMP_BLENDER.meta').write_text(json.dumps({'empties':[{'name':n,'type':'None'} for n in names]}),encoding='utf-8')
        # Preserve the previous exchange. Publish FBX last; Max should be idle while sending.
        backup=meta_dir/'HoudiniBackups'/uuid.uuid4().hex
        backup.mkdir(parents=True)
        targets=[(fbx_dir/'TMP_BLENDER.fbx',work/'TMP_BLENDER.fbx'),
                 (meta_dir/'TMP_BLENDER.meta',work/'TMP_BLENDER.meta'),
                 (meta_dir/'MaterialsBlender',mats)]
        old=[]
        for i,(target,source) in enumerate(targets):
            if target.exists():
                saved=backup/str(i)
                if target.is_dir(): shutil.copytree(target,saved)
                else: shutil.copy2(target,saved)
                old.append((target,saved))
        published=[]
        try:
            for target,source in targets[1:]+targets[:1]:
                if target.is_dir(): shutil.rmtree(target)
                if source.is_dir(): shutil.copytree(source,target)
                else:
                    tmp=target.with_suffix(target.suffix+'.houdini_tmp')
                    shutil.copy2(source,tmp); os.replace(tmp,target)
                published.append(target)
        except Exception:
            for target in published:
                if target.is_dir(): shutil.rmtree(target)
                elif target.exists(): target.unlink()
            for target,saved in old:
                if target.is_dir(): shutil.rmtree(target)
                if saved.is_dir(): shutil.copytree(saved,target)
                else: shutil.copy2(saved,target)
            raise
        report={'houdini':hou.applicationVersionString(),'objects':names,'materials':len(materials),
                'material_aliases':aliases,
                'fbx':str(fbx_dir/'TMP_BLENDER.fbx'),'metadata':str(meta_dir),
                'backup':str(backup),'warnings':list(rop.warnings())}
        if not destination and any(d['use_collision'] and d['navmesh_type'] != 'block' for d in materials.values()):
            candidates = list((Path(os.environ['RTC_MAX_BRIDGE_INI']).parent,)) if os.environ.get('RTC_MAX_BRIDGE_INI') else list(
                (Path(os.environ['LOCALAPPDATA'])/'Autodesk'/'3dsMax'/'2023 - 64bit').glob('*/scripts/CarXBridge'))
            if not candidates or not all((p/'import_materials.py').is_file() and 'navmesh_type' in (p/'import_materials.py').read_text(encoding='utf-8-sig',errors='replace') for p in candidates):
                report['warnings'].append('NavMesh is present in the exported JSON, but the installed Max addon may not read navmesh_type. Install the updated CarXTechnologies/3D-bridges Max addon; old versions ignore this field.')
        (meta_dir/'HoudiniLastExport.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        return report
    finally:
        if rop: rop.destroy()
        if stage: stage.destroy()
        shutil.rmtree(work,ignore_errors=True)
        lock.unlink(missing_ok=True)


def export_selection():
    try:
        report=export_nodes(hou.selectedNodes())
        hou.ui.displayMessage('Export complete: '+str(len(report['objects']))+' objects, '+str(report['materials'])+
                              ' RenderCore materials.\nIn 3ds Max click Get from Blender.\n'+report['fbx']+
                              ('\n\n'+'\n'.join(report['warnings']) if report['warnings'] else ''))
    except Exception as e:
        hou.ui.displayMessage(str(e),severity=hou.severityType.Error)


def material_dialog():
    prepare_preview()
    index,name=hou.ui.readInput('RenderCore material name',initial_contents='RenderCore',buttons=('Create','Cancel'))
    if index!=0: return
    mat=create_material(name)
    mat.setCurrent(True,clear_all_selected=True)
    pane=hou.ui.paneTabOfType(hou.paneTabType.Parm)
    if pane: pane.setCurrentNode(mat)


def configure_viewer():
    """Enable the material/color/transparency paths required by RC preview."""
    if not hou.isUIAvailable():
        return
    viewer=hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
    if viewer:
        settings=viewer.curViewport().settings()
        settings.showGeometryColor(True)
        settings.showMaterials(True)
        settings.useTransparency(True)
        settings.setLighting(hou.viewportLighting.Headlight)


def prepare_preview():
    """Upgrade materials and attach live alpha visualization to authored outputs."""
    configure_viewer()
    from .preview import refresh
    selected=[n for n in hou.selectedNodes() if n.type().category() in (hou.sopNodeTypeCategory(),hou.objNodeTypeCategory())]
    return refresh(selected or None)


def assign_material_dialog():
    nodes=hou.selectedNodes()
    path=hou.ui.selectNode(initial_node='/mat',title='Select a bridge RenderCore material')
    if not path: return
    mat=hou.node(path)
    try:
        material_data(mat,check_files=False)
        for obj,sop in _selection(nodes):
            assign=sop.parent().createNode('material','assign_RenderCore')
            assign.setInput(0,sop); assign.parm('shop_materialpath1').set(path)
            assign.setDisplayFlag(True); assign.setRenderFlag(True)
            assign.moveToGoodPosition(); assign.setCurrent(True,clear_all_selected=True)
            from .preview import attach
            attach(obj, assign)
    except Exception as e: hou.ui.displayMessage(str(e),severity=hou.severityType.Error)
