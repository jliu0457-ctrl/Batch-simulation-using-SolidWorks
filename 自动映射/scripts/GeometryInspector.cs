using System;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW=SolidWorks.Interop.sldworks;
public static class GeometryInspector {
 static Dictionary<string,object> O(){return new Dictionary<string,object>();}
 static object[] A(object x){return x as object[] ?? new object[0];}
 static double[] P(object x){var p=(SW.ISketchPoint)x;return new[]{p.X,p.Y,p.Z};}
 static object Entity(object x){
  var d=O();
  if(x is SW.ISketchPoint){d["kind"]="point";d["p"]=P(x);}
  else if(x is SW.ISketchSegment){
   var s=(SW.ISketchSegment)x;d["kind"]="segment";d["type"]=s.GetType();d["construction"]=s.ConstructionGeometry;
   if(s.GetType()==0){var l=(SW.ISketchLine)x;d["start"]=P(l.GetStartPoint2());d["end"]=P(l.GetEndPoint2());}
   else if(s.GetType()==1){var c=(SW.ISketchArc)x;d["center"]=P(c.GetCenterPoint2());d["radius"]=c.GetRadius();}
  }else{d["kind"]="other";}
  return d;
 }
 static object Surface(SW.IFace2 f){
  var d=O();var s=(SW.ISurface)f.GetSurface();d["area"]=f.GetArea();d["box"]=f.GetBox();
  if(s.IsCone()){d["kind"]="cone";d["params"]=s.ConeParams2;}
  else if(s.IsCylinder()){d["kind"]="cylinder";d["params"]=s.CylinderParams;}
  else if(s.IsPlane()){d["kind"]="plane";d["params"]=s.PlaneParams;}
  else d["kind"]="other";
  return d;
 }
 static object Feature(SW.IFeature f,int depth){
  var d=O();d["name"]=f.Name;d["type"]=f.GetTypeName2();d["error_code"]=f.GetErrorCode();var dims=new List<object>();d["dimensions"]=dims;
  try{object dd=f.GetFirstDisplayDimension();int n=0;while(dd!=null&&n++<200){var disp=(SW.IDisplayDimension)dd;var dim=(SW.IDimension)disp.GetDimension2(0);var dm=O();
    dm["name"]=dim.FullName;dm["value_SI"]=dim.SystemValue;dm["display_type"]=disp.GetType();
    var an=(SW.IAnnotation)disp.GetAnnotation();var ents=new List<object>();
    foreach(var x in A(an.GetAttachedEntities3())){try{ents.Add(Entity(x));}catch{}}
    dm["attached"]=ents;dims.Add(dm);dd=f.GetNextDisplayDimension(dd);
  }}catch(Exception ex){d["dim_error"]=ex.Message;}
  try{
   object spec=f.GetSpecificFeature2();
   if(spec is SW.ISketch){var sk=(SW.ISketch)spec;d["transform"]=((SW.IMathTransform)sk.ModelToSketchTransform).ArrayData;var segs=new List<object>();
     foreach(var s in A(sk.GetSketchSegments()))segs.Add(Entity(s));d["segments"]=segs;
   }
   if(spec is SW.IMate2){var m=(SW.IMate2)spec;d["mate_type"]=m.Type;var ents=new List<object>();
    for(int i=0;i<m.GetMateEntityCount();i++){var me=(SW.IMateEntity2)m.MateEntity(i);var e=O();e["type"]=me.ReferenceType2;e["params"]=me.EntityParams;
     var co=(SW.IComponent2)me.ReferenceComponent;e["component"]=co==null?null:co.Name2;ents.Add(e);}d["mate_entities"]=ents;
   }
  }catch(Exception ex){d["specific_error"]=ex.Message;}
  if(depth<3){var subs=new List<object>();d["subfeatures"]=subs;var sub=(SW.IFeature)f.GetFirstSubFeature();int n=0;
   while(sub!=null&&n++<200){subs.Add(Feature(sub,depth+1));sub=(SW.IFeature)sub.GetNextSubFeature();}
  }return d;
 }
 public static object Run(string root){
  var r=O();var ds=new List<object>();r["documents"]=ds;
  object com;try{com=Marshal.GetActiveObject("SldWorks.Application.34");}catch{com=Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application.34",true));}var app=(SW.ISldWorks)com;r["revision"]=app.RevisionNumber();
  foreach(var path in Directory.GetFiles(Path.Combine(root,"working","baseline"),"*.SLD*")){
   var d=O();d["file"]=Path.GetFileName(path);ds.Add(d);
   try{int er=0,wa=0;int ty=path.EndsWith("SLDASM")?2:1;var doc=(SW.IModelDoc2)app.OpenDoc6(path,ty,3,"",ref er,ref wa);
    d["open_errors"]=er;var fs=new List<object>();d["features"]=fs;var f=(SW.IFeature)doc.FirstFeature();int n=0;
    while(f!=null&&n++<1000){fs.Add(Feature(f,0));f=(SW.IFeature)f.GetNextFeature();}
    if(ty==1){var bs=new List<object>();d["bodies"]=bs;foreach(var x in A(((SW.IPartDoc)doc).GetBodies2(0,false))){var b=(SW.IBody2)x;var bd=O();bd["name"]=b.Name;bd["box"]=b.GetBodyBox();var faces=new List<object>();foreach(var fc in A(b.GetFaces()))faces.Add(Surface((SW.IFace2)fc));bd["faces"]=faces;bs.Add(bd);}}
    if(ty==2){var cs=new List<object>();d["components"]=cs;foreach(var x in A(((SW.IAssemblyDoc)doc).GetComponents(false))){var c=(SW.IComponent2)x;var co=O();co["name"]=c.Name2;co["transform"]=((SW.IMathTransform)c.Transform2).ArrayData;cs.Add(co);}}
   }catch(Exception ex){d["error"]=ex.ToString();}
  }r["status"]="read_complete";return r;
 }
}