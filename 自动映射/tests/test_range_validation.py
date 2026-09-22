#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`Validate-DesignRanges.py` 里**不需要 SolidWorks** 的那部分：续跑跳过集与试验序列。

续跑那几条最要紧 —— 判错只有两种下场：要么白跑一遍（浪费时间，能忍），
要么**该跑的没跑、报告里却算它已经通过**（不能忍，因为没人会发现）。
所以这里专挑后一种方向测。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

MAPPING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MAPPING / "scripts"))
sys.path.insert(0, str(MAPPING))

import flow_geometry as fg  # noqa: E402


def _load_validator():
    """脚本名带连字符，不是合法标识符，只能用 importlib 装。"""
    path = MAPPING / "scripts" / "Validate-DesignRanges.py"
    spec = importlib.util.spec_from_file_location("Validate_DesignRanges", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["Validate_DesignRanges"] = module
    spec.loader.exec_module(module)
    return module


V = _load_validator()

BASE = {"c_mm": 32.0, "e_mm": 3.7, "phi_deg": 8.25, "alpha_deg": 35.5,
        "Dmax_mm": 191.31786408589767, "bm_mm": 7.5, "ds_mm": 45.0}
# 每个变量的默认范围就是把基准值放宽 ±10% —— 基准值必须落在自己的范围内，
# 否则 load_config 会在测到别的东西之前先炸。
RANGES = {name: {"lower": value * 0.9, "upper": value * 1.1, "unit": "mm", "meaning": ""}
          for name, value in BASE.items()}
RANGES["phi_deg"] = {"lower": 5.0, "upper": 11.0, "unit": "deg", "meaning": ""}
RANGES["alpha_deg"] = {"lower": 20.0, "upper": 40.0, "unit": "deg", "meaning": ""}
RANGES["Dmax_mm"] = {"lower": 184.1, "upper": 193.9, "unit": "mm", "meaning": ""}
RANGES["bm_mm"] = {"lower": 7.0, "upper": 8.0, "unit": "mm", "meaning": ""}
RANGES["ds_mm"] = {"lower": 37.152, "upper": 49.0, "unit": "mm", "meaning": ""}


def _args(**over):
    args = V.parser().parse_args([])
    for key, value in over.items():
        setattr(args, key, value)
    return args


def _validator(**over):
    config = {"baseline": dict(BASE), "ranges": RANGES,
              "paired_corner_tests": [["phi_deg", "alpha_deg"]],
              "triple_corner_tests": [["c_mm", "e_mm", "Dmax_mm"]],
              "global_corner_tests": True}
    return V.RangeValidator(config, _args(**over))


def _write_session(tmp_path: Path, session: str, cases: list[dict]) -> Path:
    path = tmp_path / session / "range_validation_summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"session": session, "cases": cases}, ensure_ascii=False),
                    encoding="utf-8")
    return path


def _case(tag, design, outcome, run=None):
    return {"tag": tag, "design": design, "outcome": outcome, "run": run or f"run_{tag}",
            "elapsed_seconds": 111.0, "completed": outcome == "pass",
            "physical_mapping_verified": outcome == "pass"}


# ---------------------------------------------------------------- 设计点指纹

def test_design_key_ignores_key_order():
    assert V.design_key(dict(BASE)) == V.design_key(dict(reversed(list(BASE.items()))))


def test_design_key_separates_different_points():
    other = dict(BASE, Dmax_mm=184.1)
    assert V.design_key(BASE) != V.design_key(other)


def test_design_key_separates_floats_that_differ_by_a_hair():
    """0.01 mm 的差就是"可用"和"失败"的距离 —— 指纹必须认得出。"""
    assert V.design_key(dict(BASE, Dmax_mm=183.99772)) != V.design_key(dict(BASE, Dmax_mm=183.99214))


# ---------------------------------------------------------------- 读旧会话

def test_loads_pass_and_fail(tmp_path):
    path = _write_session(tmp_path, "old", [
        _case("baseline", BASE, "pass"),
        _case("axis_bm_mm_lower", dict(BASE, bm_mm=7.0), "fail"),
    ])
    index, sessions, not_inherited = V.load_prior_results([str(path)])
    assert sessions == ["old"]
    assert not_inherited == []
    assert index[("baseline", V.design_key(BASE))]["case"]["outcome"] == "pass"
    assert len(index) == 2


