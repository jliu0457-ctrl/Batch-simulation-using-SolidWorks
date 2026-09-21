#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Flow Simulation 项目配置的 快照 / 迁移 工具。

用途：把现有项目的「边界条件 + 目标」完整存成 JSON，等新建完项目后再灌回去。

用法：
    python flow_transfer.py dump                       # 只读，快照当前项目
    python flow_transfer.py dump --out snap.json
    python flow_transfer.py list                        # 只列出项目名
    python flow_transfer.py restore snap.json --project 流体力学仿真_内部
    python flow_transfer.py restore snap.json --project X --dry-run

⚠️ restore 是**写操作**，会往目标项目里加特征。默认会先检查同名特征并拒绝重复创建。
⚠️ 需要 SolidWorks 正在运行，且目标文档是活动文档。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import win32com.client as win32
except ImportError:
    sys.exit("需要 pywin32：pip install pywin32")

PROGID = "NIKCommonApi2.BaseApiObject"
PRODUCT = "Flow Simulation"
VERSION = "2026"

FEATURE_TYPE_BC = 2
FEATURE_TYPE_GOAL = 12


# ---------------------------------------------------------------- 连接

def connect():
    api = win32.Dispatch(PROGID)
    if not api.LoadProductAPI2(PRODUCT, VERSION):
        sys.exit(f"LoadProductAPI2({PRODUCT!r}, {VERSION!r}) 返回 False")
    return api


def find_sldworks_pid():
    import subprocess
    out = subprocess.run(["tasklist"], capture_output=True, text=True, errors="replace").stdout
    for line in out.splitlines():
        if line.lower().startswith("sldworks.exe"):
            return int(line.split()[1])
    sys.exit("没找到运行中的 SLDWORKS.exe")


def safe_get(obj, *attrs):
    """COM 对象的属性名在不同接口下不一样，挨个试。
    有些是属性（.Name），有些是方法（GetPathName()）——是方法就调用。"""
    for a in attrs:
        try:
            v = getattr(obj, a)
        except Exception:
            continue
        if v is None:
            continue
        if callable(v):
            try:
                v2 = v()
                if v2 is not None:
                    return v2
            except Exception:
                continue
        else:
            return v
    return None


def retry(fn, times=3, delay=1.0):
    """Flow API 在 SolidWorks 忙/有模态框时会短暂不响应，重试几次。"""
    import time
    for i in range(times):
        try:
            v = fn()
            if v is not None:
                return v
        except Exception:
            pass
        if i < times - 1:
            time.sleep(delay)
    return None


def active_project(api, pid):
    app = api.Attach2RunningObject2(pid)
    doc = app.ActiveDocument
    cfg = doc.ActiveConfiguration
    return doc, cfg, cfg.GetFluidDynamicAnalysisProject()


def doc_label(doc):
    """Flow API 的 ModelDoc 用 .Name；CAD API 的 IModelDoc2 用 GetTitle/GetPathName。"""
    name = safe_get(doc, "Name", "GetTitle")
    path = safe_get(doc, "PathName", "GetPathName")
    return name, path


def safe_call(obj, method, *a):
    """调 COM 方法，失败就返回 None —— 有些方法在特定状态下会抛。"""
    try:
        fn = getattr(obj, method)
    except Exception:
        return None
    try:
        return fn(*a)
    except Exception:
        return None


def project_names(cfg):
    out = safe_call(cfg, "GetProjectNames")
    return [str(x) for x in out] if out else []


# ---------------------------------------------------------------- 读取

def read_param(p):
    """尽量把参数值读出来。实测 Value / LongValue / BoolValue 三种可用。"""
    out = {"type": int(p.Type)}
    for attr, key in (("Value", "value"), ("LongValue", "long"),
                      ("BoolValue", "bool"), ("StringValue", "string")):
        try:
            v = getattr(p, attr)
        except Exception:
            continue
        if v is None:
            continue
        try:
            if isinstance(v, str):
                out[key] = v
            elif isinstance(v, bool):
                out[key] = bool(v)
            else:
                f = float(v)
                out[key] = int(f) if f == int(f) and abs(f) < 2**31 else f
        except (TypeError, ValueError):
            out[key] = str(v)
    return out


