#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读：量出 S3「开口识别」到底花多久，以及组件限定究竟省了多少。

背景：codex 在 §7.10 把 S3 从"枚举整套装配体的全部圆边"改成"只枚举
`入口管道` / `出口管道` / `03阀体`"（见 `enumerate_open_circular_edges`）。
原理上一定更少：改动前基线 448 条圆边、实测 25.7~48.1 秒。

但**少枚举 ≠ 少花时间**是可能的 —— 这一阶段的耗时如果由每次 COM 往返的延迟
主导，而不是由边数主导，那么裁掉组件不会带来相称的收益。这个探针就是为了
把这个分辨清楚，而不是靠推算。

做法：**同一份装配体、同一个 SolidWorks 会话**里交替跑 A/B 各两轮：
    A) 现在的实现（限定三个组件）
    B) 旧行为（不筛组件，全部走一遍）
分别记「秒数」和「返回的圆边条数」。同一会话内重复两轮还能顺带看出
这一阶段自身的抖动幅度。

用法::

    python scripts/Probe-S3Cost.py <runName>

只读：开装配体、读圆边、关文档，**不保存任何东西**。
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

MAPPING_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MAPPING_ROOT.parent
for _p in (str(REPO_ROOT), str(MAPPING_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import flow_geometry as fg   # noqa: E402
import flow_project as fpj   # noqa: E402
import flow_session as fses  # noqa: E402
import sw_api as swa         # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "run_flow_sample", MAPPING_ROOT / "scripts" / "Run-FlowSample.py")
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)


def unrestricted_edges(document) -> list[dict]:
    """旧行为：不筛组件，把每个组件的圆边都跨 COM 枚举一遍。

    刻意**逐行照抄** `R.enumerate_open_circular_edges` 的循环体，只去掉那个
    `continue` —— 否则测出来的差异里会混进"实现不同"而不是"筛没筛组件"。
    """
    rows = []
    for comp in swa.component_list(document):
        if not comp:
            continue
        name = str(swa.safe_get(comp, "Name2", default="") or "")
        try:
            t = swa.transform_array(swa.total_transform(comp))
        except Exception:  # noqa: BLE001
            continue
        for edge in swa.circular_edges(comp):
            rows.append({
                "component": name,
                "center_m": [R._r(v) for v in R.transform_point(t, edge["center_m"])],
                "normal": [R._r(v) for v in R.transform_vector(t, edge["normal"])],
                "radius_m": R._r(edge["radius_m"]),
                "vertices": 0 if edge["closed"] else 2,
            })
    return rows


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    run = Path(fses.RUNS_ROOT) / argv[0]
    assembly = next(p for p in run.glob("*.SLDASM") if not p.name.startswith("~$"))

    session = fses.SwSession.attach()
    try:
        fpj.open_assembly(session, assembly, "开度45°")
        # ⚠️ 几何枚举必须用**原生 ModelDoc**（`session.sw.ActiveDoc`）。
        # `open_assembly` 返回的不是它，拿它去 `component_list` 只会得到 0 个组件 ——
        # 而且**不报错**，表现为"边数 0、耗时 0.0s"，看起来像"这一阶段不要钱"。
        document = session.sw.ActiveDoc
        if document is None:
            raise RuntimeError("打开装配体之后拿不到原生 SolidWorks 文档")
        print(f"run        = {argv[0]}")
        print(f"组件数     = {len(swa.component_list(document))}")
        print()
        print(f"{'轮次':<5}{'版本':<10}{'耗时':>9}{'圆边条数':>10}")
        widest: list[dict] = []
        for round_no, order in ((1, ["A", "B"]), (2, ["B", "A"]), (3, ["A", "B"])):
            # ⚠️ 各轮**交替顺序**。每轮都让 A 先跑的话，A 会稳定背上"这一轮第一次
            # 几何枚举"的开销，测出来的差异里就混进了顺序效应，
            # 而不是组件筛选真正的收益。交替之后 A/B 各先跑一次。
            for which in order:
                fn = R.enumerate_open_circular_edges if which == "A" else unrestricted_edges
                t0 = time.perf_counter()
                rows = fn(document)
                dt = time.perf_counter() - t0
                label = "A 限定版" if which == "A" else "B 全量版"
                print(f"{round_no:<5}{label:<10}{dt:>8.1f}s{len(rows):>10}")
                if which == "B":
                    widest = rows
        comps = sorted({r["component"] for r in widest})
        print(f"\n全量版覆盖的组件（{len(comps)} 个）: {comps}")
    finally:
        try:
            session.close_all_documents()
        finally:
            session.unload()
    return 0


if __name__ == "__main__":
    sys.exit(main())
