param(
    [Parameter(Mandatory=$true)][string]$AssemblyPath,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [switch]$Apply,
    [switch]$Save
)
# Adds a concentric mate between the shaft (04阀轴) working section and the disc (11蝶板)
# stem bore, then reports the mate list, the mate errors, and how far the two axes were
# apart before and after.  Modelled on the working AddMate5 pattern in RepairAssembly.cs.
#
# Without -Apply it only inspects: mate list, axis offset, and which faces it would pick.
# With    -Apply it performs the mate and rebuilds.  The caller is responsible for pointing
# -AssemblyPath at a scratch copy; nothing here saves the document.
# ASCII only; Chinese appears as unicode escapes in the C# source.
$ErrorActionPreference='Stop'
$interop='D:\Program Files\2026SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll'
if(-not (Test-Path -LiteralPath $interop -PathType Leaf)){ throw "interop missing: $interop" }
$source=@'
using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW=SolidWorks.Interop.sldworks;

public static class ShaftDiscConcentric {
 static object[] A(object x){return x as object[] ?? new object[0];}
 static string S(Func<string> f){try{return f();}catch{return "";}}

 static SW.IFeature Find(SW.IModelDoc2 d,string name){
  var f=(SW.IFeature)d.FirstFeature();
  while(f!=null){
   if(f.Name==name) return f;
   var s=(SW.IFeature)f.GetFirstSubFeature();
   while(s!=null){ if(s.Name==name) return s; s=(SW.IFeature)s.GetNextSubFeature(); }
   f=(SW.IFeature)f.GetNextFeature();
  }
  return null;
 }

 static object[] Errors(SW.IModelDoc2 d){
  var es=new List<object>();
  var f=(SW.IFeature)d.FirstFeature();
  while(f!=null){
   if(f.GetErrorCode()!=0) es.Add(new{name=f.Name,type=S(()=>f.GetTypeName2()),code=f.GetErrorCode()});
   var s=(SW.IFeature)f.GetFirstSubFeature();
   while(s!=null){ if(s.GetErrorCode()!=0) es.Add(new{name=s.Name,type=S(()=>s.GetTypeName2()),code=s.GetErrorCode()}); s=(SW.IFeature)s.GetNextSubFeature(); }
   f=(SW.IFeature)f.GetNextFeature();
  }
  return es.ToArray();
 }

 static void WalkMates(SW.IFeature f,List<object> rows){
  while(f!=null){
   string t=S(()=>f.GetTypeName2());
   if(t.IndexOf("Mate",StringComparison.OrdinalIgnoreCase)>=0 && t!="MateGroup"){
    var names=new List<string>();
    try{
     var m=(SW.IMate2)f.GetSpecificFeature2();
     if(m!=null) for(int i=0;i<m.GetMateEntityCount();i++){
      var e=(SW.IMateEntity2)m.MateEntity(i);
      var c=(SW.IComponent2)e.ReferenceComponent;
      names.Add(c==null?"<assembly>":c.Name2);
     }
    }catch(Exception ex){ names.Add("<err "+ex.Message+">"); }
    rows.Add(new Dictionary<string,object>{{"name",f.Name},{"type",t},
      {"error",f.GetErrorCode()},{"suppressed",S(()=>f.IsSuppressed()?"1":"0")},{"components",names.ToArray()}});
   }
   var sub=(SW.IFeature)f.GetFirstSubFeature();
   if(sub!=null) WalkMates(sub,rows);
   f=(SW.IFeature)f.GetNextFeature();
  }
 }
 static object[] MateStates(SW.IModelDoc2 doc){
  var rows=new List<object>();
  WalkMates((SW.IFeature)doc.FirstFeature(),rows);
  return rows.ToArray();
 }

