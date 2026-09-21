param(
    [Parameter(Mandatory=$true)][string]$AssemblyPath,
    [Parameter(Mandatory=$true)][string]$OutputPath
)
# Read-only. Dumps each core component's centre of mass in WORLD coordinates, computed from
# the solid bodies' mass properties transformed by the component's transform.  Used to see
# how far each part actually moved between two states.
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

public static class CentroidProbe {
 static object[] A(object x){return x as object[] ?? new object[0];}
 static string S(Func<string> f){try{return f();}catch{return "";}}
 static double[] D(Func<double[]> f){try{return f();}catch{return new double[0];}}

 static double[] W(double[] t,double[] p){
  if(t==null||t.Length<12) return p;
  return new[]{
   p[0]*t[0]+p[1]*t[3]+p[2]*t[6]+t[9],
   p[0]*t[1]+p[1]*t[4]+p[2]*t[7]+t[10],
   p[0]*t[2]+p[1]*t[5]+p[2]*t[8]+t[11]
  };
 }

 public static object Run(string assemblyPath){
  assemblyPath=Path.GetFullPath(assemblyPath);
  object com;
  try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  int e=0,w=0;
  var doc=(SW.IModelDoc2)app.OpenDoc6(assemblyPath,2,3,"\u5f00\u5ea645\u00b0",ref e,ref w);
  if(doc==null)throw new Exception("open failed: errors="+e+" warnings="+w);
  var r=new Dictionary<string,object>();
  try{
   var assy=(SW.IAssemblyDoc)doc;
   r["assembly"]=doc.GetPathName();
   r["rebuild_ok"]=doc.ForceRebuild3(false);
   var rows=new List<object>();
   foreach(var o in A(assy.GetComponents(false))){
    var c=o as SW.IComponent2; if(c==null) continue;
    var nm=S(()=>c.Name2);
    var pd=c.GetModelDoc2() as SW.IPartDoc;
    if(pd==null) continue;
    double[] t=D(()=>((double[])((SW.IMathTransform)c.Transform2).ArrayData));
    // volume-weighted centroid over all solid bodies, in part coordinates
    double vx=0,vy=0,vz=0,vt=0;
    try{
     foreach(var bo in A(pd.GetBodies2(0,false))){
      var b=bo as SW.IBody2; if(b==null) continue;
      var mp=b.GetMassProperties(0) as double[];
      if(mp==null||mp.Length<4) continue;
      double vol=Math.Abs(mp[3]);
      vx+=mp[0]*vol; vy+=mp[1]*vol; vz+=mp[2]*vol; vt+=vol;
     }
    }catch{}
    if(vt<=0) continue;
    var local=new[]{vx/vt,vy/vt,vz/vt};
    var world=W(t,local);
    rows.Add(new{
     component=nm,
     volume_mm3=vt*1e9,
     centroid_local_mm=new[]{local[0]*1000,local[1]*1000,local[2]*1000},
     centroid_world_mm=new[]{world[0]*1000,world[1]*1000,world[2]*1000},
     transform=t
    });
   }
   r["components"]=rows.ToArray();
  }
  finally{try{app.CloseDoc(doc.GetTitle());}catch{}}
  return r;
 }
}
'@
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop)
$result=[CentroidProbe]::Run([IO.Path]::GetFullPath($AssemblyPath))
$json=$result | ConvertTo-Json -Depth 30
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
