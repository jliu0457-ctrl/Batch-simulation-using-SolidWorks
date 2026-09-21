param(
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [string]$SketchHint = "\u8349\u56fe2"
)
# Read-only.  Inspects whichever document is currently active in SolidWorks - used when that
# session is busy and cannot open new files.  Dumps reference planes, cylinders, the sketch
# inventory and the sketch dimensions.  Never writes to the document.
# ASCII only; Chinese appears as unicode escapes in the C# source.
$ErrorActionPreference='Stop'
# callers pass Chinese names as \uXXXX escapes (this file stays ASCII); decode them here
$SketchHint = [regex]::Replace($SketchHint,'\\u([0-9a-fA-F]{4})',
    [System.Text.RegularExpressions.MatchEvaluator]{ param($m) [string][char][int]("0x"+$m.Groups[1].Value) })
$interop='D:\Program Files\2026SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll'
if(-not (Test-Path -LiteralPath $interop -PathType Leaf)){ throw "interop missing: $interop" }
$source=@'
using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW=SolidWorks.Interop.sldworks;

public static class ActivePartProbe {
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

 public static object Run(string hint){
  object com; try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  var r=new Dictionary<string,object>();
  var log=new List<object>();
  var doc=app.ActiveDoc as SW.IModelDoc2;
  if(doc==null){ r["error"]="no active document"; return r; }
  r["title"]=S(()=>doc.GetTitle());
  r["path"]=S(()=>doc.GetPathName());
  r["type"]=S(()=>doc.GetType().ToString());
  r["is_part"]=(doc as SW.IPartDoc)!=null;

  // --- reference planes ---
  var planes=new List<object>();
  foreach(string pn in new[]{"\u524d\u89c6\u57fa\u51c6\u9762","\u4e0a\u89c6\u57fa\u51c6\u9762",
                             "\u53f3\u89c6\u57fa\u51c6\u9762","\u57fa\u51c6\u97622","\u57fa\u51c6\u97623"}){
   var pf=FindFeat(doc,pn);
   if(pf==null){ planes.Add(new Dictionary<string,object>{{"name",pn},{"found",false}}); continue; }
   try{
    var rp=(SW.IRefPlane)pf.GetSpecificFeature2();
    var arr=(double[])((SW.IMathTransform)rp.Transform).ArrayData;
    planes.Add(new Dictionary<string,object>{{"name",pn},{"found",true},
      {"origin_mm",new[]{Math.Round(arr[9]*1000,4),Math.Round(arr[10]*1000,4),Math.Round(arr[11]*1000,4)}},
      {"normal",new[]{Math.Round(arr[6],5),Math.Round(arr[7],5),Math.Round(arr[8],5)}}});
   }catch(Exception ex){ planes.Add(new Dictionary<string,object>{{"name",pn},{"found",true},{"error",ex.Message}}); }
  }
  r["reference_planes"]=planes.ToArray();

  // --- cylinders ---
  var cyls=new List<Dictionary<string,object>>();
  try{
   var pd=doc as SW.IPartDoc;
   if(pd!=null) foreach(var bo in A(pd.GetBodies2(0,false))){
    var b=bo as SW.IBody2; if(b==null) continue;
    foreach(var fo in A(b.GetFaces())){
     var f=fo as SW.IFace2; if(f==null) continue;
     var s=f.GetSurface() as SW.ISurface; if(s==null) continue;
     var crow=new Dictionary<string,object>();
     crow["area_mm2"]=Math.Round(Math.Abs(f.GetArea())*1e6,3);
     if(s.IsCylinder()){
      var cp=(double[])s.CylinderParams;
      if(cp[6]*1000.0<3) continue;
      crow["kind"]="cylinder";
      crow["radius_mm"]=Math.Round(cp[6]*1000,3);
      crow["point_mm"]=new[]{Math.Round(cp[0]*1000,4),Math.Round(cp[1]*1000,4),Math.Round(cp[2]*1000,4)};
      crow["axis"]=new[]{Math.Round(cp[3],5),Math.Round(cp[4],5),Math.Round(cp[5],5)};
     } else if(s.IsCone()){
      var q=(double[])s.ConeParams;
      crow["kind"]="cone";
      crow["radius_mm"]=Math.Round(q[6]*1000,3);
      crow["half_angle_deg"]=Math.Round(q[7]*180.0/Math.PI,4);
      crow["point_mm"]=new[]{Math.Round(q[0]*1000,4),Math.Round(q[1]*1000,4),Math.Round(q[2]*1000,4)};
      crow["axis"]=new[]{Math.Round(q[3],5),Math.Round(q[4],5),Math.Round(q[5],5)};
     } else if(s.IsPlane()){
      var pp=(double[])s.PlaneParams;
      crow["kind"]="plane";
      crow["normal"]=new[]{Math.Round(pp[0],5),Math.Round(pp[1],5),Math.Round(pp[2],5)};
      crow["point_mm"]=new[]{Math.Round(pp[3]*1000,4),Math.Round(pp[4]*1000,4),Math.Round(pp[5]*1000,4)};
     } else continue;
     cyls.Add(crow);
    }
   }
  }catch(Exception ex){ log.Add(new{step="cyls",error=ex.Message}); }
  r["cylinders"]=cyls.OrderByDescending(x=>Convert.ToDouble(x["area_mm2"])).ToArray();