 // every cylindrical face of a component, in PART coordinates
 static List<SW.IFace2> Cyls(SW.IComponent2 c,Func<double[],bool> want){
  var outp=new List<SW.IFace2>();
  var p=c.GetModelDoc2() as SW.IPartDoc; if(p==null) return outp;
  foreach(var b in A(p.GetBodies2(0,false)))
   foreach(var x in A(((SW.IBody2)b).GetFaces())){
    var f=(SW.IFace2)x; var s=f.GetSurface() as SW.ISurface;
    if(s==null||!s.IsCylinder()) continue;
    if(want((double[])s.CylinderParams)) outp.Add(f);
   }
  return outp;
 }

 static double[] CylParams(SW.IFace2 f){ return (double[])((SW.ISurface)f.GetSurface()).CylinderParams; }

 // axis-to-axis distance in world coordinates
 static double[] AxisOffset(SW.IComponent2 a,SW.IFace2 af,SW.IComponent2 b,SW.IFace2 bf){
  var ta=(double[])((SW.IMathTransform)a.Transform2).ArrayData;
  var tb=(double[])((SW.IMathTransform)b.Transform2).ArrayData;
  double[] pa=CylParams(af), pb=CylParams(bf);
  double[] wa=W(ta,new[]{pa[0],pa[1],pa[2]}); double[] ua=Wv(ta,new[]{pa[3],pa[4],pa[5]});
  double[] wb=W(tb,new[]{pb[0],pb[1],pb[2]}); double[] ub=Wv(tb,new[]{pb[3],pb[4],pb[5]});
  double dot=ua[0]*ub[0]+ua[1]*ub[1]+ua[2]*ub[2];
  double[] d={wb[0]-wa[0],wb[1]-wa[1],wb[2]-wa[2]};
  double along=d[0]*ua[0]+d[1]*ua[1]+d[2]*ua[2];
  double len2=d[0]*d[0]+d[1]*d[1]+d[2]*d[2]-along*along;
  return new[]{ Math.Sqrt(Math.Max(0,len2))*1000, Math.Abs(dot) };
 }
 static double[] W(double[] t,double[] p){ return new[]{ p[0]*t[0]+p[1]*t[3]+p[2]*t[6]+t[9], p[0]*t[1]+p[1]*t[4]+p[2]*t[7]+t[10], p[0]*t[2]+p[1]*t[5]+p[2]*t[8]+t[11] }; }
 static double[] Wv(double[] t,double[] p){ return new[]{ p[0]*t[0]+p[1]*t[3]+p[2]*t[6], p[0]*t[1]+p[1]*t[4]+p[2]*t[7], p[0]*t[2]+p[1]*t[5]+p[2]*t[8] }; }

