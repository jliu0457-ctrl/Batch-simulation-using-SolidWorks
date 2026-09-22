#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run-Batch 里**不需要 SolidWorks** 的那部分：读表、编号命名、跨表去重。

读表那几条最要紧 —— 读错了不是报错，是**悄悄跑错样本或漏跑样本**，
而这批要跑好几个小时，等发现时已经晚了。所以这里专挑「静默出错」的路径测。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from openpyxl import Workbook

MAPPING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MAPPING / "scripts"))
sys.path.insert(0, str(MAPPING))

import flow_geometry as fg  # noqa: E402


def _load_batch():
    """脚本名带连字符（`Run-Batch.py`），不是合法标识符，只能用 importlib 装。"""
    path = MAPPING / "scripts" / "Run-Batch.py"
    spec = importlib.util.spec_from_file_location("Run_Batch", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["Run_Batch"] = module
    spec.loader.exec_module(module)
    return module


B = _load_batch()

DESIGN = {"c_mm": 32.0, "e_mm": 3.7, "phi_deg": 8.25,
          "alpha_deg": 35.5, "Dmax_mm": 191.3, "bm_mm": 7.5, "ds_mm": 45.0}
HEADER = [fg.ID_COLUMNS[0], *fg.DESIGN_COLUMNS]


def _write_xlsx(path: Path, header, rows) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(list(header))
    for row in rows:
        sheet.append(list(row))
    workbook.save(path)
    workbook.close()
    return path


def _row(sample_id=1, **overrides):
    design = dict(DESIGN)
    design.update(overrides)
    return [sample_id] + [design[c] for c in fg.DESIGN_COLUMNS]


# ---------------------------------------------------------------- 读表

def test_reads_a_normal_table(tmp_path):
    path = _write_xlsx(tmp_path / "a.xlsx", HEADER, [_row(1), _row(2, c_mm=31.0)])
    designs = B.read_designs_from_xlsx(path)

    assert [d["样本序号"] for d in designs] == [1, 2]
    assert designs[0]["c_mm"] == 32.0
    assert designs[1]["c_mm"] == 31.0
    assert set(designs[0]) == {fg.ID_COLUMNS[0], *fg.DESIGN_COLUMNS}


def test_column_order_does_not_matter(tmp_path):
    """按**表头名字**取列，不按位置 —— 别人把列顺序改了也必须读对。"""
    shuffled = list(reversed(HEADER))
    values = dict(zip(HEADER, _row(7, Dmax_mm=188.5)))
    path = _write_xlsx(tmp_path / "a.xlsx", shuffled, [[values[h] for h in shuffled]])

    designs = B.read_designs_from_xlsx(path)
    assert designs[0]["样本序号"] == 7
    assert designs[0]["Dmax_mm"] == 188.5
    assert designs[0]["c_mm"] == 32.0


def test_missing_column_is_an_error(tmp_path):
    header = [h for h in HEADER if h != "ds_mm"]
    path = _write_xlsx(tmp_path / "a.xlsx", header, [[v for h, v in zip(HEADER, _row(1)) if h != "ds_mm"]])
    with pytest.raises(ValueError, match="缺列"):
        B.read_designs_from_xlsx(path)


def test_duplicate_ids_are_an_error(tmp_path):
    """编号撞车 = 两个样本会写同一个 run 目录，必须当场拦下。"""
    path = _write_xlsx(tmp_path / "a.xlsx", HEADER, [_row(3), _row(3, c_mm=31.0)])
    with pytest.raises(ValueError, match="重复"):
        B.read_designs_from_xlsx(path)


def test_blank_rows_are_skipped(tmp_path):
    path = _write_xlsx(tmp_path / "a.xlsx", HEADER, [_row(1), [None] * 8, _row(2)])
    assert [d["样本序号"] for d in B.read_designs_from_xlsx(path)] == [1, 2]


def test_non_numeric_cell_is_an_error(tmp_path):
    """空单元格不得被当成 0 —— 那会造出一个没人在意的坏设计点。"""
    bad = _row(1)
    bad[3] = None
    path = _write_xlsx(tmp_path / "a.xlsx", HEADER, [bad])
    with pytest.raises(ValueError, match="不是数字"):
        B.read_designs_from_xlsx(path)


def test_row_without_an_id_is_an_error(tmp_path):
    """有数据没编号 → 报错。静默跳过就等于悄悄少跑一个样本。"""
    path = _write_xlsx(tmp_path / "a.xlsx", HEADER, [[None] + _row(1)[1:]])
    with pytest.raises(ValueError, match="没有编号"):
        B.read_designs_from_xlsx(path)


def test_out_of_domain_design_is_rejected(tmp_path):
    """走的是和单样本同一套校验（valve_mapping.validate_design）。"""
    path = _write_xlsx(tmp_path / "a.xlsx", HEADER, [_row(1, alpha_deg=190.0)])
    with pytest.raises(ValueError):
        B.read_designs_from_xlsx(path)


# ---------------------------------------------------------------- 编号 → run 名

def test_run_name_is_safe_and_deterministic():
    assert B.run_name_for(7) == "sample_7"
    assert B.run_name_for("3200") == "sample_3200"
    assert B.run_name_for(7) == B.run_name_for(7)


def test_run_name_sanitises_unsafe_ids():
    """编号里带空格/点/中文时不能直接当目录名 —— 换成下划线，且仍要合法。"""
    assert B.run_name_for("S 07") == "sample_S_07"
    assert B.run_name_for("a.b") == "sample_a_b"
    assert B.run_name_for("样本1") == "sample___1"


# ---------------------------------------------------------------- 跨表去重

def test_collect_designs_merges_the_whole_folder(tmp_path, monkeypatch):
    _write_xlsx(tmp_path / "a.xlsx", HEADER, [_row(1), _row(2)])
    _write_xlsx(tmp_path / "b.xlsx", HEADER, [_row(3)])
    monkeypatch.setattr(B, "VARIABLE_DIR", tmp_path)

    designs, sources = B.collect_designs(None)
    assert [d["样本序号"] for d in designs] == [1, 2, 3]
    assert sorted(set(sources)) == ["a.xlsx", "b.xlsx"]


def test_cross_file_duplicate_ids_are_an_error(tmp_path, monkeypatch):
    """两份表出现同一个编号 → 会互相覆盖 run 目录，必须拦下。"""
    _write_xlsx(tmp_path / "a.xlsx", HEADER, [_row(1)])
    _write_xlsx(tmp_path / "b.xlsx", HEADER, [_row(1, c_mm=31.0)])
    monkeypatch.setattr(B, "VARIABLE_DIR", tmp_path)

    with pytest.raises(SystemExit, match="撞车"):
        B.collect_designs(None)


def test_empty_folder_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "VARIABLE_DIR", tmp_path)
    with pytest.raises(SystemExit, match="没有 .xlsx"):
        B.collect_designs(None)