def test_inconclusive_is_not_inherited(tmp_path):
    """COM 挂了/超时不是几何证据 —— 继承它等于把上次的意外当成本次的结论。"""
    path = _write_session(tmp_path, "old", [
        _case("axis_bm_mm_lower", dict(BASE, bm_mm=7.0), "inconclusive"),
        _case("axis_c_mm_lower", dict(BASE, c_mm=30.0), "pass"),
    ])
    index, _, not_inherited = V.load_prior_results([str(path)])
    assert len(index) == 1
    assert not_inherited == ["old/axis_bm_mm_lower"]


def test_session_name_resolves_under_sessions_root(tmp_path, monkeypatch):
    monkeypatch.setattr(V, "SESSIONS_ROOT", tmp_path)
    _write_session(tmp_path, "rangev1c_x", [_case("baseline", BASE, "pass")])
    index, sessions, _ = V.load_prior_results(["rangev1c_x"])
    assert sessions == ["rangev1c_x"] and len(index) == 1


def test_missing_session_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(V, "SESSIONS_ROOT", tmp_path)
    with pytest.raises(FileNotFoundError):
        V.load_prior_results(["不存在的会话"])


def test_multiple_sessions_merge(tmp_path):
    a = _write_session(tmp_path, "a", [_case("baseline", BASE, "pass")])
    b = _write_session(tmp_path, "b", [_case("axis_bm_mm_lower", dict(BASE, bm_mm=7.0), "fail")])
    index, sessions, _ = V.load_prior_results([str(a), str(b)])
    assert len(index) == 2 and sessions == ["a", "b"]


# ---------------------------------------------------------------- 跳过集

def test_matching_tag_and_design_is_inherited(tmp_path):
    path = _write_session(tmp_path, "old", [_case("axis_bm_mm_lower", dict(BASE, bm_mm=7.0), "pass")])
    v = _validator(resume_from=[str(path)])
    assert v.will_inherit("axis_bm_mm_lower", dict(BASE, bm_mm=7.0), "axis_endpoint") is not None


def test_same_tag_but_changed_design_must_rerun(tmp_path):
    """**这条是续跑的核心陷阱**：改了范围之后，同名端点的含义已经变了。

    只按标签跳过的话，`axis_Dmax_mm_lower` 会拿着旧的 179.9 失败记录去交差，
    而新一轮问的是 184.1 行不行 —— 报告里就会写着一个没人验证过的结论。
    """
    path = _write_session(tmp_path, "old", [
        _case("axis_Dmax_mm_lower", dict(BASE, Dmax_mm=179.9), "fail"),
    ])
    v = _validator(resume_from=[str(path)])
    assert v.will_inherit("axis_Dmax_mm_lower", dict(BASE, Dmax_mm=184.1), "axis_endpoint") is None, \
        "设计点变了就必须重跑"


def test_baseline_is_never_inherited(tmp_path):
    """baseline 是环境哨兵：继承它就把"这台 SolidWorks 此刻能跑通"这一条跳过了。"""
    path = _write_session(tmp_path, "old", [_case("baseline", BASE, "pass")])
    v = _validator(resume_from=[str(path)])
    assert v.will_inherit("baseline", dict(BASE), "baseline") is None
    assert v.prior[("baseline", V.design_key(BASE))]["case"]["outcome"] == "pass", \
        "旧记录仍然留着，只是不用它"


def test_run_case_uses_the_same_judgement_as_the_preview(tmp_path):
    """`run_case` 必须直接调 `will_inherit` —— 判据复制一份出来就会漂，

    而漂的方向是"预告说实跑、实际复用"，也就是有个用例根本没跑却没人发现。
    """
    source = (MAPPING / "scripts" / "Validate-DesignRanges.py").read_text(encoding="utf-8")
    body = source.split("def run_case(", 1)[1].split("\n    def ", 1)[0]
    assert "self.will_inherit(" in body, "run_case 必须复用同一判据，不要自己再查一遍 prior"


# ---------------------------------------------------------------- 试验序列

