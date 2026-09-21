param(
    [Parameter(Mandatory=$true)][string]$Folder,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [switch]$AttemptDimension
)
# Inspects the disc part: reference-plane orientations, cylinders, and the full inventory of
# the sketch that feeds the stem-bore cut.  Optionally tries to add a driving dimension from
# the right datum plane to the bore circle.  Never touches the V7 template - point -Folder at
# a scratch copy.
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

public static class Sketch2Probe {
 static object[] A(object x){return x as object[] ?? new object[0];}
 static string S(Func<string> f){try{return f();}catch{return "";}}
 static double N(Func<double> f){try{return f();}catch{return 0;}}

 static SW.IFeature FindFeat(SW.IModelDoc2 d,string n){
  for(var f=(SW.IFeature)d.FirstFeature();f!=null;f=(SW.IFeature)f.GetNextFeature()){
   if(S(()=>f.Name)==n) return f;
   for(var s=(SW.IFeature)f.GetFirstSubFeature();s!=null;s=(SW.IFeature)s.GetNextSubFeature())
    if(S(()=>s.Name)==n) return s;
  }
  return null;
 }

 // the sketch that owns the bore: whichever sketch's circle matches the bore radius
 static string BoreSketchName(SW.IModelDoc2 doc,double targetR){
  for(var f=(SW.IFeature)doc.FirstFeature();f!=null;f=(SW.IFeature)f.GetNextFeature()){
   var nm=S(()=>f.Name);
   if(nm.IndexOf("\u8349\u56fe",StringComparison.Ordinal)!=0) continue;
   SW.ISketch sk=null; try{sk=f.GetSpecificFeature2() as SW.ISketch;}catch{}
   if(sk==null) continue;
   foreach(var o in A(sk.GetSketchSegments())){
    var arc=o as SW.ISketchArc; if(arc==null) continue;
    double r=N(()=>arc.GetRadius());
    if(Math.Abs(r-targetR)<0.0005) return nm;
   }
  }
  return null;
 }

