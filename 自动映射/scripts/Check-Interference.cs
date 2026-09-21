// Standalone interference check for a finished trial folder.  Compiled twin of
// Test-Interference.ps1 (this machine's execution policy blocks .ps1 files).
//
// It WRITES NOTHING.  The assembly is opened writable only because interference detection builds
// temporary interference bodies, and read-only opens have crashed SolidWorks here.  The document
// is closed without saving, so the trial folder is unchanged.
//
// Two passes are reported:
//   * TreatCoincidenceAsInterference = false  (the headline number; designed mating faces that
//     merely touch must not count)
//   * = true  (the count with touching faces included) - if the two differ only by zero-volume
//     pairs, the part is genuinely clear rather than sitting exactly flush.
//
// Usage: Check-Interference.exe <trialFolder> <assemblyFile> <output.json>
using System;
using System.IO;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW = SolidWorks.Interop.sldworks;

public static class CheckInterference
{
    static object[] A(object x) { return x as object[] ?? new object[0]; }
    static string S(Func<string> f) { try { return f(); } catch { return ""; } }

    public static int Main(string[] argv)
    {
        if (argv.Length < 3)
        {
            Console.Error.WriteLine("usage: Check-Interference.exe <trialFolder> <assemblyFile> <output.json>");
            return 2;
        }
        string folder = Path.GetFullPath(argv[0]);
        string asmPath = Path.GetFullPath(Path.Combine(folder, argv[1]));
        string outPath = argv[2];
        if (!File.Exists(asmPath)) { Console.Error.WriteLine("assembly not found: " + asmPath); return 3; }

        object com;
        try { com = Marshal.GetActiveObject("SldWorks.Application.34"); }
        catch (Exception ex) { Console.Error.WriteLine("cannot attach - start SolidWorks first: " + ex.Message); return 4; }
        var app = (SW.ISldWorks)com;

        var r = new Dictionary<string, object>();
        r["folder"] = folder;
        r["assembly"] = asmPath;
        try
        {
            int e = 0, w = 0;
            var doc = (SW.IModelDoc2)app.OpenDoc6(asmPath, 2, 1, "\u5f00\u5ea645\u00b0", ref e, ref w);
            if (doc == null) throw new Exception("open failed: errors=" + e + " warnings=" + w);
            try
            {
                r["open_errors"] = e; r["open_warnings"] = w;
                var adoc = (SW.IAssemblyDoc)doc;
                r["assembly_rebuild_ok"] = doc.ForceRebuild3(false);
                var comps = new List<string>();
                foreach (var o in A(adoc.GetComponents(false))) { var c = o as SW.IComponent2; if (c != null) comps.Add(S(() => c.Name2)); }
                r["components"] = comps.ToArray();

                var mgr = (SW.IInterferenceDetectionMgr)adoc.InterferenceDetectionManager;
                if (mgr == null) throw new Exception("InterferenceDetectionManager unavailable");
                mgr.IncludeMultibodyPartInterferences = false;

                mgr.TreatCoincidenceAsInterference = false;
                var pass1 = Collect(mgr);
                r["interference_count"] = pass1.Count;
                r["interferences"] = pass1.ToArray();

                mgr.TreatCoincidenceAsInterference = true;
                var pass2 = Collect(mgr);
                r["coincident_count"] = pass2.Count;
                r["coincident_total_mm3"] = Sum(pass2);

                try { mgr.TreatCoincidenceAsInterference = false; } catch { }
                try { mgr.Done(); } catch { }
                r["finished"] = true;
            }
            finally { try { app.CloseDoc(doc.GetTitle()); } catch { } }   // never saved
        }
        catch (Exception ex) { r["exception"] = ex.ToString(); }

        File.WriteAllText(outPath, Json.Write(r), new UTF8Encoding(false));
        Console.WriteLine("written: " + outPath);
        return 0;
    }

    static List<Dictionary<string, object>> Collect(SW.IInterferenceDetectionMgr mgr)
    {
        var rows = new List<Dictionary<string, object>>();
        var arr = mgr.GetInterferences() as Array;
        int n = arr == null ? 0 : arr.Length;
        for (int i = 0; i < n; i++)
        {
            SW.IInterference it = null;
            try { it = arr.GetValue(i) as SW.IInterference; } catch { continue; }
            if (it == null) continue;
            var names = new List<string>();
            try
            {
                var cs = it.Components;
                if (cs is Array) foreach (object o in (Array)cs) { var c = o as SW.IComponent2; names.Add(c == null ? "<null>" : S(() => c.Name2)); }
            }
            catch (Exception ex) { names.Add("<err " + ex.Message + ">"); }
            double vol = 0; try { vol = it.Volume * 1e9; } catch { }
            object box = null;
            try
            {
                var b = it.GetInterferenceBody() as SW.IBody2;
                var bb = b == null ? null : b.GetBodyBox() as double[];
                if (bb != null && bb.Length == 6)
                    box = new[]{ Math.Round(bb[0]*1000,3), Math.Round(bb[1]*1000,3), Math.Round(bb[2]*1000,3),
                                 Math.Round(bb[3]*1000,3), Math.Round(bb[4]*1000,3), Math.Round(bb[5]*1000,3) };
            }
            catch { }
            rows.Add(new Dictionary<string, object>{
                {"index", i}, {"components", names.ToArray()}, {"volume_mm3", Math.Round(vol, 3)}, {"box_mm", box}});
        }
        return rows;
    }

    static double Sum(List<Dictionary<string, object>> rows)
    {
        double t = 0;
        foreach (var row in rows) { object v; if (row.TryGetValue("volume_mm3", out v) && v is double) t += (double)v; }
        return Math.Round(t, 3);
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