def test_plan_covers_every_direct_case():
    tags = [spec["tag"] for spec in _validator().plan()]
    assert tags[0] == "baseline"
    assert len(tags) == len(set(tags)), "标签不能重复，否则跳过集会互相撞车"
    for field in V.FIELDS:
        assert f"axis_{field}_lower" in tags and f"axis_{field}_upper" in tags
    for first, second in (("phi_deg", "alpha_deg"),):
        for a in ("lower", "upper"):
            for b in ("lower", "upper"):
                assert f"corner_{first}_{a}_{second}_{b}" in tags
    assert "global_lower" in tags and "global_upper" in tags


def test_plan_endpoints_take_the_configured_range_values():
    by_tag = {spec["tag"]: spec for spec in _validator().plan()}
    assert by_tag["axis_Dmax_mm_lower"]["design"]["Dmax_mm"] == 184.1
    assert by_tag["axis_Dmax_mm_lower"]["design"]["c_mm"] == BASE["c_mm"], "其余变量保持基准"
    assert by_tag["global_lower"]["design"]["Dmax_mm"] == 184.1
    assert by_tag["global_lower"]["design"]["alpha_deg"] == 20.0


def test_no_interaction_corners_flag_drops_them():
    tags = [spec["tag"] for spec in _validator(no_interaction_corners=True).plan()]
    assert not any(tag.startswith("corner_") for tag in tags)


def test_triple_plan_has_all_eight_corners():
    tags = [spec["tag"] for spec in _validator().plan() if spec["purpose"] == "triple_corner"]
    assert len(tags) == 8
    assert len(tags) == len(set(tags))


def test_no_triple_corners_flag_drops_them():
    tags = [spec["tag"] for spec in _validator(no_triple_corners=True).plan()]
    assert not any(tag.startswith("triple_") for tag in tags)


def test_no_global_corners_flag_drops_them():
    tags = [spec["tag"] for spec in _validator(no_global_corners=True).plan()]
    assert not any(tag.startswith("global_") for tag in tags)


def test_design_on_upper_path_has_correct_endpoints():
    v = _validator()
    assert v.design_on_upper_path(0.0) == BASE
    assert v.design_on_upper_path(1.0) == {
        field: float(RANGES[field]["upper"]) for field in V.FIELDS
    }


def test_design_on_upper_path_interpolates_every_field():
    v = _validator()
    midpoint = v.design_on_upper_path(0.5)
    for field in V.FIELDS:
        assert midpoint[field] == pytest.approx(
            (BASE[field] + float(RANGES[field]["upper"])) / 2.0
        )


# ---------------------------------------------------------------- 记账

def _stub_run_case(outcomes):
    """假的 run_case：不碰 SolidWorks，只按标签给结论。"""
    def stub(tag, design, **kw):
        return {"tag": tag, "design": design, "run": f"run_{tag}",
                "outcome": outcomes.get(tag, "pass"), "elapsed_seconds": 0.0}
    return stub


def test_axis_bookkeeping_covers_every_field():
    """重构时曾把 `axis_results[field]` 的初始化漏掉，跑了两例才 KeyError。

    这个记账不需要 SolidWorks，所以值得离线钉住：每个变量都得有 lower/upper 两条。
    """
    v = _validator()
    v.run_case = _stub_run_case({})
    v.run_axis_tests()
    for field in V.FIELDS:
        assert set(v.summary["axis_results"][field]) == {"lower", "upper"}


def test_corner_and_global_bookkeeping():
    v = _validator()
    v.run_case = _stub_run_case({})
    v.run_axis_tests()
    v.run_interaction_corners()
    v.run_triple_corners()
    v.run_global_corners()
    assert len(v.summary["interaction_corner_results"]) == 4
    assert len(v.summary["triple_corner_results"]) == 8
    assert len(v.summary["global_corner_results"]) == 2


