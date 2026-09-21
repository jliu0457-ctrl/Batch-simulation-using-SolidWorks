#!/usr/bin/env python3
"""Create four lids with the installed Flow Simulation Create Lids command."""
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pythoncom
import win32com.client as win32

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import flow_transfer as ft

CONFIGURATION = "开度45°"
PM_OK = -2
FACE = 2

PROBES = [
    {"name": "inlet", "point_m": [-0.8, 0.110, 0.0], "direction": [1.0, 0.0, 0.0]},
    {"name": "outlet", "point_m": [1.3, 0.110, 0.0], "direction": [-1.0, 0.0, 0.0]},
    {"name": "upper_body", "point_m": [0.029, 0.0037, 0.3], "direction": [0.0, 0.0, -1.0]},
    {"name": "lower_body", "point_m": [0.029, 0.0037, -0.4], "direction": [0.0, 0.0, 1.0]},
]


def now():
    return datetime.now(timezone.utc).isoformat()


def main():
    source = Path(sys.argv[1]).resolve()
    target = Path(sys.argv[2]).resolve()
    report_path = target / "flow_lids.json"
    if target.exists():
        raise RuntimeError(f"Target already exists: {target}")
    shutil.copytree(source, target)
    report = {"started_utc": now(), "source": str(source), "target": str(target), "status": "starting", "probes": PROBES}
    pythoncom.CoInitialize()
    api = interactive = model = None
    try:
        api = ft.connect()
        interactive = ft.safe_get(api, "Attach2RunningObject")
        if interactive is None:
            raise RuntimeError("Flow/SolidWorks clean instance is not running")
        assembly_path = next(target.glob("*.SLDASM"))
        model = interactive.OpenDocument(str(assembly_path), CONFIGURATION)
        if model is None:
            raise RuntimeError("Could not open cloned capless assembly")
        sw = win32.GetActiveObject("SldWorks.Application")
        native = sw.ActiveDoc
        report["opened_assembly"] = str(native.GetPathName)
        flow_document = interactive.ActiveDocument
        configuration = flow_document.ActiveConfiguration
        existing_projects = list(configuration.GetProjectNames() or [])
        report["removed_projects"] = []
        for project_name in existing_projects:
            removed = bool(configuration.RemoveProject(str(project_name)))
            report["removed_projects"].append({"name": str(project_name), "removed": removed})
            if not removed:
                raise RuntimeError(f"Could not remove inherited Flow project: {project_name}")
        if existing_projects:
            report["save_after_project_removal"] = bool(flow_document.Save())
        before = [str(c.Name2) for c in list(native.GetComponents(False) or [])]
        native.ClearSelection2(True)
        selections = []
        for index, probe in enumerate(PROBES):
            args = probe["point_m"] + probe["direction"] + [0.001, FACE, index > 0, 0, 0]
            ok = bool(native.Extension.SelectByRay(*args))
            component = None
            if ok:
                selected_component = native.SelectionManager.GetSelectedObjectsComponent4(index + 1, -1)
                component = str(selected_component.Name2) if selected_component is not None else None
            selections.append({**probe, "selected": ok, "component": component})
        report["selections"] = selections
        report["selection_count"] = int(native.SelectionManager.GetSelectedObjectCount2(-1))
        if report["selection_count"] != 4 or not all(item["selected"] for item in selections):
            raise RuntimeError(f"Expected four selected opening faces: {selections}")
        flow_app = sw.GetAddInObject("FloWorks.App")
        if flow_app is None:
            raise RuntimeError("Flow add-in object is unavailable")
        report["create_lids_call"] = flow_app.TB_CloseHole()
        time.sleep(1.0)
        report["property_manager_ok"] = bool(sw.RunCommand(PM_OK, ""))
        time.sleep(2.0)
        report["rebuild"] = bool(native.ForceRebuild3(False))
        errors = win32.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warnings = win32.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        report["save"] = bool(native.Save3(1, errors, warnings))
        report["save_errors"] = int(errors.value)
        report["save_warnings"] = int(warnings.value)
        after = [str(c.Name2) for c in list(native.GetComponents(False) or [])]
        report["components_before"] = before
        report["components_after"] = after
        report["new_components"] = [name for name in after if name not in before]
        report["status"] = "lids_created"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["finished_utc"] = now()
        target.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if interactive is not None and model is not None:
            try: interactive.CloseActiveDoc()
            except Exception: pass
        if api is not None:
            try: api.UnloadProductAPI()
            except Exception: pass
        pythoncom.CoUninitialize()
    print(json.dumps({"status": report["status"], "report": str(report_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
