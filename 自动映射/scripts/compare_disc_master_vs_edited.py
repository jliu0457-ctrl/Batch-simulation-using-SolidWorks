#!/usr/bin/env python3
"""Compare the disc the user edited (root folder, c = 31) against the untouched V7 template
(c = 32).

Both are opened read-only through the running SolidWorks.  The point of the comparison is to
answer one question: does `c` move the disc's own geometry, and by how much?

In the assembly the three rings shift a full 1.0000 mm when c drops by 1, but the disc shifts
only 0.0333 mm.  If the disc part is byte-different but geometrically identical, then
`D2@草图1 = "c"` is a cosmetic driver and the fix lives in the sketch; if its sealing face
moved 1.0000 mm, the fix lives in the assembly mates instead.

Read-only: neither document is saved.  Equation reading is deliberately not attempted - the
late-bound IEquationMgr is unreliable here and the numbers we need come from Parameter().
"""
import json
import sys
import time
from pathlib import Path

import pythoncom
import win32com.client as win32

MAPPING = Path(__file__).resolve().parents[1]
EDITED = MAPPING.parent / "8“D94R3Y-CL600C-11蝶板.SLDPRT"
TEMPLATE = MAPPING / "working" / "assembly_batch_v7" / "8“D94R3Y-CL600C-11蝶板.SLDPRT"
OUT = MAPPING / "_analysis" / "disc_edited_vs_master.json"

AXIS_TOL = 0.999


def api_array(obj):
    """SolidWorks hands back either a SAFEARRAY or a plain tuple; normalise to a list."""
    if obj is None:
        return []
    if isinstance(obj, (tuple, list)):
        return list(obj)
    try:
        return [obj[i] for i in range(obj.GetLowerBound(0), obj.GetUpperBound(0) + 1)]
    except Exception:  # noqa: BLE001
        return []


def value(obj, name, *args):
    """Late binding is inconsistent about properties vs methods; accept either."""
    attr = getattr(obj, name)
    return attr(*args) if callable(attr) else attr


def late(obj):
    """Early binding is now cached for this typelib and several members differ there, so
    unwrap every object we walk through to a plain late-bound IDispatch view."""
    try:
        return win32.dynamic.Dispatch(obj._oleobj_)
    except Exception:  # noqa: BLE001
        return obj


def snapshot(doc, errors=None):
    errors = errors if errors is not None else []
    faces, cyls = [], []
    bodies = api_array(late(doc).GetBodies2(0, False))
    errors.append(f"bodies={len(bodies)}")
    for body in bodies:
        faces_raw = api_array(late(body).GetFaces())
        errors.append(f"faces={len(faces_raw)}")
        for face in faces_raw:
            f = late(face)
            try:
                surface = late(f.GetSurface())
                area = round(abs(f.GetArea()) * 1e6, 3)
                if surface.IsPlane():
                    nx, ny, nz, px, py, pz = surface.PlaneParams
                    if abs(nx) >= AXIS_TOL:
                        faces.append({"x_mm": round(px * 1000, 4), "nx": round(nx, 4),
                                      "area_mm2": area})
                    continue
                if surface.IsCylinder():
                    cp = surface.CylinderParams
                    cyls.append({"r_mm": round(cp[6] * 1000, 4),
                                 "axis_x": round(cp[3], 4), "area_mm2": area})
            except Exception as exc:  # noqa: BLE001
                if len(errors) < 12:
                    errors.append(f"face skipped: {exc}")
                continue
    faces.sort(key=lambda r: (r["x_mm"], r["area_mm2"]))
    cyls.sort(key=lambda r: (r["r_mm"], r["area_mm2"]))
    return faces, cyls


def attach(retries=30, delay=3.0):
    last = None
    for _ in range(retries):
        try:
            return win32.GetActiveObject("SldWorks.Application")
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(delay)
    raise RuntimeError(f"could not attach to SolidWorks: {last}")


def read(path, sw):
    result = sw.OpenDoc6(str(path), 1, 1, "", 0, 0)
    doc, errors, warnings = (result + (None, None))[:3] if isinstance(result, tuple) else (result, None, None)
    note = []
    if doc is None:
        # 65536 means the file is already open in this session - reuse that document
        already = [d for d in api_array(sw.GetDocuments())
                   if str(d.GetPathName).lower() == str(path).lower()]
        if already:
            doc = already[0]
            note.append(f"reused already-open document (OpenDoc6 said {errors})")
        else:
            return {"error": f"OpenDoc6 failed errors={errors} warnings={warnings}"}
    title = value(doc, "GetTitle")
    doc.ForceRebuild3(False)
    faces, cyls = snapshot(doc, note)
    dims = {}
    for name in ("D1@草图1", "D2@草图1", "D1@基准面2", "D1@草图2", "D2@草图2"):
        try:
            dims[name] = round(late(doc.Parameter(name)).SystemValue * 1000, 5)
        except Exception:  # noqa: BLE001
            dims[name] = None
    try:
        sw.CloseDoc(title)
    except Exception:  # noqa: BLE001
        pass
    return {"path": str(path), "title": title, "dimensions_mm": dims,
            "notes": note, "faces": faces, "cylinders": cyls}


def main():
    pythoncom.CoInitialize()
    sw = attach()
    report = {"edited": read(EDITED, sw), "template": read(TEMPLATE, sw)}

    e, t = report["edited"], report["template"]
    if "error" not in e and "error" not in t:
        ef = {(r["x_mm"], r["area_mm2"]) for r in e["faces"]}
        tf = {(r["x_mm"], r["area_mm2"]) for r in t["faces"]}
        report["faces_only_in_edited"] = sorted(ef - tf)
        report["faces_only_in_template"] = sorted(tf - ef)
        ec = {(r["r_mm"], r["area_mm2"]) for r in e["cylinders"]}
        tc = {(r["r_mm"], r["area_mm2"]) for r in t["cylinders"]}
        report["cyls_only_in_edited"] = sorted(ec - tc)
        report["cyls_only_in_template"] = sorted(tc - ec)

    with open(OUT, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)

    for label, side in (("编辑版(c 应为 31)", e), ("母版(c 应为 32)", t)):
        print(f"=== {label} ===")
        if side.get("error"):
            print("   ERROR:", side["error"])
        else:
            print("   尺寸:", side["dimensions_mm"])
            for n in side.get("notes", []):
                print("   注:", n)
            print("   面(normal 沿 X):", len(side["faces"]), "  圆柱面:", len(side["cylinders"]))

    print()
    print("只在编辑版出现的面:", len(report.get("faces_only_in_edited", [])))
    for r in report.get("faces_only_in_edited", [])[:20]:
        print("   ", r)
    print("只在母版出现的面:", len(report.get("faces_only_in_template", [])))
    for r in report.get("faces_only_in_template", [])[:20]:
        print("   ", r)
    print()
    print("只在编辑版出现的圆柱面:", len(report.get("cyls_only_in_edited", [])))
    for r in report.get("cyls_only_in_edited", [])[:20]:
        print("   ", r)
    print("只在母版出现的圆柱面:", len(report.get("cyls_only_in_template", [])))
    for r in report.get("cyls_only_in_template", [])[:20]:
        print("   ", r)
    print()
    print("report ->", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
