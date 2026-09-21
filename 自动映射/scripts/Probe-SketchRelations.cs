// Dumps a sketch's full constraint structure: points, segments, geometric RELATIONS and
// dimension attachments.
//
// Why the relations matter: the seal ring's 194.37 does not move its cut line while the body's
// 194.37 does, and the only way to see why is to compare what each line is tied to.  Dimensions
// alone cannot answer it - the difference lives in the relations.  Reading them is also the only
// principled way to decide what to copy into the ring, rather than inventing a construction.
//
// Read-only.  Attaches to the active document and writes nothing.
//
// Usage: Probe-SketchRelations.exe <out.json>
using System;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW = SolidWorks.Interop.sldworks;

public static class SketchRelationsProbe
{
    static object[] A(object x) { return x as object[] ?? new object[0]; }
    static string S(Func<string> f) { try { return f(); } catch { return ""; } }
    static double N(Func<double> f) { try { return f(); } catch { return 0; } }
    static object R(double v) { return Math.Round(v, 4); }
    static object P(SW.ISketchPoint p) { return new object[]{ R(p.X*1000), R(p.Y*1000), R(p.Z*1000) }; }

    public static int Main(string[] argv)
    {
        if (argv.Length < 1) { Console.Error.WriteLine("usage: Probe-SketchRelations.exe <out.json>"); return 2; }
        object com;
        try { com = Marshal.GetActiveObject("SldWorks.Application.34"); }
        catch (Exception ex) { Console.Error.WriteLine("cannot attach: " + ex.Message); return 3; }
        var app = (SW.ISldWorks)com;
        var doc = (SW.IModelDoc2)app.ActiveDoc;
        if (doc == null) { Console.Error.WriteLine("no active document"); return 4; }

        var r = new Dictionary<string, object>();
        r["title"] = S(() => doc.GetTitle());
        var sketches = new List<object>();

        try
        {
            for (var f = (SW.IFeature)doc.FirstFeature(); f != null; f = (SW.IFeature)f.GetNextFeature())
            {
                var sk = f.GetSpecificFeature2() as SW.ISketch;
                if (sk == null) continue;
                var row = new Dictionary<string, object>();
                row["name"] = f.Name;

                // dimensions, with what each one is attached to
                var dims = new List<object>();
                object disp = null;
                try { disp = f.GetFirstDisplayDimension(); } catch { }
                int guard = 0;
                while (disp != null && guard++ < 200)
                {
                    try
                    {
                        var dd = disp as SW.IDisplayDimension;
                        var d = dd == null ? null : dd.GetDimension2(0) as SW.IDimension;
                        if (d != null)
                            dims.Add(new Dictionary<string, object>{
                                {"name", S(() => d.FullName)},
                                {"value_mm", R(N(() => d.SystemValue) * 1000)},
                                {"refs", Describe((disp as SW.IDisplayDimension))}});
                    }
                    catch { }
                    try { disp = f.GetNextDisplayDimension(disp); } catch { disp = null; }
                }
                row["dimensions"] = dims.ToArray();

                // segments: a line is only meaningful here with both endpoints and its flag
                var segs = new List<object>();
                foreach (var o in A(sk.GetSketchSegments()))
                {
                    var seg = o as SW.ISketchSegment; if (seg == null) continue;
                    var e = new Dictionary<string, object>();
                    try { e["construction"] = seg.ConstructionGeometry; } catch { }
                    var ln = seg as SW.ISketchLine;
                    if (ln != null)
                    {
                        e["kind"] = "line";
                        try { e["from"] = P((SW.ISketchPoint)ln.GetStartPoint2()); } catch { }
                        try { e["to"] = P((SW.ISketchPoint)ln.GetEndPoint2()); } catch { }
                    }
                    else { e["kind"] = "other"; }
                    segs.Add(e);
                }
                row["segments"] = segs.ToArray();

                // THE payload: every geometric relation on every segment, and the entities it binds.
                // Read per-segment, not per-sketch: ISketch exposes only a RelationManager, while
                // ISketchSegment.GetRelations() is the documented enumerator.
                var rels = new List<object>();
                var seen = new List<string>();
                foreach (var o in A(sk.GetSketchSegments()))
                {
                    var seg = o as SW.ISketchSegment; if (seg == null) continue;
                    foreach (var o2 in A(seg.GetRelations()))
                    {
                        var rel = o2 as SW.ISketchRelation; if (rel == null) continue;
                        var e = new Dictionary<string, object>();
                        try { e["type"] = rel.GetRelationType(); } catch { }
                        var ents = new List<object>();
                        object raw = null;
                        try { raw = rel.GetDefinitionEntities2(); } catch { }
                        if (raw == null) { try { raw = rel.GetDefinitionEntities(); } catch { } }
                        foreach (var o3 in A(raw)) ents.Add(Ent(o3));
                        e["entities"] = ents.ToArray();
                        string key = Json.Write(e);
                        if (!seen.Contains(key)) { seen.Add(key); rels.Add(e); }
                    }
                }
                // Relations that live on a POINT rather than a segment - a horizontal relation
                // or a point-to-point coincidence pins a point and never shows up in the
                // per-segment walk.  This is where a "the point cannot move vertically" answer
                // hides, so each row records which point it belongs to.
                foreach (var o in A(sk.GetSketchPoints2()))
                {
                    var pt = o as SW.ISketchPoint; if (pt == null) continue;
                    foreach (var o2 in A(pt.GetRelations()))
                    {
                        var rel = o2 as SW.ISketchRelation; if (rel == null) continue;
                        var e = new Dictionary<string, object>();
                        try { e["type"] = rel.GetRelationType(); } catch { }
                        e["on_point"] = P(pt);
                        var ents = new List<object>();
                        object raw = null;
                        try { raw = rel.GetDefinitionEntities2(); } catch { }
                        if (raw == null) { try { raw = rel.GetDefinitionEntities(); } catch { } }
                        foreach (var o3 in A(raw)) ents.Add(Ent(o3));
                        e["entities"] = ents.ToArray();
                        string key = Json.Write(e);
                        if (!seen.Contains(key)) { seen.Add(key); rels.Add(e); }
                    }
                }
                row["relations"] = rels.ToArray();

                sketches.Add(row);
            }
        }
        catch (Exception ex) { r["exception"] = ex.ToString(); }
        r["sketches"] = sketches.ToArray();
        File.WriteAllText(argv[0], Json.Write(r), new System.Text.UTF8Encoding(false));
        Console.WriteLine("sketches: " + sketches.Count + " -> " + argv[0]);
        return 0;
    }

