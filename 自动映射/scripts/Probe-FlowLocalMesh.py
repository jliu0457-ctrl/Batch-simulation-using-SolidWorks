#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读：把根目录那个人工成功工程里的**全部特征**读出来，重点是局部网格。

为什么要读而不是猜：Flow 的两套编号空间（XML 的 `ExcelParameterType` 与公开 API 的
`IParametrizedFeature` 参数号）不是一套，从 `1.xmlconfig` 里看到
`FluidCellsRefinementLevel=3` **推不出**它在公开 API 里是哪个号。
唯一的可靠来源是**活的工程**。

用法：
    python scripts/Probe-FlowLocalMesh.py [输出.json]
    python scripts/Probe-FlowLocalMesh.py --use-open-doc [输出.json]

只读：打开装配体 → 读特征 → **不保存** → 关闭自己打开的文档 → 卸 Flow API。

`--use-open-doc`：**读用户已经打开的那个文档**，不开也不关任何东西。
用户正开着工程调参数时（门禁会拒绝普通模式），这是唯一能读数又不打扰他的方式。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MAPPING_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MAPPING_ROOT.parent
for _p in (str(REPO_ROOT), str(MAPPING_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import flow_project as fpj   # noqa: E402
import flow_session as fses  # noqa: E402
import sw_api as swa         # noqa: E402

HUMAN_ASSEMBLY = REPO_ROOT / '8“D94R3Y-CL600C-00组装图.SLDASM'
CONFIG = "开度45°"


def describe_parameters(feature, *, raw: bool = False) -> list[dict]:
    """逐个参数读：号、用哪个访问器、值。

    `raw=True` 时额外记 `_raw`：四个访问器**各自**的原始返回（含 None 和元组），
    不做任何过滤。查"某个 UI 控件到底是哪个参数号"时必须用这个模式 ——
    过滤掉 None 的版本会让读不出值的参数看起来像不存在（实测踩过：
    `CellPerGap` 就是被这样误判成"API 里没有这个参数"的）。
    """
    out = []
    for p in fpj._enum_parameters(feature):
        entry: dict = {"type": int(p.Type)}
        raws: dict = {}
        for attr, key in (("GetValue", "value"), ("GetLongValue", "long"),
                          ("GetBoolValue", "bool"), ("GetStringValue", "string")):
            try:
                got = getattr(p, attr)()
            except Exception as exc:  # noqa: BLE001
                raws[attr] = f"EXC {type(exc).__name__}"
                continue
            raws[attr] = repr(got)
            unwrapped = fpj.unwrap_getter(got)
            if unwrapped is None:
                continue
            if isinstance(unwrapped, bool):
                entry[key] = bool(unwrapped)
            elif isinstance(unwrapped, str):
                if unwrapped:
                    entry[key] = unwrapped
            else:
                f = float(unwrapped)
                entry[key] = int(f) if f == int(f) and abs(f) < 2 ** 31 else f
        if raw:
            entry["_raw"] = raws
        out.append(entry)
    return out


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    use_open = "--use-open-doc" in argv
    if use_open:
        argv.remove("--use-open-doc")
    out_path = Path(argv[0]) if argv else (MAPPING_ROOT / "_analysis" / "flow_local_mesh_probe.json")

    if not HUMAN_ASSEMBLY.is_file():
        print(f"找不到根目录的装配体：{HUMAN_ASSEMBLY}", file=sys.stderr)
        return 2

    session = fses.SwSession.attach()
    report: dict = {"assembly": str(HUMAN_ASSEMBLY), "configuration": CONFIG,
                    "mode": "use_open_doc" if use_open else "attach"}
    #: ⚠️ 只在**门禁通过、确实是我们开的文档**时才执行收尾关闭。
    #: 门禁失败（比如用户自己开着文档）时若照样关，就会把用户的文档关掉。
    opened_by_us = False
    try:
        if use_open:
            # 用户正开着工程：**只读已经打开的那个**，不开不关任何东西。
            # 不做完整门禁（那个要求 ActiveDoc is None —— 这里恰恰相反）。
            # 只做两条最低限度的安全检查。
            pids = fses.list_solidworks_pids()
            if len(pids) != 1:
                raise RuntimeError(f"SLDWORKS.exe 实例数 {len(pids)}，期望恰好 1")
            # ⚠️ 必须用 **NIK 的 ModelDoc**（`interactive.ActiveDocument`），
            # 不能用原生的 `sw.ActiveDoc` —— 原生那个的 `ActiveConfiguration` 返回的是
            # SolidWorks 自己的 `IConfiguration`，没有 `GetProjectNames`/`ActivateProject`。
            # 症状就是 `AttributeError: ActiveDoc.ActiveConfiguration`（实测踩过）。
            document = session.interactive.ActiveDocument
            if document is None:
                raise RuntimeError("没有打开任何文档 —— 这个模式要求先手工打开根目录装配体")
            title = str(swa.safe_get(document, "Name", default="") or "")
            report["active_doc_title"] = title
            if "组装图" not in title:
                raise RuntimeError(f"当前活动文档是 {title!r}，不是根目录的组装图")
        else:
            # 不需要 run 的门禁：只要没别的文档开着、模板没被动过就行
            report["gate"] = session.gate_or_raise()
            document = fpj.open_assembly(session, HUMAN_ASSEMBLY, CONFIG)
            opened_by_us = True
        configuration = fpj.active_configuration(document, CONFIG)
        report["projects"] = [str(n) for n in (configuration.GetProjectNames() or [])]
        if not report["projects"]:
            raise RuntimeError("根目录这份装配体里没有 Flow 工程")

        name = report["projects"][0]
        project = configuration.ActivateProject(name, False)
        if project is None:
            raise RuntimeError(f"激活工程 {name!r} 失败")
        report["active_project"] = name

        enum = project.EnumFeatures()
        if enum is None:
            raise RuntimeError("EnumFeatures 返回空 —— 通常是有模态对话框卡着")
        enum.Reset()
        features: list[dict] = []
        errors: list[str] = []
        while True:
            try:
                feature = enum.Next()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Next(): {type(exc).__name__}: {exc}")
                break
            if feature is None:
                break
            # 每个特征单独兜错 —— 某一种特征不支持某个接口（实测 E_NOINTERFACE）
            # 不该把整次 dump 搞死。
            entry: dict = {"index": len(features)}
            try:
                entry["name"] = str(swa.safe_get(feature, "Name", default="") or "")
            except Exception as exc:  # noqa: BLE001
                entry["name_error"] = f"{type(exc).__name__}: {exc}"
            try:
                entry["type"] = int(feature.Type)
            except Exception as exc:  # noqa: BLE001
                entry["type_error"] = f"{type(exc).__name__}: {exc}"
            try:
                topo = feature.GetInterface("ITopologyBasedFeature")
                if topo is not None:
                    entry["references"] = [str(r) for r in (topo.GetReferencesNames() or [])]
            except Exception as exc:  # noqa: BLE001
                entry["references_error"] = f"{type(exc).__name__}: {exc}"
            try:
                entry["parameters"] = describe_parameters(feature, raw=True)
            except Exception as exc:  # noqa: BLE001
                entry["parameters_error"] = f"{type(exc).__name__}: {exc}"
            try:
                goal = feature.GetInterface("IParameterGoal")
                if goal is not None:
                    entry["IParameterGoal"] = {"Parameter": int(goal.Parameter),
                                               "ValueToCalculate": int(goal.ValueToCalculate)}
            except Exception as exc:  # noqa: BLE001
                entry["goal_interface_error"] = f"{type(exc).__name__}: {exc}"
            features.append(entry)
        report["features"] = features
        report["feature_count"] = len(features)
        if errors:
            report["enumeration_errors"] = errors

        # 全局网格设置也顺手读一下
        try:
            control = project.GetCalculationControlOptions()
            report["calculation_control"] = {
                k: swa.safe_get(control, k) for k in
                ("UseGoalsConvergence", "MaximumIterations", "MaximumTravels",
                 "UseMaximumIterations", "UseMaximumTravels", "FinishConditionsRule")
            }
        except Exception as exc:  # noqa: BLE001
            report["calculation_control_error"] = f"{type(exc).__name__}: {exc}"
        try:
            report["global_mesh"] = {"MeshSettings": swa.safe_get(project.GlobalMeshSettings,
                                                                 "MeshSettings")}
        except Exception as exc:  # noqa: BLE001
            report["global_mesh_error"] = f"{type(exc).__name__}: {exc}"

    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            if opened_by_us:
                session.close_all_documents()
            else:
                report["close_skipped"] = "本轮没有打开任何文档（多半是门禁没过），不动用户的文档"
        finally:
            session.unload()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(out_path), "features": report.get("feature_count"),
                      "error": report.get("error")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
