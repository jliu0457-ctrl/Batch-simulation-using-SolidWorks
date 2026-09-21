param(
    [Parameter(Mandatory=$true)][string]$PartPath,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [switch]$Apply,
    [double]$Offset = 1.0
)
# Tries to give the disc's stem bore an X-position driving dimension, then measures whether
# the bore actually follows it.  Nothing is ever saved - the document is closed without
# saving - so the part on disk is untouched even with -Apply.
#
# Why this shape: 草图2 feeds both 凸台-拉伸1 and 切除-拉伸1.  Its Ø45 hole is two r=22.5 arcs
# whose centre sits on the crossing of the vertical construction line (x=0) and the horizontal
# one (y=e), so the hole has no X degree of freedom to dimension.  Three selection strategies
# are tried in order; whichever produces a NEW dimension wins, and -Apply then drives it to
# Offset mm and reports how far the bore moved.
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

public static class BoreAxialDim {
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

 static double[] BoreOrigin(SW.IModelDoc2 doc){
  var pd=doc as SW.IPartDoc; if(pd==null) return null;
  double best=-1; double[] bestPt=null;
  try{
   foreach(var o in A(pd.GetBodies2(0,false))){
    var b=o as SW.IBody2; if(b==null) continue;
    foreach(var fo in A(b.GetFaces())){
     var fc=fo as SW.IFace2; if(fc==null) continue;
     var sf=fc.GetSurface() as SW.ISurface; if(sf==null||!sf.IsCylinder()) continue;
     var cp=(double[])sf.CylinderParams;
     if(Math.Abs(cp[5])<0.99) continue;
     double rad=cp[6]*1000.0;
     if(rad<15||rad>30) continue;
     double area=0; try{area=Math.Abs(fc.GetArea());}catch{}
     if(area>best){ best=area; bestPt=new[]{cp[0]*1000,cp[1]*1000,cp[2]*1000}; }
    }
   }
  }catch{}
  return bestPt;
 }

 static List<SW.IDimension> Dims(SW.IFeature sf){
  var list=new List<SW.IDimension>();
  object disp=null; try{disp=sf.GetFirstDisplayDimension();}catch{}
  int g=0;
  while(disp!=null&&g++<300){
   try{ var d=(SW.IDimension)((SW.IDisplayDimension)disp).GetDimension2(0); if(d!=null) list.Add(d); }catch{}
   try{disp=sf.GetNextDisplayDimension(disp);}catch{disp=null;}
  }
  return list;
 }
 static List<string> DimLabels(List<SW.IDimension> ds){
  var o=new List<string>();
  foreach(var d in ds) o.Add(S(()=>d.FullName.Split('@')[0])+" = "+Math.Round(N(()=>d.SystemValue)*1000,4));
  return o;
 }

