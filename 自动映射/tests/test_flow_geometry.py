#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flow_geometry 的离线验证 —— 不连接 SolidWorks，用仓里已有的两份真值文件当靶子。

  靶子 A: working/flow_lid_reference_faces.json
          模板还有【原生封盖】时抓的，每盖 3 个面。已知它是基准七变量设计的封盖。
  靶子 B: working/seven_variable_trials/flow_lids_013/open_circular_edges.json
          448 条圆边，世界坐标，同一份基准几何。

这两份是本次唯一能证明「几何识别写对了」的证据。跑法：
    cd 自动映射 && python -m pytest tests/test_flow_geometry.py -q
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

MAPPING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MAPPING / "scripts"))

import flow_geometry as fg  # noqa: E402

REFERENCE_FACES = MAPPING / "working" / "flow_lid_reference_faces.json"
OPEN_EDGES = MAPPING / "working" / "seven_variable_trials" / "flow_lids_013" / "open_circular_edges.json"

#: 基准几何下四个开口的内面位置（来自 flow_lid_reference_faces.json，人工核对过）
REFERENCE_INNER_PLANE = {
    "inlet": -0.6052332694371678,
    "outlet": 1.105233251722155,
    "upper_body": 0.10123323400713326,
    "lower_body": -0.23773323400713325,
}
REFERENCE_INNER_AREA = {
    "inlet": 0.03660342203376061,
    "outlet": 0.036603422033760616,
    "upper_body": 0.0009368124082605323,
    "lower_body": 0.003506824466153257,
}


def _load(path: Path):
    if not path.is_file():
        pytest.skip(f"缺少真值文件 {path}")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def edges():
    return _load(OPEN_EDGES)["circular_edges"]


@pytest.fixture(scope="module")
def caps_by_component():
    faces = _load(REFERENCE_FACES)["faces"]
    grouped: dict[str, list] = {}
    for f in faces:
        grouped.setdefault(f["component"], []).append(f)
    return grouped


# ---------------------------------------------------------------- 靶子 B：开口识别

def test_pipe_openings_derived_from_geometry(edges):
    """入口/出口的射线必须从管口的两圈同心圆边推出来，不靠硬编码。"""
    got = fg.pipe_openings(edges)

    for logical, comp, sign, extreme in (
        ("inlet", "入口管道", -1, -0.615),
        ("outlet", "出口管道", +1, 1.115),
    ):
        o = got[logical]
        ev = o["evidence"]
        assert o["expect_component"] == comp
        assert ev["ring_count"] == 2, f"{logical}: 外端应有内外两圈圆边"
        assert ev["inner_radius_m"] == pytest.approx(0.105, abs=1e-6)
        assert ev["outer_radius_m"] == pytest.approx(0.120, abs=1e-6)
        # 横向偏置必须落在两圈之间 —— 沿轴线打会命中阀轴/蝶板
        assert ev["inner_radius_m"] < ev["transverse_offset_m"] < ev["outer_radius_m"]
        assert ev["outer_extreme_m"] == pytest.approx(extreme, abs=1e-6)
        # 起点在实体之外（入口更负、出口更正），方向朝内
        assert sign * o["ray_point_m"][0] > sign * extreme
        assert o["ray_direction"] == [-sign, 0.0, 0.0]
        # y 方向按中值偏置，z 落在轴线上
        assert o["ray_point_m"][1] == pytest.approx(ev["transverse_offset_m"], abs=1e-6)
        assert o["ray_point_m"][2] == pytest.approx(0.0, abs=1e-6)


def test_body_ports_have_calibration_flag(edges):
    """上开口已标定、下开口未标定 —— 这个事实必须留在数据里，不能被抹平。"""
    got = fg.body_port_openings(edges)
    assert got["upper_body"]["calibrated"] is True, "上开口已由 z=±0.111 的 r=27/30 环带标定"
    assert got["lower_body"]["calibrated"] is False, "下开口尚未标定，必须显式标记"
    for name, o in got.items():
        assert fg.BODY_COMPONENT in o["expect_component"], "阀体的组件名是全名（含 03阀体）"
        assert o["ray_direction"][2] == (-1.0 if name == "upper_body" else 1.0)
        # 射线横向偏置必须落在内孔环带里，否则会打到阀轴
        lo, hi = o["evidence"]["innermost_pair_m"]
        assert lo < o["evidence"]["used_offset_m"] < hi * 1.5


def test_openings_complete(edges):
    got = fg.openings_from_edges(edges)
    assert tuple(got) == fg.LOGICAL_OPENINGS
    for o in got.values():
        assert len(fg.ray_select_args(o, append=False)) == 11


