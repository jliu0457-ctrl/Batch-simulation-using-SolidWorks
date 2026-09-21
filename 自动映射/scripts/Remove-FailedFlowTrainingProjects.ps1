param([string]$AssemblyPath = '')

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$workspace = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not $AssemblyPath) {
    $assemblies = @(Get-ChildItem -LiteralPath $workspace -File -Filter '*.SLDASM')
    if ($assemblies.Count -ne 1) { throw 'Expected exactly one assembly in the workspace root.' }
    $AssemblyPath = $assemblies[0].FullName
}
$AssemblyPath = [IO.Path]::GetFullPath($AssemblyPath)
if ([IO.Path]::GetDirectoryName($AssemblyPath) -cne [IO.Path]::GetFullPath($workspace).TrimEnd('\')) {
    throw 'Assembly cleanup is restricted to the workspace root assembly.'
}
if (-not (Test-Path -LiteralPath $AssemblyPath -PathType Leaf)) { throw "Assembly not found: $AssemblyPath" }

$interop = 'D:\Program Files\2026SW\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll'
if (-not (Test-Path -LiteralPath $interop -PathType Leaf)) { throw "SolidWorks interop not found: $interop" }
[Reflection.Assembly]::LoadFrom($interop) | Out-Null
$source = @'
using System;
using System.Collections;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
using SW = SolidWorks.Interop.sldworks;

public static class FailedFlowProjectCleaner
{
    static string Decode(string value) { return Encoding.UTF8.GetString(Convert.FromBase64String(value)); }
    static List<string> Names(dynamic configuration)
    {
        var result = new List<string>();
        object raw = configuration.GetProjectNames();
        if (raw is Array) foreach (object value in (Array)raw) if (value != null) result.Add(Convert.ToString(value));
        else if (raw != null) result.Add(Convert.ToString(raw));
        return result;
    }
    public static IDictionary<string, object> Run(string assemblyPath)
    {
        var report = new Dictionary<string, object>();
        SW.ISldWorks app = null; SW.IModelDoc2 model = null; dynamic nca = null;
        string title = null;
        try
        {
            app = (SW.ISldWorks)Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application.34", true));
            int errors = 0, warnings = 0;
            model = (SW.IModelDoc2)app.OpenDoc6(assemblyPath, 2, 1, "", ref errors, ref warnings);
            if (model == null || errors != 0) throw new InvalidOperationException("OpenDoc6 failed; errors=" + errors + " warnings=" + warnings);
            title = model.GetTitle();
            int pid = app.GetProcessID();
            nca = Activator.CreateInstance(Type.GetTypeFromProgID("NIKCommonApi2.BaseApiObject", true));
            if (!(bool)nca.LoadProductAPI2("Flow Simulation", "2026")) throw new InvalidOperationException("Cannot load Flow Simulation API 2026.");
            dynamic interactive = nca.Attach2RunningObject2(pid);
            if (interactive == null) throw new InvalidOperationException("Cannot attach Flow API to SOLIDWORKS PID " + pid);
            dynamic document = interactive.ActiveDocument;
            dynamic configuration = document.ActiveConfiguration;
            string original = Decode("5rWB5L2T5Yqb5a2m5Lu/55yf");
            string[] targets = {
                Decode("5rWB5L2T5Yqb5a2m5Lu/55yfX+WGhemDqOiuree7g192MQ=="),
                Decode("5rWB5L2T5Yqb5a2m5Lu/55yfX+WGhemDqOiuree7g192Mg==")
            };
            List<string> before = Names(configuration);
            if (!before.Contains(original)) throw new InvalidOperationException("Original Flow Simulation project is missing; refusing to change or save the assembly.");
            if (configuration.ActivateProject(original, false) == null) throw new InvalidOperationException("Could not activate the original Flow Simulation project.");
            var removed = new List<string>(); var absent = new List<string>();
            foreach (string target in targets)
            {
                if (before.Contains(target))
                {
                    if (!(bool)configuration.RemoveProject(target)) throw new InvalidOperationException("Failed to remove exact test project: " + target);
                    removed.Add(target);
                }
                else absent.Add(target);
            }
            List<string> remaining = Names(configuration);
            if (!remaining.Contains(original) || remaining.Contains(targets[0]) || remaining.Contains(targets[1]))
                throw new InvalidOperationException("Post-cleanup project verification failed; refusing to save.");
            if (!(bool)document.Save()) throw new InvalidOperationException("The cleaned assembly was not saved.");
            report["assembly"] = assemblyPath; report["removed"] = removed.ToArray();
            report["absent"] = absent.ToArray(); report["remaining_projects"] = remaining.ToArray(); report["saved"] = true;
            return report;
        }
        finally
        {
            if (nca != null) try { nca.UnloadProductAPI(); } catch { }
            if (app != null)
            {
                if (!String.IsNullOrEmpty(title)) try { app.CloseDoc(title); } catch { }
                try { app.ExitApp(); } catch { }
            }
        }
    }
}
'@
Add-Type -TypeDefinition $source -ReferencedAssemblies @($interop, 'Microsoft.CSharp')
[FailedFlowProjectCleaner]::Run($AssemblyPath) | ConvertTo-Json -Depth 5
