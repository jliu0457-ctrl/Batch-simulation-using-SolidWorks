namespace Fixed45 {
using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW=SolidWorks.Interop.sldworks;
public static class RepairAssembly {
 static object[] A(object x){return x as object[]??new object[0];}
 static System.Collections.Generic.Dictionary<string,object> O(){return new System.Collections.Generic.Dictionary<string,object>();}
 static void CloseOwned(SW.ISldWorks a,string root){var ts=new List<string>();var d=(SW.IModelDoc2)a.GetFirstDocument();while(d!=null){if(d.GetPathName().StartsWith(root+"\\",StringComparison.OrdinalIgnoreCase))ts.Add(d.GetTitle());d=(SW.IModelDoc2)d.GetNext();}foreach(var t in ts)a.CloseDoc(t);}
 static SW.IFeature Find(SW.IModelDoc2 d,string name){var f=(SW.IFeature)d.FirstFeature();while(f!=null){if(f.Name==name)return f;var s=(SW.IFeature)f.GetFirstSubFeature();while(s!=null){if(s.Name==name)return s;s=(SW.IFeature)s.GetNextSubFeature();}f=(SW.IFeature)f.GetNextFeature();}return null;}
 static object[] Errors(SW.IModelDoc2 d){var es=new List<object>();var f=(SW.IFeature)d.FirstFeature();while(f!=null){if(f.GetErrorCode()!=0)es.Add(new{name=f.Name,code=f.GetErrorCode()});var s=(SW.IFeature)f.GetFirstSubFeature();while(s!=null){if(s.GetErrorCode()!=0)es.Add(new{name=s.Name,code=s.GetErrorCode()});s=(SW.IFeature)s.GetNextSubFeature();}f=(SW.IFeature)f.GetNextFeature();}return es.ToArray();}
 static SW.IFace2 Cylinder(SW.IComponent2 c,bool shaft){
  var p=(SW.IPartDoc)c.GetModelDoc2();var fs=new List<SW.IFace2>();
  foreach(var b in A(p.GetBodies2(0,false)))foreach(var x in A(((SW.IBody2)b).GetFaces())){var f=(SW.IFace2)x;var s=(SW.ISurface)f.GetSurface();if(!s.IsCylinder())continue;var v=(double[])s.CylinderParams;
   if(shaft){if(Math.Abs(v[6]-.0225)<1e-6&&Math.Abs(v[3])>.9999&&Math.Abs(v[1])<1e-6&&Math.Abs(v[2])<1e-6)fs.Add(f);}
   else if(Math.Abs(v[5])>.9999&&Math.Abs(v[0])<1e-6&&Math.Abs(v[1]-.0037)<1e-6)fs.Add(f);
  }if(fs.Count==0)throw new Exception("semantic shaft/bore cylinder missing");return fs.OrderByDescending(z=>z.GetArea()).First();
 }
 static double[] World(SW.IComponent2 c,double[] p,bool vector){var t=(double[])((SW.IMathTransform)c.Transform2).ArrayData;return new[]{p[0]*t[0]+p[1]*t[3]+p[2]*t[6]+(vector?0:t[9]),p[0]*t[1]+p[1]*t[4]+p[2]*t[7]+(vector?0:t[10]),p[0]*t[2]+p[1]*t[5]+p[2]*t[8]+(vector?0:t[11])};}
 static object Axes(SW.IComponent2 body,SW.IComponent2 shaft,SW.IFace2 bf,SW.IFace2 sf){
  bf=Cylinder(body,false);sf=Cylinder(shaft,true);var bp=(double[])((SW.ISurface)bf.GetSurface()).CylinderParams;var sp=(double[])((SW.ISurface)sf.GetSurface()).CylinderParams;
  var b=World(body,bp.Take(3).ToArray(),false);var s=World(shaft,sp.Take(3).ToArray(),false);var bu=World(body,bp.Skip(3).Take(3).ToArray(),true);var su=World(shaft,sp.Skip(3).Take(3).ToArray(),true);
  double dot=bu.Zip(su,(x,y)=>x*y).Sum();double[] delta=s.Zip(b,(x,y)=>x-y).ToArray();double along=delta.Zip(bu,(x,y)=>x*y).Sum();
  double dist=Math.Sqrt(Math.Max(0,delta.Sum(x=>x*x)-along*along))*1000;return new{body_point_m=b,shaft_point_m=s,body_axis=bu,shaft_axis=su,axis_abs_dot=Math.Abs(dot),axis_offset_mm=dist};
 }
 static object AxisState(SW.IModelDoc2 doc){var cs=A(((SW.IAssemblyDoc)doc).GetComponents(false)).Cast<SW.IComponent2>().ToArray();var b=cs.Single(c=>c.Name2.Contains("03阀体"));var s=cs.Single(c=>c.Name2.Contains("04阀轴"));if(b.Transform2==null||s.Transform2==null||b.GetModelDoc2()==null||s.GetModelDoc2()==null)return new{status="component_unavailable",body_suppression=b.GetSuppression(),shaft_suppression=s.GetSuppression()};return Axes(b,s,Cylinder(b,false),Cylinder(s,true));}
 static object MateDetails(SW.IFeature feature){
  var names=new List<string>();try{var mate=(SW.IMate2)feature.GetSpecificFeature2();if(mate!=null)for(int i=0;i<mate.GetMateEntityCount();i++){var entity=(SW.IMateEntity2)mate.MateEntity(i);var component=(SW.IComponent2)entity.ReferenceComponent;names.Add(component==null?"<assembly>":component.Name2);}}catch(Exception ex){names.Add("<error: "+ex.Message+">");}
  return new{name=feature.Name,type=feature.GetTypeName2(),components=names.ToArray()};
 }
 static double[] TransformData(SW.IComponent2 component){
  try{var transform=(SW.IMathTransform)component.Transform2;return transform==null?new double[0]:((double[])transform.ArrayData).ToArray();}
  catch{return new double[0];}
 }
 static object[] ComponentStates(SW.IAssemblyDoc assembly){
  var rows=new List<object>();
  foreach(var component in A(assembly.GetComponents(false)).Cast<SW.IComponent2>().OrderBy(c=>c.Name2)){
   string path="",configuration="";bool isVirtual=false,isFixed=false;
   try{path=component.GetPathName();}catch{}
   try{configuration=component.ReferencedConfiguration;}catch{}
   try{isVirtual=component.IsVirtual;}catch{}
   try{isFixed=component.IsFixed();}catch{}
   rows.Add(new{name=component.Name2,path=path,referenced_configuration=configuration,suppression=component.GetSuppression(),is_virtual=isVirtual,is_fixed=isFixed,transform=TransformData(component)});
  }
  return rows.ToArray();
 }
 static object[] MateStates(SW.IModelDoc2 doc){
  var rows=new List<object>();var feature=(SW.IFeature)doc.FirstFeature();
  while(feature!=null){var type=feature.GetTypeName2();if(type!=null&&type.IndexOf("Mate",StringComparison.OrdinalIgnoreCase)>=0)rows.Add(new{name=feature.Name,type=type,error=feature.GetErrorCode(),details=MateDetails(feature)});feature=(SW.IFeature)feature.GetNextFeature();}
  return rows.ToArray();
 }
 public static object Run(string root){
  return RunFolder(root,"assembly_repair_v4",true);
 }
 public static object RunFolder(string root,string folderName,bool createFromSnapshot){
  var r=O();r["scope"]="working/"+folderName;r["training_ready"]=false;
  string folder=Path.Combine(root,"working",folderName);
  if(createFromSnapshot){if(Directory.Exists(folder))throw new Exception("refuse existing repair folder");Directory.CreateDirectory(folder);foreach(var p in Directory.GetFiles(Path.Combine(root,"source_snapshot"),"*.SLD*"))File.Copy(p,Path.Combine(folder,Path.GetFileName(p)));}
  else if(!Directory.Exists(folder))throw new DirectoryNotFoundException(folder);
  object com;try{com=Marshal.GetActiveObject("SldWorks.Application.34");}catch{com=Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application.34",true));}var app=(SW.ISldWorks)com;CloseOwned(app,root);
  try{
   int er=0,wa=0;var path=Directory.GetFiles(folder,"*.SLDASM").Single();var doc=(SW.IModelDoc2)app.OpenDoc6(path,2,1,"",ref er,ref wa);if(doc==null||doc.GetPathName()!=path)throw new Exception("assembly path mismatch");
   int act=0;app.ActivateDoc3(doc.GetTitle(),false,0,ref act);
   var assy=(SW.IAssemblyDoc)doc;var comps=A(assy.GetComponents(false)).Cast<SW.IComponent2>().ToArray();var body=comps.Single(c=>c.Name2.Contains("03阀体"));var shaft=comps.Single(c=>c.Name2.Contains("04阀轴"));
   var pathChecks=new List<object>();r["component_path_checks"]=pathChecks;string expectedRoot=Path.GetFullPath(folder).TrimEnd('\\')+"\\";
   foreach(var component in comps){string componentPath="";try{componentPath=component.GetPathName();}catch{}bool isVirtual=false;try{isVirtual=component.IsVirtual;}catch{}
    // 虚拟零部件（焊缝/入口管道/出口管道）的数据存在装配体内部，GetPathName() 会返回
    // SolidWorks 的临时释放目录（...\Temp\swx*\VC~~\...），不属于“引用逃逸”，必须排除。
    bool external=componentPath.Length>0 && !isVirtual;bool underRun=!external||Path.GetFullPath(componentPath).StartsWith(expectedRoot,StringComparison.OrdinalIgnoreCase);pathChecks.Add(new{name=component.Name2,path=componentPath,is_virtual=isVirtual,is_external=external,under_run_folder=underRun});if(!underRun)throw new Exception("component reference escaped run folder: "+component.Name2+" -> "+componentPath);}
   r["initial_active_configuration"]=doc.ConfigurationManager.ActiveConfiguration.Name;r["initial_components"]=ComponentStates(assy);r["initial_mates"]=MateStates(doc);
   var bf=Cylinder(body,false);var sf=Cylinder(shaft,true);var before=new List<object>();r["before_configurations"]=before;
   foreach(var conf in new[]{"默认","开度45°"}){doc.ShowConfiguration2(conf);doc.ForceRebuild3(false);before.Add(new{config=conf,axis=AxisState(doc),errors=Errors(doc)});}
   doc.ShowConfiguration2("开度45°"); comps=A(assy.GetComponents(false)).Cast<SW.IComponent2>().ToArray();body=comps.Single(c=>c.Name2.Contains("03阀体"));shaft=comps.Single(c=>c.Name2.Contains("04阀轴"));
   r["before_mate_components"]=ComponentStates(assy);r["before_mate_mates"]=MateStates(doc);
   var preserved=new List<object>();r["preserved_locks"]=preserved;var lockDetails=new List<object>();r["lock_details"]=lockDetails;
   foreach(var name in new[]{"锁定1","锁定2","锁定3"}){var f=Find(doc,name);if(f==null)throw new Exception("missing lock "+name);lockDetails.Add(MateDetails(f));preserved.Add(new{name=name,preserved=true});}
   doc.ClearSelection2(true);var sm=(SW.ISelectionMgr)doc.SelectionManager;var sd=sm.CreateSelectData();sd.Mark=1;
   bf=Cylinder(body,false);sf=Cylinder(shaft,true);var bfe=(SW.IEntity)body.GetCorrespondingEntity(bf);var sfe=(SW.IEntity)shaft.GetCorrespondingEntity(sf);
   if(!bfe.Select4(false,sd)||!sfe.Select4(true,sd))throw new Exception("cylinder face selection failed");
   int me=0;var mate=assy.AddMate5(1,2,false,0,0,0,1,1,0,0,0,false,false,0,out me);r["mate_error"]=me;r["mate_error_enum_meaning"]="swAddMateError_NoError=1";r["mate_created"]=mate!=null;if(mate==null||me!=1)throw new Exception("concentric shaft locating mate failed");
   doc.ClearSelection2(true);doc.ForceRebuild3(false);r["after_mate_components"]=ComponentStates(assy);r["after_mate_mates"]=MateStates(doc);
   var states=new List<object>();r["angle_sweep"]=states;
   var angle=(SW.IDimension)doc.Parameter("D1@角度2");if(angle==null)throw new Exception("opening angle dimension missing");
   foreach(double deg in new[]{45.0}){int code=angle.SetSystemValue3(deg*Math.PI/180,1,null);bool rebuild=doc.ForceRebuild3(false);states.Add(new{requested_deg=deg,actual_deg=angle.SystemValue*180/Math.PI,set_status=code,rebuild_ok=rebuild,axis=AxisState(doc),errors=Errors(doc)});}
   angle.SetSystemValue3(Math.PI/4,1,null);r["final_rebuild_ok"]=doc.ForceRebuild3(false);r["final_errors"]=Errors(doc);r["before_save_components"]=ComponentStates(assy);r["before_save_mates"]=MateStates(doc);int se=0,sw=0;r["save_ok"]=doc.Save3(1,ref se,ref sw);r["save_errors"]=se;
   CloseOwned(app,root);
   int oe=0,ow=0;doc=(SW.IModelDoc2)app.OpenDoc6(path,2,3,"开度45°",ref oe,ref ow);assy=(SW.IAssemblyDoc)doc;comps=A(assy.GetComponents(false)).Cast<SW.IComponent2>().ToArray();body=comps.Single(c=>c.Name2.Contains("03阀体"));shaft=comps.Single(c=>c.Name2.Contains("04阀轴"));
   r["reopen_axis"]=AxisState(doc);r["reopen_errors"]=Errors(doc);r["reopen_active_configuration"]=doc.ConfigurationManager.ActiveConfiguration.Name;r["reopen_components"]=ComponentStates(assy);r["reopen_mates"]=MateStates(doc);r["status"]="repair_trial_finished";
  }catch(Exception ex){r["status"]="repair_trial_failed";r["error"]=ex.ToString();}
  finally{CloseOwned(app,root);}return r;
 }
}
}