 public static object Run(string assemblyPath,bool apply,bool save){
  assemblyPath=Path.GetFullPath(assemblyPath);
  object com; try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  var r=new Dictionary<string,object>();
  var log=new List<object>();
  int e=0,w=0;
  var doc=(SW.IModelDoc2)app.OpenDoc6(assemblyPath,2,1,"\u5f00\u5ea645\u00b0",ref e,ref w);
  if(doc==null){ r["error"]="open failed errors="+e+" warnings="+w; return r; }
  string title=S(()=>doc.GetTitle());
  try{
   doc.ForceRebuild3(false);
   var assy=(SW.IAssemblyDoc)doc;
   var comps=A(assy.GetComponents(false)).Cast<SW.IComponent2>().ToArray();
   var disc=comps.FirstOrDefault(c=>c.Name2.Contains("11\u8776\u677f"));
   var shaft=comps.FirstOrDefault(c=>c.Name2.Contains("04\u9600\u8f74"));
   if(disc==null||shaft==null){ r["error"]="disc or shaft component missing"; return r; }
   r["disc"]=disc.Name2; r["shaft"]=shaft.Name2;

   var cstates=new List<object>();
   foreach(var c in comps){
    cstates.Add(new Dictionary<string,object>{{"name",c.Name2},
      {"suppression",S(()=>c.GetSuppression().ToString())},
      {"is_fixed",S(()=>c.IsFixed()?"1":"0")},
      {"visible",S(()=>c.Visible.ToString())}});
   }
   r["components"]=cstates.ToArray();
   r["mates_before"]=MateStates(doc);
   r["errors_before"]=Errors(doc);

   // shaft working section: local axis along X, radius 15..30mm; take the largest area
   var shaftCyls=Cyls(shaft,v=>Math.Abs(v[3])>0.9999 && Math.Abs(v[4])<1e-6 && Math.Abs(v[5])<1e-6 && v[6]*1000>15 && v[6]*1000<30)
                 .OrderByDescending(f=>f.GetArea()).ToList();
   // disc stem bore: local axis along Z, radius within 1mm of the shaft's
   double shaftR = shaftCyls.Count>0 ? CylParams(shaftCyls[0])[6]*1000 : 22.0;
   var discCyls=Cyls(disc,v=>Math.Abs(v[5])>0.9999 && Math.Abs(v[3])<1e-6 && Math.Abs(v[4])<1e-6
                             && Math.Abs(v[6]*1000-shaftR)<1.5)
               .OrderByDescending(f=>f.GetArea()).ToList();

   var shaftInfo=new List<object>();
   foreach(var f in shaftCyls.Take(6)){ var p=CylParams(f);
     shaftInfo.Add(new{r_mm=Math.Round(p[6]*1000,4),area_mm2=Math.Round(f.GetArea()*1e6,2),
       axis=new[]{Math.Round(p[3],4),Math.Round(p[4],4),Math.Round(p[5],4)},
       point_mm=new[]{Math.Round(p[0]*1000,4),Math.Round(p[1]*1000,4),Math.Round(p[2]*1000,4)}}); }
   var discInfo=new List<object>();
   foreach(var f in discCyls.Take(6)){ var p=CylParams(f);
     discInfo.Add(new{r_mm=Math.Round(p[6]*1000,4),area_mm2=Math.Round(f.GetArea()*1e6,2),
       axis=new[]{Math.Round(p[3],4),Math.Round(p[4],4),Math.Round(p[5],4)},
       point_mm=new[]{Math.Round(p[0]*1000,4),Math.Round(p[1]*1000,4),Math.Round(p[2]*1000,4)}}); }
   r["shaft_cylinders"]=shaftInfo.ToArray();
   r["disc_cylinders"]=discInfo.ToArray();
   r["shaft_radius_mm"]=Math.Round(shaftR,4);
   if(shaftCyls.Count==0||discCyls.Count==0){ r["error"]="no candidate cylinder"; return r; }

   var sf=shaftCyls[0]; var df=discCyls[0];
   var before=AxisOffset(shaft,sf,disc,df);
   r["axis_offset_before_mm"]=Math.Round(before[0],6);
   r["axis_parallel_dot_before"]=Math.Round(before[1],8);
   log.Add(new{step="before",offset_mm=Math.Round(before[0],6),parallel_dot=Math.Round(before[1],8)});

   if(!apply){ r["applied"]=false; r["log"]=log.ToArray(); return r; }

   // 锁定配合会过约束，先全部抑制（与适配器 ReleaseOverConstrainingLocks 同一做法）
   var released=new List<string>();
   for(var f=(SW.IFeature)doc.FirstFeature();f!=null;f=(SW.IFeature)f.GetNextFeature()){
    var stack=new Stack<SW.IFeature>(); stack.Push(f);
    while(stack.Count>0){
     var cur=stack.Pop();
     string nm=S(()=>cur.Name), ty=S(()=>cur.GetTypeName2());
     if(ty=="MateLock" && nm.StartsWith("锁定") && !cur.IsSuppressed()){
      try{
       bool ok=cur.SetSuppression2(0,1,null);
       if(!ok) ok=cur.SetSuppression(0);
       if(ok && cur.IsSuppressed()) released.Add(nm);
       else log.Add(new{step="suppress_no_effect",mate=nm});
      }catch(Exception ex){ log.Add(new{step="unsuppress_fail",mate=nm,error=ex.Message}); }
     }
     for(var sub=(SW.IFeature)cur.GetFirstSubFeature();sub!=null;sub=(SW.IFeature)sub.GetNextSubFeature()) stack.Push(sub);
    }
   }
   if(released.Count>0) doc.ForceRebuild3(false);
   r["locks_released"]=released.ToArray();
   log.Add(new{step="release_locks",count=released.Count,names=released.ToArray()});

   doc.ClearSelection2(true);
   var sm=(SW.ISelectionMgr)doc.SelectionManager;
   var sd=sm.CreateSelectData(); sd.Mark=1;
   var dfe=(SW.IEntity)disc.GetCorrespondingEntity(df);
   var sfe=(SW.IEntity)shaft.GetCorrespondingEntity(sf);
   if(dfe==null||sfe==null){ r["error"]="GetCorrespondingEntity returned null"; return r; }
   bool ok1=dfe.Select4(false,sd), ok2=sfe.Select4(true,sd);
   log.Add(new{step="select",disc=ok1,shaft=ok2,selected=sm.GetSelectedObjectCount2(-1)});
   if(!ok1||!ok2){ r["error"]="face selection failed"; r["log"]=log.ToArray(); return r; }

   int me=0;
   var mate=assy.AddMate5(1,2,false,0,0,0,1,1,0,0,0,false,false,0,out me);
   r["mate_error"]=me; r["mate_created"]=mate!=null;
   log.Add(new{step="addmate",error_code=me,created=mate!=null});
   doc.ClearSelection2(true);
   r["rebuild_ok"]=doc.ForceRebuild3(false);
   r["mates_after"]=MateStates(doc);
   r["errors_after"]=Errors(doc);

   // re-pick the faces (the document changed) and measure again
   var comps2=A(assy.GetComponents(false)).Cast<SW.IComponent2>().ToArray();
   var disc2=comps2.FirstOrDefault(c=>c.Name2.Contains("11\u8776\u677f"));
   var shaft2=comps2.FirstOrDefault(c=>c.Name2.Contains("04\u9600\u8f74"));
   if(disc2!=null&&shaft2!=null){
    var sc2=Cyls(shaft2,v=>Math.Abs(v[3])>0.9999 && v[6]*1000>15 && v[6]*1000<30).OrderByDescending(f=>f.GetArea()).ToList();
    var dc2=Cyls(disc2,v=>Math.Abs(v[5])>0.9999 && Math.Abs(v[6]*1000-shaftR)<1.5).OrderByDescending(f=>f.GetArea()).ToList();
    if(sc2.Count>0&&dc2.Count>0){
     var after=AxisOffset(shaft2,sc2[0],disc2,dc2[0]);
     r["axis_offset_after_mm"]=Math.Round(after[0],6);
     r["axis_parallel_dot_after"]=Math.Round(after[1],8);
     log.Add(new{step="after",offset_mm=Math.Round(after[0],6),parallel_dot=Math.Round(after[1],8)});
    }
   }
   if(save){
    int se=0,sw=0;
    r["save_ok"]=doc.Save3(1,ref se,ref sw);
    r["save_errors"]=se; r["save_warnings"]=sw;
   }
   r["applied"]=true;
   r["log"]=log.ToArray();
  }
  catch(Exception ex){ r["exception"]=ex.ToString(); r["log"]=log.ToArray(); }
  finally{ try{doc.ClearSelection2(true);}catch{} try{app.CloseDoc(title);}catch{} }
  return r;
 }
}
'@
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
try{ Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop) -ErrorAction Stop }
catch{ Write-Host "=== Add-Type failed ==="; Write-Host $_.Exception.Message
        if($_.Exception.InnerException){ Write-Host "--- inner ---"; Write-Host $_.Exception.InnerException.Message }; exit 1 }
$result=[ShaftDiscConcentric]::Run([IO.Path]::GetFullPath($AssemblyPath),[bool]$Apply,[bool]$Save)
$json=$result | ConvertTo-Json -Depth 30
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