 public static object Run(string folder,bool attempt){
  folder=Path.GetFullPath(folder);
  object com; try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks (is it running?)");}
  var app=(SW.ISldWorks)com;
  var r=new Dictionary<string,object>();
  var log=new List<object>();
  string disc=Directory.GetFiles(folder,"*.SLDPRT").FirstOrDefault(x=>x.Contains("11\u8776\u677f"));
  if(disc==null){ r["error"]="disc part not found"; return r; }
  r["file"]=Path.GetFileName(disc);
  int e=0,w=0;
  var doc=(SW.IModelDoc2)app.OpenDoc6(disc,1,1,"",ref e,ref w);
  if(doc==null){ r["error"]="open failed "+e+","+w; return r; }
  try{
   doc.ForceRebuild3(false);

   // ---- cylinders: find the stem bore ----
   double boreR=0; double[] boreOrigin=null;
   var cyls=new List<Dictionary<string,object>>();
   try{
    foreach(var bo in A(((SW.IPartDoc)doc).GetBodies2(0,false))){
     var b=bo as SW.IBody2; if(b==null) continue;
     foreach(var fo in A(b.GetFaces())){
      var f=fo as SW.IFace2; if(f==null) continue;
      var s=f.GetSurface() as SW.ISurface; if(s==null||!s.IsCylinder()) continue;
      var cp=(double[])s.CylinderParams;
      double rad=cp[6]*1000.0;
      if(rad<5) continue;
      var crow=new Dictionary<string,object>();
      crow["radius_mm"]=Math.Round(rad,3);
      crow["origin_mm"]=new[]{Math.Round(cp[0]*1000,4),Math.Round(cp[1]*1000,4),Math.Round(cp[2]*1000,4)};
      crow["axis"]=new[]{Math.Round(cp[3],5),Math.Round(cp[4],5),Math.Round(cp[5],5)};
      crow["area_mm2"]=Math.Round(Math.Abs(f.GetArea())*1e6,2);
      cyls.Add(crow);
     }
    }
   }catch(Exception ex){ log.Add(new{step="cyls",error=ex.Message}); }
   var biggest=cyls.OrderByDescending(x=>Convert.ToDouble(x["area_mm2"])).ToList();
   r["cylinders"]=biggest.ToArray();
   if(biggest.Count>0){
    boreR=Convert.ToDouble(biggest[0]["radius_mm"])/1000.0;
    double[] o0=(double[])biggest[0]["origin_mm"];
    boreOrigin=new[]{o0[0]/1000.0,o0[1]/1000.0,o0[2]/1000.0};
    r["bore_radius_mm"]=Math.Round(boreR*1000,3);
   }

   // ---- reference planes ----
   var planes=new List<object>();
   foreach(string pn in new[]{"\u524d\u89c6\u57fa\u51c6\u9762","\u4e0a\u89c6\u57fa\u51c6\u9762",
                              "\u53f3\u89c6\u57fa\u51c6\u9762","\u57fa\u51c6\u97622"}){
    var pf=FindFeat(doc,pn);
    if(pf==null){ planes.Add(new{name=pn,found=false}); continue; }
    try{
     var rp=(SW.IRefPlane)pf.GetSpecificFeature2();
     var arr=(double[])((SW.IMathTransform)rp.Transform).ArrayData;
     planes.Add(new{name=pn,found=true,
                    origin_mm=new[]{Math.Round(arr[9]*1000,4),Math.Round(arr[10]*1000,4),Math.Round(arr[11]*1000,4)},
                    normal=new[]{Math.Round(arr[6],5),Math.Round(arr[7],5),Math.Round(arr[8],5)}});
    }catch(Exception ex){ planes.Add(new{name=pn,found=true,error=ex.Message}); }
   }
   r["reference_planes"]=planes.ToArray();

   // ---- the bore sketch ----
   string skName=BoreSketchName(doc,boreR);
   r["bore_sketch"]=skName;
   if(skName==null){ r["warn"]="no sketch contains a circle of the bore radius"; return r; }
   var sf=FindFeat(doc,skName);
   var sk=sf.GetSpecificFeature2() as SW.ISketch;
   var segs=new List<object>();
   var segObjs=new List<SW.ISketchSegment>();
   int si=0;
   foreach(var o in A(sk.GetSketchSegments())){
    var seg=o as SW.ISketchSegment; if(seg==null) continue;
    segObjs.Add(seg); si++;
    var row=new Dictionary<string,object>();
    row["i"]=si;
    row["typename"]=S(()=>seg.GetType().ToString());
    row["isConstruction"]=S(()=>seg.ConstructionGeometry?"1":"0");
    var arc=seg as SW.ISketchArc;
    if(arc!=null){
     row["kind"]="arc";
     row["radius_mm"]=Math.Round(N(()=>arc.GetRadius())*1000,4);
     row["is_circle"]=S(()=>arc.IsCircle().ToString());
     var c=(SW.ISketchPoint)arc.GetCenterPoint2();
     row["center_mm"]=new[]{Math.Round(c.X*1000,4),Math.Round(c.Y*1000,4),Math.Round(c.Z*1000,4)};
    }
    var ln=seg as SW.ISketchLine;
    if(ln!=null){
     row["kind"]="line";
     var p1=(SW.ISketchPoint)ln.GetStartPoint2(); var p2=(SW.ISketchPoint)ln.GetEndPoint2();
     row["from_mm"]=new[]{Math.Round(p1.X*1000,4),Math.Round(p1.Y*1000,4),Math.Round(p1.Z*1000,4)};
     row["to_mm"]=new[]{Math.Round(p2.X*1000,4),Math.Round(p2.Y*1000,4),Math.Round(p2.Z*1000,4)};
    }
    segs.Add(row);
   }
   r["sketch_segments"]=segs.ToArray();
   r["sketch_segment_count"]=segs.Count;

   var pts=new List<object>();
   foreach(var o in A(sk.GetSketchPoints2())){
    var pt=o as SW.ISketchPoint; if(pt==null) continue;
    pts.Add(new{x_mm=Math.Round(pt.X*1000,4),y_mm=Math.Round(pt.Y*1000,4),z_mm=Math.Round(pt.Z*1000,4),
                type=S(()=>pt.Type.ToString())});
   }
   r["sketch_points"]=pts.ToArray();

   // ---- the sketch plane frame, so we know which sketch axis means what ----
   try{
    var tm=(SW.IMathTransform)((SW.ISketch)sk).ModelToSketchTransform;
    var arr=(double[])tm.ArrayData;
    r["sketch_frame"]=new{
     origin_mm=new[]{Math.Round(arr[9]*1000,4),Math.Round(arr[10]*1000,4),Math.Round(arr[11]*1000,4)},
     xAxis=new[]{Math.Round(arr[0],5),Math.Round(arr[1],5),Math.Round(arr[2],5)},
     yAxis=new[]{Math.Round(arr[3],5),Math.Round(arr[4],5),Math.Round(arr[5],5)},
     zAxis=new[]{Math.Round(arr[6],5),Math.Round(arr[7],5),Math.Round(arr[8],5)}};
   }catch(Exception ex){ log.Add(new{step="frame",error=ex.Message}); }

   // ---- dimensions on that sketch ----
   var dims=new List<object>();
   object disp=sf.GetFirstDisplayDimension(); int g=0;
   while(disp!=null&&g++<300){
    try{
     var d=(SW.IDimension)((SW.IDisplayDimension)disp).GetDimension2(0);
     if(d!=null) dims.Add(new{full=d.FullName,
                              value_mm=Math.Round(N(()=>d.SystemValue)*1000,4),
                              name=d.Name});
    }catch{}
    try{disp=sf.GetNextDisplayDimension(disp);}catch{disp=null;}
   }
   r["sketch_dimensions"]=dims.ToArray();

   // ---- optional: add a dimension from 右视基准面 to the bore circle ----
   if(attempt){
    var right=FindFeat(doc,"\u53f3\u89c6\u57fa\u51c6\u9762");
    int boreIdx=-1;
    for(int k=0;k<segObjs.Count;k++){
     var arc=segObjs[k] as SW.ISketchArc; if(arc==null) continue;
     if(Math.Abs(N(()=>arc.GetRadius())-boreR)<0.0005){ boreIdx=k; break; }
    }
    log.Add(new{step="locate",right_plane=right!=null,bore_seg=boreIdx});
    if(right==null||boreIdx<0){ r["log"]=log.ToArray(); return r; }
    sf.Select2(false,0);
    doc.EditSketch(); string editing="1";
    log.Add(new{step="edit_sketch",ok=editing,active=S(()=>(doc.GetActiveSketch2() as SW.ISketch)!=null?"1":"0")});
    var sk2=doc.GetActiveSketch2() as SW.ISketch;
    if(sk2==null){ r["log"]=log.ToArray(); return r; }
    var segs2=A(sk2.GetSketchSegments());
    var target=segs2[boreIdx] as SW.ISketchSegment;
    var arc2=target as SW.ISketchArc;
    var ctr=(SW.ISketchPoint)arc2.GetCenterPoint2();

    // strategy A: select circle segment + plane, then AddDimension2
    string detailA="";
    try{
     doc.ClearSelection2(true);
     target.Select4(false,null);
     int n1=((SW.ISelectionMgr)doc.SelectionManager).GetSelectedObjectCount2(-1);
     right.Select2(true,0);
     int n2=((SW.ISelectionMgr)doc.SelectionManager).GetSelectedObjectCount2(-1);
     var got=doc.AddDimension2(ctr.X+0.02,ctr.Y+0.02,ctr.Z);
     detailA="sel1="+n1+" sel2="+n2+" addDim="+(got==null?"null":"ok");
    }catch(Exception ex){ detailA="EX "+ex.Message; }
    log.Add(new{step="strategyA_circle+plane",detail=detailA});
    doc.ClearSelection2(true);

    // strategy B: select the centre sketch point + plane, then AddDimension2
    string detailB="";
    try{
     doc.ClearSelection2(true);
     SW.ISketchPoint centre=null;
     foreach(var o in A(sk2.GetSketchPoints2())){
      var pt=o as SW.ISketchPoint; if(pt==null) continue;
      if(Math.Abs(pt.X-ctr.X)<1e-9&&Math.Abs(pt.Y-ctr.Y)<1e-9&&Math.Abs(pt.Z-ctr.Z)<1e-9){ centre=pt; break; }
     }
     if(centre==null) detailB="centre point not in GetSketchPoints2";
     else{
      centre.Select4(false,null);
      int n1=((SW.ISelectionMgr)doc.SelectionManager).GetSelectedObjectCount2(-1);
      right.Select2(true,0);
      int n2=((SW.ISelectionMgr)doc.SelectionManager).GetSelectedObjectCount2(-1);
      var got=doc.AddDimension2(ctr.X+0.02,ctr.Y+0.02,ctr.Z);
      detailB="sel1="+n1+" sel2="+n2+" addDim="+(got==null?"null":"ok");
     }
    }catch(Exception ex){ detailB="EX "+ex.Message; }
    log.Add(new{step="strategyB_centre+plane",detail=detailB});
    doc.ClearSelection2(true);

    doc.ForceRebuild3(false);
    var after=new List<string>();
    var sf2=FindFeat(doc,skName);
    object d3=sf2.GetFirstDisplayDimension(); int g3=0;
    while(d3!=null&&g3++<300){
     try{ var dd=(SW.IDimension)((SW.IDisplayDimension)d3).GetDimension2(0);
          if(dd!=null) after.Add(dd.FullName+" = "+Math.Round(N(()=>dd.SystemValue)*1000,4)+"mm"); }catch{}
     try{d3=sf2.GetNextDisplayDimension(d3);}catch{d3=null;}
    }
    log.Add(new{step="dims_after",count=after.Count,dims=after.ToArray()});
    doc.EditSketch();
    doc.ForceRebuild3(false);
    r["log"]=log.ToArray();
   }
  }
  catch(Exception ex){ r["exception"]=ex.ToString(); }
  finally{ try{app.CloseDoc(doc.GetTitle());}catch{} }
  return r;
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
$result=[Sketch2Probe]::Run([IO.Path]::GetFullPath($Folder),[bool]$AttemptDimension)
$json=$result | ConvertTo-Json -Depth 30
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
