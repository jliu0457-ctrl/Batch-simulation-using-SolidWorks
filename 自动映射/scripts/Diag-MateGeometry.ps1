param(
    [Parameter(Mandatory=$true)][string]$AssemblyPath,
    [Parameter(Mandatory=$true)][string]$OutputPath
)
# Read-only diagnostic. OpenDoc6(..., options=3) == Silent|ReadOnly.
# Dumps, per named mate: referenced entities, their ACTUAL geometry in assembly
# coordinates (plane point+normal / cylinder axis+radius), and the measured gap.
# Also dumps all part dimensions and part equations. Closes without saving.
# All Chinese literals are unicode escapes so this file stays pure ASCII.
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

public static class MateGeometryDiag {
 static object[] A(object x){return x as object[] ?? new object[0];}
 static string S(Func<string> f){try{return f();}catch{return "";}}
 static double[] D(Func<double[]> f){try{return f();}catch{return new double[0];}}

 static double Dot(double[] a,double[] b){return a[0]*b[0]+a[1]*b[1]+a[2]*b[2];}
 static double[] Sub(double[] a,double[] b){return new[]{a[0]-b[0],a[1]-b[1],a[2]-b[2]};}
 static double[] Norm(double[] v){
  double L=Math.Sqrt(v[0]*v[0]+v[1]*v[1]+v[2]*v[2]);
  if(L<=0)return v; return new[]{v[0]/L,v[1]/L,v[2]/L};
 }

 // SolidWorks IMathTransform.ArrayData is column-major: t[0..2]=X, t[3..5]=Y, t[6..8]=Z, t[9..11]=origin
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

 public class Geo {
  public string kind="unknown";
  public double[] p=new double[]{0,0,0};      // world point
  public double[] n=new double[]{0,0,0};      // world normal / axis
  public double[] plocal=null;                // part-local point
  public double[] nl=new double[]{0,0,0};     // part-local normal / axis
  public double[] tf=null;                    // component transform ArrayData
  public string compName="";
  public string compErr="";
  public double r=0;       // cylinder radius, mm
  public double area=0;    // face area, mm^2
  public string err="";
 }


 static Geo GeoOf(SW.IMateEntity2 e){
  var g=new Geo();
  try{
   object refObj=null;
   try{refObj=e.Reference;}catch(Exception ex){g.err="ref:"+ex.Message;return g;}
   var ent=refObj as SW.IEntity;
   if(ent==null){g.err="no_ientity";return g;}
   var face=ent as SW.IFace2;
   if(face==null){g.kind="non_face";return g;}
   try{g.area=Math.Abs(face.GetArea())*1e6;}catch{}
   var s=face.GetSurface() as SW.ISurface;
   if(s==null){g.err="no_surface";return g;}
   SW.IComponent2 c=null;
   try{c=(SW.IComponent2)e.ReferenceComponent;}catch(Exception ex){g.compErr="cast:"+ex.Message;}
   if(c!=null){
    g.compName=c.Name2;
    g.tf=D(()=>((double[])((SW.IMathTransform)c.Transform2).ArrayData));
   }
   if(s.IsPlane()){
    var pp=(double[])s.PlaneParams;
    g.kind="plane";
    g.plocal=new[]{pp[0],pp[1],pp[2]};
    g.nl=Norm(new[]{pp[3],pp[4],pp[5]});
   } else if(s.IsCylinder()){
    var cp=(double[])s.CylinderParams;
    g.kind="cylinder";
    g.plocal=new[]{cp[0],cp[1],cp[2]};
    g.nl=Norm(new[]{cp[3],cp[4],cp[5]});
    g.r=cp[6]*1000.0;
   } else if(s.IsCone()){
    g.kind="cone";
   } else { g.kind="other"; }
   g.p=W(c,g.plocal,false);
   g.n=Norm(W(c,g.nl,true));
  }catch(Exception ex){g.err=ex.Message;}
  return g;
 }

 static object Vec(double[] v){ return v==null?null:new[]{v[0],v[1],v[2]}; }

 static object Measure(Geo a,Geo b){
  if(a.kind=="plane"&&b.kind=="plane"){
   double nd=Dot(a.n,b.n);
   double d1=Dot(a.n,Sub(b.p,a.p));
   double d2=Dot(b.n,Sub(a.p,b.p));
   return new{kind="plane_plane",normal_dot=nd,anti_parallel=nd<0,
              gap_from_a_mm=Math.Abs(d1)*1000,gap_from_b_mm=Math.Abs(d2)*1000};
  }
  if(a.kind=="cylinder"&&b.kind=="cylinder"){
   var d=Sub(b.p,a.p);
   double along=Dot(d,a.n);
   double perp2=Dot(d,d)-along*along;
   return new{kind="cyl_cyl",axis_dot=Dot(a.n,b.n),
              axis_offset_mm=Math.Sqrt(Math.Max(0,perp2))*1000,
              r1_mm=a.r,r2_mm=b.r,radius_diff_mm=Math.Abs(a.r-b.r)};
  }
  return new{kind=a.kind+"_"+b.kind};
 }

