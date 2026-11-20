"""Render the actual Houdini OpenGL preview on a synthetic alpha comparison."""
import sys,tempfile,json
from pathlib import Path
import hou
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'package/python3.11libs'))
from rtc_bridge.bridge import create_material
from rtc_bridge.preview import attach
OUT=Path(sys.argv[1]) if len(sys.argv)>1 else Path(tempfile.mkdtemp(prefix='rtc_alpha_visual_'))
OUT.mkdir(parents=True,exist_ok=True)
hou.hipFile.clear(suppress_save_prompt=True)
for row,(name,variant) in enumerate([('Layered','0000'),('Decal','200'),('Opaque','11')]):
    mat=create_material(name,dict(name=name,variant=variant,collision='none',textures=[]))
    # Stable unlit comparison, not a lighting/shader fidelity test.
    group=mat.parmTemplateGroup()
    lighting=hou.ToggleParmTemplate('ogl_light','Viewport lighting',default_value=False)
    group.append(lighting);mat.setParmTemplateGroup(group)
    obj=hou.node('/obj').createNode('geo',name)
    src=obj.createNode('stash','AUTHORED');g=hou.Geometry()
    g.addAttrib(hou.attribType.Point,'Alpha',1.)
    g.addAttrib(hou.attribType.Point,'Cd',(0.,0.,0.))
    g.addAttrib(hou.attribType.Prim,'shop_materialpath',mat.path())
    for col,a in enumerate([0.,.25,.5,1.]):
        face=g.createPolygon()
        face.setAttribValue('shop_materialpath',mat.path())
        for x,y in [(0,0),(1,0),(1,1),(0,1)]:
            p=g.createPoint();p.setPosition((col*1.5+x,(2-row)*1.5+y,0));p.setAttribValue('Alpha',a);face.addVertex(p)
    src.parm('stash').set(g)
    pre=attach(obj,src)
    # OpenGL ROP uses render flags; ONLY this synthetic scene renders the
    # display stream. Normal scenes retain the authored render stream.
    pre.setRenderFlag(True)
cam=hou.node('/obj').createNode('cam','preview_camera')
cam.parmTuple('t').set((2.75,2.,10.));cam.parm('projection').set('ortho');cam.parm('orthowidth').set(7.)
cam.parmTuple('res').set((1400,900))
rop=hou.node('/out').createNode('opengl','preview_check')
rop.parm('camera').set(cam.path());rop.parm('picture').set(str(OUT/'alpha_preview.png'))
rop.parm('tres').set(1);rop.parmTuple('res').set((1400,900));rop.parm('shadows').set(0)
rop.parm('transparency').set(1);rop.parm('hqlighting').set(0)
rop.parm('vobjects').set('/obj/Layered /obj/Decal /obj/Opaque')
hou.hipFile.save(str(OUT/'alpha_preview.hip'))
rop.render()
print(json.dumps({'image':str(OUT/'alpha_preview.png'),'errors':rop.errors(),'warnings':rop.warnings()}))
