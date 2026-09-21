#!/usr/bin/env python3
import glob
import json
import sys
from pathlib import Path

import pythoncom
import win32com.client as win32
from win32com.client import gencache

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import flow_transfer as ft


def value(obj, name):
    member = getattr(obj, name)
    return member() if callable(member) else member


def main():
    template = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2]).resolve()
    pythoncom.CoInitialize()
    api = ft.connect()
    interactive = ft.safe_get(api, "Attach2RunningObject")
    if interactive is None:
        raise RuntimeError("Flow/SolidWorks clean instance is not running")
    assembly_path = next(template.glob("*.SLDASM"))
    model = interactive.OpenDocument(str(assembly_path), "开度45°")
    if model is None:
        raise RuntimeError("Could not open V6 template")
    sw = win32.GetActiveObject("SldWorks.Application")
    sw_types = gencache.EnsureModule("{83A33D31-27C5-11CE-BFD4-00400513BB57}", 0, 34, 0)
    assembly = sw.ActiveDoc
    rows = []
    for component in list(assembly.GetComponents(False) or []):
        path = Path(str(component.GetPathName))
        if not path.stem.startswith("封盖"):
            continue
        part = sw.GetOpenDocumentByName(str(path))
        if part is None:
            continue
        for body in list(part.GetBodies2(0, False) or []):
            for raw_face in list(body.GetFaces() or []):
                face = sw_types.IFace(raw_face._oleobj_)
                surface = value(face, "GetSurface")
                box = [float(x) for x in value(face, "GetBox")]
                center = [(box[i] + box[i + 3]) / 2.0 for i in range(3)]
                item = {
                    "cap": path.stem,
                    "component": str(component.Name2),
                    "area_m2": float(value(face, "GetArea")),
                    "box_m": box,
                    "center_m": center,
                    "is_plane": bool(value(surface, "IsPlane")),
                    "entity_name": str(part.GetEntityName(face) or ""),
                }
                if item["is_plane"]:
                    item["plane_params"] = [float(x) for x in value(surface, "PlaneParams")]
                rows.append(item)
    output.write_text(json.dumps({"template": str(template), "faces": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    interactive.CloseActiveDoc()
    api.UnloadProductAPI()
    pythoncom.CoUninitialize()


if __name__ == "__main__":
    main()
