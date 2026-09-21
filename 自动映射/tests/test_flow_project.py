#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flow_project 里**不需要 SolidWorks** 的部分。

重点验三件事：
  1. 参数写入会检查 SetValue 的返回码 —— 现有 flow_transfer.set_param 的缺陷就在这里，
     它返回一个从没被解析的字符串，所以 InvalidValue(4) 会被当成功。
  2. 体积流量/力矩的 ValueToCalculate 读到 16/23 时**不回写**。
  3. 内部流动门禁读的是 `<ProblemType>` 而不是 `<FlowSpaceType>`，
     而且要能在**没更新配置文件的旧工程**上正确地判否。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

MAPPING = Path(__file__).resolve().parents[1]
REPO = MAPPING.parent
sys.path.insert(0, str(MAPPING / "scripts"))
sys.path.insert(0, str(REPO))

import flow_project as fp  # noqa: E402

HUMAN_XMLCONFIG = REPO / "1" / "1.xmlconfig"
HUMAN_STDOUT = REPO / "1" / "1.stdout"
HUMAN_XMLCONFIG_EXTERNAL = REPO / "1" / "1.before_internal_training_fix.xmlconfig.bak"


# ---------------------------------------------------------------- 假 COM 对象

class FakeParameter:
    """模拟 IParameter。``reject`` 里的 type 号一写就返回 InvalidValue(4)。"""

    def __init__(self, ptype, value=0.0, long_value=0, bool_value=False):
        self.Type = ptype
        self._value = float(value)
        self._long = int(long_value)
        self._bool = bool(bool_value)
        self.writes = []

    def SetValue(self, v):
        self.writes.append(("value", v))
        rc = 4 if getattr(self, "reject", False) else 0
        if rc == 0:
            self._value = float(v)
        return rc

    def SetLongValue(self, v):
        self.writes.append(("long", v))
        rc = 4 if getattr(self, "reject", False) else 0
        if rc == 0:
            self._long = int(v)
        return rc

    def SetBoolValue(self, v):
        self.writes.append(("bool", v))
        rc = 4 if getattr(self, "reject", False) else 0
        if rc == 0:
            self._bool = bool(v)
        return rc

    def SetStringValue(self, v):
        self.writes.append(("string", v))
        return 4 if getattr(self, "reject", False) else 0

    def GetValue(self):
        return self._value

    def GetLongValue(self):
        return self._long

    def GetBoolValue(self):
        return self._bool

    def GetStringValue(self):
        return ""


class FakeEnumParameters:
    def __init__(self, params):
        self._params, self._i = params, -1

    def Reset(self):
        self._i = -1
        return self

    def Next(self):
        self._i += 1
        return self._params[self._i] if self._i < len(self._params) else None


class FakeParametrized:
    def __init__(self, params):
        self._params = params

    def EnumParameters(self):
        return FakeEnumParameters(self._params)


class FakeFeature:
    def __init__(self, params, interfaces=None):
        self._params = params
        self.Name = ""
        self.Type = 2
        self.rebuilt = []
        self.added = None
        self.fail_rebuild = False
        self._interfaces = {"IParametrizedFeature": FakeParametrized(params)}
        self._interfaces.update(interfaces or {})

    def GetInterface(self, name):
        return self._interfaces.get(name)

    def SetName(self, name):
        self.Name = name
        return True

    def Rebuild(self, project):
        self.rebuilt.append(project)
        return not self.fail_rebuild


