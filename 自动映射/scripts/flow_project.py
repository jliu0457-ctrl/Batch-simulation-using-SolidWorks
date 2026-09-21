#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Flow 工程与特征的写入，每一项都配读回校验。

**重要背景：Flow 特征的「写」这条路在本项目【从未验证过】。**
`flow_transfer.set_param` / `restore_feature` 是探路产物：`set_param` 返回一个从没被解析的
字符串，所以 ``SetValue`` 返回 ``InvalidValue(4)`` 也会被当成成功。本模块修掉这一点。

官方接口（已对 `artifacts/flow_api_help/` 核准，勿凭记忆改）::

    FDAProject.CreateTemporaryFeature(nikFeaturesTypes_e) -> Features
    FDAProject.AddTemporaryFeature(feature)               -> VARIANT_BOOL
    Feature.GetInterface("IParametrizedFeature" | "IParameterGoal" | "ITopologyBasedFeature")
    ParametrizedFeature.EnumParameters() -> EnumParameters  (Reset/Next)
    Parameter.SetValue(double)        -> nikParameterManageValueErrors_e   ← 必须检查返回值
    Parameter.SetLongValue(int)       -> 同上
    Parameter.SetBoolValue(bool)      -> 同上
    ITopologyBasedFeature.RemoveAllReferencies()
    ITopologyBasedFeature.AddFaces(Unit, comp, body, use_face_name, face_name, use_color, r,g,b)
    ITopologyBasedFeature.AddComponent(Unit, component_name)
    ITopologyBasedFeature.UpdateReferenciesFromSelection(Unit) -> LONG   ← 返回引用条数
    ITopologyBasedFeature.GetReferencesNames()
    Feature.SetName(name) -> VARIANT_BOOL
    Feature.Rebuild(FDAProject) -> VARIANT_BOOL ; 失败读 FDAProject.GetLastRebuildError()
    ModelDoc.CreateProjectFromTemplate(template_path, new_project_name)
    ModelConfiguration.ActivateProject(name, vbCreateNewWindow) / RemoveProject(name)

⚠️ ``Parameter.SetValue`` 的文档明说「所有参数值一律 SI」。
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

MAPPING_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MAPPING_ROOT.parent
sys.path.insert(0, str(REPO_ROOT))

REFERENCE_PATH = MAPPING_ROOT / "config" / "flow_physics_reference.json"

#: 官方帮助里的 nikFeaturesTypes_e
FEATURE_BC = 2
FEATURE_SURFACE_GOAL = 12
FEATURE_LOCAL_MESH = 14
#: nikFeatureTopolReferenceTypes_e
FTR_TOPOLOGY = 0

#: nikParameterManageValueErrors_e —— SetValue/SetLongValue/... 的返回码
PARAM_NO_ERROR = 0
PARAM_ERROR_NAMES = {
    0: "NoError", 1: "InvalidDependencyType", 2: "ErrorUnknown",
    3: "InvalidEquation", 4: "InvalidValue",
}

#: nikGoalValueCalculateTypes_e 的合法范围。超出范围的值一律不回写 ——
#: 实测体积流量/力矩读到 16/23，那是 nikGoalParameters_e 空间的号，不是计算方式。
GOAL_CALC_VALID = range(0, 5)


class FlowWriteError(RuntimeError):
    """Flow 侧写入失败。调用方必须就此停止，不得保存。"""


# ---------------------------------------------------------------- 参照

