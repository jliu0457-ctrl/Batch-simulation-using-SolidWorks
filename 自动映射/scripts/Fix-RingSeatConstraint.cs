// Makes 09密封圈's 194.37 dimension an actual driver of the sealing cone.
//
// Why the ring does not respond to s:  D7@草图1 = 194.37 measures the distance between two
// free sketch points, (-32, -92.9393) on the centre line and (-4.1093, 99.4193).  Both are
// loose - the second one merely happens to lie ON the cut line, it is not one of its
// endpoints - so changing D7 just slides them along the lines they sit on and the trapezoid
// never moves.  The user confirmed this in the UI: setting D7 to 205 changed nothing.
//
// 03阀体 has the same construction and it works, because there (-4.1093, 99.4193) IS the
// vertex of the cut line, so D5@草图5 = 194.37 drags the line.  This tool reproduces that
// structure in the ring by making the cut line's end point COINCIDENT with the (-4.1093,
// 99.4193) point.  The line's direction is already pinned by the half-angle dimension, so the
// constraint can only translate it - which is exactly the missing behaviour.
//
// The line shortens from ~78 mm to ~52 mm.  The ring's material only spans x = -35.75 .. -28.25
// and the shortened line still spans x = -56.69 .. -4.11, so the cut still crosses the part.
//
// Read-only unless --apply is passed.  Always run without --apply first.
//
// Usage: Fix-RingSeatConstraint.exe <part.SLDPRT> <out.json> [--apply] [--save]
using System;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using SW = SolidWorks.Interop.sldworks;

public static class RingSeatConstraint
{
    // the two points, in mm, as read off 09密封圈 草图1 by Probe-DimReference
    const double P2X = -4.1093, P2Y = 99.4193;      // D7's endpoint 2 - the one that must drive the line
    const double LEX = 21.8359, LEY = 103.761;      // the cut line's near end - the one to drag onto P2
    const double LTY = 140.8601;                    // the far corner welded to it by the vertical edge
    const double LFX = -56.6948, LFY = 90.6195;     // the cut line's far end - expected to stay put
    const double TolMm = 0.05;

    static object[] A(object x) { return x as object[] ?? new object[0]; }
    static string S(Func<string> f) { try { return f(); } catch { return ""; } }
    static double N(Func<double> f) { try { return f(); } catch { return 0; } }