def read_goal_interface(feature):
    """目标用 IParameterGoal，不是 IParametrizedFeature。"""
    try:
        goal = feature.GetInterface("IParameterGoal")
    except Exception:
        return None
    if goal is None:
        return None
    out = {}
    for attr in ("Parameter", "ValueToCalculate"):
        try:
            v = goal.__getattr__(attr) if hasattr(goal, "__getattr__") else getattr(goal, attr)
            out[attr] = int(v)
        except Exception:
            try:
                out[attr] = int(getattr(goal, attr))
            except Exception:
                pass
    return out or None


def read_features(project):
    features = []
    enum = retry(lambda: project.EnumFeatures(), times=5, delay=1.5)
    if enum is None:
        raise SystemExit(
            "EnumFeatures() 拿不到枚举 —— Flow API 没响应。\n"
            "常见原因：SolidWorks 里有模态对话框开着（向导窗口、错误弹窗、\n"
            "或者 Flow 正在计算）。请关掉所有弹窗、等 SolidWorks 空闲后重试。")
    enum.Reset()
    while True:
        f = enum.Next()
        if f is None:
            break
        item = {"name": f.Name, "type": int(f.Type)}

        try:
            topo = f.GetInterface("ITopologyBasedFeature")
            if topo is not None:
                item["references"] = [str(x) for x in topo.GetReferencesNames()]
        except Exception as e:
            item["references_error"] = str(e)

        if item["type"] == FEATURE_TYPE_GOAL:
            gi = read_goal_interface(f)
            if gi:
                item["goal_interface"] = gi

        try:
            pf = f.GetInterface("IParametrizedFeature")
            if pf is not None:
                ps = pf.EnumParameters()
                if ps is not None:
                    ps.Reset()
                    params = []
                    while True:
                        p = ps.Next()
                        if p is None:
                            break
                        params.append(read_param(p))
                    if params:
                        item["parameters"] = params
        except Exception as e:
            item["parameters_error"] = str(e)

        features.append(item)
    return features