def load_reference(path: Path = REFERENCE_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def goal_calc_to_write(observed: Any) -> int | None:
    """把读回来的 ``ValueToCalculate`` 翻译成「该不该回写」。

    只有落在 0..4（nikGoalValueCalculateTypes_e）才是真的计算方式；
    16/23 之类是另一个空间的号，**读到多少都不回写**。
    返回 None 表示「不回写」。
    """
    if observed is None:
        return None
    try:
        value = int(observed)
    except (TypeError, ValueError):
        return None
    return value if value in GOAL_CALC_VALID else None


# ---------------------------------------------------------------- 参数写入

def _enum_parameters(feature) -> list:
    pf = feature.GetInterface("IParametrizedFeature")
    if pf is None:
        raise FlowWriteError("特征不暴露 IParametrizedFeature")
    ps = pf.EnumParameters()
    if ps is None:
        raise FlowWriteError("EnumParameters() 返回空")
    out, ps = [], (ps.Reset() or ps)
    while True:
        p = ps.Next()
        if p is None:
            break
        out.append(p)
    return out


def read_parameter_value(p):
    """按参数自己的访问器读当前值。返回 (值, 用的哪个键) 或 (None, None)。"""
    for attr, key in (("GetValue", "value"), ("GetLongValue", "long"),
                      ("GetBoolValue", "bool"), ("GetStringValue", "string")):
        try:
            raw = unwrap_getter(getattr(p, attr)())
        except Exception:  # noqa: BLE001
            continue
        if raw is None:
            continue
        if isinstance(raw, bool):
            return bool(raw), key
        if isinstance(raw, str):
            return raw, key
        f = float(raw)
        return (int(f) if f == int(f) and abs(f) < 2 ** 31 else f), key
    return None, None


def _spec_value(spec: Mapping[str, Any]):
    for key in ("long", "bool", "value", "string"):
        if key in spec:
            return spec[key], key
    raise FlowWriteError(f"参数规格没有值键：{spec}")


def _values_equal(got, want) -> bool:
    if isinstance(want, bool) or isinstance(got, bool):
        return bool(got) == bool(want)
    if isinstance(want, str) or isinstance(got, str):
        return str(got) == str(want)
    try:
        return abs(float(got) - float(want)) <= 1e-9
    except (TypeError, ValueError):
        return False


def set_parameters(feature, specs: Sequence[Mapping[str, Any]], *, tolerant: bool = False) -> list[dict]:
    """把参数调成规格要求的样子，**只写需要改的那些**，并检查返回码。

    规格里的键决定用哪个 setter —— 这个约定抄自实测 dump
    （``working/seven_variable_trials/template_baseline_010/flow_rebuild_diagnostic.json``），
    消除了「3 到底是 long 还是 double」的歧义。

    ⚠️ **先读当前值，相同就跳过写入。** 新特征里大量参数已经是目标值（尤其那一片 0），
    硬写会被服务器以 `InvalidValue(4)` 拒掉 —— 实测：局部网格的 16 个参数写到
    `type=70（目标值 0）` 就炸了，而那项本来就是 0。目标是"调成这个样子"，
    不是"每个都写一遍"。
    """
    wanted = {int(s["type"]): s for s in specs}
    found, report = {}, []
    for p in _enum_parameters(feature):
        ptype = int(p.Type)
        if ptype not in wanted:
            continue
        spec = wanted[ptype]
        found[ptype] = spec
        want, key = _spec_value(spec)
        got, _ = read_parameter_value(p)
        if got is not None and _values_equal(got, want):
            report.append({"type": ptype, "rc": PARAM_NO_ERROR, "rc_name": "NoError",
                           "skipped": "already", "value": want})
            continue
        if key == "long":
            rc = p.SetLongValue(int(want))
        elif key == "bool":
            rc = p.SetBoolValue(bool(want))
        elif key == "string":
            rc = p.SetStringValue(str(want))
        else:
            rc = p.SetValue(float(want))
        entry = {"type": ptype, "rc": int(rc), "rc_name": PARAM_ERROR_NAMES.get(int(rc), "?"),
                 "previous": got, "wrote": want}
        report.append(entry)
        if int(rc) != PARAM_NO_ERROR:
            entry["failed"] = True
            if tolerant:
                # 宽容模式：记录并继续，由调用方按 essential_types 决定要不要判否。
                # 用在局部网格上 —— 那 16 个号里混着几何容差，个别写不上是正常的。
                continue
            raise FlowWriteError(
                f"参数 type={ptype} 写入失败：{entry['rc_name']}(rc={rc})，"
                f"原值={got!r} 目标={want!r}")

    missing = sorted(set(wanted) - set(found))
    if missing:
        raise FlowWriteError(
            f"这些参数号在特征里找不到，没能写入：{missing}。"
            "参数集可能随版本/条件类型变化 —— 不要假定 21 个参数恒定存在。")
    return report


def unwrap_getter(value):
    """把**参数 getter** 的返回值从「(错误码, 值)」元组里解出来 —— **错误码必须校验**。

    实测：`Parameter.GetValue/GetLongValue/GetBoolValue/GetStringValue` 返回
    `(错误码, 值)`，错误码取 `nikParameterManageValueErrors_e`：

        0 = NoError        1 = InvalidDependencyType    2 = ErrorUnknown
        3 = InvalidEquation                             4 = InvalidValue

    **访问器类型不对就是 4。** 实测 `type 67`（真值是 `GetLongValue → (0, 2)`）用
    `GetValue` 调返回 `(4, 0.0)` —— 只取最后一个元素的话会读出 `0.0`，
    一个**假的值**，而且看起来完全合法。

    ⚠️ 这条不修的话有两个真实后果：
      * `read_parameter_value` 按顺序试访问器、第一个非 None 就返回 ——
        会稳定命中**错误的那个访问器**，读回一个假 0；
      * 于是 `set_parameters` 的「已经是目标值就跳过」判断建立在假值上。

    非元组、或长度不足 2 的元组，按原样返回（那些不是参数 getter 的形状）。
    """
    if isinstance(value, tuple) and len(value) >= 2 and isinstance(value[0], int):
        if value[0] != PARAM_NO_ERROR:
            return None
        return value[-1]
    if isinstance(value, tuple) and value:
        return value[-1]
    return value


def verify_parameters(feature, specs: Sequence[Mapping[str, Any]], *, tol: float = 1e-6) -> list[dict]:
    """写完之后逐个读回，确认真的落进去了。"""
    wanted = {int(s["type"]): s for s in specs}
    report = []
    for p in _enum_parameters(feature):
        ptype = int(p.Type)
        if ptype not in wanted:
            continue
        spec = wanted[ptype]
        if "long" in spec:
            got, want = int(unwrap_getter(p.GetLongValue())), int(spec["long"])
        elif "bool" in spec:
            got, want = bool(unwrap_getter(p.GetBoolValue())), bool(spec["bool"])
        elif "string" in spec:
            got, want = str(unwrap_getter(p.GetStringValue())), str(spec["string"])
        else:
            got, want = float(unwrap_getter(p.GetValue())), float(spec["value"])
        ok = (abs(got - want) <= tol) if isinstance(want, float) else (got == want)
        report.append({"type": ptype, "want": want, "got": got, "ok": ok})
        if not ok:
            raise FlowWriteError(f"参数 type={ptype} 读回不符：写了 {want!r}，读回 {got!r}")
    return report


# ---------------------------------------------------------------- 引用绑定

def topology_of(feature):
    topo = feature.GetInterface("ITopologyBasedFeature")
    if topo is None:
        raise FlowWriteError("特征不暴露 ITopologyBasedFeature")
    return topo


def reference_names(feature) -> list[str]:
    try:
        names = topology_of(feature).GetReferencesNames()
    except Exception:  # noqa: BLE001
        return []
    if names is None:
        return []
    if isinstance(names, str):
        return [names]
    return [str(n) for n in names]


#: `nikFeatureTopolReferenceTypes_e`
FTR_FACE = 1


def bind_faces(
    feature,
    *,
    component_candidates: Sequence[str],
    face_name: str | None = None,
    model_body_name: str = "",
    select=None,
) -> dict:
    """把拓扑引用绑到面/组件上。**把所有可能的调用形式都试一遍**，回报哪个成了。

    要试的组合：
      * ``Unit``：``nikFTR_topology=0``（整个组件）还是 ``nikFTR_face=1``（组件的面）——
        官方帮助里两个都在，描述分别是「组件」和「组件的面」，用哪个取决于目标类型，
        **实测前无法判定**
      * 组件名两种写法：装配体的 ``封盖1-1`` 与 Flow 引用串的 ``封盖1<1>``
      * 名字绑定 vs 现场选中：``AddFaces(use_face_name=True, face_name=...)``
        还是先在原生 CAD 里选中面再 ``UpdateReferenciesFromSelection(Unit)``

    每一个组合都读 `GetReferencesNames()` 判成败 —— 空列表就是没成。
    一次性穷举比一次赌一个快得多：每次实机尝试都要几十秒。
    """
    topo = topology_of(feature)
    attempts: list[dict] = []

    def try_one(label: str, fn, *, needs_selection: bool) -> bool:
        topo.RemoveAllReferencies()
        entry: dict = {"attempt": label}
        if needs_selection:
            if select is None:
                attempts.append({**entry, "error": "没有选择回调，无法先选中"})
                return False
            picked = select()
            entry["selected"] = picked
            if not picked:
                attempts.append({**entry, "error": "选择回调一个面都没选中"})
                return False
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            attempts.append({**entry, "error": f"{type(exc).__name__}: {exc}"})
            return False
        names = reference_names(feature)
        attempts.append({**entry, "names": names})
        return bool(names)

    # ⚠️ **`AddFaces` 是在【当前选择】上按条件过滤，不是凭名字创建引用。**
    # 官方帮助 Remarks 原话："The component_name and model_body_name parameters are
    # used as filters and can be empty."；官方示例写的是全空过滤器
    # `AddFaces(0, "", "", False, "", True, 255, 0, 0)` —— 靠的就是用户先在界面上选中面。
    # 所以**每一次尝试之前都必须先选中**；不选就调，返回成功但引用为空（实测踩过）。
    combos: list[tuple[str, Any]] = []
    for unit in (FTR_FACE, FTR_TOPOLOGY):
        combos.append((f"选中 -> AddFaces(unit={unit}, 过滤器全空)",
                       (lambda u=unit: topo.AddFaces(u, "", model_body_name,
                                                      False, "", False, 0, 0, 0)), True))
        if face_name:
            for comp in component_candidates:
                combos.append((f"选中 -> AddFaces(unit={unit}, comp={comp!r}, face_name)",
                               (lambda u=unit, c=comp: topo.AddFaces(
                                   u, c, model_body_name, True, face_name, False, 0, 0, 0)), True))
        combos.append((f"选中 -> UpdateReferenciesFromSelection({unit})",
                       (lambda u=unit: topo.UpdateReferenciesFromSelection(u)), True))

    winner = None
    for label, fn, needs in combos:
        if try_one(label, fn, needs_selection=needs):
            winner = label
            break

    names = reference_names(feature)
    result = {"attempts": attempts, "references": names, "winner": winner,
              "component_candidates": list(component_candidates), "face_name": face_name}
    if not names:
        raise FlowWriteError(
            f"绑定失败：把 {len(attempts)} 种调用形式都试过了，引用列表仍为空 —— {attempts}")
    return result


def bind_from_selection(feature, *, select, expect: int | None = None) -> dict:
    """先在原生 CAD 里选中面，再用**当前选择**重绑引用。

    这条路专治「密封面没有稳定名字」：封盖面能用 `SetEntityName` 打名字，
    但蝶板/大垫片/密封圈上的密封锥面没有 —— 那就每次现场按几何判据选出面来，
    直接 `UpdateReferenciesFromSelection(0)`。

    返回值是引用条数（官方签名 `-> LONG`），所以能直接断言 > 0，
    比 `GetReferencesNames()` 那种字符串回读更硬。

    ``select`` 必须是个可调用对象，负责 `ClearSelection2(True)` 然后逐个 `Select4`，
    并返回选中的面数。
    """
    topo = topology_of(feature)
    topo.RemoveAllReferencies()
    selected = int(select())
    bound = int(topo.UpdateReferenciesFromSelection(FTR_TOPOLOGY) or 0)
    names = reference_names(feature)
    result = {"selected": selected, "bound": bound, "references": names,
              "expect": expect, "via": "UpdateReferenciesFromSelection"}
    if bound <= 0 or not names:
        raise FlowWriteError(
            f"按选择重绑失败：选中 {selected} 个面，绑定 {bound} 条，引用 {names}")
    if expect is not None and selected != expect:
        raise FlowWriteError(f"选中面数 {selected}，期望 {expect}")
    return result


def bind_component(feature, *, component_candidates: Sequence[str]) -> dict:
    """给力矩这类目标绑【组件】引用（不是面）。"""
    topo = topology_of(feature)
    topo.RemoveAllReferencies()
    attempts = []
    for comp in component_candidates:
        try:
            topo.AddComponent(FTR_TOPOLOGY, comp)
            names = reference_names(feature)
            attempts.append({"component": comp, "names": names})
            if names:
                break
        except Exception as exc:  # noqa: BLE001
            attempts.append({"component": comp, "error": f"{type(exc).__name__}: {exc}"})
    names = reference_names(feature)
    result = {"attempts": attempts, "references": names}
    if not names:
        raise FlowWriteError(f"组件引用绑定失败：试过 {attempts}")
    return result


# ---------------------------------------------------------------- 特征提交

def finalize_feature(project, feature, *, name: str, rebuild_first: bool = True) -> dict:
    """命名 → 校验 → 提交。

    ``rebuild_first=True`` 时先 ``Feature.Rebuild(project)`` 再 ``AddTemporaryFeature`` ——
    这是交接文档 §7.6 定的顺序：**只有 Rebuild 才真正暴露「面不在固液边界上」这类错误**，
    先提交再发现就等于往工程里塞了个坏特征。
    """
    report: dict[str, Any] = {"name": name}
    report["renamed"] = bool(feature.SetName(name))

    if rebuild_first:
        try:
            report["rebuild"] = bool(feature.Rebuild(project))
        except Exception as exc:  # noqa: BLE001
            report["rebuild"] = False
            report["rebuild_error"] = f"{type(exc).__name__}: {exc}"
        if not report["rebuild"]:
            report["last_rebuild_error"] = _last_rebuild_error(project)
            raise FlowWriteError(
                f"特征 {name!r} 重建失败：{report.get('last_rebuild_error') or report.get('rebuild_error')}")

    report["added"] = bool(project.AddTemporaryFeature(feature))

    if not rebuild_first:
        try:
            report["rebuild"] = bool(feature.Rebuild(project))
        except Exception as exc:  # noqa: BLE001
            report["rebuild"] = False
            report["rebuild_error"] = f"{type(exc).__name__}: {exc}"
    if not report["added"]:
        raise FlowWriteError(f"AddTemporaryFeature({name!r}) 返回 False")
    if not report.get("rebuild"):
        report["last_rebuild_error"] = _last_rebuild_error(project)
        raise FlowWriteError(
            f"特征 {name!r} 提交后重建失败：{report.get('last_rebuild_error') or report.get('rebuild_error')}")
    return report


def _last_rebuild_error(project) -> str:
    try:
        return str(project.GetLastRebuildError())
    except Exception as exc:  # noqa: BLE001
        return f"(读不到: {type(exc).__name__}: {exc})"


# ---------------------------------------------------------------- 高级：BC / 目标

def add_boundary(project, spec: Mapping[str, Any], *, component_candidates, face_name=None,
                 select=None) -> dict:
    feature = project.CreateTemporaryFeature(FEATURE_BC)
    if feature is None:
        raise FlowWriteError(f"CreateTemporaryFeature(2) 返回空，无法建边界条件 {spec['name']!r}")
    report = {"name": spec["name"], "role": spec.get("role")}
    report["parameters_written"] = set_parameters(feature, spec["parameters"])
    report["references"] = bind_faces(feature, component_candidates=component_candidates,
                                      face_name=face_name, select=select)
    report["parameters_readback"] = verify_parameters(feature, spec["parameters"])
    report.update(finalize_feature(project, feature, name=spec["name"]))
    return report


def add_surface_goal(project, spec: Mapping[str, Any], *, component_candidates=(),
                     face_name=None, select=None, expect_faces=None,
                     bind_component_too=None) -> dict:
    """建一个面目标。

    两种绑定方式：
      * 封盖上的目标 —— 给 `component_candidates` / `face_name`，走名字绑定
      * 密封面上的目标 —— 给 `select`（一个负责在原生 CAD 里选中面的回调），
        走 `UpdateReferenciesFromSelection(0)`，因为那些面没有稳定名字
    """
    feature = project.CreateTemporaryFeature(FEATURE_SURFACE_GOAL)
    if feature is None:
        raise FlowWriteError(f"CreateTemporaryFeature(12) 返回空，无法建目标 {spec['name']!r}")
    goal = feature.GetInterface("IParameterGoal")
    if goal is None:
        raise FlowWriteError(f"{spec['name']!r}: 特征不暴露 IParameterGoal")

    report = {"name": spec["name"], "parameter": spec["parameter"]}
    rc = goal.SetParameter(int(spec["parameter"]))
    report["set_parameter_rc"] = {"rc": int(rc), "rc_name": PARAM_ERROR_NAMES.get(int(rc), "?")}
    if int(rc) != PARAM_NO_ERROR:
        raise FlowWriteError(f"{spec['name']!r}: SetParameter({spec['parameter']}) 返回 {rc}")

    calc = goal_calc_to_write(spec.get("value_to_calculate"))
    report["value_to_calculate"] = calc
    if calc is not None:
        rc2 = goal.SetValueToCalculate(int(calc))
        report["set_value_to_calculate_rc"] = {"rc": int(rc2), "rc_name": PARAM_ERROR_NAMES.get(int(rc2), "?")}
        if int(rc2) != PARAM_NO_ERROR:
            raise FlowWriteError(f"{spec['name']!r}: SetValueToCalculate({calc}) 返回 {rc2}")
    else:
        report["value_to_calculate_skipped"] = (
            f"实测读到 {spec.get('value_to_calculate')!r}，不在 0..4 —— 那是另一个编号空间的号，不回写")

    if select is not None:
        report["references"] = bind_from_selection(feature, select=select, expect=expect_faces)
        if bind_component_too:
            # 力矩Z 除了面还要挂【组件】引用（阀轴面 + 蝶板组件）。
            # ⚠️ 判据必须是「非空」而不是「不是 None」—— 调用方对不需要组件引用的目标
            # 会传一个空列表，写成 `is not None` 会让面绑好的目标反而报
            # "组件引用没挂上"（实测踩过，一次干掉三个目标）。
            names = report["references"]["references"]
            topo = topology_of(feature)
            for comp in bind_component_too:
                topo.AddComponent(FTR_TOPOLOGY, comp)
            report["component_references"] = reference_names(feature)
            if len(report["component_references"]) <= len(names):
                raise FlowWriteError(
                    f"{spec['name']!r}: 组件引用没挂上（{report['component_references']}）")
    else:
        report["references"] = bind_faces(feature, component_candidates=component_candidates,
                                          face_name=face_name, select=select)
    report.update(finalize_feature(project, feature, name=spec["name"]))
    return report


# ---------------------------------------------------------------- 工程

def open_assembly(session, assembly: Path, configuration_name: str) -> Any:
    """打开装配体。**只开装配体，不逐个开零件**（零件会铺满窗口并卡字体对话框）。"""
    model = session.interactive.OpenDocument(str(assembly), configuration_name)
    if model is None:
        raise FlowWriteError(f"打不开装配体 {assembly}")
    document = session.interactive.ActiveDocument
    if document is None:
        raise FlowWriteError("OpenDocument 之后 ActiveDocument 仍为空")
    return document


def active_configuration(document, expected_name: str):
    configuration = document.ActiveConfiguration
    name = str(getattr(configuration, "Name", "") or "")
    if name != expected_name:
        raise FlowWriteError(f"活动配置是 {name!r}，期望 {expected_name!r}")
    return configuration


def create_project(document, configuration, fwp: Path, project_name: str) -> dict:
    """从 .fwp 模板新建工程。

    ⚠️ `internal_water.fwp` 是**二进制格式**（`\\xde\\x05\\x00\\x00` 开头），
    API 也没有「把现有工程存为模板」的方法 —— 所以「把根目录那份完整工程做成模板」
    这条路走不通，只能新建 + 从头写特征。见 plan §1.9。
    """
    fwp = Path(fwp)
    if not fwp.is_file():
        raise FlowWriteError(f"Flow 工程模板不存在：{fwp}")

    report: dict[str, Any] = {"template": str(fwp), "project_name": project_name}
    existing = [str(n) for n in (configuration.GetProjectNames() or [])]
    report["projects_before"] = existing
    if project_name in existing:
        report["removed_same_name"] = bool(configuration.RemoveProject(project_name))

    project = document.CreateProjectFromTemplate(str(fwp), project_name)
    if project is None:
        raise FlowWriteError(f"CreateProjectFromTemplate 返回空（模板 {fwp}）")
    project = configuration.ActivateProject(project_name, False)
    if project is None:
        raise FlowWriteError(f"ActivateProject({project_name!r}) 返回空")

    files = project.ProjectFiles
    directory = Path(str(getattr(files, "ProjectDirectory", "") or "")).resolve()
    report["project_directory"] = str(directory)
    report["directory_exists_now"] = directory.is_dir()
    # ⚠️ 此刻目录**通常还不存在** —— Flow 要等到写文件（UpdateConfigAndDataFiles /
    # 求解）时才真正建出来。实测：拿「目录必须存在」当门禁，会在 S6 死掉，
    # 而工程其实建成功了。这里只断言「路径归位」，存在性留给 S8 的门禁。
    report["project_directory_note"] = (
        "此刻可能还没落盘；S8 的内部域门禁读 *.xmlconfig 时才会 verify")
    for key in ("GEOMFile", "CPTFile", "FLDFile"):
        value = str(getattr(files, key, "") or "")
        if value:
            resolved = Path(value).resolve()
            if directory != resolved and directory not in resolved.parents:
                raise FlowWriteError(f"{key} 指向工程目录之外：{resolved}")
    report["ok"] = True
    return {"project": project, "report": report}


def add_local_mesh(project, spec: Mapping[str, Any], *, component_candidates: Sequence[Sequence[str]],
                   essential_types: Sequence[int] = ()) -> dict:
    """建局部网格特征（`nikLocalMesh = 14`）。

    **为什么必须有它**：没有局部网格时流体单元只有 3892 个，人工成功那次是 18048 ——
    差 4.6 倍，算出来的目标值看着接近其实是粗网格蒙的。

    ⚠️ **只写「决定细化」的那几个参数，其余留给新特征的默认值。**
    实测踩过两次：把人工那 16 个值**全部照抄**会炸 ——
      * `type=70 目标 0` → `InvalidValue`（本来就是 0，不该写）
      * `type=79 原是 0.3175（≈18.2° 的几何角度容差）目标 0` → `InvalidValue`
        那是个**实际有效的容差值**，人工特征上读到 0 并不代表这里也该是 0
    真正决定网格疏密的是细化级别与「细化所有流体单元」开关，`essential_types`
    里列的必须写成功，其余写不上只记录、不中断。
    """
    feature = project.CreateTemporaryFeature(int(spec["feature_type"]))
    if feature is None:
        raise FlowWriteError(
            f"CreateTemporaryFeature({spec['feature_type']}) 返回空，无法建局部网格")
    report: dict[str, Any] = {"name": spec["name"]}
    report["parameters_written"] = set_parameters(feature, spec["parameters"], tolerant=True)

    failures = {int(r["type"]) for r in report["parameters_written"]
                if r.get("failed") or "error" in r}
    essential = set(int(t) for t in essential_types) or set()
    missing_essential = sorted(essential & failures)
    if missing_essential:
        raise FlowWriteError(
            f"局部网格的关键参数没写进去：{missing_essential} —— "
            "网格会因此偏粗，别拿这份结果当数。详情：" +
            str([r for r in report["parameters_written"] if int(r["type"]) in missing_essential]))
    report["essential_types"] = sorted(essential)
    report["non_essential_failures"] = sorted(failures - essential)

    topo = topology_of(feature)
    topo.RemoveAllReferencies()
    picked, failures = [], []
    for candidates in component_candidates:
        before = len(reference_names(feature))
        for name in candidates:
            try:
                topo.AddComponent(FTR_TOPOLOGY, name)
            except Exception as exc:  # noqa: BLE001
                failures.append({"component": name, "error": f"{type(exc).__name__}: {exc}"})
                continue
            if len(reference_names(feature)) > before:
                picked.append(name)
                break
        else:
            failures.append({"component": list(candidates), "error": "所有写法都没绑上"})

    names = reference_names(feature)
    report["references"] = names
    report["components_picked"] = picked
    if failures:
        report["component_failures"] = failures
    if len(names) != len(component_candidates):
        raise FlowWriteError(
            f"局部网格要绑 {len(component_candidates)} 个零件，只绑上 {len(names)} 个 —— "
            f"成功 {picked}，失败 {failures}")

    report["parameters_readback"] = verify_parameters(feature, spec["parameters"])
    report.update(finalize_feature(project, feature, name=str(spec["name"])))
    return report


def internal_flow_gate(project_dir: Path) -> dict:
    """内部流动门禁：读 xmlconfig 的 `<ProblemType>`。**不用 FlowSpaceType** —— 它内外都是 0。"""
    project_dir = Path(project_dir)
    xmls = sorted(project_dir.glob("*.xmlconfig"))
    if not xmls:
        raise FlowWriteError(f"{project_dir} 下没有 *.xmlconfig —— UpdateConfigAndDataFiles 跑过了吗？")
    text = xmls[0].read_text(encoding="utf-8", errors="replace")

    def flag(tag: str):
        m = re.search(rf"<{tag} value=\"(-?\d+)\"", text)
        return int(m.group(1)) if m else None

    problem_type, flow_space = flag("ProblemType"), flag("FlowSpaceType")
    result = {"xmlconfig": str(xmls[0]), "ProblemType": problem_type,
              "FlowSpaceType": flow_space, "note": "内部流动判据是 ProblemType==1；FlowSpaceType 内外都是 0"}
    if problem_type != 1:
        raise FlowWriteError(f"工程不是内部流动：ProblemType={problem_type}（FlowSpaceType={flow_space}）")
    result["ok"] = True
    return result


def solver_log_level(project_dir: Path) -> int | None:
    """从求解器日志读实际用的网格档位。**xmlconfig 的 ResultResolution 比它小 1。**

    人工成功那次 xmlconfig 写 2、日志打 3 —— 所以 res6 ↔ ResultResolution=5。
    写脚本时别直接拿 xmlconfig 的数当分辨率。
    """
    project_dir = Path(project_dir)
    for name in ("1.stdout", "1.cpt.stdout"):
        path = project_dir / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"Result resolution:\s*(\d+)", text)
        if m:
            return int(m.group(1))
        m = re.search(r"Mesh LEVEL\s*=\s*(\d+)", text)
        if m:
            return int(m.group(1))
    return None