# ---------------------------------------------------------------- 靶子 A：封盖分类

def test_every_cap_classifies_to_its_opening(caps_by_component):
    assert len(caps_by_component) == 4, f"应有四个封盖，得到 {sorted(caps_by_component)}"
    for comp, faces in caps_by_component.items():
        info = fg.classify_cap(faces)
        logical = info["logical"]
        assert info["thin_axis"] in ("X", "Z")
        assert info["inner_plane_value_m"] == pytest.approx(
            REFERENCE_INNER_PLANE[logical], abs=1e-6), f"{comp} 的内面位置不对"
        assert math.sqrt(info["inner_face"]["area_m2"] / math.pi) == pytest.approx(
            math.sqrt(REFERENCE_INNER_AREA[logical] / math.pi), abs=1e-6)
        # 内面必须比外面更靠流体侧
        assert info["inner_plane_value_m"] != info["outer_plane_value_m"]


def test_cap_component_names_are_not_trusted(caps_by_component):
    """封盖编号不稳定是实测事实（一次 3,2,1,4，另一次 4,1,2,3）。

    这条测试把「名字与开口无关」钉死：把组件名打乱，配对结果必须一模一样。
    """
    openings = {k: {"reference_inner_plane": v} for k, v in REFERENCE_INNER_PLANE.items()}

    straight = fg.pair_caps_to_openings(caps_by_component, openings)
    shuffled_names = list(caps_by_component)
    shuffled = {f"封盖{9 - i}": caps_by_component[name] for i, name in enumerate(shuffled_names)}
    scrambled = fg.pair_caps_to_openings(shuffled, openings)

    assert {k: v["inner_plane_value_m"] for k, v in straight.items()} == \
           {k: v["inner_plane_value_m"] for k, v in scrambled.items()}


def test_pairing_rejects_two_caps_on_one_opening(caps_by_component):
    """两个封盖挤在同一个开口上（下开口没盖住）必须报错 —— 证明门禁不是摆设。

    注意：把两个封盖的【面数据互换名字】是**不应该**报错的 —— 分类只读几何、
    不信名字，这正是 test_cap_component_names_are_not_trusted 钉死的性质。
    真正要拦的是「某个开口没有盖 / 有盖但不在这儿」。
    """
    names = list(caps_by_component)
    upper = next(n for n in names if fg.classify_cap(caps_by_component[n])["logical"] == "upper_body")
    lower = next(n for n in names if fg.classify_cap(caps_by_component[n])["logical"] == "lower_body")

    broken = dict(caps_by_component)
    broken[lower] = caps_by_component[upper]  # 下开口那个位置也放成上开口的盖

    openings = {k: {"reference_inner_plane": v} for k, v in REFERENCE_INNER_PLANE.items()}
    with pytest.raises(ValueError, match="配到 2 个封盖"):
        fg.pair_caps_to_openings(broken, openings)


def test_pairing_rejects_wrong_position(caps_by_component):
    """封盖位置偏离参照 1mm 以上必须拒 —— S5 的容差门禁。"""
    openings = {k: {"reference_inner_plane": v + 0.01} for k, v in REFERENCE_INNER_PLANE.items()}
    with pytest.raises(ValueError, match="超过"):
        fg.pair_caps_to_openings(caps_by_component, openings)


# ---------------------------------------------------------------- S3 射线门禁

def test_ray_hit_gate_accepts_the_real_run(edges):
    """用 flow_lids_013 实际记录的命中结果喂门禁，必须通过。"""
    lips = _load(MAPPING / "working" / "seven_variable_trials" / "flow_lids_013" / "flow_lids.json")
    openings = fg.openings_from_edges(edges)
    hits = []
    for s in lips["selections"]:
        hits.append({"logical": s["name"], "component": s["component"], "selected": s["selected"]})
    # 记录里 upper_body/lower_body 的名字与逻辑名一致
    seen = fg.check_ray_hits(hits, openings)
    assert seen["inlet"]["got"] == "入口管道"
    assert seen["outlet"]["got"] == "出口管道"
    assert fg.BODY_COMPONENT in seen["upper_body"]["got"]
    assert fg.BODY_COMPONENT in seen["lower_body"]["got"]


