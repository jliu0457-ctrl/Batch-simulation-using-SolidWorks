using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;
using SW=SolidWorks.Interop.sldworks;
public static class CadExperiments {
 static Dictionary<string,object> O(){return new Dictionary<string,object>();}
 static object[] A(object x){return x as object[] ?? new object[0];}
 static List<object> Errors(SW.IModelDoc2 d){var es=new List<object>();var f=(SW.IFeature)d.FirstFeature();int n=0;while(f!=null&&n++<1000){int e=f.GetErrorCode();if(e!=0)es.Add(new {name=f.Name,code=e});var sub=(SW.IFeature)f.GetFirstSubFeature();int sn=0;while(sub!=null&&sn++<300){int se=sub.GetErrorCode();if(se!=0)es.Add(new{name=sub.Name,code=se});sub=(SW.IFeature)sub.GetNextSubFeature();}f=(SW.IFeature)f.GetNextFeature();}return es;}
 static List<object> Measure(SW.IModelDoc2 doc){
  var outp=new List<object>();foreach(var x in A(((SW.IPartDoc)doc).GetBodies2(0,false))){var b=(SW.IBody2)x;var bd=O();bd["name"]=b.Name;bd["box"]=b.GetBodyBox();var extremes=new List<object>();foreach(var dir in new[]{new[]{1.0,0,0},new[]{-1.0,0,0},new[]{0.0,1,0},new[]{0.0,-1,0},new[]{0.0,0,1},new[]{0.0,0,-1}}){double px=0,py=0,pz=0;bool good=b.GetExtremePoint(dir[0],dir[1],dir[2],out px,out py,out pz);extremes.Add(new{direction=dir,point=new[]{px,py,pz},ok=good});}bd["extremes"]=extremes;bd["mass_properties_density1"]=b.GetMassProperties(1);
   var surfaces=new List<object>();foreach(var y in A(b.GetFaces())){var f=(SW.IFace2)y;var s=(SW.ISurface)f.GetSurface();if(s.IsCone()||s.IsCylinder()||s.IsPlane()){var fd=O();fd["kind"]=s.IsCone()?"cone":s.IsCylinder()?"cylinder":"plane";fd["params"]=s.IsCone()?s.ConeParams2:s.IsCylinder()?s.CylinderParams:s.PlaneParams;fd["area_m2"]=f.GetArea();surfaces.Add(fd);}}
   bd["surfaces"]=surfaces;outp.Add(bd);
  }return outp;
 }
 static void CloseOwned(SW.ISldWorks app,string taskRoot){
  var titles=new List<string>();var doc=(SW.IModelDoc2)app.GetFirstDocument();int n=0;
  while(doc!=null&&n++<500){string p=doc.GetPathName();if(p.StartsWith(taskRoot+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase))titles.Add(doc.GetTitle());doc=(SW.IModelDoc2)doc.GetNext();}
  foreach(var t in titles)app.CloseDoc(t);
 }
 public static object Run(string taskRoot,string planPath){
  taskRoot=Path.GetFullPath(taskRoot).TrimEnd(Path.DirectorySeparatorChar);
  var js=new JavaScriptSerializer();js.MaxJsonLength=50000000;
  var plan=js.Deserialize<Dictionary<string,object>>(File.ReadAllText(planPath));var results=new List<object>();var r=O();r["results"]=results;r["purpose"]="candidate_dimension_sensitivity_only";r["training_ready"]=false;
  object com;try{com=Marshal.GetActiveObject("SldWorks.Application.34");}catch{com=Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application.34",true));}
  var app=(SW.ISldWorks)com;r["revision"]=app.RevisionNumber();if(!app.RevisionNumber().StartsWith("34."))throw new Exception("SolidWorks2026 required");
  CloseOwned(app,taskRoot);
  foreach(var obj in (System.Collections.IEnumerable)plan["experiments"]){
   var ex=(Dictionary<string,object>)obj;var er=O();er["id"]=ex["id"];results.Add(er);var docs=new List<SW.IModelDoc2>();
   try{
    var folder=Path.GetFullPath((string)ex["folder"]);var allowed=Path.Combine(taskRoot,"working","candidate_trials")+Path.DirectorySeparatorChar;
    if(!folder.StartsWith(allowed,StringComparison.OrdinalIgnoreCase)||!Directory.Exists(folder))throw new Exception("folder outside isolated candidate trials");
    var already=(SW.IModelDoc2)app.GetFirstDocument();while(already!=null){if(Directory.GetFiles(folder,"*.SLD*").Any(p=>String.Equals(Path.GetFileNameWithoutExtension(p),Path.GetFileNameWithoutExtension(already.GetTitle()),StringComparison.OrdinalIgnoreCase)))throw new Exception("same-named unowned document open; refusing mutation");already=(SW.IModelDoc2)already.GetNext();}
    var dr=new List<object>();er["documents"]=dr;var ops=((System.Collections.IEnumerable)ex["operations"]).Cast<object>().Select(z=>(Dictionary<string,object>)z).ToArray();bool ok=true;
    foreach(var path in Directory.GetFiles(folder,"*.SLDPRT")){
     var d=O();d["file"]=Path.GetFileName(path);dr.Add(d);int oe=0,ow=0;var doc=(SW.IModelDoc2)app.OpenDoc6(path,1,1,"",ref oe,ref ow);
     if(doc==null||!String.Equals(Path.GetFullPath(doc.GetPathName()),path,StringComparison.OrdinalIgnoreCase))throw new Exception("part open path mismatch");
     docs.Add(doc);d["open_errors"]=oe;d["open_warnings"]=ow;var changes=new List<object>();d["changes"]=changes;
     var precheck=new Dictionary<string,double>();foreach(var pre in ops.Where(z=>(string)z["file"]==Path.GetFileName(path))){var pd=(SW.IDimension)doc.Parameter((string)pre["parameter"]);if(pd==null)throw new Exception("missing "+pre["parameter"]);precheck[(string)pre["parameter"]]=pd.SystemValue;}
     foreach(var op in ops.Where(z=>(string)z["file"]==Path.GetFileName(path))){
      var dim=(SW.IDimension)doc.Parameter((string)op["parameter"]);if(dim==null)throw new Exception("missing parameter "+op["parameter"]);
      double before=precheck[(string)op["parameter"]];double expected=Convert.ToDouble(op["baseline_SI"]);if(Math.Abs(before-expected)>1e-8)throw new Exception("unexpected baseline "+op["parameter"]);
      double requested=Convert.ToDouble(op["value_SI"]);int status=dim.SetSystemValue3(requested,1,null);double after=dim.SystemValue;
      changes.Add(new{parameter=op["parameter"],before_SI=before,requested_SI=requested,after_SI=after,set_status=status});if(status!=0||Math.Abs(after-requested)>1e-8)ok=false;
     }
     d["rebuild_ok"]=doc.ForceRebuild3(false);d["feature_errors"]=Errors(doc);d["measurement"]=Measure(doc);
     int se=0,sw=0;d["save_ok"]=doc.Save3(1,ref se,ref sw);d["save_errors"]=se;d["save_warnings"]=sw;
     if(!(bool)d["rebuild_ok"]||((List<object>)d["feature_errors"]).Count>0||!(bool)d["save_ok"]||se!=0||oe!=0)ok=false;
    }
    var asm=Directory.GetFiles(folder,"*.SLDASM").Single();int ae=0,aw=0;var ad=(SW.IModelDoc2)app.OpenDoc6(asm,2,1,"",ref ae,ref aw);if(ad==null||!String.Equals(ad.GetPathName(),asm,StringComparison.OrdinalIgnoreCase))throw new Exception("assembly open path mismatch");docs.Add(ad);
    var ar=O();er["assembly"]=ar;ar["config"]=ad.ConfigurationManager.ActiveConfiguration.Name;ar["open_errors"]=ae;ar["rebuild_ok"]=ad.ForceRebuild3(false);ar["feature_errors"]=Errors(ad);
    var refs=new List<object>();ar["references"]=refs;
    foreach(var x in A(((SW.IAssemblyDoc)ad).GetComponents(false))){var co=(SW.IComponent2)x;string cp=co.GetPathName();bool virt=co.IsVirtual;refs.Add(new{name=co.Name2,path=cp,is_virtual=virt});if(!virt&&!cp.StartsWith(folder+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase))ok=false;}
    int ase=0,asw=0;ar["save_ok"]=ad.Save3(1,ref ase,ref asw);ar["save_errors"]=ase;ar["save_warnings"]=asw;
    if(!(bool)ar["rebuild_ok"]||((List<object>)ar["feature_errors"]).Count>0||ae!=0||ase!=0||!(bool)ar["save_ok"])ok=false;
    CloseOwned(app,taskRoot);docs.Clear();
    var persisted=new List<object>();er["persisted_readback"]=persisted;
    foreach(var group in ops.GroupBy(z=>(string)z["file"])){
     int e2=0,w2=0;var doc=(SW.IModelDoc2)app.OpenDoc6(Path.Combine(folder,group.Key),1,3,"",ref e2,ref w2);docs.Add(doc);
     foreach(var op in group){var dim=(SW.IDimension)doc.Parameter((string)op["parameter"]);double v=dim.SystemValue;persisted.Add(new{file=group.Key,parameter=op["parameter"],value_SI=v});if(Math.Abs(v-Convert.ToDouble(op["value_SI"]))>1e-8)ok=false;}
    }
    er["cad_edit_rebuild_passed"]=ok;er["physical_mapping_verified"]=false;
   }catch(Exception err){er["error"]=err.ToString();er["cad_edit_rebuild_passed"]=false;}
   finally{CloseOwned(app,taskRoot);}
   File.WriteAllText(Path.Combine(taskRoot,"artifacts","candidate_trials_progress.json"),js.Serialize(r));
  }r["status"]="experiments_finished";return r;
 }
}