def apply_mesh_and_control(project, reference: Mapping[str, Any]) -> dict:
    """网格级别 + 收敛控制，设完读回。

    ⚠️ **`UseManualMaximumTravels` 必须设，而且必须在 `MaximumTravels` 之前设。**
    只设 `MaximumTravels=8` 而 `UseManualMaximumTravels` 留在默认 False 时，
    XML 里落成 `UseManualMaxDV=0` + `MaxDV=4`（Flow 自算的值）——
    设进去的 8 **被忽略**，求解停在 travel 4.003 而不是人工的 8.00589。
    更坑的是读回 `control.MaximumTravels` 仍然显示 8，**读回校验抓不到**（实测踩过）。

    ⚠️ `UseGoalsConvergence` 要**开**着（人工的 XML 是 `UseConv=1`）。
    它只是"允许提前收敛就停"，不会阻止撞 travel 上限 ——
    人工那次就是撞上限停的（`SG 力矩Z` 只到 15.7%，永远收敛不了）。
    """
    conv = reference["convergence"]
    report: dict[str, Any] = {"requested": {
        "MaximumIterations": conv["max_iterations"],
        "MaximumTravels": conv["max_travels"],
        "UseGoalsConvergence": conv["use_goals_convergence"],
        "UseManualMaximumTravels": conv.get("use_manual_maximum_travels", True),
    }}
    control = project.GetCalculationControlOptions()
    if control is None:
        raise FlowWriteError("GetCalculationControlOptions() 返回空")

    problems = []
    def _set(attr, value):
        try:
            setattr(control, attr, value)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{attr}={value} 设置失败：{type(exc).__name__}: {exc}")
            return
        try:
            got = getattr(control, attr)
        except Exception:  # noqa: BLE001
            got = "?"
        report[f"readback_{attr}"] = got
        if got != value and not (isinstance(got, float) and isinstance(value, float) and abs(got - value) < 1e-9):
            problems.append(f"{attr} 读回 {got!r}，期望 {value!r}")

    _set("UseGoalsConvergence", bool(conv["use_goals_convergence"]))
    _set("UseMaximumIterations", True)
    _set("MaximumIterations", int(conv["max_iterations"]))
    _set("UseManualMaximumTravels", bool(conv.get("use_manual_maximum_travels", True)))
    _set("UseMaximumTravels", True)
    _set("MaximumTravels", int(conv["max_travels"]))

    report["problems"] = problems
    if problems:
        raise FlowWriteError("网格/收敛设置没落全：\n  " + "\n  ".join(problems))
    report["readback_warning"] = (
        "`readback_MaximumTravels` 不足以证明生效 —— 实测它会在被忽略时照样回 8。"
        "真判据是求解前 `1.xmlconfig` 里 `UseManualMaxDV=1` 且 `MaxDV=8`。")
    report["ok"] = True
    return report