 static object Ent(SW.IMateEntity2 e){
  SW.IComponent2 c=null;
  try{c=e.ReferenceComponent as SW.IComponent2;}catch{}
  int rt=-1;try{rt=e.ReferenceType2;}catch{}
  object raw=null;try{raw=e.EntityParams;}catch{}
  double[] rawCut=null;
  try{
   var d=raw as double[];
   if(d!=null){int k=Math.Min(d.Length,12);rawCut=new double[k];Array.Copy(d,rawCut,k);}
  }catch{}
  var g=GeoOf(e);
  return new{
   component=(c==null?"<assembly>":c.Name2),
   reference_type=rt,
   geometry_kind=g.kind,
   geometry_error=g.err,
   component_from_geom=g.compName,
   component_cast_error=g.compErr,
   has_transform=(g.tf!=null&&g.tf.Length>=12),
   transform=g.tf,
   point_local_mm=(g.plocal==null?null:new[]{g.plocal[0]*1000,g.plocal[1]*1000,g.plocal[2]*1000}),
   normal_local=Vec(g.nl),
   point_m=Vec(g.p),
   normal_or_axis=Vec(g.n),
   radius_mm=g.r,
   face_area_mm2=g.area,
   entity_params_raw=rawCut
  };
 }

 static object Mate(SW.IModelDoc2 d,string name){
  var f=(SW.IFeature)null;
  for(var x=(SW.IFeature)d.FirstFeature();x!=null;x=(SW.IFeature)x.GetNextFeature()){
   if(x.Name==name){f=x;break;}
   for(var s=(SW.IFeature)x.GetFirstSubFeature();s!=null;s=(SW.IFeature)s.GetNextSubFeature())
    if(s.Name==name){f=s;break;}
   if(f!=null)break;
  }
  if(f==null)return new{name=name,missing=true};
  var m=f.GetSpecificFeature2() as SW.IMate2;
  var ents=new List<object>();
  var geos=new List<Geo>();
  if(m!=null){
   int n=0;try{n=m.GetMateEntityCount();}catch{}
   for(int i=0;i<n;i++){
    try{
     var me=(SW.IMateEntity2)m.MateEntity(i);
     ents.Add(Ent(me));
     geos.Add(GeoOf(me));
    }catch(Exception ex){ents.Add(new{error=ex.Message});}
   }
  }
  object meas=null;
  if(geos.Count==2){try{meas=Measure(geos[0],geos[1]);}catch(Exception ex){meas=new{error=ex.Message};}}
  int mt=-1;try{mt=m==null?-1:m.Type;}catch{}
  double curDev=0,maxDev=0;
  try{curDev=m.GetCurrentMisalignedDeviation();}catch{}
  try{maxDev=m.GetMaximumMisalignedDeviation();}catch{}
  bool sup=false;try{sup=f.IsSuppressed();}catch{}
  return new{
   name=f.Name,type=S(()=>f.GetTypeName2()),error=f.GetErrorCode(),
   mate_type=mt,suppressed=sup,
   current_misaligned_deviation_mm=curDev,
   maximum_misaligned_deviation_mm=maxDev,
   measurement=meas,
   entities=ents.ToArray()
  };
 }

 static object[] AllMates(SW.IModelDoc2 d){
  var rows=new List<object>();
  for(var f=(SW.IFeature)d.FirstFeature();f!=null;f=(SW.IFeature)f.GetNextFeature()){
   rows.Add(new{name=f.Name,type=S(()=>f.GetTypeName2()),error=f.GetErrorCode(),
                suppressed=S(()=>f.IsSuppressed().ToString()),level=0});
   for(var s=(SW.IFeature)f.GetFirstSubFeature();s!=null;s=(SW.IFeature)s.GetNextSubFeature()){
    rows.Add(new{name=s.Name,type=S(()=>s.GetTypeName2()),error=s.GetErrorCode(),
                 suppressed=S(()=>s.IsSuppressed().ToString()),level=1});
   }
  }
  return rows.ToArray();
 }

 static object[] Comps(SW.IAssemblyDoc a){
  var rows=new List<object>();
  foreach(var o in A(a.GetComponents(false))){
   var c=o as SW.IComponent2; if(c==null) continue;
   rows.Add(new{
    name=S(()=>c.Name2),
    suppression=S(()=>c.GetSuppression().ToString()),
    is_virtual=S(()=>c.IsVirtual.ToString()),
    is_fixed=S(()=>c.IsFixed().ToString())
   });
  }
  return rows.ToArray();
 }

 static object[] Dims(SW.IModelDoc2 part){
  var rows=new List<object>();
  if(part==null)return rows.ToArray();
  var seen=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
  for(var f=(SW.IFeature)part.FirstFeature();f!=null;f=(SW.IFeature)f.GetNextFeature()){
   object disp=null;
   try{disp=f.GetFirstDisplayDimension();}catch{}
   int guard=0;
   while(disp!=null&&guard++<500){
    var dd=(SW.IDisplayDimension)disp;
    try{
     var dim=(SW.IDimension)dd.GetDimension2(0);
     if(dim!=null&&seen.Add(dim.FullName))
      rows.Add(new{feature=f.Name,name=dim.FullName,value_SI=dim.SystemValue,value_mm=dim.SystemValue*1000});
    }catch{}
    try{disp=f.GetNextDisplayDimension(disp);}catch{disp=null;}
   }
  }
  return rows.ToArray();
 }

