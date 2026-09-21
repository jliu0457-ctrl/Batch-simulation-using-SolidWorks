param(
    [Parameter(Mandatory=$true)][string]$SketchName,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [double]$DeltaMm = 2.0,
    [int]$MaxIndex = 15
)
# Read-only in effect: perturbs one sketch dimension at a time in memory, rebuilds, snapshots
# every face, then restores the dimension.  The document is NEVER saved.
#
# Why: on the disc, `c` is written to exactly one dimension of 草图1 and only the sealing cone
# follows it - the plate faces stay pinned, so the part is sheared instead of translated and the
# gasket and the seat ring both cut into it.  This probe answers the missing half of the
# question: which dimension of that sketch positions each face?
#
# Attach order matters: .NET's Marshal.GetActiveObject only connects once a document is already
# open, so drive it from scripts/open_part.py first, then run this.
#
# ASCII only - Chinese is built from code points inside the C#.
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

public static class SketchDimSweep {
 static object[] A(object x){return x as object[] ?? new object[0];}
 static string S(Func<string> f){try{return f();}catch{return "";}}
 static double N(Func<double> f){try{return f();}catch{return 0;}}

 static object[] Faces(SW.IModelDoc2 doc){
  var rows=new List<object>();
  var pd=doc as SW.IPartDoc; if(pd==null) return rows.ToArray();
  foreach(var b in A(pd.GetBodies2(0,false)))
   foreach(var x in A(((SW.IBody2)b).GetFaces())){
    var f=(SW.IFace2)x; var s=f.GetSurface() as SW.ISurface; if(s==null) continue;
    double area=Math.Round(Math.Abs(f.GetArea())*1e6,3);
    if(s.IsPlane()){
     var p=(double[])s.PlaneParams;
     rows.Add(new Dictionary<string,object>{{"kind","plane"},{"area_mm2",area},
       {"normal",new[]{Math.Round(p[0],5),Math.Round(p[1],5),Math.Round(p[2],5)}},
       {"point_mm",new[]{Math.Round(p[3]*1000,4),Math.Round(p[4]*1000,4),Math.Round(p[5]*1000,4)}}});
    } else if(s.IsCone()){
     var q=(double[])s.ConeParams;
     rows.Add(new Dictionary<string,object>{{"kind","cone"},{"area_mm2",area},
       {"radius_mm",Math.Round(q[6]*1000,4)},
       {"half_angle_deg",Math.Round(q[7]*180.0/Math.PI,4)},
       {"axis",new[]{Math.Round(q[3],5),Math.Round(q[4],5),Math.Round(q[5],5)}},
       {"point_mm",new[]{Math.Round(q[0]*1000,4),Math.Round(q[1]*1000,4),Math.Round(q[2]*1000,4)}}});
    } else if(s.IsCylinder()){
     var q=(double[])s.CylinderParams;
     rows.Add(new Dictionary<string,object>{{"kind","cylinder"},{"area_mm2",area},
       {"radius_mm",Math.Round(q[6]*1000,4)},
       {"axis",new[]{Math.Round(q[3],5),Math.Round(q[4],5),Math.Round(q[5],5)}},
       {"point_mm",new[]{Math.Round(q[0]*1000,4),Math.Round(q[1]*1000,4),Math.Round(q[2]*1000,4)}}});
    }
   }
  return rows.ToArray();
 }

 public static object Run(string sketch,double delta,int maxIndex){
  object com; try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks - open a document first (open_part.py)");}
  var app=(SW.ISldWorks)com;
  var r=new Dictionary<string,object>();
  var doc=(SW.IModelDoc2)app.ActiveDoc;
  if(doc==null){ r["error"]="no active document"; return r; }
  r["title"]=S(()=>doc.GetTitle());
  r["sketch"]=sketch;
  r["delta_mm"]=delta;
  try{
   doc.ForceRebuild3(false);
   r["baseline"]=Faces(doc);

   var names=new List<string>();
   var bases=new Dictionary<string,double>();
   for(int i=1;i<=maxIndex;i++){
    string nm="D"+i+"@"+sketch;
    SW.IDimension d=null; try{ d=doc.Parameter(nm) as SW.IDimension; }catch{}
    if(d==null) continue;
    names.Add(nm); bases[nm]=Math.Round(N(()=>d.SystemValue)*1000,4);
   }
   r["dimensions_mm"]=bases;

   var sweep=new Dictionary<string,object>();
   foreach(var nm in names){
    var row=new Dictionary<string,object>();
    row["base_mm"]=bases[nm];
    SW.IDimension d=null; try{ d=doc.Parameter(nm) as SW.IDimension; }catch{}
    if(d==null){ row["set"]=false; sweep[nm]=row; continue; }
    try{ d.SystemValue=(bases[nm]+delta)/1000.0; row["set"]=true; }
    catch(Exception ex){ row["set"]=false; row["error"]=ex.Message; sweep[nm]=row; continue; }
    doc.ForceRebuild3(false);
    row["after"]=Faces(doc);
    try{ SW.IDimension d2=doc.Parameter(nm) as SW.IDimension; if(d2!=null) d2.SystemValue=bases[nm]/1000.0; }catch{}
    doc.ForceRebuild3(false);
    sweep[nm]=row;
   }
   r["sweep"]=sweep;
  }
  catch(Exception ex){ r["exception"]=ex.ToString(); }
  return r;   // caller never saves the document
 }
}
'@
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
try{
    Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop) -ErrorAction Stop
}catch{
    Write-Host "=== Add-Type failed ==="
    Write-Host $_.Exception.Message
    if($_.Exception.InnerException){ Write-Host "--- inner ---"; Write-Host $_.Exception.InnerException.Message }
    exit 1
}
$result=[SketchDimSweep]::Run($SketchName,$DeltaMm,$MaxIndex)
$json=$result | ConvertTo-Json -Depth 30
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