#: `1.xmlconfig` 里每个目标的收敛判据三元组。人工 = (0.5, 1, 1)，我们的默认 = (3, 0, 0)。
_CRITERIA_TAGS = ("CriteriaPercentage", "CriteriaType", "CriteriaVariableType")


def apply_goal_criteria_percentage(project_dir: Path, reference: Mapping[str, Any]) -> dict:
    """把 `1.xmlconfig` 里**每个目标**的收敛判据改成人工那一套（0.5%）。

    ⚠️ **为什么只能改序列化文件**（三条都实测过）：
      * `CalculationControlOptions` 一共只有 14 个属性，没有任何 criteria 相关项；
      * `IParameterGoal` 只有 `Parameter` / `ValueToCalculate`；
      * 目标特征的 `EnumParameters()` 返回**空** —— **人工工程里也是空的**，
        所以不是"我们建的方式不对"，是 Flow 根本没把判据暴露成参数。

    ⚠️⚠️ **实测这个改写是无效的**（`flow_baseline_004`，2026-09-21）：
    改完文件里确实是 (0.5, 1, 1)，但 `Solve2` 时 Flow **用自己的内存状态重写了整个
    xmlconfig**，求解后文件里又变回 (3, 0, 0)，`1.info.json` 里的 criteria 也不是
    0.5%×value。内存状态的来源是 FWP 模板 + API 写入，**这两条路都碰不到 criteria**。

    保留它是因为：成本极低、幂等、且万一以后版本行为变了能自动生效。
    **真正的停机保证是 `convergence.use_goals_convergence = False`** ——
    关掉"按目标收敛停机"，求解就只会撞 travel 上限（人工的停机点本来就是 travel 8）。

    改的位置严格限定在 `<ConvergenceOptions><GoalsInfo>…</GoalsInfo>` 里，
    全文件里这三个标签**只出现在那里**（人工与我们两份都实测过计数 = 目标数）。
    """
    conv = reference["convergence"]
    pct = float(conv["goal_criteria_percentage"])
    project_dir = Path(project_dir)
    path = project_dir / "1.xmlconfig"
    report: dict[str, Any] = {"path": str(path), "percentage": pct}
    if not path.is_file():
        raise FlowWriteError(f"{path} 不存在 —— 先 UpdateConfigAndDataFiles()")
    text = path.read_text(encoding="utf-8", errors="surrogateescape")

    i, j = text.find("<GoalsInfo"), text.find("</GoalsInfo>")
    if i < 0 or j < 0:
        raise FlowWriteError(f"{path} 里找不到 <GoalsInfo> 块")
    head, body, tail = text[:i], text[i:j], text[j:]

    def _rewrite(tag: str, value: str) -> tuple[int, str]:
        pattern = re.compile(rf'(<{tag} value=")[^"]*(")')
        body_new, n = pattern.subn(rf"\g<1>{value}\g<2>", body)
        return n, body_new

    before = {tag: re.findall(rf'<{tag} value="([^"]*)"', body) for tag in _CRITERIA_TAGS}
    counts = {}
    for tag, value in zip(_CRITERIA_TAGS, (f"{pct:g}", "1", "1")):
        n, body = _rewrite(tag, value)
        counts[tag] = n
    after = {tag: re.findall(rf'<{tag} value="([^"]*)"', body) for tag in _CRITERIA_TAGS}

    report.update({"before": before, "after": after, "rewritten_counts": counts})
    if not counts["CriteriaPercentage"]:
        raise FlowWriteError(f"{path} 的 GoalsInfo 里没有 CriteriaPercentage，改不了")
    if len(set(after["CriteriaPercentage"])) != 1:
        raise FlowWriteError(f"{path} 改完后 CriteriaPercentage 不唯一：{after['CriteriaPercentage']}")

    tmp = path.with_suffix(".xmlconfig.tmp")
    tmp.write_text(head + body + tail, encoding="utf-8", errors="surrogateescape")
    tmp.replace(path)
    report["ok"] = True
    report["verify_note"] = ("真判据在求解后：读 `1.info.json` 的 goals[].criteria，"
                             f"应等于 {pct} × value。对不上就是被 Flow 覆盖回去了。")
    return report


