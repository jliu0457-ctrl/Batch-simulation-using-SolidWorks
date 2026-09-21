// Minimal csc-driven stand-in for Apply-SevenVariableDesign.ps1.  This machine's execution policy
// blocks .ps1 files, so this does the three things the script does *around* the adapter - copy the
// pinned v6 template into a fresh isolated trial folder, call SevenVariableAdapter.ApplyAndMeasure,
// write the report - and nothing else.  It deliberately does NOT re-implement the script's manifest
// hash check; run the .ps1 when you want that gate, or check the hashes separately.
//
// Usage: Run-OneDesign.exe <root> <runName> <designJsonFile>
using System;
using System.IO;
using System.Text;
using System.Collections.Generic;

public static class RunOneDesign
{
    public static int Main(string[] argv)
    {
        if (argv.Length < 3)
        {
            Console.Error.WriteLine("usage: Run-OneDesign.exe <root> <runName> <designJsonFile>");
            return 2;
        }
        string root = Path.GetFullPath(argv[0]);
        string run = argv[1];
        string designPath = Path.GetFullPath(argv[2]);
        string src = Path.Combine(root, "working", "assembly_batch_v6");
        string folder = Path.Combine(root, "working", "seven_variable_trials", run);
        if (!Directory.Exists(src)) { Console.Error.WriteLine("template missing: " + src); return 3; }
        if (Directory.Exists(folder)) { Console.Error.WriteLine("refusing to overwrite existing run: " + folder); return 4; }
        CopyDir(src, folder);
        Console.WriteLine("template -> " + folder);

        string json = File.ReadAllText(designPath);
        Console.WriteLine("design  -> " + json.Trim());
        object report = SevenVariableAdapter.ApplyAndMeasure(root, folder, json);

        string outPath = Path.Combine(root, "_analysis", "e2e_" + run + ".json");
        Directory.CreateDirectory(Path.GetDirectoryName(outPath));
        File.WriteAllText(outPath, Json.Write(report), new UTF8Encoding(false));
        Console.WriteLine("report  -> " + outPath);
        return 0;
    }

    static void CopyDir(string s, string d)
    {
        Directory.CreateDirectory(d);
        foreach (var f in Directory.GetFiles(s)) File.Copy(f, Path.Combine(d, Path.GetFileName(f)), true);
        foreach (var sub in Directory.GetDirectories(s)) CopyDir(sub, Path.Combine(d, Path.GetFileName(sub)));
    }
}

static class Json
{
    public static string Write(object o) { var sb = new StringBuilder(); W(sb, o); return sb.ToString(); }
    static void W(StringBuilder sb, object o)
    {
        if (o == null) { sb.Append("null"); return; }
        var s = o as string;
        if (s != null) { sb.Append('"').Append(s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", "\\n").Replace("\r", "\\r").Replace("\t", "\\t")).Append('"'); return; }
        if (o is bool) { sb.Append(((bool)o) ? "true" : "false"); return; }
        if (o is double) { sb.Append(((double)o).ToString("R", System.Globalization.CultureInfo.InvariantCulture)); return; }
        if (o is float) { sb.Append(((float)o).ToString("R", System.Globalization.CultureInfo.InvariantCulture)); return; }
        if (o is int || o is long) { sb.Append(o.ToString()); return; }
        var d = o as System.Collections.IDictionary;
        if (d != null)
        {
            sb.Append('{'); bool first = true;
            foreach (System.Collections.DictionaryEntry e in d)
            { if (!first) sb.Append(','); first = false; W(sb, e.Key); sb.Append(':'); W(sb, e.Value); }
            sb.Append('}'); return;
        }
        var en = o as System.Collections.IEnumerable;
        if (en != null)
        {
            sb.Append('['); bool first = true;
            foreach (var e in en) { if (!first) sb.Append(','); first = false; W(sb, e); }
            sb.Append(']'); return;
        }
        var props = o.GetType().GetProperties();
        if (props.Length > 0)
        {
            sb.Append('{'); bool first = true;
            foreach (var p in props)
            { if (!first) sb.Append(','); first = false; W(sb, p.Name); sb.Append(':'); W(sb, p.GetValue(o, null)); }
            sb.Append('}'); return;
        }
        sb.Append('"').Append(o.ToString()).Append('"');
    }
}
