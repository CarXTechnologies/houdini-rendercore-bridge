"""Read the files made by stock Max 'Send to Blender'; no Max code required."""
import json,math,os,re
from pathlib import Path
import uuid
import hou
from .bridge import create_material,material_data,exchange_paths,safe_name,bake_geometry,scene_materials_by_name,VARIANTS,SOCKETS,SCHEMA,normalize_navmesh,sync_preview


def _read_materials(folder):
    if not folder.is_dir(): raise ValueError('No Max materials folder: '+str(folder))
    result={}
    for path in sorted(folder.glob('*.mat')):
        raw=path.read_bytes()
        encoding='utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig'
        data=json.loads(raw.decode(encoding))
        name=data.get('name');variant=str(data.get('variant'))
        if not isinstance(name,str) or not name: raise ValueError('Invalid material name in '+str(path))
        if variant not in VARIANTS or data.get('shader')!=VARIANTS[variant]:
            raise ValueError('Unsupported RenderCore material: '+name+' / '+str(data.get('shader')))
        if data.get('collision') not in {c[0] for c in SCHEMA['collision_types']}:
            raise ValueError('Unknown collision surface on '+name)
        data['variant']=variant
        data['navmesh_type']=normalize_navmesh(data.get('navmesh_type','block'))
        data['use_collision']=data.get('use_collision') in (True,'True','true',1)
        seen=set()
        for tex in data.get('textures',[]):
            if tex.get('name') not in SOCKETS or tex['name'] in seen: raise ValueError('Unknown or duplicate texture slot on '+name)
            seen.add(tex['name'])
            if not isinstance(tex.get('path'),str): raise ValueError('Invalid texture path on '+name)
            tex['scale']=float(tex.get('scale',1))
            if not math.isfinite(tex['scale']) or tex['scale']<=0: raise ValueError('Invalid tiling on '+name)
        if name in result and result[name]!=data: raise ValueError('Conflicting material sidecars: '+name)
        result[name]=data
    if not result: raise ValueError('No RenderCore .mat files in '+str(folder))
    return result