class FakeTopology:
    """模拟 `ITopologyBasedFeature`。

    ⚠️ 刻意重现真实行为：**`AddFaces` 是在当前选择上过滤，没有选择就什么都不加**
    （官方 Remarks："component_name and model_body_name are used as filters"）。
    不这么模拟的话，测试会放过"不选就调 AddFaces"这个坑 —— 实测中它返回成功、
    引用却为空，表现成"绑定失败但看不出为什么"。
    """

    def __init__(self, accept_names=(), accept_components=None):
        self.refs = []
        self.accept_names = set(accept_names)
        self.accept_components = set(accept_components or ())
        self.removed = 0
        self.selection: list[str] = []
        self.addfaces_calls = 0
        self.update_calls = 0

    def RemoveAllReferencies(self):
        self.removed += 1
        self.refs = []

    def AddFaces(self, unit, comp, body, use_face_name, face_name, use_color, r, g, b):
        self.addfaces_calls += 1
        if not self.selection:
            return True                     # 返回成功，但不加任何引用 —— 真实行为
        if not use_face_name:
            return True                     # 过滤器全空：这里刻意不成功，好逼出按名字那条路
        # 按【组件名】判定成败 —— 这样第一种写法失败时会真的回退到第二种
        if comp in self.accept_components or face_name in self.accept_names:
            self.refs.append(f"面<1>@{comp or face_name}<1>")
        return True

    def AddComponent(self, unit, comp):
        if comp in self.accept_components:
            self.refs.append(f"{comp}<1>")
            return True
        return False

    def UpdateReferenciesFromSelection(self, unit):
        self.update_calls += 1
        if self.selection:
            self.refs = [f"面<1>@{s}<1>" for s in self.selection]
        return len(self.refs)

    def GetReferencesNames(self):
        return list(self.refs) or None


class FakeProject:
    def __init__(self, features=None):
        self.added = []
        self.rebuild_error = "(no error)"

    def AddTemporaryFeature(self, feature):
        self.added.append(feature)
        return True

    def GetLastRebuildError(self):
        return self.rebuild_error


# ---------------------------------------------------------------- 参照配置

def test_reference_config_is_wellformed():
    ref = fp.load_reference()
    assert ref["enum"]["feature_type_boundary_condition"] == 2
    assert ref["enum"]["feature_type_surface_goal"] == 12
    assert ref["enum"]["topology_reference_type"] == 0
    assert len(ref["boundaries"]) == 2
    assert len(ref["goals"]) == 7
    for name in ("SG CV入口静压", "SG CV出口静压", "SG CV入口端面体积流量",
                 "SG 蝶板法向压力", "SG 密比压 平均", "SG 密比压 最大", "SG 力矩Z"):
        assert any(g["name"] == name for g in ref["goals"]), name


def test_boundaries_differ_only_where_expected():
    """两个边界条件只差 type 41（2 vs 10）和 type 19（3 vs 0），其余逐位相同。"""
    inlet, outlet = fp.load_reference()["boundaries"]
    a = {p["type"]: p for p in inlet["parameters"]}
    b = {p["type"]: p for p in outlet["parameters"]}
    assert set(a) == set(b), "两个边界条件的参数号集合应当一致"
    differing = {t for t in a if a[t] != b[t]}
    assert differing == {41, 19}, f"只应差 41 和 19，实际差 {sorted(differing)}"
    assert a[41]["long"] == 2 and b[41]["long"] == 10
    assert a[19]["value"] == 3.0 and b[19]["value"] == 0.0


def test_every_parameter_spec_has_exactly_one_accessor():
    """每个参数规格必须恰好带一个值键 —— 这是消除 long/double 歧义的约定。"""
    ref = fp.load_reference()
    for spec in ref["boundaries"]:
        for p in spec["parameters"]:
            keys = [k for k in ("long", "bool", "value", "string") if k in p]
            assert len(keys) == 1, f"type {p['type']} 带了 {keys}"
            assert isinstance(p["type"], int)


# ---------------------------------------------------------------- 参数写入

def test_set_parameters_writes_each_with_its_own_setter():
    specs = [{"type": 41, "long": 2}, {"type": 19, "value": 3.0}, {"type": 61, "bool": True}]
    params = [FakeParameter(41, long_value=0), FakeParameter(19),
              FakeParameter(61, bool_value=False)]
    feature = FakeFeature(params)

    report = fp.set_parameters(feature, specs)
    assert [r["type"] for r in report] == [41, 19, 61]
    assert all(r["rc"] == 0 for r in report)
    assert params[0].writes == [("long", 2)]
    assert params[1].writes == [("value", 3.0)]
    assert params[2].writes == [("bool", True)]