    public static int Main(string[] argv)
    {
        if (argv.Length < 2) { Console.Error.WriteLine("usage: Fix-RingSeatConstraint.exe <part.SLDPRT> <out.json> [--apply] [--save]"); return 2; }
        string part = argv[0], outPath = argv[1];
        bool apply = false, save = false, shorten = false, norel = false, pinLeft = false, redraw = false, split = false;
        for (int i = 2; i < argv.Length; i++) { if (argv[i] == "--apply") apply = true; if (argv[i] == "--save") save = true; if (argv[i] == "--shorten") shorten = true; if (argv[i] == "--norel") norel = true; if (argv[i] == "--pinleft") pinLeft = true; if (argv[i] == "--redraw") redraw = true; if (argv[i] == "--split") split = true; }

        object com;
        try { com = Marshal.GetActiveObject("SldWorks.Application.34"); }
        catch (Exception ex) { Console.Error.WriteLine("cannot attach (start SolidWorks and open nothing): " + ex.Message); return 3; }
        var app = (SW.ISldWorks)com;

        // Gate.  The real hazard is OpenDoc6 silently reusing an already-open document when the
        // file name matches (returns 65536) - that is how a probe ends up measuring the wrong
        // part.  A different name is safe to open alongside, so the check is name-based rather
        // than "the session must be empty": the user's own originals on the desktop stay open
        // and untouched while this works on a uniquely-named scratch copy.
        string target = Path.GetFileNameWithoutExtension(part);
        try
        {
            var n = A(app.GetDocuments());
            foreach (var o in n)
            {
                var d = o as SW.IModelDoc2; if (d == null) continue;
                string t = S(() => Path.GetFileNameWithoutExtension(d.GetPathName()));
                if (string.Equals(t, target, StringComparison.OrdinalIgnoreCase))
                { Console.Error.WriteLine("refusing: a document named '" + target + "' is already open"); return 5; }
            }
            Console.WriteLine("gate: revision=" + S(() => app.RevisionNumber()) + " visible=" + S(() => app.Visible.ToString())
                              + " other_docs=" + n.Length);
        }
        catch (Exception ex) { Console.Error.WriteLine("gate failed: " + ex.Message); return 6; }

        var r = new Dictionary<string, object>();
        r["part"] = part; r["apply"] = apply; r["save"] = save; r["shorten"] = shorten; r["norel"] = norel; r["pinleft"] = pinLeft; r["redraw"] = redraw; r["split"] = split;

        int err = 0, warn = 0;
        string title = S(() => Path.GetFileNameWithoutExtension(part));
        var doc = (SW.IModelDoc2)app.OpenDoc6(part, 1, 0, "", ref err, ref warn);
        if (doc == null) { Console.Error.WriteLine("OpenDoc6 failed err=" + err); return 7; }
        try
        {
            doc.ForceRebuild3(false);
            object[] ptsA = null, ptsB = null;
            r["before"] = Geom(doc, out ptsA);
            r["sketch_points"] = ptsA;

            // Locate the sketch by CONTENT, not by name: the feature tree is localised, so the
            // sketch is "草图1" here, not "Sketch1".  The sketch that owns the D7 endpoint is
            // the one we want, and that point is unambiguous.
            SW.ISketch swSketch = null; SW.IFeature swFeat = null;
            var names = new List<object>();
            for (var f = (SW.IFeature)doc.FirstFeature(); f != null; f = (SW.IFeature)f.GetNextFeature())
            {
                var sk = f.GetSpecificFeature2() as SW.ISketch;
                if (sk == null) continue;
                names.Add(f.Name);
                double dd; if (FindPoint(sk, P2X, P2Y, out dd) != null) { swSketch = sk; swFeat = f; break; }
            }
            r["sketch_names"] = names.ToArray();
            if (swSketch == null) { r["error"] = "no sketch owns the D7 endpoint"; }
            else
            {
                r["sketch"] = swFeat.Name;
                var hitP2 = Nearest(swSketch, P2X, P2Y);
                var hitLE = Nearest(swSketch, LEX, LEY);
                r["found_p2"] = hitP2;
                r["found_line_end"] = hitLE;

                if (hitP2 == null || hitLE == null) r["error"] = "target point(s) not found within tolerance";
                else if (!apply) r["note"] = "dry run - pass --apply to add the coincidence";
                else
                {
                    // the sketch has to be open for its points to be selectable
                    doc.ClearSelection2(true);
                    var okEdit = swFeat.Select2(false, 0);
                    doc.EditSketch();
                    r["edit_sketch"] = okEdit;

                    var sk2 = doc.SketchManager.ActiveSketch ?? swSketch;

                    // SPLIT the bushing at the 194.37 end point, so it becomes TWO segments meeting
                    // at a real vertex there - which is exactly how 03阀体 is built.  This is the
                    // structural difference that decides everything:
                    //
                    //   阀体  : the construction line's end IS a vertex of the bushing.  A vertex
                    //           cannot slide along its own line, so raising 194.37 can only be
                    //           absorbed by BOTH ends moving - measured, the growth splits 50/50
                    //           and the cone travels 47.6% of ds/2.
                    //   密封圈: the construction line's end merely LIES ON the whole bushing.  That
                    //           point slides freely, so the solver absorbs the whole increment by
                    //           translating the bushing - measured 95.2% of ds, exactly double.
                    //
                    // Splitting is the one operation not tried before (the earlier failures were
                    // add-a-relation, translate-then-relate, pin-then-relate, and delete-and-redraw).
                    if (split)
                    {
                        // SplitOpenSegment acts on the SELECTED segment ("splits the selected open
                        // sketch segment at the given point"), so the bushing has to be picked out
                        // first - calling it with an empty selection is a silent no-op.
                        SW.ISketchSegment bush = null;
                        foreach (var o in A(sk2.GetSketchSegments()))
                        {
                            var sg = o as SW.ISketchSegment; if (sg == null) continue;
                            var ln = sg as SW.ISketchLine; if (ln == null) continue;
                            var p1 = ln.GetStartPoint2() as SW.ISketchPoint;
                            var p2 = ln.GetEndPoint2() as SW.ISketchPoint;
                            if (p1 == null || p2 == null) continue;
                            bool a1 = Math.Abs(p1.X*1000-LEX) < 0.01 && Math.Abs(p1.Y*1000-LEY) < 0.01;
                            bool a2 = Math.Abs(p2.X*1000-LEX) < 0.01 && Math.Abs(p2.Y*1000-LEY) < 0.01;
                            bool b1 = Math.Abs(p1.X*1000-LFX) < 0.01 && Math.Abs(p1.Y*1000-LFY) < 0.01;
                            bool b2 = Math.Abs(p2.X*1000-LFX) < 0.01 && Math.Abs(p2.Y*1000-LFY) < 0.01;
                            if ((a1 && b2) || (a2 && b1)) { bush = sg; break; }
                        }
                        r["bushing_selected"] = bush != null && bush.Select4(false, null);
                        doc.SketchManager.SplitOpenSegment(P2X/1000.0, P2Y/1000.0, 0.0);
                        r["split_called"] = true;
                        sk2 = doc.SketchManager.ActiveSketch ?? sk2;
                        var pts = new List<object>();
                        double dz0 = 0;
                        foreach (var o in A(sk2.GetSketchPoints2()))
                        {
                            var pp = o as SW.ISketchPoint; if (pp == null) continue;
                            double dd0 = Math.Sqrt(Math.Pow(pp.X*1000-P2X,2) + Math.Pow(pp.Y*1000-P2Y,2));
                            if (dd0 < 0.05) pts.Add(new object[]{ Math.Round(pp.X*1000,4), Math.Round(pp.Y*1000,4) });
                        }
                        r["points_at_p2_after_split"] = pts.ToArray();
                    }

                    double da = 0, db = 0;
                    var a = FindPoint(sk2, P2X, P2Y, out da);
                    var b = FindPoint(sk2, LEX, LEY, out db);
                    double dx = 0, dy = 0;   // how far the near end must travel to reach the 194.37 point

                    // STEP 1 - move the geometry.  Dragging the segment's end onto the 194.37 point
                    // SHORTENS it (that point already lies on the segment), which leaves the
                    // infinite line - and therefore the revolved cone - bit-identical; the region
                    // that stops being cut is entirely above the ring's material.  Measured with
                    // --norel: box, volume and cone area all come back bit-identical.
                    if (shorten && a != null && b != null)
                    {
                        double db2 = 0;
                        var lt = FindPoint(sk2, LEX, LTY, out db2);   // corner welded to the end by the vertical edge
                        doc.ClearSelection2(true);
                        bool t1 = lt != null && lt.Select4(false, null);
                        bool t2 = b.Select4(true, null);
                        dx = P2X - b.X*1000; dy = P2Y - b.Y*1000;
                        r["translate_mm"] = new[]{ Math.Round(dx,4), Math.Round(dy,4) };
                        r["translate_selected"] = new[]{ t1, t2 };
                        doc.SketchModifyTranslate(dx/1000.0, dy/1000.0, 0.0, 0.0);
                        r["translated"] = true;
                        sk2 = doc.SketchManager.ActiveSketch ?? sk2;
                        double db3 = 0;
                        var b2 = FindPoint(sk2, P2X, P2Y, out db3);
                        r["end_now_at_p2_mm"] = b2 == null ? null : new object[]{ Math.Round(b2.X*1000,4), Math.Round(b2.Y*1000,4) };

                        // STEP 1b - put the top corner's HEIGHT back.  Translating both corners by the
                        // full vector also moved the far one down by dy, which breaks the top edge's
                        // horizontality against the corner that did not move.  The sketch is then in
                        // an INVALID state, the solver has to repair it, and the repair it picks is
                        // to slide the whole bushing along itself - which is what wrecked the part in
                        // every earlier attempt (volume 89696 -> 100674).
                        //
                        // Moving this corner in x only leaves all four edges satisfied at once:
                        // vertical 14->13, horizontal 13->12, vertical 12->11, and 11->14 still
                        // collinear with the original bushing line.  Nothing is left to repair.
                        double db4 = 0;
                        var c13 = FindPoint(sk2, LEX + dx, LTY + dy, out db4);
                        r["c13_found_mm"] = c13 == null ? null : new object[]{ Math.Round(c13.X*1000,4), Math.Round(c13.Y*1000,4) };
                        r["c13_nudge_mm"] = new[]{ 0.0, Math.Round(-dy,4) };
                        if (c13 != null && Math.Abs(dy) > 1e-6)
                        {
                            doc.ClearSelection2(true);
                            c13.Select4(false, null);
                            doc.SketchModifyTranslate(0.0, -dy/1000.0, 0.0, 0.0);
                            sk2 = doc.SketchManager.ActiveSketch ?? sk2;
                            double db5 = 0;
                            var c13b = FindPoint(sk2, P2X, LTY, out db5);
                            r["c13_final_mm"] = c13b == null ? null : new object[]{ Math.Round(c13b.X*1000,4), Math.Round(c13b.Y*1000,4) };
                        }
                    }

                    // STEP 2 - remove the slide freedom BEFORE tying the point down.  The trapezoid
                    // may travel LENGTHWISE along the cut line: the direction is pinned by the
                    // half-angle but nothing stops it sliding.  So a COINCIDENT relation is just as
                    // well satisfied by sliding the whole trapezoid, and that is the solution the
                    // solver picks - measured three times, volume 89696 -> 100674, bounding box
                    // x -35.75..-28.25 -> -41.99..-22.01.  Nudging the far corners back afterwards
                    // does not help, the next rebuild slides them again.
                    //
                    // Pinning the far corner's HEIGHT removes that freedom, because the slide has a
                    // y component of -0.165, while leaving the line free to travel perpendicular to
                    // itself - which is the motion s has to produce.  The pin must come AFTER the
                    // translate: pinning first would block the slide the translate itself needs.
                    // Its value is a constant (90.6195), so no formula is invented.
                    if (pinLeft)
                    {
                        double dAxis = 0;
                        var c11 = FindPoint(sk2, LFX, LFY, out dAxis);
                        var axis = FindAxisLine(sk2);
                        doc.ClearSelection2(true);
                        bool q1 = c11 != null && c11.Select4(false, null);
                        bool q2 = axis != null && axis.Select4(true, null);
                        r["pin_selected"] = new[]{ q1, q2 };
                        if (q1 && q2)
                        {
                            var disp = doc.AddDimension2(LFX/1000.0, (LFY - 25.0)/1000.0, 0.0);
                            var dd2 = disp as SW.IDisplayDimension;
                            var pindim = dd2 == null ? null : dd2.GetDimension2(0) as SW.IDimension;
                            r["pin_dim"] = pindim == null ? null : S(() => pindim.FullName);
                            r["pin_value_mm"] = pindim == null ? null : (object)Math.Round(N(() => pindim.SystemValue) * 1000, 4);
                        }
                        sk2 = doc.SketchManager.ActiveSketch ?? sk2;
                    }

                    sk2 = doc.SketchManager.ActiveSketch ?? sk2;

                    // REDRAW, the way the body was drawn.  Adding the coincidence to a bushing
                    // that ends far from the construction line always fails: the solver satisfies
                    // it by sliding the whole trapezoid along the bushing (measured four times,
                    // volume 89696 -> 100674), because a rigid slide is as valid as shortening and
                    // is what the solver prefers.  03阀体 never hits this - its bushing was DRAWN
                    // starting on the construction line's end, so no relation ever had to be added.
                    // So do the same here: drop the bushing and redraw it from (P2) to its far end,
                    // letting SolidWorks create the coincidence itself.
                    if (redraw)
                    {
                        double dfa = 0, dfb = 0;
                        var farEnd = FindPoint(sk2, LFX, LFY, out dfa);   // (-56.6948, 90.6195)
                        var nearEnd = FindPoint(sk2, LEX, LEY, out dfb);  // (21.8359, 103.761)
                        SW.ISketchSegment oldSeg = null;
                        foreach (var o in A(sk2.GetSketchSegments()))
                        {
                            var seg2 = o as SW.ISketchSegment; if (seg2 == null) continue;
                            var ln = seg2 as SW.ISketchLine; if (ln == null) continue;
                            var p1 = ln.GetStartPoint2() as SW.ISketchPoint;
                            var p2 = ln.GetEndPoint2() as SW.ISketchPoint;
                            if (p1 == null || p2 == null) continue;
                            bool a1 = Math.Abs(p1.X*1000-LEX) < 0.01 && Math.Abs(p1.Y*1000-LEY) < 0.01;
                            bool a2 = Math.Abs(p2.X*1000-LEX) < 0.01 && Math.Abs(p2.Y*1000-LEY) < 0.01;
                            bool b1 = Math.Abs(p1.X*1000-LFX) < 0.01 && Math.Abs(p1.Y*1000-LFY) < 0.01;
                            bool b2 = Math.Abs(p2.X*1000-LFX) < 0.01 && Math.Abs(p2.Y*1000-LFY) < 0.01;
                            if ((a1 && b2) || (a2 && b1)) { oldSeg = seg2; break; }
                        }
                        r["bushing_found"] = oldSeg != null;
                        r["far_end_found"] = farEnd != null;
                        if (oldSeg != null && farEnd != null)
                        {
                            doc.ClearSelection2(true);
                            oldSeg.Select4(false, null);
                            doc.EditDelete();               // remove the mis-drawn bushing
                            r["bushing_deleted"] = true;
                            // redraw from the EXISTING construction-line end point to the EXISTING
                            // far corner; SolidWorks merges coincident points and adds the relations
                            var nl = doc.SketchManager.CreateLine(P2X/1000.0, P2Y/1000.0, 0.0, LFX/1000.0, LFY/1000.0, 0.0);
                            r["redrawn"] = nl != null;
                            doc.SketchManager.InsertSketch(true);
                            doc.SketchManager.InsertSketch(true);   // MakeSketchActive toggle on this build
                            r["insert_sketch_called"] = true;
                        }
                        sk2 = doc.SketchManager.ActiveSketch ?? sk2;
                        doc.ForceRebuild3(false);
                        r["redraw_after"] = Geom(doc, out ptsB);
                        r["redraw_points"] = ptsB;
                    }
                    if (true) { r["note"] = redraw ? "redraw path" : "relation path"; }
                    if (norel || redraw) { r["note"] = "geometry path only, relation skipped"; }
                    SW.ISketchPoint a3, b3;
                    if (split)
                    {
                        // weld the split vertex to the construction line's end: after the split there
                        // are TWO points stacked at P2, and this is exactly the body's `type 2`
                        var both = FindPoints(sk2, P2X, P2Y);
                        r["points_at_p2"] = both.Count;
                        a3 = both.Count > 0 ? both[0] : null;
                        b3 = both.Count > 1 ? both[1] : null;
                    }
                    else
                    {
                        a3 = norel ? null : FindPoint(sk2, P2X, P2Y, out da);
                        b3 = FindPoint(sk2, LEX, LEY, out db);
                    }
                    if (a3 == null || b3 == null) { r["error"] = "points vanished after EditSketch"; }
                    else
                    {
                        doc.ClearSelection2(true);
                        bool s1 = a3.Select4(false, null);
                        bool s2 = b3.Select4(true, null);
                        r["selected"] = new[]{ s1, s2 };
                        // this overload returns void on SW2026
                        doc.SketchAddConstraints("sgCOINCIDENT");
                        r["constraint_called"] = true;

                        // The solver is free to satisfy the move by translating the WHOLE trapezoid,
                        // and it does - it drags the far side along by the same vector, which throws
                        // the cut off the material and grew the volume 89696 -> 100674.
                        //
                        // Nothing in the constraint set forces that: the far corners may stay put
                        // and the quadrilateral simply resizes.  11 does not even need to move,
                        // because it already lies on the line through the new end point.  So put the
                        // far side back by hand and let the solver settle from there - it can only
                        // adjust 12's height, which is ~45 mm clear of the material either way.
                        var f1 = FindPoint(sk2, LFX + dx, LFY + dy, out db);   // corner at the cut line's far end, where the slide left it
                        var f2 = FindPoint(sk2, LFX + dx, LTY + dy, out db);   // corner directly above it, where the slide left it
                        double bx = 0, by = 0;
                        if (f1 != null) { bx = LFX - f1.X*1000; by = LFY - f1.Y*1000; }
                        r["left_before_mm"] = f1 == null ? null : new object[]{ Math.Round(f1.X*1000,4), Math.Round(f1.Y*1000,4) };
                        r["left_nudge_mm"] = new[]{ Math.Round(bx,4), Math.Round(by,4) };
                        if (f1 != null && (Math.Abs(bx) > 1e-6 || Math.Abs(by) > 1e-6))
                        {
                            doc.ClearSelection2(true);
                            bool u1 = f1.Select4(false, null);
                            bool u2 = f2 != null && f2.Select4(true, null);
                            r["left_selected"] = new[]{ u1, u2 };
                            doc.SketchModifyTranslate(bx/1000.0, by/1000.0, 0.0, 0.0);
                            // read straight back: does the nudge even survive inside the sketch,
                            // before the exit re-solves?
                            double dz = 0; var chk = new List<object>();
                            foreach (var probe2 in new double[][]{ new double[]{LFX,LFY}, new double[]{LFX,LTY}, new double[]{P2X,P2Y} })
                            { var pz = FindPoint(sk2, probe2[0], probe2[1], out dz);
                              chk.Add(pz == null ? (object)"gone" : (object)new object[]{ Math.Round(pz.X*1000,4), Math.Round(pz.Y*1000,4) }); }
                            r["corners_after_nudge"] = chk.ToArray();
                        }
                    }
                    doc.SketchManager.InsertSketch(true);   // exit
                    doc.ClearSelection2(true);
                    doc.ForceRebuild3(false);
                    r["after"] = Geom(doc, out ptsB);
                    r["sketch_points_after"] = ptsB;

                    if (save)
                    {
                        int sErr = 0, sWarn = 0;
                        bool sv = doc.Save3(1, ref sErr, ref sWarn);
                        r["saved"] = sv; r["save_err"] = sErr;
                    }
                }
            }
        }
        catch (Exception ex) { r["exception"] = ex.ToString(); }

        try { doc.ClearSelection2(true); int e2 = 0, w2 = 0; app.CloseDoc(doc.GetTitle()); } catch { }
        File.WriteAllText(outPath, Json.Write(r), new System.Text.UTF8Encoding(false));
        Console.WriteLine("written: " + outPath);
        return 0;
    }


