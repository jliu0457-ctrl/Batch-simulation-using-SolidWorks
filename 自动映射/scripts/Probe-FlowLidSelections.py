#!/usr/bin/env python3
import json
import math
import sys
from pathlib import Path

import pythoncom
import win32com.client as win32
from win32com.client import gencache

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import flow_transfer as ft


OPENINGS = [
    {"name": "inlet", "center_m": [-0.605233269437168, 0.0, 0.0], "radius_m": math.sqrt(0.0366034220337606 / math.pi), "axis": [1.0, 0.0, 0.0]},
    {"name": "outlet", "center_m": [1.10523325172216, 0.0, 0.0], "radius_m": math.sqrt(0.0366034220337606 / math.pi), "axis": [1.0, 0.0, 0.0]},
    {"name": "upper_body", "center_m": [0.0, 0.0037, 0.101233234007133], "radius_m": math.sqrt(0.000936812408260532 / math.pi), "axis": [0.0, 0.0, 1.0]},
    {"name": "lower_body", "center_m": [0.0, 0.0037, -0.237733234007133], "radius_m": math.sqrt(0.00350682446615326 / math.pi), "axis": [0.0, 0.0, 1.0]},
]


def main():
    run = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2]).resolve()
    pythoncom.CoInitialize()
    api = ft.connect()
    interactive = ft.safe_get(api, "Attach2RunningObject")
    if interactive is None:
        raise RuntimeError("Flow/SolidWorks clean instance is not running")
    assembly_path = next(run.glob("*.SLDASM"))
    model = interactive.OpenDocument(str(assembly_path), "开度45°")
    if model is None:
        raise RuntimeError("Could not open capless assembly")
    sw = win32.GetActiveObject("SldWorks.Application")
    sw_types = gencache.EnsureModule("{83A33D31-27C5-11CE-BFD4-00400513BB57}", 0, 34, 0)
    native = sw.ActiveDoc
    manager = native.SelectionManager
    selected = []
    details = []
    for opening in OPENINGS:
        candidates = []
        for radius_mm in range(5, 151, 2):
            radius = radius_mm / 1000.0
            cx, cy, cz = opening["center_m"]
            if opening["name"] == "inlet":
                point, direction = [-0.8, cy + radius, cz], [1.0, 0.0, 0.0]
            elif opening["name"] == "outlet":
                point, direction = [1.3, cy + radius, cz], [-1.0, 0.0, 0.0]
            elif opening["name"] == "upper_body":
                point, direction = [cx + radius, cy, 0.3], [0.0, 0.0, -1.0]
            else:
                point, direction = [cx + radius, cy, -0.4], [0.0, 0.0, 1.0]
            native.ClearSelection2(True)
            ok = bool(native.Extension.SelectByRay(*(point + direction + [0.0011, 2, False, 0, 0])))
            if not ok:
                continue
            component = manager.GetSelectedObjectsComponent4(1, -1)
            face = manager.GetSelectedObject6(1, -1)
            is_plane = None
            try:
                surface = face.GetSurface()
                is_plane = bool(surface.IsPlane)
            except Exception:
                pass
            row = {"probe_radius_mm": radius_mm, "component": str(component.Name2) if component is not None else None,
                   "is_plane": is_plane, "ray_point_m": point, "direction": direction}
            if row not in candidates:
                candidates.append(row)
        selected.append({**opening, "candidates": candidates})
    output.write_text(json.dumps({"assembly": str(assembly_path), "probes": selected, "selection_count": len(details), "selected": details}, ensure_ascii=False, indent=2), encoding="utf-8")
    native.ClearSelection2(True)
    interactive.CloseActiveDoc()
    api.UnloadProductAPI()
    pythoncom.CoUninitialize()


if __name__ == "__main__":
    main()