 public static object Run(string partPath,bool apply,double offset){
  partPath=Path.GetFullPath(partPath);
  object com; try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  var r=new Dictionary<string,object>();
  var log=new List<object>();
  int e=0,w=0;
  var doc=(SW.IModelDoc2)app.OpenDoc6(partPath,1,1,"",ref e,ref w);
  if(doc==null){ r["error"]="open failed "+e+","+w; return r; }
  string title=S(()=>doc.GetTitle());
  try{
   doc.ForceRebuild3(false);
   var bore0=BoreOrigin(doc);
   r["bore_before_mm"]=bore0;
   if(bore0==null){ r["error"]="stem bore cylinder not found"; return r; }

   var sf=FindFeat(doc,"\u8349\u56fe2");
   if(sf==null){ r["error"]="sketch2 not found"; return r; }
   r["dims_before"]=DimLabels(Dims(sf)).ToArray();

   sf.Select2(false,0);
   doc.EditSketch();
   var sk=doc.GetActiveSketch2() as SW.ISketch;
   if(sk==null){ r["error"]="could not enter sketch2"; return r; }

   var segs=A(sk.GetSketchSegments());
   SW.ISketchSegment boreArc=null,verticalLine=null,horizLine=null;
   for(int k=0;k<segs.Length;k++){
    var seg=segs[k] as SW.ISketchSegment; if(seg==null) continue;
    var arc=seg as SW.ISketchArc;
    if(arc!=null && Math.Abs(N(()=>arc.GetRadius())*1000-22.5)<0.05 && boreArc==null) boreArc=seg;
    var ln=seg as SW.ISketchLine;
    if(ln!=null){
     var p1=(SW.ISketchPoint)ln.GetStartPoint2(); var p2=(SW.ISketchPoint)ln.GetEndPoint2();
     if(Math.Abs(p1.X*1000)<1e-6 && Math.Abs(p2.X*1000)<1e-6 && verticalLine==null) verticalLine=seg;
     if(Math.Abs(p1.Y*1000-3.7)<1e-4 && Math.Abs(p2.Y*1000-3.7)<1e-4 && horizLine==null) horizLine=seg;
    }
   }
   var rightPlane=FindFeat(doc,"\u53f3\u89c6\u57fa\u51c6\u9762");
   log.Add(new{step="locate",bore_arc=boreArc!=null,vertical_line=verticalLine!=null,
               horizontal_line=horizLine!=null,plane=rightPlane!=null});
   if(boreArc==null){ r["error"]="diameter-45 arc not found in sketch2"; return r; }

   var before=new HashSet<string>(Dims(sf).Select(d=>S(()=>d.FullName)));
   var ctr=(SW.ISketchPoint)((SW.ISketchArc)boreArc).GetCenterPoint2();

   // --- strategy A: bore arc + right datum plane ---
   string madeA=""; string newA=null;
   try{
    doc.ClearSelection2(true);
    boreArc.Select4(false,null);
    if(rightPlane!=null) rightPlane.Select2(true,0);
    var d=doc.AddDimension2(ctr.X+0.02,ctr.Y+0.02,ctr.Z);
    var now=Dims(sf);
    var added=now.FirstOrDefault(x=>!before.Contains(S(()=>x.FullName)));
    newA=added==null?null:S(()=>added.FullName);
    madeA="dim="+(d==null?"null":"ok")+" new="+(newA==null?"none":newA);
   }catch(Exception ex){ madeA="EX "+ex.Message; }
   log.Add(new{step="A_arc+plane",detail=madeA});
   doc.ClearSelection2(true);

   // --- strategy B: bore arc + the vertical construction centreline ---
   string madeB=""; string newB=null;
   if(newA==null && verticalLine!=null){
    try{
     doc.ClearSelection2(true);
     boreArc.Select4(false,null);
     verticalLine.Select4(true,null);
     var d=doc.AddDimension2(ctr.X-0.02,ctr.Y+0.02,ctr.Z);
     var now=Dims(sf);
     var added=now.FirstOrDefault(x=>!before.Contains(S(()=>x.FullName)));
     newB=added==null?null:S(()=>added.FullName);
     madeB="dim="+(d==null?"null":"ok")+" new="+(newB==null?"none":newB);
    }catch(Exception ex){ madeB="EX "+ex.Message; }
    log.Add(new{step="B_arc+centreline",detail=madeB});
    doc.ClearSelection2(true);
   }

   // --- strategy C: bore arc alone, horizontal dimension ---
   string madeC=""; string newC=null;
   if(newA==null && newB==null){
    try{
     doc.ClearSelection2(true);
     boreArc.Select4(false,null);
     var d=doc.AddHorizontalDimension2(ctr.X+0.02,ctr.Y+0.02,ctr.Z);
     var now=Dims(sf);
     var added=now.FirstOrDefault(x=>!before.Contains(S(()=>x.FullName)));
     newC=added==null?null:S(()=>added.FullName);
     madeC="dim="+(d==null?"null":"ok")+" new="+(newC==null?"none":newC);
    }catch(Exception ex){ madeC="EX "+ex.Message; }
    log.Add(new{step="C_arc+horizontal",detail=madeC});
    doc.ClearSelection2(true);
   }

   string winner=newA??newB??newC;
   r["new_dimension"]=winner;
   if(winner==null){
    log.Add(new{step="verdict",text="no new dimension could be created"});
    r["log"]=log.ToArray();
    doc.EditSketch();                    // leave sketch edit
    return r;
   }

   // --- drive it and see whether the bore follows ---
   doc.EditSketch();                      // exit sketch edit so the solid rebuilds
   doc.ForceRebuild3(false);
   if(apply){
    var sf2=FindFeat(doc,"\u8349\u56fe2");
    var dim=Dims(sf2).FirstOrDefault(x=>S(()=>x.FullName)==winner);
    if(dim==null){ log.Add(new{step="drive",error="winner not found after exit"}); }
    else{
     double v0=N(()=>dim.SystemValue);
     int st=-1; string err="";
     try{ st=dim.SetSystemValue3(v0+offset/1000.0,1,null); }catch(Exception ex){ err=ex.Message; }
     double v1=N(()=>dim.SystemValue);
     doc.ForceRebuild3(false);
     var bore1=BoreOrigin(doc);
     double moved=-1;
     if(bore1!=null&&bore0!=null){
      double dx=bore1[0]-bore0[0],dy=bore1[1]-bore0[1],dz=bore1[2]-bore0[2];
      moved=Math.Sqrt(dx*dx+dy*dy+dz*dz);
      r["bore_delta_mm"]=new[]{Math.Round(dx,6),Math.Round(dy,6),Math.Round(dz,6)};
     }
     log.Add(new{step="drive",status=st,error=err,
                 value_before_mm=Math.Round(v0*1000,4),value_after_mm=Math.Round(v1*1000,4),
                 requested_mm=Math.Round((v0+offset/1000.0)*1000,4),
                 bore_moved_mm=Math.Round(moved,6)});
    }
   }
   r["dims_after"]=DimLabels(Dims(FindFeat(doc,"\u8349\u56fe2"))).ToArray();
   r["log"]=log.ToArray();
  }
  catch(Exception ex){ r["exception"]=ex.ToString(); r["log"]=log.ToArray(); }
  finally{
   try{ doc.ClearSelection2(true); }catch{}
   try{ app.CloseDoc(title); }catch{}          // closed WITHOUT saving
  }
  return r;
 }
}
'@
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
try{
    Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop) -ErrorAction Stop
}catch{
    Write-Host "=== Add-Type failed ==="; Write-Host $_.Exception.Message
    if($_.Exception.InnerException){ Write-Host "--- inner ---"; Write-Host $_.Exception.InnerException.Message }
    exit 1
}
$result=[BoreAxialDim]::Run([IO.Path]::GetFullPath($PartPath),[bool]$Apply,$Offset)
$json=$result | ConvertTo-Json -Depth 30
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
