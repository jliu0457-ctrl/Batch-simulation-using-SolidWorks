#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`Clean-RunDirs.py` 里**不需要 SolidWorks** 的部分：分类、保护、以及最要紧的一条 ——
**默认一个字节都不许删。**

这个工具是唯一会主动 rmtree 历史目录的东西，所以它的失败模式只有一种：
不该删的删了。测试全朝着这个方向写。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

MAPPING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MAPPING / "scripts"))
sys.path.insert(0, str(MAPPING))


def _load_cleaner():
    """脚本名带连字符，不是合法标识符，只能用 importlib 装。"""
    path = MAPPING / "scripts" / "Clean-RunDirs.py"
    spec = importlib.util.spec_from_file_location("Clean_RunDirs", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["Clean_RunDirs"] = module
    spec.loader.exec_module(module)
    return module


C = _load_cleaner()


def _make_run(root: Path, name: str, *, report: bool = False) -> Path:
    d = root / name
    (d / "1").mkdir(parents=True)
    (d / "8“D94R3Y-CL600C-11蝶板.SLDPRT").write_bytes(b"x" * 4096)
    (d / "1" / "1.fbd").write_bytes(b"y" * 8192)
    if report:
        (d / "flow_sample.json").write_text('{"status":"completed"}', encoding="utf-8")
        (d / "training_sample.csv").write_text("a,b\n", encoding="utf-8")
    return d


def _wire(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    reports = tmp_path / "_sample_reports"
    runs.mkdir(exist_ok=True)
    monkeypatch.setattr(C, "RUNS_ROOT", runs)
    monkeypatch.setattr(C, "REPORTS_DIR", reports)
    return runs, reports


# ---------------------------------------------------------------- 分类

@pytest.mark.parametrize("name,expected", [
    ("rangev1e_20260922T065312Z_001_baseline", "范围验证"),
    ("sample_7", "批量样本"),
    ("alpha35p5", "诊断扫描"),
    ("v_c_mm_32p2846674925624", "诊断扫描"),
    ("failpt", "诊断扫描"),
    ("flow_lids_013", "早期样本"),
    ("完全看不懂的名字", "其它"),
])
def test_classify(name, expected):
    assert C.classify(name) == expected


# ---------------------------------------------------------------- 默认不删

def test_list_only_deletes_nothing(tmp_path, monkeypatch, capsys):
    """**这条是这个工具最重要的一条测试。** 不带 --apply 时一个目录都不许少。"""
    runs, _ = _wire(tmp_path, monkeypatch)
    _make_run(runs, "sample_1")
    _make_run(runs, "rangev1e_x_001_baseline")

    assert C.main([]) == 0

    assert sorted(p.name for p in runs.iterdir()) == ["rangev1e_x_001_baseline", "sample_1"]
    assert "只列出" in capsys.readouterr().out


# ---------------------------------------------------------------- 保护

def test_flow_lids_013_is_never_deleted(tmp_path, monkeypatch):
    """交接文档点名保留的参照数据 —— 加 --apply 也不许动它。"""
    runs, _ = _wire(tmp_path, monkeypatch)
    _make_run(runs, "flow_lids_013")

    C.main(["--apply", "--older-than-min", "0"])

    assert (runs / "flow_lids_013").is_dir()


def test_extra_protect_wins(tmp_path, monkeypatch):
    runs, _ = _wire(tmp_path, monkeypatch)
    _make_run(runs, "sample_9")

    C.main(["--apply", "--older-than-min", "0", "--protect", "sample_9"])

    assert (runs / "sample_9").is_dir()


def test_recently_touched_is_left_alone(tmp_path, monkeypatch):
    """防呆：刚动过的目录不动 —— 别把正在跑的那个删了。"""
    runs, _ = _wire(tmp_path, monkeypatch)
    _make_run(runs, "sample_5")

    C.main(["--apply", "--older-than-min", "30"])

    assert (runs / "sample_5").is_dir(), "刚建的目录在 30 分钟保护窗内"


def test_kind_filter_leaves_other_kinds_alone(tmp_path, monkeypatch):
    runs, _ = _wire(tmp_path, monkeypatch)
    _make_run(runs, "sample_1")
    _make_run(runs, "alpha20")

    C.main(["--apply", "--older-than-min", "0", "--kind", "诊断扫描"])

    assert not (runs / "alpha20").exists()
    assert (runs / "sample_1").is_dir(), "不在 --kind 里的类别不许动"


# ---------------------------------------------------------------- 真删

def test_apply_deletes_and_salvages_reports(tmp_path, monkeypatch):
    runs, reports = _wire(tmp_path, monkeypatch)
    _make_run(runs, "sample_1", report=True)

    C.main(["--apply", "--older-than-min", "0"])

    assert not (runs / "sample_1").exists()
    assert (reports / "sample_1.flow_sample.json").is_file()
    assert (reports / "sample_1.training_sample.csv").is_file()


def test_a_dir_without_reports_is_still_deleted(tmp_path, monkeypatch):
    """和历史不一样：这里是**用户主动要清**，不是批量跑完的自动清理。

    自动清理找不到主报告就不敢删（怕删完什么都没剩）；这个工具是用户看着清单点的，
    验证器留下的副本本来就没有 `flow_sample.json`，照删。
    """
    runs, _ = _wire(tmp_path, monkeypatch)
    _make_run(runs, "rangev1c_x_001_baseline", report=False)

    C.main(["--apply", "--older-than-min", "0"])

    assert not (runs / "rangev1c_x_001_baseline").exists()


def test_missing_runs_root_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "RUNS_ROOT", tmp_path / "根本没有")
    assert C.main([]) == 0