def test_set_parameters_skips_values_that_already_match():
    """已经是目标值的参数**不写**。

    实测踩过：局部网格的 16 个参数里有大片本来就是 0，硬写会被服务器以
    `InvalidValue(4)` 拒掉（第 4 个 `type=70（目标 0）` 就炸了）。
    目标是「调成这个样子」，不是「每个都写一遍」。
    """
    specs = [{"type": 67, "long": 3}, {"type": 68, "long": 0}, {"type": 69, "long": 2}]
    params = [FakeParameter(67, long_value=0), FakeParameter(68, long_value=0),
              FakeParameter(69, long_value=0)]
    report = fp.set_parameters(FakeFeature(params), specs)

    assert params[0].writes == [("long", 3)], "需要改的要写"
    assert params[1].writes == [], "本来就是 0，不该写"
    assert params[2].writes == [("long", 2)]
    assert report[1].get("skipped") == "already"
    assert all(not r.get("error") for r in report)


def test_set_parameters_rejects_a_failed_write():
    """InvalidValue(4) 必须抛 —— 这正是 flow_transfer.set_param 漏掉的那一环。"""
    params = [FakeParameter(41)]
    params[0].reject = True
    with pytest.raises(fp.FlowWriteError, match="InvalidValue"):
        fp.set_parameters(FakeFeature(params), [{"type": 41, "long": 2}])


def test_set_parameters_reports_missing_parameter_numbers():
    """参数集随版本/条件类型变化 —— 号找不到要报错，不能静默跳过。"""
    with pytest.raises(fp.FlowWriteError, match="找不到"):
        fp.set_parameters(FakeFeature([FakeParameter(41)]),
                          [{"type": 41, "long": 2}, {"type": 999, "value": 1.0}])


@pytest.mark.parametrize("raw,expected", [
    ((0, 2), 2),                      # GetLongValue 成功
    ((0, 0.0), 0.0),                  # GetValue 成功
    ((0, "0 m"), "0 m"),              # GetStringValue 成功
    ((0, False), False),              # GetBoolValue 成功
    ((4, 0.0), None),                 # ⚠️ 访问器类型不对 —— 必须当失败，不能当 0
    ((4, ""), None),
    ((4, False), None),
    ((1, 0), None),                   # InvalidDependencyType 也算失败
    ((3, 0.0), None),                 # InvalidEquation
    (None, None),                     # 有的出参就是 None（合法返回值）
    ("plain", "plain"),               # 不是元组 → 原样返回
])
def test_unwrap_getter_checks_the_error_code(raw, expected):
    """**访问器类型不对时返回的错误码必须被校验掉。**

    实测的事故：`type 67` 真值是 `GetLongValue → (0, 2)`，但用 `GetValue` 调返回
    `(4, 0.0)`。只取元组最后一个元素就会读出 `0.0` —— 一个**看起来完全合法的假值**。
    下游 `read_parameter_value` 是按顺序试访问器、第一个非 None 就返回，
    于是会稳定命中错误的那个访问器，把「访问器用错了」读成「值就是 0」；
    `set_parameters` 的「已经是目标值就跳过」判断也就建立在假值上了。
    """
    assert fp.unwrap_getter(raw) == expected


def test_every_local_mesh_parameter_uses_the_measured_accessor():
    """局部网格 16 个参数的访问器必须和实测一致（`_analysis/local_mesh_raw.json`）。

    实测发现过 `type 70` 是 **bool** 却写成 `long`（同一类错在 76/79/81/82 上也犯过）。
    这条测试把实测对照表（config 的 `_accessor_table.verified`）钉住，
    以后谁改错访问器就红。
    """
    ref = fp.load_reference()
    verified = ref["local_mesh"]["_accessor_table"]["verified"]
    for spec in ref["local_mesh"]["parameters"]:
        ptype = spec["type"]
        want = verified.get(str(ptype))
        assert want, f"type {ptype} 不在实测对照表里"
        keys = [k for k in ("long", "bool", "value", "string") if k in spec]
        assert len(keys) == 1, f"type {ptype} 应恰好带一个访问器键，实际 {keys}"
        assert want.startswith(keys[0]), (
            f"type {ptype} 实测是 {want}，配置用了 {keys[0]} —— 访问器对不上")


