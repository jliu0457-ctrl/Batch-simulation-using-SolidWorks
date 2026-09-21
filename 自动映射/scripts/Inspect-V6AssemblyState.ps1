param([Parameter(Mandatory=$true)][string]$Folder)
$ErrorActionPreference='Stop'
$interop='D:\Program Files\2026SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll'
if(-not (Test-Path -LiteralPath $interop -PathType Leaf)){throw "SolidWorks interop missing: $interop"}
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
$source=@'
using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW=SolidWorks.Interop.sldworks;
public static class V6AssemblyState {
 static object[] A(object x){return x as object[]??new object[0];}
 static object Errors(SW.IModelDoc2 d){var a=new List<object>();for(var f=(SW.IFeature)d.FirstFeature();f!=null;f=(SW.IFeature)f.GetNextFeature()){if(f.GetErrorCode()!=0)a.Add(new{name=f.Name,type=f.GetTypeName2(),code=f.GetErrorCode()});for(var s=(SW.IFeature)f.GetFirstSubFeature();s!=null;s=(SW.IFeature)s.GetNextSubFeature())if(s.GetErrorCode()!=0)a.Add(new{name=s.Name,type=s.GetTypeName2(),code=s.GetErrorCode()});}return a.ToArray();}
 static object Components(SW.IAssemblyDoc a){return A(a.GetComponents(false)).Cast<SW.IComponent2>().OrderBy(c=>c.Name2).Select(c=>new{name=c.Name2,path=c.GetPathName(),suppression=c.GetSuppression(),fixed_state=SafeFixed(c),transform=Transform(c)}).ToArray();}
 static object Features(SW.IModelDoc2 d){var rows=new List<object>();for(var f=(SW.IFeature)d.FirstFeature();f!=null;f=(SW.IFeature)f.GetNextFeature()){var children=new List<object>();for(var s=(SW.IFeature)f.GetFirstSubFeature();s!=null;s=(SW.IFeature)s.GetNextSubFeature())children.Add(new{name=s.Name,type=s.GetTypeName2(),error=s.GetErrorCode()});rows.Add(new{name=f.Name,type=f.GetTypeName2(),error=f.GetErrorCode(),children=children.ToArray()});}return rows.ToArray();}
 static bool SafeFixed(SW.IComponent2 c){try{return c.IsFixed();}catch{return false;}}
 static double[] Transform(SW.IComponent2 c){try{var t=(SW.IMathTransform)c.Transform2;return t==null?new double[0]:(double[])t.ArrayData;}catch{return new double[0];}}
 static void CloseOwned(SW.ISldWorks a,string root){var names=new List<string>();for(var d=(SW.IModelDoc2)a.GetFirstDocument();d!=null;d=(SW.IModelDoc2)d.GetNext()){var p=d.GetPathName();if(!String.IsNullOrEmpty(p)&&p.StartsWith(root+"\\",StringComparison.OrdinalIgnoreCase))names.Add(d.GetTitle());}foreach(var n in names)a.CloseDoc(n);}
 public static object Run(string folder){folder=Path.GetFullPath(folder).TrimEnd('\\');object com;try{com=Marshal.GetActiveObject("SldWorks.Application.34");}catch{com=Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application.34",true));}var app=(SW.ISldWorks)com;app.Visible=true;CloseOwned(app,folder);int e=0,w=0;var p=Directory.GetFiles(folder,"*.SLDASM").Single();var d=(SW.IModelDoc2)app.OpenDoc6(p,2,1,"",ref e,ref w);if(d==null)throw new Exception("OpenDoc6 failed: "+e+","+w);var a=(SW.IAssemblyDoc)d;var beforeComponents=Components(a);var beforeErrors=Errors(d);var beforeFeatures=Features(d);bool rebuild=d.ForceRebuild3(false);var afterComponents=Components(a);var afterErrors=Errors(d);var afterFeatures=Features(d);CloseOwned(app,folder);return new{assembly=p,open_errors=e,open_warnings=w,before_components=beforeComponents,before_errors=beforeErrors,before_features=beforeFeatures,rebuild_ok=rebuild,after_components=afterComponents,after_errors=afterErrors,after_features=afterFeatures};}
}
'@
Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop,'System.Core')
[V6AssemblyState]::Run([IO.Path]::GetFullPath($Folder)) | ConvertTo-Json -Depth 30
