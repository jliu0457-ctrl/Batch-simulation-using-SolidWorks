param(
    [Parameter(Mandatory=$true)][string]$AssemblyPath,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [Parameter(Mandatory=$true)][string]$TokensJson
)
$tokens=[IO.File]::ReadAllText([IO.Path]::GetFullPath($TokensJson),[Text.UTF8Encoding]::new($false))
# Read-only. Dumps every cylindrical face of the shaft and the disc whose axis is
# roughly parallel to X, with its radius and world-space axis point. Used to check
# whether the disc bore stays concentric with the shaft after parameterization.
# All Chinese literals in the C# source are unicode escapes so this file stays pure ASCII.
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

public static class ShaftBoreProbe {
 static object[] A(object x){return x as object[] ?? new object[0];}
 static string S(Func<string> f){try{return f();}catch{return "";}}
 static double[] D(Func<double[]> f){try{return f();}catch{return new double[0];}}

 static double[] W(SW.IComponent2 c,double[] p,bool vec){
  if(c==null)return p;
  double[] t=D(()=>((double[])((SW.IMathTransform)c.Transform2).ArrayData));
  if(t==null||t.Length<12)return p;
  return new[]{
   p[0]*t[0]+p[1]*t[3]+p[2]*t[6]+(vec?0:t[9]),
   p[0]*t[1]+p[1]*t[4]+p[2]*t[7]+(vec?0:t[10]),
   p[0]*t[2]+p[1]*t[5]+p[2]*t[8]+(vec?0:t[11])
  };
 }
 static double[] Norm(double[] v){
  double L=Math.Sqrt(v[0]*v[0]+v[1]*v[1]+v[2]*v[2]);
  return L<=0?v:new[]{v[0]/L,v[1]/L,v[2]/L};
 }

 public static object Run(string assemblyPath,string token){
  assemblyPath=Path.GetFullPath(assemblyPath);
  object com;
  try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  int e=0,w=0;
  var doc=(SW.IModelDoc2)app.OpenDoc6(assemblyPath,2,3,"\u5f00\u5ea645\u00b0",ref e,ref w);
  if(doc==null)throw new Exception("open failed: errors="+e+" warnings="+w);
  try{
   var assy=(SW.IAssemblyDoc)doc;
   SW.IComponent2 comp=null;
   foreach(var o in A(assy.GetComponents(false))){
    var c=o as SW.IComponent2; if(c==null) continue;
    if(S(()=>c.Name2).Contains(token)){comp=c;break;}
   }
   if(comp==null)throw new Exception("component not found: "+token);
   var pdoc=comp.GetModelDoc2() as SW.IModelDoc2;
   var part=pdoc as SW.IPartDoc;
   var rows=new List<object>();
   foreach(var o in A(part.GetBodies2(0,false))){
    var b=o as SW.IBody2; if(b==null) continue;
    foreach(var fo in A(b.GetFaces())){
     var f=fo as SW.IFace2; if(f==null) continue;
     var s=f.GetSurface() as SW.ISurface;
     if(s==null)continue;
     double area=0; try{area=Math.Abs(f.GetArea())*1e6;}catch{}
     if(s.IsCylinder()){
      var cp=(double[])s.CylinderParams;
      var p=W(comp,new[]{cp[0],cp[1],cp[2]},false);
      var a=Norm(W(comp,new[]{cp[3],cp[4],cp[5]},true));
      double r=cp[6]*1000.0;
      if(r<5)continue;                                  // ignore tiny holes
      rows.Add(new{
       kind="cylinder", radius_mm=r, half_angle_deg=0.0, area_mm2=area,
       point_mm=new[]{p[0]*1000,p[1]*1000,p[2]*1000},
       axis=new[]{Math.Round(a[0],6),Math.Round(a[1],6),Math.Round(a[2],6)}
      });
     } else if(s.IsCone()){
      var kp=(double[])s.ConeParams;
      var p=W(comp,new[]{kp[0],kp[1],kp[2]},false);
      var a=Norm(W(comp,new[]{kp[3],kp[4],kp[5]},true));
      double r=kp[6]*1000.0;
      double half=0; try{half=Math.Abs(kp[7])*180.0/Math.PI;}catch{}
      rows.Add(new{
       kind="cone", radius_mm=r, half_angle_deg=Math.Round(half,6), area_mm2=area,
       point_mm=new[]{p[0]*1000,p[1]*1000,p[2]*1000},
       axis=new[]{Math.Round(a[0],6),Math.Round(a[1],6),Math.Round(a[2],6)}
      });
     }
    }
   }
   return new{component=token, cylinders=rows.ToArray()};
  }
  finally{try{app.CloseDoc(doc.GetTitle());}catch{}}
 }
}
'@
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop)
$result=@()
foreach($tok in ($tokens | ConvertFrom-Json)){
    $result+=[ShaftBoreProbe]::Run([IO.Path]::GetFullPath($AssemblyPath), [string]$tok)
}
$json=$result | ConvertTo-Json -Depth 30
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