  // --- feature tree, one level deep, for orientation ---
  var feats=new List<string>();
  for(var f=(SW.IFeature)doc.FirstFeature();f!=null;f=(SW.IFeature)f.GetNextFeature()){
   feats.Add(S(()=>f.Name)+" ["+S(()=>f.GetTypeName2())+"]");
  }
  r["features"]=feats.ToArray();

  // --- the hinted sketch ---
  var sf=FindFeat(doc,hint);
  r["sketch_found"]=sf!=null;
  if(sf!=null){
   var sk=sf.GetSpecificFeature2() as SW.ISketch;
   if(sk!=null){
    var segs=new List<object>();
    int si=0;
    foreach(var o in A(sk.GetSketchSegments())){
     var seg=o as SW.ISketchSegment; if(seg==null) continue; si++;
     var row=new Dictionary<string,object>();
     row["i"]=si;
     row["typename"]=S(()=>seg.GetType().ToString());
     row["construction"]=S(()=>seg.ConstructionGeometry?"1":"0");
     var arc=seg as SW.ISketchArc;
     if(arc!=null){
      row["kind"]="arc";
      row["radius_mm"]=Math.Round(N(()=>arc.GetRadius())*1000,4);
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
     pts.Add(new Dictionary<string,object>{{"x_mm",Math.Round(pt.X*1000,4)},{"y_mm",Math.Round(pt.Y*1000,4)},
                                          {"z_mm",Math.Round(pt.Z*1000,4)},{"type",S(()=>pt.Type.ToString())}});
    }
    r["sketch_points"]=pts.ToArray();

    try{
     var tm=(SW.IMathTransform)sk.ModelToSketchTransform;
     var arr=(double[])tm.ArrayData;
     r["sketch_frame"]=new Dictionary<string,object>{
      {"origin_mm",new[]{Math.Round(arr[9]*1000,4),Math.Round(arr[10]*1000,4),Math.Round(arr[11]*1000,4)}},
      {"xAxis",new[]{Math.Round(arr[0],5),Math.Round(arr[1],5),Math.Round(arr[2],5)}},
      {"yAxis",new[]{Math.Round(arr[3],5),Math.Round(arr[4],5),Math.Round(arr[5],5)}},
      {"zAxis",new[]{Math.Round(arr[6],5),Math.Round(arr[7],5),Math.Round(arr[8],5)}}};
    }catch(Exception ex){ log.Add(new{step="frame",error=ex.Message}); }

    var dims=new List<object>();
    object disp=sf.GetFirstDisplayDimension(); int g=0;
    while(disp!=null&&g++<300){
     try{
      var d=(SW.IDimension)((SW.IDisplayDimension)disp).GetDimension2(0);
      if(d!=null) dims.Add(new Dictionary<string,object>{{"full",d.FullName},
        {"value_mm",Math.Round(N(()=>d.SystemValue)*1000,4)}});
     }catch{}
     try{disp=sf.GetNextDisplayDimension(disp);}catch{disp=null;}
    }
    r["sketch_dimensions"]=dims.ToArray();
   }
  }
  r["log"]=log.ToArray();
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
$result=[ActivePartProbe]::Run($SketchHint)
$json=$result | ConvertTo-Json -Depth 30
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
