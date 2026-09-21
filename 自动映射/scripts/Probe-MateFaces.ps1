param(
    [Parameter(Mandatory=$true)][string]$AssemblyPath,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [string[]]$Names = @()
)
# Read-only.  For each named mate, reports every mate entity: which component it belongs to,
# the surface type of the referenced face, its parameters in PART coordinates, and where that
# face sits in WORLD coordinates after the component transform.
#
# Why: 重合4 (disc <-> gasket) holds the gasket at its old world position even though the
# gasket's own geometry slides 1mm, and the disc's sealing cone also moves 1mm.  That means
# 重合4 cannot be referencing the sealing cone - this dump shows which face it really uses.
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

public static class MateFaces {
 static object[] A(object x){return x as object[] ?? new object[0];}
 static string S(Func<string> f){try{return f();}catch{return "";}}
 static string CP(params int[] cps){ var s=""; foreach(int c in cps) s+=((char)c).ToString(); return s; }
 static double R(double v){ return Math.Round(v,5); }

 static void Walk(SW.IFeature f,bool asSub,List<SW.IFeature> outp){
  while(f!=null){
   outp.Add(f);
   var sub=(SW.IFeature)f.GetFirstSubFeature();
   if(sub!=null) Walk(sub,true,outp);
   f = asSub ? (SW.IFeature)f.GetNextSubFeature() : (SW.IFeature)f.GetNextFeature();
  }
 }
 static List<SW.IFeature> All(SW.IModelDoc2 d){
  var l=new List<SW.IFeature>(); Walk((SW.IFeature)d.FirstFeature(),false,l); return l;
 }
 static double[] W(double[] t,double[] p){
  return new[]{ p[0]*t[0]+p[1]*t[3]+p[2]*t[6]+t[9],
                p[0]*t[1]+p[1]*t[4]+p[2]*t[7]+t[10],
                p[0]*t[2]+p[1]*t[5]+p[2]*t[8]+t[11] };
 }

 static object EntityInfo(SW.IMateEntity2 me){
  var row=new Dictionary<string,object>();
  var comp=me.ReferenceComponent as SW.IComponent2;
  row["component"]= comp==null ? "<assembly>" : S(()=>comp.Name2);
  double[] t=null;
  if(comp!=null){ try{ t=(double[])((SW.IMathTransform)comp.Transform2).ArrayData; }catch{} }

  // EntityParams: SolidWorks hands back the geometry of the referenced entity directly
  double[] ep=null;
  try{ ep=(double[])me.EntityParams; }catch(Exception ex){ row["entity_params_error"]=ex.Message; }
  if(ep!=null){
   var p=new List<double>();
   foreach(double v in ep) p.Add(R(v*1000.0));
   row["entity_params_mm"]=p.ToArray();
  }

  var ent=me.Reference;
  if(ent!=null && t!=null){
   try{
    var f=ent as SW.IFace2;
    if(f!=null){
     var s=f.GetSurface() as SW.ISurface;
     row["face_kind"]= s.IsPlane()?"plane": s.IsCone()?"cone": s.IsCylinder()?"cylinder":
                        s.IsSphere()?"sphere": s.IsTorus()?"torus":"other";
     row["face_area_mm2"]=Math.Round(Math.Abs(f.GetArea())*1e6,3);
     if(s.IsPlane()){
      var q=(double[])s.PlaneParams;
      row["part_point_mm"]=new[]{R(q[3]*1000),R(q[4]*1000),R(q[5]*1000)};
      row["part_normal"]=new[]{R(q[0]),R(q[1]),R(q[2])};
      row["world_point_mm"]=new[]{Math.Round(W(t,new[]{q[3],q[4],q[5]})[0]*1000,4),
                                  Math.Round(W(t,new[]{q[3],q[4],q[5]})[1]*1000,4),
                                  Math.Round(W(t,new[]{q[3],q[4],q[5]})[2]*1000,4)};
     } else if(s.IsCone()||s.IsCylinder()){
      double[] cp= s.IsCone() ? (double[])s.ConeParams : (double[])s.CylinderParams;
      row["part_point_mm"]=new[]{R(cp[0]*1000),R(cp[1]*1000),R(cp[2]*1000)};
      row["part_axis"]=new[]{R(cp[3]),R(cp[4]),R(cp[5])};
      row["radius_mm"]=R(cp[6]*1000);
      row["world_point_mm"]=new[]{Math.Round(W(t,new[]{cp[0],cp[1],cp[2]})[0]*1000,4),
                                  Math.Round(W(t,new[]{cp[0],cp[1],cp[2]})[1]*1000,4),
                                  Math.Round(W(t,new[]{cp[0],cp[1],cp[2]})[2]*1000,4)};
     }
     return row;
    }
    row["face_kind"]="not_a_face";
   }catch(Exception ex){ row["ref_error"]=ex.Message; }
  } else if(ent!=null){
   try{ row["ref_type"]=S(()=>ent.GetType().Name); }catch{}
  }
  return row;
 }

 public static object Run(string assemblyPath,string[] names){
  if(names==null||names.Length==0)
   names=new[]{CP(0x91cd,0x5408)+"4", CP(0x91cd,0x5408)+"3", CP(0x540c,0x5fc3)+"8", CP(0x91cd,0x5408)+"47"};
  string config=CP(0x5f00,0x5ea6)+"45"+CP(0xb0);
  assemblyPath=Path.GetFullPath(assemblyPath);
  object com; try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  var r=new Dictionary<string,object>();
  int e=0,w=0;
  var doc=(SW.IModelDoc2)app.OpenDoc6(assemblyPath,2,1,config,ref e,ref w);
  if(doc==null){ r["error"]="open failed errors="+e+" warnings="+w; return r; }
  string title=S(()=>doc.GetTitle());
  try{
   doc.ForceRebuild3(false);
   var feats=All(doc);
   var outRows=new List<object>();
   foreach(string name in names){
    SW.IFeature feature=null;
    foreach(var f in feats){ if(String.Equals(S(()=>f.Name),name,StringComparison.Ordinal)){ feature=f; break; } }
    if(feature==null){ outRows.Add(new Dictionary<string,object>{{"mate",name},{"found",false}}); continue; }
    var row=new Dictionary<string,object>{{"mate",name},{"found",true},{"type",S(()=>feature.GetTypeName2())}};
    var ents=new List<object>();
    try{
     var m=(SW.IMate2)feature.GetSpecificFeature2();
     if(m!=null) for(int i=0;i<m.GetMateEntityCount();i++){
      var me=(SW.IMateEntity2)m.MateEntity(i);
      ents.Add(EntityInfo(me));
     }
    }catch(Exception ex){ row["error"]=ex.Message; }
    row["entities"]=ents.ToArray();
    outRows.Add(row);
   }
   r["mates"]=outRows.ToArray();
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
$nameArg=$null
if($Names -and $Names.Count -gt 0){ $nameArg=[string[]]$Names }
$result=[MateFaces]::Run([IO.Path]::GetFullPath($AssemblyPath),$nameArg)
$json=$result | ConvertTo-Json -Depth 20
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
