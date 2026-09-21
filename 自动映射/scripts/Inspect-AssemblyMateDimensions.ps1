param(
    [Parameter(Mandatory=$true)][string]$AssemblyPath,
    [string]$OutputPath
)
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
public static class AssemblyMateDimensionInspector {
 static object[] A(object value){return value as object[] ?? new object[0];}
 static SW.IFeature Find(SW.IModelDoc2 doc,string name){for(var f=(SW.IFeature)doc.FirstFeature();f!=null;f=(SW.IFeature)f.GetNextFeature()){if(f.Name==name)return f;for(var s=(SW.IFeature)f.GetFirstSubFeature();s!=null;s=(SW.IFeature)s.GetNextSubFeature())if(s.Name==name)return s;}return null;}
 static object Entity(SW.IMateEntity2 entity){var component=(SW.IComponent2)entity.ReferenceComponent;return new{component=component==null?null:component.Name2,reference_type=entity.ReferenceType2,parameters=entity.EntityParams};}
 static object Mate(SW.IModelDoc2 doc,string name){var f=Find(doc,name);if(f==null)return new{name=name,missing=true};var m=(SW.IMate2)f.GetSpecificFeature2();var entities=new List<object>();if(m!=null)for(int i=0;i<m.GetMateEntityCount();i++)entities.Add(Entity((SW.IMateEntity2)m.MateEntity(i)));return new{name=f.Name,type=f.GetTypeName2(),error=f.GetErrorCode(),mate_type=m==null?-1:m.Type,entities=entities.ToArray()};}
 static object Dimensions(SW.IModelDoc2 part){var rows=new List<object>();var seen=new HashSet<string>(StringComparer.OrdinalIgnoreCase);for(var f=(SW.IFeature)part.FirstFeature();f!=null;f=(SW.IFeature)f.GetNextFeature()){object display=null;try{display=f.GetFirstDisplayDimension();}catch{}int guard=0;while(display!=null&&guard++<200){var dd=(SW.IDisplayDimension)display;try{var d=(SW.IDimension)dd.GetDimension2(0);if(seen.Add(d.FullName))rows.Add(new{feature=f.Name,name=d.FullName,value_SI=d.SystemValue});}catch{}try{display=f.GetNextDisplayDimension(display);}catch{display=null;}}}return rows.ToArray();}
 public static object Run(string assemblyPath){assemblyPath=Path.GetFullPath(assemblyPath);object com;try{com=Marshal.GetActiveObject("SldWorks.Application.34");}catch{com=Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application.34",true));}var app=(SW.ISldWorks)com;int e=0,w=0;var doc=(SW.IModelDoc2)app.OpenDoc6(assemblyPath,2,3,"",ref e,ref w);if(doc==null)throw new Exception("read-only open failed: "+e+","+w);var assembly=(SW.IAssemblyDoc)doc;var tokens=new[]{"08\u5927\u57ab\u7247","09\u5bc6\u5c01\u5708","10\u538b\u677f","11\u8776\u677f"};var parts=new List<object>();foreach(var c in A(assembly.GetComponents(false)).Cast<SW.IComponent2>().Where(c=>tokens.Any(t=>c.Name2.Contains(t)))){var p=(SW.IModelDoc2)c.GetModelDoc2();parts.Add(new{component=c.Name2,path=c.GetPathName(),dimensions=p==null?new object[0]:Dimensions(p)});}var names=new[]{"\u91cd\u54083","\u91cd\u54084","\u9501\u5b9a1","\u9501\u5b9a2","\u9501\u5b9a3","\u540c\u5fc33"};var mates=names.Select(n=>Mate(doc,n)).ToArray();return new{assembly=doc.GetPathName(),open_errors=e,open_warnings=w,mates=mates,parts=parts.ToArray()};}
}
'@
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop)
$result=[AssemblyMateDimensionInspector]::Run([IO.Path]::GetFullPath($AssemblyPath))
$json=$result | ConvertTo-Json -Depth 30
if($OutputPath){ [IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath),$json,[Text.UTF8Encoding]::new($false)) }
$json