def test_ray_start_can_be_overridden_without_touching_code(edges):
    """上/下开口沿射线的起点还没离线证实，必须能不改代码就换掉，且换过要留痕。"""
    derived = fg.openings_from_edges(edges)
    assert "start_overridden" not in derived["upper_body"]
    assert derived["upper_body"]["ray_point_m"][2] > 0.316, "推导值从阀体外侧起步"

    tuned = fg.openings_from_edges(edges, ray_start_overrides={"upper_body": 0.3})
    assert tuned["upper_body"]["ray_point_m"][2] == 0.3
    assert tuned["upper_body"]["start_overridden"] is True
    assert tuned["upper_body"]["evidence"]["ray_start_override"]["derived_m"] == \
        derived["upper_body"]["ray_point_m"][2]
    # 横向偏置不受影响 —— 那是推出来的，不该被覆盖掉
    assert tuned["upper_body"]["ray_point_m"][0] == derived["upper_body"]["ray_point_m"][0]

    with pytest.raises(ValueError, match="未知的开口"):
        fg.openings_from_edges(edges, ray_start_overrides={"nope": 1.0})


def test_ray_hit_gate_rejects_wrong_component(edges):
    """射线打到阀轴（沿轴线打就是这个后果）必须被拦下。"""
    openings = fg.openings_from_edges(edges)
    hits = [{"logical": k, "component": openings[k]["expect_component"], "selected": True}
            for k in fg.LOGICAL_OPENINGS]
    hits[0]["component"] = '8“D94R3Y-CL600C-04阀轴-1'
    with pytest.raises(ValueError, match="命中"):
        fg.check_ray_hits(hits, openings)


def test_ray_hit_gate_rejects_missing_selection(edges):
    openings = fg.openings_from_edges(edges)
    hits = [{"logical": k, "component": openings[k]["expect_component"], "selected": True}
            for k in fg.LOGICAL_OPENINGS]
    hits[2]["selected"] = False
    with pytest.raises(ValueError, match="没选中"):
        fg.check_ray_hits(hits, openings)


# ---------------------------------------------------------------- 训练行

HUMAN_GOALS = {
    "SG CV入口静压": 204866.94744261276,
    "SG CV出口静压": 101325.0,
    "SG CV入口端面体积流量": 0.10270053171754644,
    "SG 蝶板法向压力": 208732.16603450547,
    "SG 密比压 平均": 114367.04218157964,
    "SG 密比压 最大": 204397.6227894705,
    "SG 力矩Z": -17.682352991876606,
}
HUMAN_DESIGN = {"c_mm": 32.0, "e_mm": 3.7, "phi_deg": 8.25, "alpha_deg": 35.5,
                "Dmax_mm": 191.31786408589767, "bm_mm": 7.5, "ds_mm": 45.0}


def test_training_row_matches_contract():
    row = fg.build_training_row(HUMAN_DESIGN, HUMAN_GOALS, 998.2)
    assert list(row) == list(fg.TRAINING_COLUMNS)
    assert fg.training_row_matches_contract(row) == []
    assert row["Cv"] == pytest.approx(419.68256004096025, rel=1e-9), "与 valve_mapping 实测值一致"


def test_training_row_refuses_to_fill_zeros():
    """缺标签必须留空，且不得用 0 顶替。"""
    incomplete = dict(HUMAN_GOALS)
    del incomplete["SG 密比压 最大"]
    incomplete["SG 力矩Z"] = None
    row = fg.build_training_row(HUMAN_DESIGN, incomplete, 998.2)
    assert row["SG 密比压 最大"] is None, "缺失的目标必须留 None，不得补 0"
    assert row["SG 力矩Z"] is None
    assert fg.training_row_matches_contract(row) == []


def test_training_row_requires_cv_inputs():
    """三个 CV 目标缺任何一个，整行不成立。"""
    for missing in ("SG CV入口静压", "SG CV出口静压", "SG CV入口端面体积流量"):
        bad = dict(HUMAN_GOALS)
        del bad[missing]
        with pytest.raises(ValueError, match="不得补零|无法计算"):
            fg.build_training_row(HUMAN_DESIGN, bad, 998.2)


# ------------------------------------------------ 契约分组：不许再用位置切片