# ---------------------------------------------------------------- 失败不停

def test_a_failed_sample_does_not_stop_the_batch(monkeypatch):
    """**失败就应该继续跑下一个** —— 这是定好的口径，不是可选项。

    回归：这里曾经写成「连续 3 次失败就停」。实测失败率 82% 时
    P(连续 3 次失败) ≈ 0.55，平均每 4~5 个样本就误停一次 ——
    等于把「这个设计点造不出来」当成了「环境坏了」，批量根本跑不完。
    """
    monkeypatch.setattr(B.fses, "probe_alive", lambda *a, **k: (True, "ok"))
    stop, why = B.should_stop_after_failure()
    assert stop is False, f"SolidWorks 还活着，就不该因为样本失败而停（{why}）"


def test_only_a_dead_solidworks_stops_the_batch(monkeypatch):
    monkeypatch.setattr(B.fses, "probe_alive", lambda *a, **k: (False, "MK_E_UNAVAILABLE"))
    stop, why = B.should_stop_after_failure()
    assert stop is True
    assert "MK_E_UNAVAILABLE" in why, "停下时要带上原因，方便判断"


def test_the_batch_has_no_consecutive_failure_breaker():
    """源码级守卫：别再把这个熔断加回来。"""
    source = (MAPPING / "scripts" / "Run-Batch.py").read_text(encoding="utf-8")
    assert "CONSECUTIVE_FAILURE_STOP" not in source


# ---------------------------------------------------------------- 真实数据（只验不变量）

def test_the_real_variable_table_reads_cleanly():
    """`Variables/` 里真放着的表必须能读通 —— 这是批量开跑的第一步。

    只断言**不变量**，不钉具体数值：这份表是别人放的，随时会换。
    """
    files = sorted(p for p in B.VARIABLE_DIR.glob("*.xlsx") if not p.name.startswith("~$"))
    if not files:
        pytest.skip("Variables/ 里没有表 —— 还没放数据")
    designs, sources = B.collect_designs(None)

    assert designs, "读出来一个样本都没有"
    ids = [d["样本序号"] for d in designs]
    assert len(ids) == len(set(ids)), "编号必须唯一"
    for design in designs:
        assert set(design) == {fg.ID_COLUMNS[0], *fg.DESIGN_COLUMNS}
        for name in fg.DESIGN_COLUMNS:
            assert isinstance(design[name], float), f"{name} 必须是 float"
    assert len(sources) == len(designs)