def test_verify_parameters_catches_a_silent_mismatch():
    params = [FakeParameter(19, value=0.0)]
    specs = [{"type": 19, "value": 3.0}]
    fp.set_parameters(FakeFeature(params), specs)  # 写了 3.0
    params[0]._value = 1.0                          # 假装没落进去
    with pytest.raises(fp.FlowWriteError, match="读回不符"):
        fp.verify_parameters(FakeFeature(params), specs)


def test_verify_parameters_passes_on_the_real_round_trip():
    params = [FakeParameter(41, long_value=0), FakeParameter(19)]
    specs = [{"type": 41, "long": 2}, {"type": 19, "value": 3.0}]
    feature = FakeFeature(params)
    fp.set_parameters(feature, specs)
    report = fp.verify_parameters(feature, specs)
    assert all(r["ok"] for r in report)


# ---------------------------------------------------------------- 目标计算方式

@pytest.mark.parametrize("observed,expected", [
    (2, 2), (1, 1), (0, 0), (3, 3),
    (16, None), (23, None), (100, None), (None, None), ("x", None),
])
def test_goal_calc_only_written_when_in_range(observed, expected):
    """体积流量读到 16、力矩读到 23 —— 那是 nikGoalParameters_e 空间的号，**不回写**。"""
    assert fp.goal_calc_to_write(observed) == expected


def test_goal_specs_mark_the_two_anomalies():
    goals = {g["name"]: g for g in fp.load_reference()["goals"]}
    assert goals["SG CV入口端面体积流量"]["value_to_calculate"] is None
    assert goals["SG 力矩Z"]["value_to_calculate"] is None
    assert goals["SG 蝶板法向压力"]["value_to_calculate"] == 1
    assert goals["SG 密比压 平均"]["value_to_calculate"] == 2


# ---------------------------------------------------------------- 引用绑定

def _selector(topo, faces):
    """返回一个「选中这些面」的回调，模拟原生 CAD 里的 Select4。"""
    def _select():
        topo.selection.extend(faces)
        return len(faces)
    return _select


def test_bind_faces_requires_a_selection():
    """**不先选中就调 AddFaces，返回成功但引用为空** —— 这是官方 Remarks 说的
    "component_name/model_body_name 只是过滤器"。实测踩过：14 种调用形式全试过，
    引用列表仍然空，看不出原因。"""
    topo = FakeTopology(accept_names={"FLOW_INLET_INNER"})
    feature = FakeFeature([], {"ITopologyBasedFeature": topo})
    with pytest.raises(fp.FlowWriteError, match="绑定失败"):
        fp.bind_faces(feature, component_candidates=["封盖1-1"], face_name="FLOW_INLET_INNER")
    assert not topo.selection, "这条路径上没人去选中任何面 —— 正是要复现的情形"
    # 没给选择回调时会在调用 AddFaces 之前就短路，所以 addfaces_calls 是 0；
    # 关键是**报错要说明为什么**，而不是干巴巴一句「绑定失败」
    assert topo.addfaces_calls == 0


def test_bind_faces_succeeds_once_the_face_is_selected():
    topo = FakeTopology(accept_names={"FLOW_INLET_INNER"})
    topo.selection = ["封盖1-1"]
    feature = FakeFeature([], {"ITopologyBasedFeature": topo})
    result = fp.bind_faces(feature, component_candidates=["封盖1-1"],
                           face_name="FLOW_INLET_INNER",
                           select=_selector(topo, ["封盖1-1"]))
    assert result["references"]
    assert result["winner"]
    assert topo.removed >= 1, "每次尝试前都要先清空引用"


def test_bind_faces_reports_every_attempt_when_all_fail():
    """全失败时要把每次尝试都记下来 —— 否则「绑定失败」四个字毫无诊断价值。"""
    topo = FakeTopology()
    feature = FakeFeature([], {"ITopologyBasedFeature": topo})
    with pytest.raises(fp.FlowWriteError) as excinfo:
        fp.bind_faces(feature, component_candidates=["封盖1-1"], face_name="FLOW_INLET_INNER",
                      select=lambda: 0)
    message = str(excinfo.value)
    assert "种调用形式" in message
    assert "选择回调一个面都没选中" in message


