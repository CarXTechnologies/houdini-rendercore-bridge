import hou,sys,json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'package/python3.11libs'))
from rtc_bridge import import_from_max,export_nodes,material_data,create_material
from rtc_bridge.bridge import scene_materials_by_name
EX=ROOT/'tests/sandbox/CarXBridge'
hou.hipFile.clear(suppress_save_prompt=True)
checks=[];cycles=[]
def check(label,value):
    checks.append({'check':label,'passed':bool(value)})
    if not value:raise AssertionError(label)
def wait(i):
    file=ROOT/'tests'/('max_ready_'+str(i)+'.json');deadline=time.monotonic()+200
    while not file.is_file():
        error=ROOT/'tests/max_error.json'
        if error.is_file():raise RuntimeError(error.read_text())
        if time.monotonic()>deadline:raise TimeoutError(str(file))
        time.sleep(.3)
    return json.loads(file.read_text())
def receive():return import_from_max(EX/'TMP_MAX.fbx',EX/'Materials')
def assigned(batch):
    return {p.attribValue('shop_materialpath') for obj in hou.node(batch).children() for p in obj.displayNode().geometry().prims()}
wait(0)
original=None
for i in range(3):
    wait(i)
    if i==1:
        index=scene_materials_by_name()
        index['plaster_9'].parm('rc_variant').set('15')
        index['roof_3'].parm('rc_scale1').set(2.75)
    report=receive();canonical=scene_materials_by_name()
    paths={k:m.path() for k,m in canonical.items()}
    if i==0:original=paths.copy()
    check('Cycle '+str(i)+' stable 18 materials',len(canonical)==18 and paths==original)
    check('Cycle '+str(i)+' created/reused',report['materials_created']==(18 if i==0 else 0) and report['materials_reused']==(0 if i==0 else 18))
    check('Cycle '+str(i)+' all assignments existing',assigned(report['batch'])==set(original.values()))
    if i>0:
        check('Cycle '+str(i)+' shader edit preserved',canonical['plaster_9'].evalParm('rc_variant')=='15')
        check('Cycle '+str(i)+' tiling edit preserved',canonical['roof_3'].evalParm('rc_scale1')==2.75)
    export=export_nodes([hou.node(report['batch'])],EX)
    check('Cycle '+str(i)+' export exactly 18 materials',export['materials']==18 and len(list((EX/'MaterialsBlender').glob('*.json')))==18)
    (ROOT/'tests'/('request_'+str(i)+'.json')).write_text('{}')
    max_result=wait(i+1)
    check('Cycle '+str(i)+' Max six objects',len(max_result['objects'])==6)
    check('Cycle '+str(i)+' Max stable material names',len(max_result['names'])==18)
    check('Cycle '+str(i)+' Max scripts unchanged',max_result['scripts_unchanged'])
    cycles.append({'import':report,'export':export,'max':max_result})
# Simulate an already polluted 1.1 scene: a duplicate carries different settings
# and a broken texture path. It must not block export when the original exists.
canonical=scene_materials_by_name();data=material_data(canonical['plaster_9'])
duplicate=create_material(data['name'],dict(data,variant='1'))
duplicate.parm('rc_baseMap').set('C:/missing/legacy_duplicate.png')
latest=hou.node(report['batch'])
for obj in latest.children():
    stash=obj.node('FROM_MAX');geo=hou.Geometry();geo.merge(stash.geometry())
    for prim in geo.prims():
        if prim.attribValue('shop_materialpath')==canonical['plaster_9'].path():prim.setAttribValue('shop_materialpath',duplicate.path())
    stash.parm('stash').set(geo)
before=assigned(report['batch'])
legacy=export_nodes([latest],ROOT/'tests/legacy_exchange')
check('Legacy conflict resolves to original',legacy['material_aliases'].get(duplicate.path())==canonical['plaster_9'].path())
check('Legacy export does not alter source assignments',before==assigned(report['batch']))
check('Legacy material and its edits retained',duplicate.evalParm('rc_variant')=='1' and duplicate.evalParm('rc_baseMap')=='C:/missing/legacy_duplicate.png')
again=receive()
check('Legacy reimport creates no additional materials',again['materials_created']==0 and again['materials_reused']==18)
check('Legacy reimport assigns original',assigned(again['batch'])==set(original.values()))
check('Canonical material settings unchanged',material_data(canonical['plaster_9'])==data)
hou.hipFile.save(str(ROOT/'tests/repeated_exchange.hip'))
out={'passed':all(c['passed'] for c in checks),'checks':checks,'cycles':cycles,'legacy':legacy}
(ROOT/'tests/reuse_checks.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
print('PASSED',len(checks),'checks; 3 full Max/Houdini cycles; 18 stable materials')
