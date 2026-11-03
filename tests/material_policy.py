import hou,sys,json,hashlib,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'package/python3.11libs'))
from rtc_bridge import create_material,export_nodes
from rtc_bridge.bridge import scene_materials_by_name
hou.hipFile.clear(suppress_save_prompt=True)
original=create_material('Shared')
duplicate=create_material('Shared',dict(variant='1',collision='none',textures=[]))
independent=create_material('Shared1')
index=scene_materials_by_name()
assert index['shared']==original and index['shared1']==independent
o=hou.node('/obj').createNode('geo','ReuseProbe');b=o.createNode('box');m=o.createNode('material');m.setInput(0,b)
m.parm('shop_materialpath1').set(duplicate.path());m.setDisplayFlag(True);m.setRenderFlag(True)
before=o.asCode(recurse=True);data=original.asCode(recurse=True)
result=export_nodes([o],ROOT/'tests/policy_exchange')
sidecars=[json.loads(p.read_text()) for p in (ROOT/'tests/policy_exchange/MaterialsBlender').glob('*.json')]
fbx=Path(result['fbx']).read_text()
checks={
    'Original node preferred over suffixed duplicate':index['shared']==original,
    'Intentionally distinct Source material name Shared1 remains distinct':index['shared1']==independent,
    'Exactly one material emitted':len(sidecars)==1 and sidecars[0]['name']=='Shared' and sidecars[0]['variant']=='11',
    'FBX has exactly one canonical material':len(re.findall(r'Material:\s*\d+,\s*"Material::Shared"',fbx))==1,
    'Original settings unchanged':original.asCode(recurse=True)==data,
    'Source SOPs unchanged':o.asCode(recurse=True)==before,
}
digest=hashlib.sha256(Path(result['fbx']).read_bytes()).hexdigest()
original.parm('rc_baseMap').set('C:/missing/canonical_texture.png')
try:export_nodes([o],ROOT/'tests/policy_exchange')
except ValueError as e:checks['Missing texture on canonical material still rejected']='Missing texture' in str(e)
else:checks['Missing texture on canonical material still rejected']=False
checks['Rejected export preserves previous payload']=digest==hashlib.sha256(Path(result['fbx']).read_bytes()).hexdigest()
report={'passed':all(checks.values()),'checks':checks}
(ROOT/'tests/policy_checks.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
