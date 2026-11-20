"""Houdini 20.5 integration checks; all exchange is isolated in a fresh temp dir."""
import hashlib
import json
import sys
import tempfile
import os
from pathlib import Path
import hou

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'package/python3.11libs'))
from rtc_bridge.bridge import create_material, material_data, normalize_navmesh, sync_preview, export_nodes, _selection
from rtc_bridge.importer import _read_materials, import_from_max
from rtc_bridge.preview import attach, refresh, alpha_role

OUT = Path(tempfile.mkdtemp(prefix='rtc_navmesh_alpha_'))
checks = {}


def check(name, condition):
    checks[name] = bool(condition)
    if not condition:
        raise AssertionError(name)


hou.hipFile.clear(suppress_save_prompt=True)
for value, expected in [('road','road'), ('walk','walk'), ('block','block'), ('  ROAD ','road'),
                        (0,'block'), (1,'walk'), (2,'road'), (True,'block'), ('unknown','block')]:
    check('NavMesh normalization '+repr(value), normalize_navmesh(value) == expected)

mats={}
for name, variant in [('Layered','0000'), ('Decal','200'), ('DecalTest','100'),
                      ('DecalLayered','400'), ('DecalLayeredTest','300'), ('Opaque','11'),
                      ('StandardBlend','523'), ('StandardTest','1035'), ('Collider','10')]:
    mat=create_material(name,dict(name=name,variant=variant,collision='asphalt',use_collision=True,navmesh_type='road',textures=[]))
    mats[name]=mat
    check(name+' NavMesh transport', material_data(mat)['navmesh_type']=='road')
    expected='darken' if name=='Layered' else 'opacity' if 'Decal' in name else 'ignore'
    check(name+' alpha interpretation',alpha_role(mat)==expected)
    check(name+' point alpha toggle',mat.evalParm('opacpointalpha')==(1 if expected=='opacity' else 0))

obj=hou.node('/obj').createNode('geo','AlphaProbe')
source=obj.createNode('stash','AUTHORED')
geo=hou.Geometry()
alphas=(0.0,0.25,0.5,1.0)
aa=geo.addAttrib(hou.attribType.Point,'Alpha',1.0)
cd=geo.addAttrib(hou.attribType.Point,'Cd',(0.0,0.0,0.0))
ma=geo.addAttrib(hou.attribType.Prim,'shop_materialpath','')
for row,name in enumerate(mats):
    for col,alpha in enumerate(alphas):
        prim=geo.createPolygon()
        for x,y in [(0,0),(1,0),(1,1),(0,1)]:
            pt=geo.createPoint();pt.setPosition((col*1.3+x,row*1.3+y,0))
            pt.setAttribValue(aa,alpha);pt.setAttribValue(cd,(0.2,0.4,0.6));prim.addVertex(pt)
        prim.setAttribValue(ma,mats[name].path())
source.parm('stash').set(geo)
source.setDisplayFlag(True);source.setRenderFlag(True)
original=source.geometry().freeze()
preview=attach(obj,source)
result=preview.geometry()
check('Display SOP cooks',not preview.errors())
check('Authored SOP keeps render flag',obj.renderNode()==source)
check('Preview is display only',obj.displayNode()==preview and not preview.isRenderFlagSet())
check('Selected preview resolves authored output',_selection([preview])==[(obj,source)])
check('Source point alpha unchanged',source.geometry().pointFloatAttribValues('Alpha')==original.pointFloatAttribValues('Alpha'))
check('Source Cd unchanged',source.geometry().pointFloatAttribValues('Cd')==original.pointFloatAttribValues('Cd'))
for row,name in enumerate(mats):
    for col,a in enumerate(alphas):
        v=result.prims()[row*4+col].vertices()[0]
        role=alpha_role(mats[name])
        expected_color=(a,a,a) if role=='darken' else (1.,1.,1.)
        expected_alpha=a**2 if name=='DecalLayeredTest' else a if role=='opacity' else 1.
        check(f'{name} alpha={a} preview color',all(abs(x-y)<1e-6 for x,y in zip(v.attribValue('Cd'),expected_color)))
        check(f'{name} alpha={a} preview opacity',abs(v.attribValue('Alpha')-expected_alpha)<1e-6)

