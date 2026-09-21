#!/usr/bin/env python3
"""Decisive test: does changing `c` actually move the disc's sealing face?

Copies the V7 template to a scratch folder, opens the disc there, snapshots every planar
face whose normal lies along the disc's revolve axis, sets the `c` equation 32 -> 31,
rebuilds, and snapshots again.  The scratch copy absorbs the write; the template is not
touched.  Nothing is saved.

Why this matters: in the assembly the three rings move a full 1.0000 mm when c drops by 1,
but the disc moves only 0.0333 mm.  This tells us whether the disc part itself fails to
follow - i.e. whether `D2@草图1 = "c"` is a real driver or a cosmetic one.
"""
import json
import shutil
import sys
from pathlib import Path

import pythoncom
import win32com.client as win32

MAPPING = Path(__file__).resolve().parents[1]
SOURCE = MAPPING / "working" / "assembly_batch_v7"
SCRATCH = MAPPING / "working" / "_ctest3"
OUT = MAPPING / "_analysis" / "disc_c_response.json"

AXIS_TOL = 0.999  # |normal . axis| above this counts as "perpendicular to the disc axis"


def api_array(obj):
    """SolidWorks returns COM SAFEARRAYs; normalise to a python list, 1-based indexing dropped."""
    if obj is None:
        return []
    try:
        return [obj[i] for i in range(obj.GetLowerBound(0), obj.GetUpperBound(0) + 1)]
    except Exception:  # noqa: BLE001
        return []


def planar_faces(doc):
    """Every planar face whose normal is along +/-X in part coordinates."""
    rows = []
    for body in api_array(doc.GetBodies2(0, False)):
        for face in api_array(body.GetFaces()):
            surface = face.GetSurface()
            try:
                if not surface.IsPlane():
                    continue
                params = surface.PlaneParams  # [nx,ny,nz,px,py,pz]
            except Exception:  # noqa: BLE001
                continue
            nx, ny, nz, px, py, pz = params
            if abs(nx) < AXIS_TOL:
                continue
            rows.append({
                "nx": round(nx, 6),
                "x_mm": round(px * 1000, 5),
                "area_mm2": round(abs(face.GetArea()) * 1e6, 3),
            })
    rows.sort(key=lambda r: (r["x_mm"], r["area_mm2"]))
    return rows


def cylinders(doc):
    rows = []
    for body in api_array(doc.GetBodies2(0, False)):
        for face in api_array(body.GetFaces()):
            surface = face.GetSurface()
            try:
                if not surface.IsCylinder():
                    continue
                cp = surface.CylinderParams
            except Exception:  # noqa: BLE001
                continue
            rows.append({
                "radius_mm": round(cp[6] * 1000, 4),
                "axis": [round(cp[3], 5), round(cp[4], 5), round(cp[5], 5)],
                "origin_mm": [round(cp[0] * 1000, 4), round(cp[1] * 1000, 4), round(cp[2] * 1000, 4)],
                "area_mm2": round(abs(face.GetArea()) * 1e6, 3),
            })
    rows.sort(key=lambda r: -r["area_mm2"])
    return rows


def dump(report):
    with open(OUT, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
    print("report ->", OUT)
    if report.get("error"):
        print("ERROR:", report["error"])
    for key in ("set_errors", "evaluate_all_error"):
        if report.get(key):
            print(key, "=", report[key])
    return 1


def main():
    report = {"source": str(SOURCE), "scratch": str(SCRATCH)}
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    shutil.copytree(SOURCE, SCRATCH)
    disc = next(p for p in SCRATCH.glob("*.SLDPRT") if "11蝶板" in p.name)
    report["disc"] = disc.name

    pythoncom.CoInitialize()
    sw = win32.GetActiveObject("SldWorks.Application")

    sys.path.insert(0, str(MAPPING.parent))
    import flow_transfer as ft  # noqa: E402
    api = ft.connect()
    interactive = ft.safe_get(api, "Attach2RunningObject")
    model = interactive.OpenDocument(str(disc), "")
    report["opened"] = model is not None
    doc = sw.ActiveDoc
    if doc is None or "11蝶板" not in doc.GetTitle:
        report["error"] = f"active doc is {getattr(doc, 'GetTitle', None)}"
        return dump(report)

    doc.ForceRebuild3(False)
    report["faces_c32"] = planar_faces(doc)
    report["cylinders_c32"] = cylinders(doc)

    em = doc.GetEquationMgr
    report["equations"] = []
    for i in range(em.GetCount):
        entry = {"i": i}
        try:
            entry["equation"] = em.Equation(i)
        except Exception as exc:  # noqa: BLE001
            entry["equation"] = f"ERR {exc}"
        report["equations"].append(entry)

    index = next((e["i"] for e in report["equations"] if e["equation"].startswith('"c"')), None)
    report["c_index"] = index
    if index is None:
        report["error"] = "no c equation"
        return dump(report)

    wrote = None
    for setter in ("SetEquation", "set_Equation"):
        try:
            getattr(em, setter)(index, '"c"= 31')
            wrote = setter
            break
        except Exception as exc:  # noqa: BLE001
            report.setdefault("set_errors", []).append(f"{setter}: {exc}")
    report["set_via"] = wrote
    report["c_after_set"] = em.Value(index) if wrote else None
    if wrote is None:
        return dump(report)

    try:
        em.EvaluateAll()
    except Exception as exc:  # noqa: BLE001
        report["evaluate_all_error"] = str(exc)
    report["rebuild_ok"] = bool(doc.ForceRebuild3(False))
    report["c_dim_after"] = doc.Parameter("D2@草图1").SystemValue
    report["faces_c31"] = planar_faces(doc)
    report["cylinders_c31"] = cylinders(doc)

    before = {(r["x_mm"], r["area_mm2"]) for r in report["faces_c32"]}
    after = {(r["x_mm"], r["area_mm2"]) for r in report["faces_c31"]}
    report["planar_faces_moved"] = sorted(after ^ before)

    dump(report)
    print("c equation at index", index, "-> set via", wrote)
    print("D2@草图1 after rebuild =", round(report["c_dim_after"] * 1000, 5), "mm")
    print("planar faces (normal along X) before:", len(report["faces_c32"]),
          " after:", len(report["faces_c31"]))
    print("changed entries:", len(report["planar_faces_moved"]))
    for row in report["planar_faces_moved"][:20]:
        print("   ", row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