    static object[] Describe(SW.IDisplayDimension dd)
    {
        var refs = new List<object>();
        try
        {
            dynamic ann = dd.GetAnnotation();
            object got = null;
            try { got = ann.GetAttachedEntities2(); } catch { try { got = ann.GetAttachedEntities(); } catch { } }
            foreach (var e in A(got)) refs.Add(Ent(e));
        }
        catch { }
        return refs.ToArray();
    }

    static object Ent(object e)
    {
        if (e == null) return "null";
        try { var p = e as SW.ISketchPoint; if (p != null) return new Dictionary<string, object>{ {"pt", P(p)} }; } catch { }
        try
        {
            var seg = e as SW.ISketchSegment;
            if (seg != null)
            {
                var d = new Dictionary<string, object>();
                try { d["construction"] = seg.ConstructionGeometry; } catch { }
                var ln = seg as SW.ISketchLine;
                if (ln != null)
                {
                    d["line"] = true;
                    try { var p1 = (SW.ISketchPoint)ln.GetStartPoint2(); d["from"] = P(p1); } catch { }
                    try { var p2 = (SW.ISketchPoint)ln.GetEndPoint2(); d["to"] = P(p2); } catch { }
                }
                return d;
            }
        }
        catch { }
        try { var f = e as SW.IFace2; if (f != null) return "face"; } catch { }
        return S(() => e.GetType().ToString());
    }
}

static class Json
{
    public static string Write(object o) { var sb = new System.Text.StringBuilder(); W(sb, o); return sb.ToString(); }
    static void W(System.Text.StringBuilder sb, object o)
    {
        if (o == null) { sb.Append("null"); return; }
        if (o is string) { sb.Append('"').Append(((string)o).Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", "\\n").Replace("\r", "\\r")).Append('"'); return; }
        if (o is bool) { sb.Append(((bool)o) ? "true" : "false"); return; }
        if (o is double) { sb.Append(((double)o).ToString("R", System.Globalization.CultureInfo.InvariantCulture)); return; }
        if (o is int) { sb.Append(((int)o).ToString(System.Globalization.CultureInfo.InvariantCulture)); return; }
        if (o is System.Collections.IDictionary)
        {
            sb.Append('{'); bool first = true;
            foreach (System.Collections.DictionaryEntry e in (System.Collections.IDictionary)o)
            { if (!first) sb.Append(','); first = false; W(sb, e.Key); sb.Append(':'); W(sb, e.Value); }
            sb.Append('}'); return;
        }
        if (o is System.Collections.IEnumerable)
        {
            sb.Append('['); bool first = true;
            foreach (var e in (System.Collections.IEnumerable)o) { if (!first) sb.Append(','); first = false; W(sb, e); }
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
