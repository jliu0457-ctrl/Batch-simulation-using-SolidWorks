param(
    [Parameter(Mandatory=$true)][string]$PartPath,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [double]$NewC = 31
)
# Read-only in effect: the part is opened writable and `c` is changed in memory, but the
# document is closed WITHOUT saving, so the file on disk is untouched.  Point -PartPath at a
# scratch copy anyway.
#
# Answers one question: when `c` changes, does this part MOVE or does it CHANGE SHAPE?
# Every conical / planar / cylindrical face is dumped before and after, together with the
# equation list, so a rigid translation (all faces shift by the same vector) can be told apart
# from a thickness change (one face moves, its opposite stays).
#
# Why this matters: the DISC is the anchor of the seat interference.  The gasket and the seal
# ring each translate their whole body when `c` changes, while the disc moves only its sealing
# cone and leaves its other ~50 faces pinned, so both neighbours end up cutting into it.
# (An earlier version of this comment claimed the opposite - that disc+gasket do not interfere,
# so the axial offset was fine.  That came from misreading a control run as the test run; the
# raw numbers are in _analysis/*.json and its `writes` field says which parts each run touched.)
#
# Run this on the disc to find which faces follow `c` and which stay pinned, and on the gasket
# and the seal ring to confirm they translate rigidly.
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

public static class PartCResponse {
 static object[] A(object x){return x as object[] ?? new object[0];}
 static string S(Func<string> f){try{return f();}catch{return "";}}
 static string CP(params int[] cps){ var s=""; foreach(int c in cps) s+=((char)c).ToString(); return s; }

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
     if(q[6]*1000<1) continue;
     rows.Add(new Dictionary<string,object>{{"kind","cylinder"},{"area_mm2",area},
       {"radius_mm",Math.Round(q[6]*1000,4)},
       {"axis",new[]{Math.Round(q[3],5),Math.Round(q[4],5),Math.Round(q[5],5)}},
       {"point_mm",new[]{Math.Round(q[0]*1000,4),Math.Round(q[1]*1000,4),Math.Round(q[2]*1000,4)}}});
    }
   }
  return rows.ToArray();
 }

 static object EqList(SW.IModelDoc2 doc){
  SW.IEquationMgr em=null;
  try{ em=(SW.IEquationMgr)doc.GetEquationMgr(); }catch(Exception ex){ return new{error=ex.Message}; }
  var rows=new List<object>();
  for(int i=0;i<em.GetCount();i++){
   string eq=""; double val=0;
   try{ eq=em.get_Equation(i); }catch{}
   try{ val=em.get_Value(i); }catch{}
   rows.Add(new Dictionary<string,object>{{"i",i},{"equation",eq},{"value",val}});
  }
  return rows.ToArray();
 }

 public static object Run(string partPath,double newC){
  partPath=Path.GetFullPath(partPath);
  object com; try{com=Marshal.GetActiveObject("SldWorks.Application.34");}
  catch{throw new Exception("cannot attach to SolidWorks");}
  var app=(SW.ISldWorks)com;
  var r=new Dictionary<string,object>();
  var log=new List<object>();
  int e=0,w=0;
  var doc=(SW.IModelDoc2)app.OpenDoc6(partPath,1,1,"",ref e,ref w);
  if(doc==null){ r["error"]="open failed errors="+e+" warnings="+w; return r; }
  string title=S(()=>doc.GetTitle());
  try{
   doc.ForceRebuild3(false);
   r["title"]=title;
   r["equations"]=EqList(doc);
   r["faces_before"]=Faces(doc);

   SW.IEquationMgr em=(SW.IEquationMgr)doc.GetEquationMgr();
   int idx=-1; string cname=CP(0x0063);
   for(int i=0;i<em.GetCount();i++){
    string eq=""; try{ eq=em.get_Equation(i); }catch{}
    if(eq.TrimStart().StartsWith("\""+cname+"\"")){ idx=i; break; }
   }
   r["c_equation_index"]=idx;
   if(idx<0){ r["error"]="no c equation"; return r; }
   string want="\""+cname+"\"= "+newC.ToString(System.Globalization.CultureInfo.InvariantCulture);

   bool activated=false;
   try{ int ae=0; app.ActivateDoc3(doc.GetTitle(),false,0,ref ae); activated=true; }catch(Exception ex){ log.Add(new{step="activate",error=ex.Message}); }
   r["activated"]=activated;
   try{ em.set_Equation(idx,want); }catch(Exception ex){ log.Add(new{step="set_equation",error=ex.Message}); }
   string now=""; try{ now=em.get_Equation(idx); }catch{}
   r["equation_after"]=now;
   r["equation_applied"]= now.Replace(" ","")==want.Replace(" ","");
   if(!r["equation_applied"].Equals(true)){ r["log"]=log.ToArray(); return r; }

   try{ em.EvaluateAll(); }catch(Exception ex){ log.Add(new{step="evaluate_all",error=ex.Message}); }
   r["rebuild_ok"]=doc.ForceRebuild3(false);
   r["faces_after"]=Faces(doc);
   r["log"]=log.ToArray();
  }
  catch(Exception ex){ r["exception"]=ex.ToString(); r["log"]=log.ToArray(); }
  finally{ try{app.CloseDoc(title);}catch{} }   // closed WITHOUT saving
  return r;
 }
}
'@
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
try{ Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop) -ErrorAction Stop }
catch{ Write-Host "=== Add-Type failed ==="; Write-Host $_.Exception.Message
        if($_.Exception.InnerException){ Write-Host "--- inner ---"; Write-Host $_.Exception.InnerException.Message }; exit 1 }
$result=[PartCResponse]::Run([IO.Path]::GetFullPath($PartPath),$NewC)
$json=$result | ConvertTo-Json -Depth 20
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false))
Write-Output ("written: "+$OutputPath)
