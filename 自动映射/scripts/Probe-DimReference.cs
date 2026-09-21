// Answers "what is this dimension actually measuring?" - SolidWorks can hand back the entities a
// dimension is attached to, which is the only way to tell a real driver from a decorative label
// that some other constraint has swallowed.
//
// Read-only: attaches to the already-open document, reads, writes nothing.
//
// Usage: Probe-DimReference.exe <output.json>
using System;
using System.IO;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW = SolidWorks.Interop.sldworks;

public static class DimReferenceProbe
{
    static string S(Func<string> f) { try { return f(); } catch { return ""; } }
    static double N(Func<double> f) { try { return f(); } catch { return 0; } }

    public static int Main(string[] argv)
    {
        if (argv.Length < 1) { Console.Error.WriteLine("usage: Probe-DimReference.exe <output.json>"); return 2; }
        object com;
        try { com = Marshal.GetActiveObject("SldWorks.Application.34"); }
        catch (Exception ex) { Console.Error.WriteLine("cannot attach: " + ex.Message); return 3; }
        var app = (SW.ISldWorks)com;
        var doc = (SW.IModelDoc2)app.ActiveDoc;
        if (doc == null) { Console.Error.WriteLine("no active document"); return 4; }

        var report = new Dictionary<string, object>();
        report["title"] = S(() => doc.GetTitle());
        var rows = new List<object>();
        try
        {
            for (var f = (SW.IFeature)doc.FirstFeature(); f != null; f = (SW.IFeature)f.GetNextFeature())
            {
                Walk(f, rows);
                for (var s = (SW.IFeature)f.GetFirstSubFeature(); s != null; s = (SW.IFeature)s.GetNextSubFeature())
                    Walk(s, rows);
            }
        }
        catch (Exception ex) { report["exception"] = ex.ToString(); }
        report["dimensions"] = rows.ToArray();

        File.WriteAllText(argv[0], Json.Write(report), new UTF8Encoding(false));
        Console.WriteLine("dimensions found: " + rows.Count);
        Console.WriteLine("written: " + argv[0]);
        return 0;
    }

    static void Walk(SW.IFeature f, List<object> rows)
    {
        try
        {
            if (f.GetSpecificFeature2() as SW.ISketch == null) return;
            object disp = f.GetFirstDisplayDimension();
            int guard = 0;
            while (disp != null && guard++ < 300)
            {
                try
                {
                    var dd = disp as SW.IDisplayDimension;
                    var d = dd == null ? null : dd.GetDimension2(0) as SW.IDimension;
                    if (d != null) rows.Add(Row(f.Name, d, dd));
                }
                catch { }
                try { disp = f.GetNextDisplayDimension(disp); } catch { disp = null; }
            }
        }
        catch { }
    }

    static object Row(string feature, SW.IDimension d, SW.IDisplayDimension dd)
    {
        var refs = new List<object>();
        try
        {
            dynamic ann = dd.GetAnnotation();
            object got = null;
            try { got = ann.GetAttachedEntities2(); }
            catch { try { got = ann.GetAttachedEntities(); } catch { } }
            var arr = got as Array;
            if (arr != null) foreach (var e in arr) refs.Add(Describe(e));
        }
        catch (Exception ex) { refs.Add(new { kind = "ref-error", detail = ex.Message }); }
        return new Dictionary<string, object>{
            {"feature", feature},
            {"name", S(() => d.FullName)},
            {"value_mm", Math.Round(N(() => d.SystemValue) * 1000, 4)},
            {"refs", refs.ToArray()}};
    }

    static object Describe(object e)
    {
        if (e == null) return new { kind = "null" };
        try
        {
            var pt = e as SW.ISketchPoint;
            if (pt != null)
                return new { kind = "sketchPoint",
                             xyz_mm = new[]{Math.Round(pt.X*1000,4), Math.Round(pt.Y*1000,4), Math.Round(pt.Z*1000,4)} };
        }
        catch { }
        try
        {
            var seg = e as SW.ISketchSegment;
            if (seg != null)
            {
                string kind = "sketchSegment";
                var arc = seg as SW.ISketchArc;
                if (arc != null) kind = "arc";   // IsCircle() returns int here, not bool
                var ln = seg as SW.ISketchLine;
                if (ln != null) kind = "line";
                var row = new Dictionary<string, object>{ {"kind", kind} };
                try { row["construction"] = seg.ConstructionGeometry; } catch { }
                if (ln != null)
                {
                    var p1 = (SW.ISketchPoint)ln.GetStartPoint2(); var p2 = (SW.ISketchPoint)ln.GetEndPoint2();
                    row["from_mm"] = new[]{Math.Round(p1.X*1000,4), Math.Round(p1.Y*1000,4), Math.Round(p1.Z*1000,4)};
                    row["to_mm"]   = new[]{Math.Round(p2.X*1000,4), Math.Round(p2.Y*1000,4), Math.Round(p2.Z*1000,4)};
                }
                if (arc != null)
                {
                    var c = (SW.ISketchPoint)arc.GetCenterPoint2();
                    row["radius_mm"] = Math.Round(N(() => arc.GetRadius()) * 1000, 4);
                    row["center_mm"] = new[]{Math.Round(c.X*1000,4), Math.Round(c.Y*1000,4), Math.Round(c.Z*1000,4)};
                }
                return row;
            }
        }
        catch { }
        try
        {
            var face = e as SW.IFace2;
            if (face != null)
            {
                var s = face.GetSurface() as SW.ISurface;
                string kind = "face";
                if (s != null) { if (s.IsPlane()) kind = "face:plane"; else if (s.IsCone()) kind = "face:cone";
                                 else if (s.IsCylinder()) kind = "face:cylinder"; else kind = "face:other"; }
                return new { kind = kind };
            }
        }
        catch { }
        try { var ed = e as SW.IEdge; if (ed != null) return new { kind = "edge" }; } catch { }
        return new { kind = e.GetType().ToString() };
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