def test_bind_faces_falls_back_to_the_other_component_spelling():
    """组件名两种写法（`封盖1<1>` 与 `封盖1-1`）都要试。

    第一条尝试是「过滤器全空」（官方示例的写法），它不含组件名 —— 那不构成失败，
    只要最终绑上了就行。这里断言的是**成功**与**尝试过带名字的过滤器**。
    """
    topo = FakeTopology(accept_components={"封盖1-1"})
    feature = FakeFeature([], {"ITopologyBasedFeature": topo})
    result = fp.bind_faces(feature, component_candidates=["封盖1<1>", "封盖1-1"],
                           face_name="FLOW_INLET_INNER",
                           select=_selector(topo, ["封盖1-1"]))
    assert result["references"], result["attempts"]
    assert result["winner"]
    assert any("封盖1-1" in a["attempt"] for a in result["attempts"]), \
        "带组件名的过滤器应当被尝试过"


# ---------------------------------------------------------------- 特征提交

def test_finalize_rebuilds_before_committing():
    """Rebuild 必须在 AddTemporaryFeature 之前 —— 坏特征不许进工程。"""
    project = FakeProject()
    feature = FakeFeature([])
    feature.fail_rebuild = True
    with pytest.raises(fp.FlowWriteError, match="重建失败"):
        fp.finalize_feature(project, feature, name="SG 测试")
    assert project.added == [], "重建失败的必须先拦下，不能提交"


def test_finalize_commits_a_good_feature():
    project = FakeProject()
    feature = FakeFeature([])
    report = fp.finalize_feature(project, feature, name="SG 测试")
    assert report["added"] and report["rebuild"]
    assert project.added == [feature]
    assert feature.Name == "SG 测试"


def test_finalize_reports_the_cad_rebuild_error_text():
    project = FakeProject()
    project.rebuild_error = "面<1>@封盖1<1> 未在固体和流体区域之间的边界上"
    feature = FakeFeature([])
    feature.fail_rebuild = True
    with pytest.raises(fp.FlowWriteError, match="未在固体和流体区域之间的边界上"):
        fp.finalize_feature(project, feature, name="入口速度 2")


# ---------------------------------------------------------------- 内部流动门禁

def test_internal_flow_gate_reads_problem_type_not_flow_space_type():
    gate = fp.internal_flow_gate(HUMAN_XMLCONFIG.parent)
    assert gate["ProblemType"] == 1, "人工成功那次是内部流动"
    assert gate["FlowSpaceType"] == 0, "FlowSpaceType 内外都是 0，不能当判据"


def test_internal_flow_gate_rejects_the_external_run():
    """`1.before_internal_training_fix.xmlconfig.bak` 是失封那次（外部流动），必须判否。"""
    if not HUMAN_XMLCONFIG_EXTERNAL.is_file():
        pytest.skip("缺少外部流动的对照 xmlconfig")
    import shutil
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "1.xmlconfig"
        shutil.copy2(HUMAN_XMLCONFIG_EXTERNAL, target)
        with pytest.raises(fp.FlowWriteError, match="不是内部流动"):
            fp.internal_flow_gate(td)


def test_solver_log_level_is_one_more_than_xmlconfig():
    """**验证那个"差 1"的关系式，不验证人工此刻是第几档。**

    ⚠️ 人工会调分辨率（实测从 res3 的 `ResultResolution=2` 改到 res1 的 `=0`），
    把 `== 3` / `== 2` 写死就会跟着他一起变红。真正要守住的是
    **「日志档位 = xmlconfig 的 ResultResolution + 1」** 这条换算 ——
    拿 xmlconfig 的数当分辨率就会差一档。
    """
    level = fp.solver_log_level(HUMAN_XMLCONFIG.parent)
    text = HUMAN_XMLCONFIG.read_text(encoding="utf-8", errors="replace")
    xml_rr = int(re.search(r'<ResultResolution value="(\d+)"', text).group(1))
    assert isinstance(level, int) and level >= 1, f"日志档位没解析出来：{level}"
    assert level == xml_rr + 1, (
        f"日志打 res{level}，xmlconfig 是 {xml_rr} —— 换算关系变了，"
        "`solver_log_level` 的说明要跟着改")