def import_from_max(source=None,materials_dir=None):
    """Import a new editable batch; existing geometry and materials are never replaced.

    Native FBX import handles file units and Z-up -> Y-up. Static transforms are
    then baked once into stash geometry; clean OBJ transforms remain identity.
    Original FBX material names, not sanitized Houdini names, identify sidecars.
    """
    if hou.applicationVersion()[:2]!=(20,5): raise RuntimeError('This package targets Houdini 20.5')
    fbx_dir,meta_dir=exchange_paths(scale_section='Export')
    source=Path(source) if source else fbx_dir/'TMP_MAX.fbx'
    folder=Path(materials_dir) if materials_dir else meta_dir/'Materials'
    if not source.is_file(): raise ValueError('No Max FBX. In Max click Send to Blender first: '+str(source))
    sidecars=_read_materials(folder)
    # Snapshot before native FBX import: its transient materials must never be
    # mistaken for materials already authored in the user's scene.
    existing=scene_materials_by_name()
    aliases={}
    for name in sidecars:
        alias=re.sub(r'[^a-zA-Z0-9_]','_',name)
        if alias and alias[0].isdigit():alias='_'+alias
        if alias in aliases and aliases[alias]!=name:
            raise ValueError('FBX material names become ambiguous in Houdini: '+name+' / '+aliases[alias])
        aliases[alias]=name
    native=None;batch=None;created=[]
    before={n.path() for n in hou.node('/obj').children()}
    try:
        native,messages=hou.hipFile.importFBX(str(source),suppress_save_prompt=True,merge_into_scene=True,
            import_cameras=False,import_joints_and_skin=False,import_lights=False,import_animation=False,
            import_materials=True,convert_file_paths_to_relative=False,
            convert_into_y_up_coordinate_system=True,convert_units=True,
            override_scene_frame_range=False,override_framerate=False)
        prepared=[];needed=set();warnings=[]
        # Read native names before deleting the temporary import (Houdini may
        # rename stone_3 duplicates to stone_4, stone_5, etc.).
        for obj in native.allSubChildren():
            if obj.type().category()!=hou.objNodeTypeCategory():continue
            if obj.type().name()!='geo':
                if obj.type().name() not in ('subnet',): warnings.append('Skipped helper: '+obj.path())
                continue
            sop=obj.renderNode() or obj.displayNode()
            if not sop: continue
            geo=hou.Geometry();geo.merge(sop.geometry())
            if not geo.prims():continue
            if any(p.type()!=hou.primType.Polygon for p in geo.prims()):raise ValueError('Only static polygon meshes are supported: '+obj.path())
            original=obj.evalParm('fbx_node_path').replace('\\','/').split('/')[-1] if obj.parm('fbx_node_path') else obj.name()
            attr=geo.findPrimAttrib('shop_materialpath')
            if not attr: raise ValueError('FBX has no face material assignments: '+original)
            resolved={}
            for value in set(geo.primStringAttribValues('shop_materialpath')):
                mat=(hou.node(value) if value.startswith('/') else (obj.node(value) or native.node('materials/'+value)))
                name=mat.evalParm('fbx_node_name') if mat and mat.parm('fbx_node_name') else value.split('/')[-1]
                name=aliases.get(name,name)
                if name not in sidecars: raise ValueError('No Max material sidecar for '+original+' / '+name)
                needed.add(name);resolved[value]=name
            # Make the imported batch independent of FBX and transient nodes.
            bake_geometry(geo,obj.worldTransform())
            prepared.append((original,geo,resolved))
        if not prepared: raise ValueError('No polygon meshes in Max export')
        names=[item[0].casefold() for item in prepared]
        if len(set(names))!=len(names): raise ValueError('Max mesh names must be unique')
        batch=hou.node('/obj').createNode('subnet','From_Max')
        session=uuid.uuid4().hex
        materials={};reused=[]
        for name in sorted(needed):
            mat=existing.get(name.casefold())
            if mat is None:
                mat=create_material(name,sidecars[name]);created.append(mat)
                mat.setUserData('rtc_import_session',session)
                existing[name.casefold()]=mat
                for tex in sidecars[name]['textures']:
                    if not Path(tex['path']).is_file():warnings.append('Missing texture: '+tex['path'])
            else:reused.append(name)
            sync_preview(mat)
            materials[name]=mat
        for name,geo,resolved in prepared:
            attr=geo.findPrimAttrib('shop_materialpath')
            for prim in geo.prims():prim.setAttribValue(attr,materials[resolved[prim.attribValue(attr)]].path())
            obj=batch.createNode('geo',safe_name(name))
            obj.addSpareParmTuple(hou.StringParmTemplate('rc_export_name','Max object name',1,default_value=(name,)))
            stash=obj.createNode('stash','FROM_MAX');stash.parm('stash').set(geo)
            out=obj.createNode('null','OUT');out.setInput(0,stash)
            out.setDisplayFlag(True);out.setRenderFlag(True);obj.layoutChildren()
            from .preview import attach
            attach(obj, out)
        batch.layoutChildren();batch.setUserData('rtc_import_session',session)
        batch.setUserData('rtc_max_source',str(source))
        batch.setSelected(True,clear_all_selected=True)
        # 'Unknown material' native FBX warnings are expected: all assignments
        # above have been replaced with validated RenderCore sidecar materials.
        report={'batch':batch.path(),'objects':[n for n,_,_ in prepared],
                'materials':len(materials),'warnings':sorted(set(warnings)),
                'materials_created':len(created),'materials_reused':len(reused),
                'reused_material_names':reused,
                'fbx_messages':messages,'source':str(source),
                'axis':'Max Z-up -> Houdini Y-up; meters; OBJ transforms baked'}
        batch.setUserData('rtc_import_report',json.dumps(report))
        return report
    except Exception:
        if batch:batch.destroy()
        for mat in created:mat.destroy()
        raise
    finally:
        if native:native.destroy()
        else:
            # A failed native import may create nodes before returning its root.
            for n in hou.node('/obj').children():
                if n.path() not in before and n!=batch:n.destroy()


def import_dialog():
    try:
        report=import_from_max()
        hou.ui.displayMessage('Imported from Max: '+str(len(report['objects']))+' objects, '+str(report['materials'])+
            ' RenderCore materials.\nCreated: '+str(report['materials_created'])+', reused: '+str(report['materials_reused'])+
            '\n'+report['batch']+ ('\nWarnings: '+str(len(report['warnings'])) if report['warnings'] else ''))
    except Exception as e:hou.ui.displayMessage(str(e),severity=hou.severityType.Error)
