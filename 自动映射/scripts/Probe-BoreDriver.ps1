param(
    [Parameter(Mandatory=$true)][string]$PartPath,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [double]$Step = 2.0
)
# Finds which sketch dimension moves the stem bore of a disc: perturbs each candidate
# dimension in turn, rebuilds, and reports how far the bore axis moved.  Nothing is saved.
# ASCII only; Chinese uses unicode escapes in the C# source.
$ErrorActionPreference='Stop'
$interop='D:\Program Files\2026SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll'
if(-not (Test-Path -LiteralPath $interop -PathType Leaf)){ throw "SolidWorks interop missing: $interop" }
$source=@'
using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW=SolidWorks.Interop.sldworks;

public static class BoreDriverProbe {
 static object[] A(object x){return x as object[] ?? new object[0];}
 static string S(Func<string> f){try{return f();}catch{return "";}}

 // bore = the largest cylinder whose axis is along Z, radius 15..30 mm
 static double[] BorePoint(SW.IModelDoc2 doc){
  var pd=doc as SW.IPartDoc; if(pd==null) return null;
  double best=-1; double[] bestPt=null;
  try{
   foreach(var o in A(pd.GetBodies2(0,false))){
    var b=o as SW.IBody2; if(b==null) continue;
    foreach(var fo in A(b.GetFaces())){
     var f=fo as SW.IFace2; if(f==null) continue;
     var s=f.GetSurface() as SW.ISurface; if(s==null||!s.IsCylinder()) continue;
     var cp=(double[])s.CylinderParams;
     if(Math.Abs(cp[5])<0.99) continue;                  // axis along Z
     double r=cp[6]*1000.0;
     if(r<15||r>30) continue;
     double area=0; try{area=Math.Abs(f.GetArea());}catch{}
     if(area>best){ best=area; bestPt=new[]{cp[0]*1000,cp[1]*1000,cp[2]*1000}; }
    }
   }
  }catch{}
  return bestPt;
 }

 public static object Run(string partPath,double step){
  partPath=Path.GetFullPath(partPath);
  object com;
  try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  int e=0,w=0;
  var doc=(SW.IModelDoc2)app.OpenDoc6(partPath,1,1,"",ref e,ref w);
  if(doc==null)throw new Exception("open failed: "+e+","+w);
  var r=new Dictionary<string,object>();
  try{
   doc.ForceRebuild3(false);
   var basePt=BorePoint(doc);
   r["bore_base_mm"]=basePt;
   if(basePt==null){ r["error"]="bore not found"; return r; }
   // collect candidate dimensions from the sketches and datum planes
   var cand=new List<SW.IDimension>();
   var names=new List<string>();
   for(var f=(SW.IFeature)doc.FirstFeature();f!=null;f=(SW.IFeature)f.GetNextFeature()){
    string fname=S(()=>f.Name);
    if(fname.IndexOf("\u8349\u56fe",StringComparison.Ordinal)<0 &&
       fname.IndexOf("\u57fa\u51c6\u9762",StringComparison.Ordinal)<0) continue;
    object disp=null; try{disp=f.GetFirstDisplayDimension();}catch{}
    int g=0;
    while(disp!=null&&g++<400){
     try{
      var d=(SW.IDimension)((SW.IDisplayDimension)disp).GetDimension2(0);
      if(d!=null){ cand.Add(d); names.Add(fname+" | "+d.FullName.Split('@')[0]); }
     }catch{}
     try{disp=f.GetNextDisplayDimension(disp);}catch{disp=null;}
    }
   }
   r["candidate_count"]=cand.Count;
   var rows=new List<object>();
   for(int i=0;i<cand.Count;i++){
    var d=cand[i];
    double before=0; try{before=d.SystemValue;}catch{}
    int st=-1; string err="";
    try{ st=d.SetSystemValue3(before+step/1000.0,1,null); }catch(Exception ex){ err=ex.Message; }
    double after=0; try{after=d.SystemValue;}catch{}
    bool took = (st==0) && Math.Abs(after-(before+step/1000.0))<1e-9;
    if(!took){ rows.Add(new{dimension=names[i],before_mm=before*1000,set=false,
                            status=st,after_mm=after*1000,error=err}); continue; }
    doc.ForceRebuild3(false);
    var pt=BorePoint(doc);
    object moved=null;
    if(pt!=null&&basePt!=null){
     double dx=pt[0]-basePt[0], dy=pt[1]-basePt[1], dz=pt[2]-basePt[2];
     moved=new{dx=Math.Round(dx,6),dy=Math.Round(dy,6),dz=Math.Round(dz,6),
               dist=Math.Round(Math.Sqrt(dx*dx+dy*dy+dz*dz),6)};
    }
    rows.Add(new{dimension=names[i],before_mm=Math.Round(before*1000,4),set=true,moved=moved});
    try{ d.SetSystemValue3(before,1,null); }catch{}
    doc.ForceRebuild3(false);
   }
   r["results"]=rows.ToArray();
  }
  catch(Exception ex){ r["error"]=ex.ToString(); }
  finally{ try{ app.CloseDoc(doc.GetTitle()); }catch{} }
  return r;
 }
}
'@
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop)
$result=[BoreDriverProbe]::Run([IO.Path]::GetFullPath($PartPath),$Step)
$json=$result | ConvertTo-Json -Depth 30
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
