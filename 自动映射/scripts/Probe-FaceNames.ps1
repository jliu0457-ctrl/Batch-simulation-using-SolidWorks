param(
    [Parameter(Mandatory=$true)][string]$PartPath,
    [Parameter(Mandatory=$true)][string]$OutputPath
)
# Read-only.  Lists every face of a part with its 1-based index in API order, its geometry,
# and - if the API exposes one - its SolidWorks display name (面N).  Used to pin down which
# face the UI calls "面1" and which one is the sealing cone.
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

public static class FaceNames {
 static object[] A(object x){return x as object[] ?? new object[0];}
 static string S(Func<string> f){try{return f();}catch{return "";}}
 static double R(double v){ return Math.Round(v,5); }

 public static object Run(string partPath){
  partPath=Path.GetFullPath(partPath);
  object com; try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  var r=new Dictionary<string,object>();
  int e=0,w=0;
  var doc=(SW.IModelDoc2)app.OpenDoc6(partPath,1,1,"",ref e,ref w);
  if(doc==null){ r["error"]="open failed errors="+e+" warnings="+w; return r; }
  string title=S(()=>doc.GetTitle());
  try{
   doc.ForceRebuild3(false);
   var rows=new List<object>();
   int idx=0;
   foreach(var b in A(((SW.IPartDoc)doc).GetBodies2(0,false)))
    foreach(var x in A(((SW.IBody2)b).GetFaces())){
     var f=(SW.IFace2)x; idx++;
     var s=(SW.ISurface)f.GetSurface() as SW.ISurface; if(s==null) continue;
     var row=new Dictionary<string,object>();
     row["index"]=idx;
     row["kind"]= s.IsPlane()?"plane": s.IsCone()?"cone": s.IsCylinder()?"cylinder":
                       s.IsSphere()?"sphere": s.IsTorus()?"torus":"other";
     // Identity() gives the raw swSurfaceType_e code, which names even the spline families
     row["identity"]=S(()=>s.Identity().ToString());
     row["area_mm2"]=Math.Round(Math.Abs(f.GetArea())*1e6,3);
     if(s.IsPlane()){
      var q=(double[])s.PlaneParams;
      row["point_mm"]=new[]{R(q[3]*1000),R(q[4]*1000),R(q[5]*1000)};
      row["normal"]=new[]{R(q[0]),R(q[1]),R(q[2])};
     } else if(s.IsCone()||s.IsCylinder()){
      double[] cp= s.IsCone() ? (double[])s.ConeParams : (double[])s.CylinderParams;
      row["radius_mm"]=R(cp[6]*1000);
      row["point_mm"]=new[]{R(cp[0]*1000),R(cp[1]*1000),R(cp[2]*1000)};
      row["axis"]=new[]{R(cp[3]),R(cp[4]),R(cp[5])};
      if(s.IsCone()) row["half_angle_deg"]=R(((double[])s.ConeParams)[7]*180.0/Math.PI);
     }
     rows.Add(row);
    }
   r["document"]=title;
   r["face_count"]=idx;
   r["faces"]=rows.ToArray();
  }
  catch(Exception ex){ r["exception"]=ex.ToString(); }
  finally{ try{app.CloseDoc(title);}catch{} }
  return r;
 }
}
'@
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
try{ Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop) -ErrorAction Stop }
catch{ Write-Host "=== Add-Type failed ==="; Write-Host $_.Exception.Message
        if($_.Exception.InnerException){ Write-Host "--- inner ---"; Write-Host $_.Exception.InnerException.Message }; exit 1 }
$result=[FaceNames]::Run([IO.Path]::GetFullPath($PartPath))
$json=$result | ConvertTo-Json -Depth 20
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