def test_reference_is_internally_consistent():
    """**验证 config 自己自洽，不拿根目录当基准。**

    ⚠️ 根目录 `1/` 是活跃实验台：实测人工改过 `res3 → res2 → res1`
    和局部网格 `3 → 2`。任何"和根目录此刻的值相等"的断言都会跟着他一起变红 ——
    那不是代码坏了，是设计错了。**参考值的一致性用
    `python scripts/Check-HumanReference.py` 手动核对，不进测试。**
    """
    ref = fp.load_reference()
    # 脚本建网格用的级别 == 参考真值声明它对应的级别（这两个必须一起改）
    level = ref["human_truth"]["mesh_local_fluid_refinement_level"]
    local = [p for p in ref["local_mesh"]["parameters"] if p["type"] == 67][0]["long"]
    assert level == local, (
        f"human_truth 记的是 level {level}，local_mesh 按 level {local} 建 —— 一起改")
    # 值域合理
    assert 0 <= ref["mesh"]["result_resolution"] <= 5
    assert ref["human_truth"]["cells_fluid"] > 0
    # 容差必须是相对容差且给了理由
    assert 0 < ref["human_truth"]["reference_tolerance_rel"] < 0.05
    assert ref["human_truth"]["reference_tolerance_reason"]
    # 瞬时与时间平均两列都在，且键一致
    assert set(ref["human_truth"]["goals_si"]) == set(ref["human_truth"]["time_averaged_goals_si"])


# ------------------------------------------------- 目标收敛判据（只能改 xmlconfig）

def _fake_xmlconfig(tmp_path: Path, *, criteria=("3", "0", "0"), n_goals=7) -> Path:
    body = "".join(
        f'<GoalInfo index="{i}">'
        f'<Criteria value="1"/><CriteriaAbsValue value="1"/>'
        f'<CriteriaPercentage value="{criteria[0]}"/>'
        f'<CriteriaType value="{criteria[1]}"/>'
        f'<CriteriaVariableType value="{criteria[2]}"/>'
        f'<Name value="SG G{i}"/></GoalInfo>'
        for i in range(n_goals))
    text = ('<?xml version="1.0" encoding="utf-8"?><Root>'
            f'<ConvergenceOptions index="0"><MaxIter value="500"/>'
            f'<GoalsInfo index="0">{body}</GoalsInfo></ConvergenceOptions>'
            f'<Other><CriteriaPercentage value="{criteria[0]}"/></Other></Root>')
    path = tmp_path / "1.xmlconfig"
    path.write_text(text, encoding="utf-8")
    return path


def test_goal_criteria_rewrite_touches_only_the_goalsinfo_block(tmp_path):
    path = _fake_xmlconfig(tmp_path)
    report = fp.apply_goal_criteria_percentage(tmp_path, fp.load_reference())

    assert report["rewritten_counts"]["CriteriaPercentage"] == 7
    assert report["before"]["CriteriaPercentage"] == ["3"] * 7
    assert report["after"]["CriteriaPercentage"] == ["0.5"] * 7
    assert report["after"]["CriteriaType"] == ["1"] * 7
    assert report["after"]["CriteriaVariableType"] == ["1"] * 7
    text = path.read_text(encoding="utf-8")
    # GoalsInfo **之外**的同名标签必须原样不动
    assert '<Other><CriteriaPercentage value="3"/></Other>' in text
    assert '<MaxIter value="500"/>' in text


def test_goal_criteria_rewrite_is_idempotent(tmp_path):
    _fake_xmlconfig(tmp_path)
    ref = fp.load_reference()
    fp.apply_goal_criteria_percentage(tmp_path, ref)
    second = fp.apply_goal_criteria_percentage(tmp_path, ref)
    assert second["after"]["CriteriaPercentage"] == ["0.5"] * 7
    assert second["ok"]