def read_info_json(project_dir: Path) -> dict:
    """读求解器写的 `1.info.json`（**求解器自己写的**，是"实际用了什么"的权威）。"""
    path = Path(project_dir) / "1.info.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception:  # noqa: BLE001
        return {}


#: `goal_criteria_percentage` 的单位是**百分数**（就是 xmlconfig 里的字面值 0.5），
#: 不是分数。人工 `1/1.info.json` 里 `criteria = 0.5% × value`
#: （204866.94744261276 × 0.005 = 1024.3347372130638 逐位吻合）。
CRITERIA_PERCENT_DIVISOR = 100.0


def verify_goal_criteria(project_dir: Path, reference: Mapping[str, Any],
                         goals_expected: Sequence[str]) -> dict:
    """反查判据有没有真的生效：`1.info.json` 里 `criteria == (pct/100) × value`。"""
    pct = float(reference["convergence"]["goal_criteria_percentage"])
    info = read_info_json(project_dir)
    rows = info.get("goals") or []
    if not rows:
        return {"ok": False, "problems": [f"{project_dir}/1.info.json 里没有 goals —— 求解没跑完？"]}
    out, problems = [], []
    seen = set()
    for entry in rows:
        g = entry.get("goal") or {}
        name = str(g.get("name", ""))
        seen.add(name)
        value, criteria = g.get("value"), g.get("criteria")
        if value is None or criteria is None:
            problems.append(f"{name}: info.json 缺 value/criteria")
            continue
        # ⚠️ 用**幅值**，不是带符号的值。人工 `1/1.info.json` 里
        # `SG 力矩Z` value=−17.682352991876606 而 criteria=+0.08841176495938304
        # = 0.5% × 17.6824 —— 带符号算会得到 −0.0884，符号对不上而误报。
        want = pct / CRITERIA_PERCENT_DIVISOR * abs(float(value))
        rel = abs(float(criteria) - want) / abs(want) if want else abs(float(criteria))
        out.append({"name": name, "value": value, "criteria": criteria,
                    "expected": want, "rel_error": rel, "ok": rel < 0.02})
        if rel >= 0.02:
            problems.append(f"{name}: criteria={criteria} 期望 {want:.6g}（{pct}%×value）")
    missing = [n for n in goals_expected if n not in seen]
    if missing:
        problems.append(f"info.json 里缺目标 {missing}")
    return {"percentage": pct, "goals": out, "problems": problems, "ok": not problems}