    // the horizontal construction line that runs out from the origin - the sketch's x axis, and
    // the only datum in this sketch a height dimension can usefully be taken from
    static SW.ISketchSegment FindAxisLine(SW.ISketch sk)
    {
        try
        {
            foreach (var o in A(sk.GetSketchSegments()))
            {
                var ln = o as SW.ISketchLine; if (ln == null) continue;
                var p1 = ln.GetStartPoint2() as SW.ISketchPoint;
                if (p1 == null) continue;
                if (Math.Abs(p1.X) < 1e-9 && Math.Abs(p1.Y) < 1e-9) return ln as SW.ISketchSegment;
            }
        }
        catch { }
        return null;
    }


    // all sketch points within TolMm of a coordinate - after a split there are TWO points sitting
    // on top of each other at that spot, and they are the pair that has to be welded together
    static List<SW.ISketchPoint> FindPoints(SW.ISketch sk, double x, double y)
    {
        var list = new List<SW.ISketchPoint>();
        try
        {
            foreach (var o in A(sk.GetSketchPoints2()))
            {
                var p = o as SW.ISketchPoint; if (p == null) continue;
                if (Math.Sqrt(Math.Pow(p.X*1000-x,2) + Math.Pow(p.Y*1000-y,2)) <= TolMm) list.Add(p);
            }
        }
        catch { }
        return list;
    }