def test_goal_criteria_rewrite_refuses_without_a_goalsinfo_block(tmp_path):
    (tmp_path / "1.xmlconfig").write_text("<Root><MaxIter value='500'/></Root>", encoding="utf-8")
    with pytest.raises(fp.FlowWriteError, match="GoalsInfo"):
        fp.apply_goal_criteria_percentage(tmp_path, fp.load_reference())


def test_goal_criteria_rewrite_refuses_when_file_is_missing(tmp_path):
    with pytest.raises(fp.FlowWriteError, match="不存在"):
        fp.apply_goal_criteria_percentage(tmp_path, fp.load_reference())


def test_reference_convergence_encodes_the_measured_fixes():
    """三条都是踩出来的，改任何一条之前先读 config 里的 reason 字段。

    * `use_manual_maximum_travels=True` —— 漏掉它就是 travel 4.003（人工 8.00589），
      设了 `MaximumTravels=8` 但被忽略，**读回还照样显示 8**，读回校验抓不到。
    * `use_goals_convergence=False` —— 判据改不动（API 没有），若开着，
      3% 判据会让 7 个目标在 travel ~1.8 全部报"收敛"，求解提前停。
    * `goal_criteria_percentage=0.5` —— 只是记录人工的值；改文件实测**无效**
      （Solve2 会用内存状态重写 xmlconfig），真停机保证靠上面那条。
    """
    conv = fp.load_reference()["convergence"]
    assert conv["use_manual_maximum_travels"] is True
    assert conv["use_goals_convergence"] is False
    # 行程 4（2026-09-21 由 8 改）：用户双行程对照实测，6 个收敛目标的「平均值」
    # 在 travel 4 和 8 之间只差 0.017%~0.038%，每条省约 30 s。依据记在 max_travels_note。
    assert conv["max_travels"] == 4
    assert "0.017%" in conv["max_travels_note"] and "30 s" in conv["max_travels_note"]
    assert conv["goal_criteria_percentage"] == 0.5
    assert conv["goal_criteria_application"]["method"] == "xmlconfig_edit"
    assert "实测无效" in conv["goal_criteria_application"]["measured_effectiveness"]
    assert "why_goals_convergence_is_off" in conv
    assert "stopping_note" in conv


def test_verify_goal_criteria_reads_the_solver_written_info_json(tmp_path):
    """真判据在 `1.info.json`：criteria 必须等于 percentage × value。"""
    (tmp_path / "1.info.json").write_text(json.dumps({
        "goals": [
            {"goal": {"name": "SG CV入口静压", "value": 204866.94744261276,
                      "criteria": 1024.3347372130638}},
            {"goal": {"name": "SG 力矩Z", "value": -17.682352991876606,
                      "criteria": 0.08841176495938304}},
        ]}, ensure_ascii=False), encoding="utf-8")
    report = fp.verify_goal_criteria(tmp_path, fp.load_reference(),
                                     ["SG CV入口静压", "SG 力矩Z"])
    assert report["ok"], report["problems"]
    assert all(r["rel_error"] < 1e-9 for r in report["goals"])


def test_verify_goal_criteria_catches_the_3_percent_default(tmp_path):
    """若 Flow 把文件覆盖回 3%，这里必须抓到 —— 否则会静默漂移。"""
    (tmp_path / "1.info.json").write_text(json.dumps({
        "goals": [{"goal": {"name": "SG CV入口静压", "value": 204866.94744261276,
                            "criteria": 6146.008422884383}}]}, ensure_ascii=False),
        encoding="utf-8")
    report = fp.verify_goal_criteria(tmp_path, fp.load_reference(), ["SG CV入口静压"])
    assert not report["ok"]
    assert "criteria" in report["problems"][0]


def test_verify_goal_criteria_reports_missing_goals(tmp_path):
    (tmp_path / "1.info.json").write_text(json.dumps({"goals": [
        {"goal": {"name": "SG CV入口静压", "value": 204866.94744261276,
                  "criteria": 1024.3347372130638}}]}, ensure_ascii=False), encoding="utf-8")
    report = fp.verify_goal_criteria(tmp_path, fp.load_reference(),
                                     ["SG CV入口静压", "SG 力矩Z"])
    assert not report["ok"]
    assert any("缺目标" in p for p in report["problems"])