def solver_goal_face_counts(project_dir: Path) -> dict[str, int]:
    """从求解器日志里抠 `Surface goal 'X' is on N faces`。

    这是**网格面数**（受局部网格影响），不是 CAD 面数 —— 所以只当旁证。
    但它对"选错面"非常敏感：实测选锥面时密比压 841，人工 288。
    """
    path = Path(project_dir) / "1.stdout"
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    out: dict[str, int] = {}
    for name, n in re.findall(r"Surface goal '([^']*)' is on (\d+) faces", text):
        out[name] = int(n)
    return out


def enumerate_and_rebuild(project) -> list[dict]:
    """逐个特征重建并记录结果。**失败不抛** —— 由调用方决定怎么处置。"""
    out = []
    try:
        enum = project.EnumFeatures()
    except Exception as exc:  # noqa: BLE001
        raise FlowWriteError(f"EnumFeatures() 失败：{type(exc).__name__}: {exc}") from exc
    if enum is None:
        raise FlowWriteError("EnumFeatures() 返回空 —— 通常是有模态对话框卡着，不是 API 坏了")
    enum.Reset()
    while True:
        feature = enum.Next()
        if feature is None:
            break
        entry = {"name": str(getattr(feature, "Name", "") or "")}
        try:
            entry["type"] = int(feature.Type)
        except Exception:  # noqa: BLE001
            pass
        try:
            entry["ok"] = bool(feature.Rebuild(project))
        except Exception as exc:  # noqa: BLE001
            entry["ok"] = False
            entry["error"] = f"{type(exc).__name__}: {exc}"
        if not entry["ok"]:
            entry["last_rebuild_error"] = _last_rebuild_error(project)
        out.append(entry)
    return out