    static SW.ISketchPoint FindPoint(SW.ISketch sk, double x, double y, out double dist)
    {
        dist = double.MaxValue;
        try
        {
            SW.ISketchPoint bp = null;
            foreach (var o in A(sk.GetSketchPoints2()))
            {
                var p = o as SW.ISketchPoint; if (p == null) continue;
                double d = Math.Sqrt((p.X*1000 - x)*(p.X*1000 - x) + (p.Y*1000 - y)*(p.Y*1000 - y));
                if (d < dist) { dist = d; bp = p; }
            }
            if (bp == null || dist > TolMm) return null;
            return bp;
        }
        catch { return null; }
    }

    static object Nearest(SW.ISketch sk, double x, double y)
    {
        try
        {
            var arr = A(sk.GetSketchPoints2());
            object best = null; double bd = double.MaxValue; SW.ISketchPoint bp = null;
            foreach (var o in arr)
            {
                var p = o as SW.ISketchPoint; if (p == null) continue;
                double d = Math.Sqrt((p.X*1000 - x)*(p.X*1000 - x) + (p.Y*1000 - y)*(p.Y*1000 - y));
                if (d < bd) { bd = d; bp = p; }
            }
            if (bp == null || bd > TolMm) return null;
            best = new Dictionary<string, object>{
                {"dist_mm", Math.Round(bd, 5)},
                {"xyz_mm", new[]{Math.Round(bp.X*1000,4), Math.Round(bp.Y*1000,4), Math.Round(bp.Z*1000,4)}}};
            return best;
        }
        catch (Exception ex) { return new Dictionary<string, object>{{"error", ex.Message}}; }
    }

