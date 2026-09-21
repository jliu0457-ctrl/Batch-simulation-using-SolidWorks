#!/usr/bin/env python3
"""Remove every inherited Flow Simulation project from an assembly and save it.

Usage:  python clean_flow_project.py <assembly.SLDASM>

Why: the V7 template carries a Flow project inherited from the original hand-built model.
Its boundary conditions reference faces on the four caps.  Once a dimension change moves those
caps the references stop resolving, and the next rebuild raises a MODAL dialog
("面<1>@封盖1<1> 未在固体和流体区域之间的边界上") that blocks every later COM call.  Every
parameterized run is capless and removes the project anyway, so the template is better off
without it - and a clean template means the dialog can never fire.
"""
import sys
import time

import pythoncom
import win32com.client as win32

ROOT = r"C:\Users\liujunxiang\Desktop\流体仿真2"
sys.path.insert(0, ROOT)
import flow_transfer as ft  # noqa: E402


def attach_sw(retries=20, delay=3.0):
    last = None
    for _ in range(retries):
        try:
            return win32.GetActiveObject("SldWorks.Application")
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(delay)
    raise RuntimeError(f"cannot attach to SolidWorks: {last}")


def main():
    if len(sys.argv) < 2:
        print("usage: clean_flow_project.py <assembly.SLDASM>")
        return 2
    path = sys.argv[1]

    pythoncom.CoInitialize()
    sw = attach_sw()
    for _ in range(30):
        doc = sw.ActiveDoc
        if doc is None:
            break
        sw.CloseDoc(doc.GetTitle)

    api = ft.connect()
    interactive = ft.safe_get(api, "Attach2RunningObject")
    if interactive is None:
        print("Flow API could not attach to the running SolidWorks")
        return 1
    model = interactive.OpenDocument(path, "开度45°")
    print("opened:", model is not None)
    if model is None:
        return 1

    flow_document = interactive.ActiveDocument
    configuration = flow_document.ActiveConfiguration
    before = list(configuration.GetProjectNames() or [])
    print("projects before:", before)
    removed = []
    for name in before:
        if configuration.RemoveProject(str(name)):
            removed.append(str(name))
    after = list(configuration.GetProjectNames() or [])
    print("removed:", removed)
    print("projects after:", after)

    native = sw.ActiveDoc
    if after:
        print("REFUSING to save: projects remain")
        return 1
    if native is not None:
        native.ForceRebuild3(False)
        # Save3(1, ...) is the SILENT save.  Plain Save() on an assembly raises the modal
        # "必须保存零部件文档" whenever a component is dirty, and answering it saves every open
        # document - which is how one run accidentally re-saved a whole template.  The adapter
        # needs Save() only because it edits EQUATIONS, which Save3 does not commit; removing a
        # Flow project involves no equation, so the silent call is correct here.
        errors = 0
        warnings = 0
        ok = native.Save3(1, errors, warnings)
        print("saved:", native.GetTitle, "ok=", ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