def cmd_dump(args):
    api = connect()
    pid = args.pid or find_sldworks_pid()
    doc, cfg, project = active_project(api, pid)
    if project is None:
        sys.exit("活动配置里没有 Flow Simulation 项目")

    name, path = doc_label(doc)
    snap = {
        "document": name,
        "document_path": path,
        "configuration": safe_get(cfg, "Name"),
        "project": project.Name,
        "projects_in_config": project_names(cfg),
        "features": read_features(project),
    }

    text = json.dumps(snap, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已写入 {args.out}")
    else:
        print(text)
    print(f"\n共 {len(snap['features'])} 个特征"
          f"（BC {sum(1 for f in snap['features'] if f['type'] == FEATURE_TYPE_BC)} 个，"
          f"目标 {sum(1 for f in snap['features'] if f['type'] == FEATURE_TYPE_GOAL)} 个）",
          file=sys.stderr)


def cmd_list(args):
    api = connect()
    pid = args.pid or find_sldworks_pid()
    doc, cfg, project = active_project(api, pid)
    name, path = doc_label(doc)
    print(f"文档: {name}")
    print(f"路径: {path}")
    print(f"配置: {safe_get(cfg, 'Name')}")
    print(f"项目: {project_names(cfg)}")
    print(f"当前活动项目: {project.Name if project else None}")


# ---------------------------------------------------------------- 写入

def set_param(feature, ptype, entry):
    pf = feature.GetInterface("IParametrizedFeature")
    if pf is None:
        return f"参数类型 {ptype}: 特征不暴露 IParametrizedFeature"
    ps = pf.EnumParameters()
    if ps is None:
        return f"参数类型 {ptype}: 无参数枚举"
    ps.Reset()
    while True:
        p = ps.Next()
        if p is None:
            break
        if int(p.Type) != ptype:
            continue
        if "long" in entry:
            return f"type {ptype} = {p.SetLongValue(int(entry['long']))} (long {entry['long']})"
        if "bool" in entry:
            return f"type {ptype} = {p.SetBoolValue(bool(entry['bool']))} (bool {entry['bool']})"
        if "string" in entry:
            return f"type {ptype} = {p.SetStringValue(str(entry['string']))} (string)"
        if "value" in entry:
            return f"type {ptype} = {p.SetValue(float(entry['value']))} (value {entry['value']})"
    return f"参数类型 {ptype}: 未找到"


def restore_feature(project, ftype, item, dry_run):
    log = [f"--- {item['name']} (type {ftype}) ---"]
    if dry_run:
        log.append("  [dry-run] 跳过实际创建")
        return log

    feature = project.CreateTemporaryFeature(ftype)
    if feature is None:
        log.append("  CreateTemporaryFeature 返回 None")
        return log

    for entry in item.get("parameters", []):
        log.append("  " + set_param(feature, entry["type"], entry))

    refs = item.get("references") or []
    if refs:
        try:
            topo = feature.GetInterface("ITopologyBasedFeature")
            for ref in refs:
                parts = ref.split("@", 1)
                if len(parts) != 2:
                    log.append(f"  面引用格式不支持: {ref}")
                    continue
                face, comp = parts[0], parts[1]
                ok = topo.AddFaces(0, comp, "", True, face, False, 0, 0, 0)
                log.append(f"  AddFaces {ref} -> {ok}")
        except Exception as e:
            log.append(f"  绑面失败: {e}")

    try:
        log.append(f"  SetName -> {feature.SetName(item['name'])}")
    except Exception as e:
        log.append(f"  SetName 失败: {e}")

    try:
        log.append(f"  AddTemporaryFeature -> {project.AddTemporaryFeature(feature)}")
    except Exception as e:
        log.append(f"  AddTemporaryFeature 失败: {e}")
    return log


def cmd_restore(args):
    snap = json.loads(Path(args.snap).read_text(encoding="utf-8"))
    api = connect()
    pid = args.pid or find_sldworks_pid()

    app = api.Attach2RunningObject2(pid)
    doc = app.ActiveDocument
    cfg = doc.ActiveConfiguration
    names = project_names(cfg)

    name, _ = doc_label(doc)
    print(f"文档: {name}")
    print(f"配置: {safe_get(cfg, 'Name')}")
    print(f"已有项目: {names}")
    if args.project not in names:
        sys.exit(f"目标项目 {args.project!r} 不在配置里。先新建好项目再跑。")

    project = cfg.ActivateProject(args.project, False)
    if project is None:
        sys.exit(f"激活项目 {args.project!r} 失败")

    existing = set()
    enum = project.EnumFeatures()
    enum.Reset()
    while True:
        f = enum.Next()
        if f is None:
            break
        existing.add(str(f.Name))
    if existing:
        print(f"目标项目已有特征: {sorted(existing)}")

    todo = [f for f in snap["features"] if f["type"] in (FEATURE_TYPE_BC, FEATURE_TYPE_GOAL)]
    print(f"\n将迁移 {len(todo)} 个特征"
          f"（BC {sum(1 for f in todo if f['type'] == FEATURE_TYPE_BC)}，"
          f"目标 {sum(1 for f in todo if f['type'] == FEATURE_TYPE_GOAL)}）")

    made = 0
    for item in todo:
        if item["name"] in existing:
            print(f"✓ 已存在，跳过: {item['name']}")
            continue
        for line in restore_feature(project, item["type"], item, args.dry_run):
            print(line)
        made += 1
    print(f"\n完成，尝试创建 {made} 个特征。")
    print("⚠️ 请在 GUI 里逐个检查：面引用、参数值、以及修复任何『重建错误』。")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Flow Simulation 配置快照 / 迁移")
    ap.add_argument("--pid", type=int, default=0, help="SLDWORKS.exe 进程号（默认自动找）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("dump", help="只读，快照当前项目")
    d.add_argument("--out", help="输出 JSON 路径；不给则打印到终端")
    d.set_defaults(func=cmd_dump)

    l = sub.add_parser("list", help="列出文档/配置/项目")
    l.set_defaults(func=cmd_list)

    r = sub.add_parser("restore", help="把快照灌进指定项目（写操作）")
    r.add_argument("snap", help="dump 出来的 JSON")
    r.add_argument("--project", required=True, help="目标项目名")
    r.add_argument("--dry-run", action="store_true", help="只演练不写入")
    r.set_defaults(func=cmd_restore)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