def test_training_columns_are_composed_from_named_groups():
    """契约必须由具名分组拼出来 —— 位置切片正是 2026-09-22 那次静默错位的根因。

    插入 `样本序号` 之后，`TRAINING_COLUMNS[7:14]` 会变成「样本序号 + 前 6 个目标」，
    于是 `SG 力矩Z` 被读取过滤器丢掉、写成 None，而契约校验只保 `Cv` ——
    一行缺力矩Z的数据照样算合格。所以断言必须钉在**分组**上，不是下标上。
    """
    assert fg.TRAINING_COLUMNS == (fg.ID_COLUMNS + fg.DESIGN_COLUMNS
                                   + fg.GOAL_COLUMNS + fg.LABEL_COLUMNS)
    assert len(fg.DESIGN_COLUMNS) == 7
    assert len(fg.GOAL_COLUMNS) == 7, "目标名同时就是 Flow 目标名，少数一个会静默丢标签"
    assert fg.ID_COLUMNS == ("样本序号",)
    assert fg.LABEL_COLUMNS == ("ΔP", "Cv"), "两个派生列，ΔP 在前"
    # 编号夹在设计输入与目标之间：这样「前 7 列 = 设计输入」（幂等主键）与「Cv 在末尾」都不变
    # 编号在最前（拿到表就能对回来源表），Cv 在最后
    assert fg.TRAINING_COLUMNS[0] == "样本序号"
    assert fg.TRAINING_COLUMNS[-1] == "Cv"
    # 但**没有任何地方能靠位置取到设计输入或目标** —— 一律具名
    assert fg.TRAINING_COLUMNS[1:8] == fg.DESIGN_COLUMNS


def test_every_goal_column_gets_its_value():
    """七个目标必须**逐个**落到行里 —— 这是上面那条静默错位的直接回归守卫。"""
    row = fg.build_training_row(HUMAN_DESIGN, HUMAN_GOALS, 998.2)
    for name in fg.GOAL_COLUMNS:
        assert row[name] == HUMAN_GOALS[name], f"{name} 没写进训练行"
    assert row["SG 力矩Z"] == HUMAN_GOALS["SG 力矩Z"], "力矩Z 是最容易被过滤器丢掉的那个"


def test_sample_id_is_optional_and_left_blank():
    """单样本跑不给编号 → 留空。按契约「缺失的标签留空」，**不得补零**。"""
    row = fg.build_training_row(HUMAN_DESIGN, HUMAN_GOALS, 998.2)
    assert row["样本序号"] is None
    assert fg.training_row_matches_contract(row) == [], "空编号必须仍然算合格行"


def test_delta_p_is_the_pressure_difference():
    """ΔP 必须记进表 —— Cv 是它和流量一起算出来的，不记它就反推不出 Cv 怎么来的。"""
    row = fg.build_training_row(HUMAN_DESIGN, HUMAN_GOALS, 998.2)
    expected = HUMAN_GOALS["SG CV入口静压"] - HUMAN_GOALS["SG CV出口静压"]
    assert row["ΔP"] == pytest.approx(expected, rel=1e-12)
    assert row["ΔP"] > 0, "入口静压高于出口，压差必须为正"
    # 它和 Cv 是同一对输入算出来的：Cv 能复现
    assert row["Cv"] == pytest.approx(
        fg.cv_from_si(HUMAN_GOALS["SG CV入口端面体积流量"], row["ΔP"], 998.2), rel=1e-12)


def test_contract_rejects_a_blank_delta_p():
    """ΔP 和 Cv 一样是派生列 —— 空 = 整行不成立，和「缺标签留空」是两回事。"""
    row = fg.build_training_row(HUMAN_DESIGN, HUMAN_GOALS, 998.2)
    row["ΔP"] = None
    problems = fg.training_row_matches_contract(row)
    assert any("ΔP" in p for p in problems), problems


def test_sample_id_is_carried_through_verbatim():
    """编号是标识符不是数值：整数、字符串都原样透传，且都不该被判「不是有限数」。"""
    for sid in (7, 3200, "S-07"):
        row = fg.build_training_row(HUMAN_DESIGN, HUMAN_GOALS, 998.2, sample_id=sid)
        assert row["样本序号"] == sid
        assert fg.training_row_matches_contract(row) == [], f"{sid!r} 不该被判不合格"


def test_cv_rejects_kg_per_m3_mistake():
    """ρ 必须以 kg/m³ 传入但公式内部除以 1000；把 kg/m³ 当相对密度会偏 ~31.6 倍。"""
    cv = fg.cv_from_si(0.10270053171754644, 103541.94744261276, 998.2)
    wrong = fg.cv_from_si(0.10270053171754644, 103541.94744261276, 998.2 * 1000)
    assert wrong / cv == pytest.approx(math.sqrt(1000), rel=1e-9)
    assert cv == pytest.approx(419.68, rel=1e-3)


# ---------------------------------------------------------------- 组件名

def test_component_name_helpers():
    assert fg.base_component('入口管道^8“D94R3Y-CL600C-00组装图-1') == "入口管道"
    assert fg.component_with_instance('入口管道^8“D94R3Y-CL600C-00组装图-1') == "入口管道-1"
    assert fg.base_component("8“D94R3Y-CL600C-03阀体-1") == "8“D94R3Y-CL600C-03阀体-1"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