    static object[] PointList(SW.ISketch sk)
    {
        var rows = new List<object>();
        try
        {
            foreach (var o in A(sk.GetSketchPoints2()))
            {
                var p = o as SW.ISketchPoint; if (p == null) continue;
                rows.Add(new object[]{ Math.Round(p.X*1000,4), Math.Round(p.Y*1000,4), Math.Round(p.Z*1000,4) });
            }
        }
        catch { }
        return rows.ToArray();
    }

    static object Geom(SW.IModelDoc2 doc, out object[] pts)
    {
        pts = new object[0];
        var d = new Dictionary<string, object>();
        try
        {
            var pd = doc as SW.IPartDoc;
            var bodies = A(pd.GetBodies2(0, false));
            if (bodies.Length > 0)
            {
                var box = ((SW.IBody2)bodies[0]).GetBodyBox() as double[];
                if (box != null) d["box_mm"] = new[]{ Math.Round(box[0]*1000,3), Math.Round(box[1]*1000,3), Math.Round(box[2]*1000,3),
                                                     Math.Round(box[3]*1000,3), Math.Round(box[4]*1000,3), Math.Round(box[5]*1000,3) };
            }
            var mp = doc.Extension.CreateMassProperty();
            if (mp != null) { try { mp.UseSystemUnits = true; } catch { } d["volume_mm3"] = Math.Round(N(() => mp.Volume) * 1e9, 3); }
            var cones = new List<object>();
            foreach (var b in bodies)
                foreach (var x in A(((SW.IBody2)b).GetFaces()))
                {
                    var f = (SW.IFace2)x; var s = f.GetSurface() as SW.ISurface;
                    if (s == null || !s.IsCone()) continue;
                    var q = (double[])s.ConeParams;
                    cones.Add(new Dictionary<string, object>{
                        {"R_mm", Math.Round(q[6]*1000,4)},
                        {"ha_deg", Math.Round(q[7]*180.0/Math.PI,4)},
                        {"pt_mm", new[]{Math.Round(q[0]*1000,3), Math.Round(q[1]*1000,3), Math.Round(q[2]*1000,3)}},
                        {"area_mm2", Math.Round(Math.Abs(f.GetArea())*1e6, 2)}});
                }
            d["cones"] = cones.ToArray();
        }
        catch (Exception ex) { d["error"] = ex.Message; }

        try
        {
            var sk = FindSketch(doc);
            if (sk != null) pts = PointList(sk);
        }
        catch { }
        return d;
    }

    static SW.ISketch FindSketch(SW.IModelDoc2 doc)
    {
        for (var f = (SW.IFeature)doc.FirstFeature(); f != null; f = (SW.IFeature)f.GetNextFeature())
        {
            var sk = f.GetSpecificFeature2() as SW.ISketch;
            if (sk == null) continue;
            double dd; if (FindPoint(sk, P2X, P2Y, out dd) != null) return sk;
        }
        return null;
    }
}

static class Json
{
    public static string Write(object o) { var sb = new System.Text.StringBuilder(); W(sb, o); return sb.ToString(); }
    static void W(System.Text.StringBuilder sb, object o)
    {
        if (o == null) { sb.Append("null"); return; }
        if (o is string) { sb.Append('"').Append(((string)o).Replace("\\", "\\\\").Replace("\"", "\\\"")
                                                          .Replace("\n", "\\n").Replace("\r", "\\r").Replace("\t", "\\t")).Append('"'); return; }
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
