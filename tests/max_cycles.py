from pymxs import runtime as rt
from pathlib import Path
import json,hashlib,time,traceback
ROOT=Path(__file__).resolve().parents[1]
EX=ROOT/'tests/sandbox/CarXBridge'
scripts=Path(str(rt.getDir(rt.Name('userscripts'))))/'CarXBridge'
hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in scripts.iterdir() if p.suffix in ('.ms','.py')}
def used():
    found={};slots=0
    for o in rt.geometry:
        if not o.material:continue
        mats=[o.material] if rt.getNumSubMtls(o.material)==0 else list(o.material)
        for m in mats:
            if m is None:continue
            slots+=1;found.setdefault(str(m.name),set()).add(int(rt.getHandleByAnim(m)))
    return {'names':sorted(found),'instances':{k:len(v) for k,v in found.items()},'slots':slots}
try:
    rt.loadMaxFile(str(ROOT/'tests/Liza_Houdini_VERSION.max'),quiet=True)
    rt.units.SystemType=rt.Name('meters');rt.units.SystemScale=1
    rt.saveMaxFile(str(ROOT/'tests/cycles_VERSION.max'),quiet=True)
    rt.fileIn(str(scripts/'BridgeConnector.ms'))
    rt.select(list(rt.geometry))
    rt.CarXBridge_Rollout.CarXBridge_Export(str(EX/'TMP_MAX.fbx'))
    (ROOT/'tests/max_ready_0.json').write_text(json.dumps(used()),encoding='utf-8')
    # Max Python ExecuteFile shares global variables with this driver. Never
    # use i/material/obj here: the unchanged stock scripts overwrite those.
    for cycle_index in range(3):
        request=ROOT/'tests'/('request_'+str(cycle_index)+'.json')
        deadline=time.monotonic()+240
        while not request.is_file():
            if time.monotonic()>deadline:raise TimeoutError('Houdini cycle '+str(cycle_index))
            time.sleep(.3)
        rt.CarXBridge_Rollout.CarXBridge_Import(str(EX/'TMP_BLENDER.fbx'))
        result=used()
        result['objects']=[str(o.name) for o in rt.geometry]
        result['scripts_unchanged']=hashes=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in scripts.iterdir() if p.suffix in ('.ms','.py')}
        rt.select(list(rt.geometry))
        rt.CarXBridge_Rollout.CarXBridge_Export(str(EX/'TMP_MAX.fbx'))
        (ROOT/'tests'/('max_ready_'+str(cycle_index+1)+'.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
    rt.saveMaxFile(str(ROOT/'tests/cycles_VERSION.max'),quiet=True)
except Exception:
    (ROOT/'tests/max_error.json').write_text(json.dumps({'error':traceback.format_exc()},indent=2),encoding='utf-8')