def test_solver_goal_face_counts_parses_the_human_log():
    """**验证解析器，不验证人工此刻的设置。**

    ⚠️ 这里**不能**断言具体数值。根目录 `1/` 是个活跃的实验台：
    实测人工改过 res3→res1（流体单元 6505→1256，密比压面数 119→67），
    也改过局部网格 3→2（288→119）。写死数值或对着 config 比，
    他每调一次网格测试就假报警一次 —— 那不是代码坏了。

    解析器真正该保证的是：**7 个目标的网格面数都抠得出来、都是正整数**。
    具体数值记录在 `config` 的 `human_solver_face_counts`，属于「观察」，不是「契约」。
    """
    counts = fp.solver_goal_face_counts(HUMAN_XMLCONFIG.parent)
    assert counts, "一条都没解析出来 —— 日志格式变了？"
    want = set(fp.load_reference()["goals_expected_names"] if
               "goals_expected_names" in fp.load_reference()
               else [g["name"] for g in fp.load_reference()["goals"]])
    assert set(counts) == want, f"解析出的目标名对不上：{sorted(set(counts) ^ want)}"
    assert all(isinstance(n, int) and n > 0 for n in counts.values()), counts


def test_human_truth_records_both_value_columns():
    """参考真值必须同时留**瞬时**和**时间平均**两列，且时间平均有窗口记录。

    这是「`SG 力矩Z` 到底该报哪个数」那次结论的落地：
    瞬时值在振荡带里随机（人工两次同设置运行差 1.96%），
    比对必须用时间平均 —— 所以两列都得在，缺一列就没法查。
    """
    ht = fp.load_reference()["human_truth"]
    assert ht["time_averaged_goals_si"], "缺时间平均列 —— 比对就没法做了"
    assert ht["goal_time_average_window"], "缺平均窗口记录"
    assert ht["goal_oscillation_spread_rel"], "缺振荡幅度记录"
    # 力矩Z 必须是振荡最厉害的那个（这是当初发现整件事的线索）
    spans = ht["goal_oscillation_spread_rel"]
    worst = max(spans, key=lambda k: spans[k])
    assert worst == "SG 力矩Z", f"振荡最大的应是 SG 力矩Z，实测是 {worst}"


def test_face_selection_config_targets_the_human_face_ids():
    """把 `face_selection` 的序号换算回 Flow 面 ID，必须等于人工 xmlconfig 里的 Faces_Keys。

    这就是防「又一次挑错面」的那道闸：ID = 基址 + 面序号。
    """
    ref = fp.load_reference()
    base = ref["face_id_cipher"]["base"]
    goals = {g["name"]: g for g in ref["goals"]}
    for name, goal in goals.items():
        if "face_selection" not in goal:
            continue
        ids = sorted(base[e["component"]] + i
                     for e in goal["face_selection"] for i in e["indices"])
        assert ids == sorted(goal["human_face_keys"]), name
    assert goals["SG 密比压 平均"]["face_selection"][0]["component"] == "08大垫片"
    assert goals["SG 密比压 平均"]["expect_faces"] == 4
    assert goals["SG 蝶板法向压力"]["face_selection"][0]["indices"] == [2]


def test_every_face_selection_entry_declares_a_kind_and_baseline_area():
    """kinds 是硬判据（不符就抛），baseline_area_mm2 是签名 —— 两个都不许缺。"""
    for goal in fp.load_reference()["goals"]:
        for entry in goal.get("face_selection") or []:
            n = len(entry["indices"])
            assert len(entry.get("kinds", [])) == n, goal["name"]
            assert len(entry.get("baseline_area_mm2", [])) == n, goal["name"]
            assert all(k in ("plane", "cone", "cylinder", "other") for k in entry["kinds"])


# ---------------------------------------------------------------- 模块卫生

def test_flow_project_imports_without_com():
    """本模块必须能离线 import —— COM 只能在函数内部出现。"""
    source = (MAPPING / "scripts" / "flow_project.py").read_text(encoding="utf-8")
    header = source.split("class FlowWriteError", 1)[0]
    assert "import win32com" not in header
    assert "import pythoncom" not in header


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