def wait_for_result(project_dir: Path, *, started_epoch: float, timeout_s: float,
                    poll_s: float = 5.0, on_poll=None) -> dict:
    """等 .fld 落定：新鲜、非空、连续两次 size+mtime 不变、求解器进程已退出。"""
    from flow_session import solver_running  # 局部导入，保持本模块可离线 import

    project_dir = Path(project_dir)
    deadline = time.time() + timeout_s
    stable, last = 0, None
    while time.time() < deadline:
        flds = [p for p in project_dir.glob("*.fld") if not p.name.startswith("r_")]
        if flds:
            fld = max(flds, key=lambda p: p.stat().st_mtime)
            st = fld.stat()
            fresh = st.st_mtime >= started_epoch - 2 and st.st_size > 0
            key = (st.st_size, st.st_mtime_ns)
            stable = stable + 1 if (fresh and key == last) else 0
            last = key
            info = {"fld": str(fld), "size": st.st_size, "fresh": fresh, "stable": stable,
                    "solver_running": solver_running()}
            if on_poll:
                on_poll(info)
            if fresh and stable >= 2 and not info["solver_running"]:
                info["ok"] = True
                return info
        time.sleep(poll_s)
    raise FlowWriteError(f"等待结果超时（{timeout_s:.0f}s）：{project_dir} 里的 .fld 没有落定")