 static object[] Eqs(SW.IModelDoc2 doc){
  var rows=new List<object>();
  if(doc==null)return rows.ToArray();
  SW.IEquationMgr em=null;
  try{em=(SW.IEquationMgr)doc.GetEquationMgr();}catch(Exception ex){rows.Add(new{error="mgr:"+ex.Message});return rows.ToArray();}
  if(em==null)return rows.ToArray();
  int n=0;try{n=em.GetCount();}catch(Exception ex){rows.Add(new{error="count:"+ex.Message});return rows.ToArray();}
  for(int i=0;i<n;i++){
   string s="";double v=0;
   try{s=em.get_Equation(i);}catch{}
   try{v=em.get_Value(i);}catch{}
   rows.Add(new{index=i,equation=s,value=v});
  }
  return rows.ToArray();
 }

 static object Bounds(SW.IModelDoc2 part){
  if(part==null)return null;
  var all=new List<double>();
  try{
   var pd=part as SW.IPartDoc;
   if(pd==null)return null;
   foreach(var o in A(pd.GetBodies2(0,false))){
    var b=o as SW.IBody2;if(b==null)continue;
    var box=b.GetBodyBox() as double[];
    if(box!=null&&box.Length==6)all.AddRange(box);
   }
  }catch{}
  if(all.Count==0)return null;
  double x0=double.MaxValue,y0=double.MaxValue,z0=double.MaxValue;
  double x1=double.MinValue,y1=double.MinValue,z1=double.MinValue;
  for(int i=0;i<all.Count;i+=6){
   x0=Math.Min(x0,all[i]);y0=Math.Min(y0,all[i+1]);z0=Math.Min(z0,all[i+2]);
   x1=Math.Max(x1,all[i+3]);y1=Math.Max(y1,all[i+4]);z1=Math.Max(z1,all[i+5]);
  }
  return new{min_mm=new[]{x0*1000,y0*1000,z0*1000},max_mm=new[]{x1*1000,y1*1000,z1*1000},
             size_mm=new[]{(x1-x0)*1000,(y1-y0)*1000,(z1-z0)*1000}};
 }

 public static object Run(string assemblyPath){
  assemblyPath=Path.GetFullPath(assemblyPath);
  object com;
  try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks; start it on the desktop session first");}
  var app=(SW.ISldWorks)com;
  int e=0,w=0;
  var doc=(SW.IModelDoc2)app.OpenDoc6(assemblyPath,2,3,"\u5f00\u5ea645\u00b0",ref e,ref w);
  if(doc==null)throw new Exception("read-only open failed: errors="+e+" warnings="+w);
  try{
   var assy=(SW.IAssemblyDoc)doc;
   string cfg="";try{cfg=doc.ConfigurationManager.ActiveConfiguration.Name;}catch{}
   var names=new[]{"\u91cd\u54083","\u91cd\u54084","\u540c\u5fc33","\u540c\u5fc354",
                   "\u9501\u5b9a1","\u9501\u5b9a2","\u9501\u5b9a3","\u5e73\u884c1","\u5e73\u884c5"};
   var tm=new List<object>();
   foreach(var n in names){try{tm.Add(Mate(doc,n));}catch(Exception ex){tm.Add(new{name=n,error=ex.Message});}}
   var tokens=new[]{"08\u5927\u57ab\u7247","09\u5bc6\u5c01\u5708","10\u538b\u677f","11\u8776\u677f","03\u9600\u4f53","04\u9600\u8f74"};
   var parts=new List<object>();
   foreach(var o in A(assy.GetComponents(false))){
    var c=o as SW.IComponent2;if(c==null)continue;
    var nm=S(()=>c.Name2);
    if(!tokens.Any(t=>nm.Contains(t)))continue;
    SW.IModelDoc2 p=null;try{p=c.GetModelDoc2() as SW.IModelDoc2;}catch{}
    parts.Add(new{
     component=nm,
     path=S(()=>c.GetPathName()),
     referenced_configuration=S(()=>c.ReferencedConfiguration),
     is_fixed=S(()=>c.IsFixed().ToString()),
     transform=D(()=>((double[])((SW.IMathTransform)c.Transform2).ArrayData)),
     bounds=Bounds(p),
     equations=Eqs(p),
     dimensions=Dims(p)
    });
   }
   return new{
    assembly=doc.GetPathName(),
    revision=S(()=>app.RevisionNumber()),
    open_errors=e,open_warnings=w,
    active_configuration=cfg,
    all_mates=AllMates(doc),
    components=Comps(assy),
    target_mates=tm.ToArray(),
    parts=parts.ToArray()
   };
  }
  finally{try{app.CloseDoc(doc.GetTitle());}catch{}}
 }
}
'@
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop)
$result=[MateGeometryDiag]::Run([IO.Path]::GetFullPath($AssemblyPath))
$json=$result | ConvertTo-Json -Depth 40
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
