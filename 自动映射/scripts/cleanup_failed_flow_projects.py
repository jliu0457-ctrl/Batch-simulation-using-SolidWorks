"""Audit or remove the two failed Flow test projects via the documented pywin32 path."""
from __future__ import annotations

import argparse
import base64
import json
import time
import sys
from pathlib import Path

import pythoncom
import win32com.client


ROOT = Path(__file__).resolve().parents[2]
ORIGINAL = base64.b64decode("5rWB5L2T5Yqb5a2m5Lu/55yf").decode("utf-8")
TARGETS = tuple(
    base64.b64decode(value).decode("utf-8")
    for value in (
        "5rWB5L2T5Yqb5a2m5Lu/55yfX+WGhemDqOiuree7g192MQ==",
        "5rWB5L2T5Yqb5a2m5Lu/55yfX+WGhemDqOiuree7g192Mg==",
    )
)


def project_names(configuration):
    raw = configuration.GetProjectNames()
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    return [str(value) for value in raw]


def stage(message: str):
    print(f"STAGE {message}", file=sys.stderr, flush=True)


def attach_flow(pid: int):
    nca = win32com.client.Dispatch("NIKCommonApi2.BaseApiObject")
    if not nca.LoadProductAPI2("Flow Simulation", "2026"):
        raise RuntimeError("LoadProductAPI2 failed")
    for _ in range(15):
        attached = nca.Attach2RunningObject2(pid)
        if attached is not None:
            return nca, attached
        time.sleep(1)
    nca.UnloadProductAPI()
    raise RuntimeError(f"Attach2RunningObject2 could not attach to SOLIDWORKS PID {pid}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    assemblies = [path for path in ROOT.glob("*.SLDASM") if not path.name.startswith("~$")]
    if len(assemblies) != 1:
        raise RuntimeError("Expected exactly one assembly in the workspace root")

    pythoncom.CoInitialize()
    sw = model = nca = None
    try:
        stage("connect_solidworks")
        try:
            sw = win32com.client.GetActiveObject("SldWorks.Application")
            stage("solidworks_connected_existing")
        except Exception:
            sw = win32com.client.DispatchEx("SldWorks.Application.34")
            stage("solidworks_dispatched_new")
        sw.Visible = False
        model = sw.ActiveDoc
        if model is None:
            stage("open_assembly")
            model = sw.OpenDoc(str(assemblies[0]), 2)
            if model is None:
                raise RuntimeError("SolidWorks OpenDoc failed")
            stage("assembly_opened")
        else:
            stage("assembly_already_open")
        time.sleep(3)
        pid_value = sw.GetProcessID
        pid = int(pid_value() if callable(pid_value) else pid_value)
        stage(f"attach_flow_pid_{pid}")
        nca, interactive = attach_flow(pid)
        stage("flow_attached")
        document = interactive.ActiveDocument
        configuration = document.ActiveConfiguration
        stage("read_project_names")
        before = project_names(configuration)
        report = {
            "mode": "apply" if args.apply else "audit",
            "assembly": str(assemblies[0]),
            "pid": pid,
            "projects_before": before,
            "removed": [],
            "saved": False,
        }
        if ORIGINAL not in before:
            raise RuntimeError("Original Flow Simulation project is missing; refusing to save")
        if args.apply:
            if configuration.ActivateProject(ORIGINAL, False) is None:
                raise RuntimeError("Could not activate original Flow Simulation project")
            for target in TARGETS:
                if target in before:
                    if not configuration.RemoveProject(target):
                        raise RuntimeError(f"Could not remove exact failed project: {target}")
                    report["removed"].append(target)
            after = project_names(configuration)
            if ORIGINAL not in after or any(target in after for target in TARGETS):
                raise RuntimeError("Post-cleanup project verification failed; refusing to save")
            if not document.Save():
                raise RuntimeError("Flow API reported that the assembly was not saved")
            report["saved"] = True
            report["projects_after"] = after
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        if nca is not None:
            try:
                nca.UnloadProductAPI()
            except Exception:
                pass
        if model is not None:
            try:
                model.Close()
            except Exception:
                pass
        if sw is not None:
            try:
                sw.ExitApp()
            except Exception:
                pass
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    main()