def test_failed_global_upper_gets_path_boundary_and_leave_one_tests():
    v = _validator(max_path_bisect_steps=3, tolerance_t=0.01)

    def fake_case(tag, design, **kw):
        # 模拟边界 t=0.75：低于它通过，达到或超过它失败。
        if tag == "global_upper":
            outcome = "fail"
        elif tag.startswith("global_upper_path_bisect_"):
            first = V.FIELDS[0]
            span = float(RANGES[first]["upper"]) - BASE[first]
            t = (design[first] - BASE[first]) / span
            outcome = "pass" if t < 0.75 else "fail"
        else:
            outcome = "pass"
        return {"tag": tag, "design": design, "run": f"run_{tag}",
                "outcome": outcome, "elapsed_seconds": 0.0}

    v.run_case = fake_case
    v.summary["global_corner_results"] = [{
        "side": "upper", "outcome": "fail", "run": "run_global_upper"
    }]
    v.run_global_upper_path_boundary()
    boundary = v.summary["global_upper_path_boundary"]
    assert boundary["nearest_verified_t"] < 0.75
    assert boundary["nearest_failed_t"] >= 0.75
    assert len(v.summary["global_upper_leave_one_results"]) == len(V.FIELDS)


def test_passing_global_upper_does_not_trigger_path_tests():
    v = _validator()
    v.summary["global_corner_results"] = [{
        "side": "upper", "outcome": "pass", "run": "run_global_upper"
    }]
    v.run_global_upper_path_boundary()
    assert v.summary["global_upper_path_boundary"]["status"] == "not_needed"
    assert v.summary["global_upper_leave_one_results"] == []


def test_a_failing_corner_seeks_conditional_boundaries():
    """两个端点各自通过、合起来失败 —— 这才是角点测试存在的意义，必须留下条件边界。"""
    v = _validator()
    v.run_case = _stub_run_case({"corner_phi_deg_upper_alpha_deg_lower": "fail"})
    v.run_axis_tests()
    v.run_interaction_corners()
    assert len(v.summary["interaction_corner_results"]) == 4
    boundaries = v.summary["conditional_interaction_boundaries"]
    assert boundaries, "角点失败却没有留下任何条件边界"
    assert {row["field"] for row in boundaries} == {"phi_deg", "alpha_deg"}


def test_inherited_case_does_not_inflate_this_session_time():
    """复用不是"跑了 0 秒"，而是"没跑"。本轮耗时统计不能把旧会话的秒数算进来。"""
    v = _validator()
    v.prior = {("axis_c_mm_lower", V.design_key(dict(BASE, c_mm=RANGES["c_mm"]["lower"]))):
               {"case": _case("axis_c_mm_lower", dict(BASE, c_mm=RANGES["c_mm"]["lower"]), "pass"),
                "session": "old"}}
    case = v.run_case("axis_c_mm_lower", dict(BASE, c_mm=RANGES["c_mm"]["lower"]),
                      purpose="axis_endpoint")
    assert case["elapsed_seconds"] == 0.0
    assert case["inherited_elapsed_seconds"] == 111.0
    assert case["inherited_session"] == "old"


def test_cleanup_refuses_anything_outside_trials_root(tmp_path, monkeypatch):
    monkeypatch.setattr(V, "RUNS_ROOT", tmp_path / "runs")
    v = _validator()
    outside = tmp_path / "outside"
    outside.mkdir()
    result = v.cleanup_run_directory(outside)
    assert result["removed"] is False
    assert result["error"] == "refused_unsafe_cleanup_target"


def test_cleanup_removes_only_direct_safe_run_folder(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    target = runs / "rangev2_case_001"
    target.mkdir(parents=True)
    (target / "temporary.SLDPRT").write_bytes(b"test")
    monkeypatch.setattr(V, "RUNS_ROOT", runs)
    v = _validator()
    result = v.cleanup_run_directory(target)
    assert result["removed"] is True
    assert not target.exists()


def test_bad_paired_corner_test_is_rejected_at_load(tmp_path):
    """配置写错要在读配置时就炸，不能等到跑完单变量端点才炸。"""
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({
        "baseline": BASE, "ranges": RANGES,
        "paired_corner_tests": [["phi_deg", "phi_deg"]],
    }, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="不合法"):
        V.load_config(path)


def test_baseline_outside_its_range_is_rejected(tmp_path):
    """改了范围却忘了基准值也在范围里 —— 必须在读配置时就炸，不能跑出个半截会话。"""
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({
        "baseline": dict(BASE, Dmax_mm=100.0), "ranges": RANGES,
    }, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="不在范围"):
        V.load_config(path)