# Live recook after painting upstream; no button press required.
edit=hou.Geometry();edit.merge(source.geometry());edit.points()[0].setAttribValue('Alpha',0.75)
source.parm('stash').set(edit)
check('Upstream painting updates live',preview.geometry().prims()[0].vertices()[0].attribValue('Cd')==(0.75,0.75,0.75))
mats['Layered'].parm('rc_variant').set('200')
sync_preview(mats['Layered']);preview.cook(force=True)
check('Variant change switches alpha role',preview.geometry().prims()[0].vertices()[0].attribValue('Alpha')==0.75)
mats['Layered'].parm('rc_variant').set('0000');sync_preview(mats['Layered'])
refresh([obj]);refresh([obj])
check('Refresh does not accumulate SOPs',sum(n.userData('rtc_alpha_preview')=='1' for n in obj.children())==1)
downstream=obj.createNode('null','WRONG_PAINT_POSITION');downstream.setInput(0,preview)
try:_selection([downstream])
except ValueError as e:check('Reject export of display-derived geometry','before RC_ALPHA_PREVIEW' in str(e))
else:check('Reject export of display-derived geometry',False)
downstream.destroy()

# Mixed material faces on shared points must get independent corner values.
shared=hou.Geometry();p=[shared.createPoint() for _ in range(4)]
for pt,pos in zip(p,[(0,0,0),(1,0,0),(1,1,0),(0,1,0)]):pt.setPosition(pos)
shared.addAttrib(hou.attribType.Point,'Alpha',0.25)
shared.addAttrib(hou.attribType.Prim,'shop_materialpath','')
for name in ['Layered','Decal']:
    prim=shared.createPolygon()
    for pt in p:prim.addVertex(pt)
    prim.setAttribValue('shop_materialpath',mats[name].path())
source.parm('stash').set(shared)
r=preview.geometry()
check('Shared points layered remains opaque',r.prims()[0].vertices()[0].attribValue('Alpha')==1.)
check('Shared points decal remains transparent',r.prims()[1].vertices()[0].attribValue('Alpha')==0.25)

# Transport, native FBX reimport and material reuse.
exp=export_nodes([preview],OUT/'exchange')
records=[json.loads(p.read_text()) for p in (OUT/'exchange/MaterialsBlender').glob('*.json')]
check('All JSON records carry NavMesh',all(d['navmesh_type']=='road' for d in records))
folder=OUT/'materials';folder.mkdir()
for i,d in enumerate(records):(folder/(str(i)+'.mat')).write_text(json.dumps(d),encoding='utf-8')
check('Max sidecar NavMesh roundtrip',all(d['navmesh_type']=='road' for d in _read_materials(folder).values()))
mats['Layered'].parm('rc_navmesh_type').set('walk')
report=import_from_max(exp['fbx'],folder)
check('Reimport reuses existing materials',report['materials_created']==0 and report['materials_reused']==2)
check('Reuse preserves locally edited NavMesh',mats['Layered'].evalParm('rc_navmesh_type')=='walk')
batch=hou.node(report['batch'])
incoming=batch.children()[0]
check('Imported batch has live preview',incoming.displayNode().userData('rtc_alpha_preview')=='1')
check('FBX export preserves raw alpha',set(round(v,6) for v in incoming.renderNode().geometry().pointFloatAttribValues('Alpha') or
      incoming.renderNode().geometry().vertexFloatAttribValues('Alpha'))=={0.25})
old=records[0].copy();old.pop('navmesh_type')
(folder/'0.mat').write_text(json.dumps(old),encoding='utf-8')
check('Legacy sidecar defaults to block',_read_materials(folder)[old['name']]['navmesh_type']=='block')

# Exercise the shelf-path warning without touching the user's Max exchange.
sandbox=OUT/'sandbox';sandbox.mkdir()
fake=OUT/'fake_max';fake.mkdir()
ini=fake/'settings.ini'
ini.write_text('[Path]\nBRIDGE='+str(sandbox/'CarXBridge')+'\n[Import]\nGlobalScale=1\n',encoding='utf-8')
(fake/'import_materials.py').write_text('# legacy addon\n',encoding='utf-8')
os.environ['RTC_MAX_BRIDGE_INI']=str(ini)
tempfile.tempdir=str(sandbox)
warned=export_nodes([preview])
check('Old Max addon produces NavMesh warning',any('navmesh_type' in s for s in warned['warnings']))
(fake/'import_materials.py').write_text('# updated transport reads navmesh_type\n',encoding='utf-8')
supported=export_nodes([preview])
check('Updated addon marker removes warning',not any('navmesh_type' in s for s in supported['warnings']))
check('Warning path uses isolated exchange',str(sandbox) in supported['fbx'])

hou.hipFile.save(str(OUT/'verification.hip'))
result={'passed':all(checks.values()),'checks':checks,'directory':str(OUT)}
(OUT/'checks.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({'passed':result['passed'],'checks':len(checks),'directory':str(OUT)}))
