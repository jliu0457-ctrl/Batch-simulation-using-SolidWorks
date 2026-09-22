#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理 `working/seven_variable_trials/` 下的历史 CAD 副本。

**默认只列出，什么都不删。** 确认清单没问题之后加 `--apply`。

用法::

    python scripts/Clean-RunDirs.py                     # 只列出（默认）
    python scripts/Clean-RunDirs.py --apply             # 真的删
    python scripts/Clean-RunDirs.py --apply --kind rangev     # 只删某一类
    python scripts/Clean-RunDirs.py --apply --older-than-min 60

清理前会把目录里的小报告搬进 `working/_sample_reports/`（和 `Run-Batch.py` 跑完
做的同一件事），再删整个目录。搬不到报告也照删 —— 这是**你主动要清历史**，
不是批量跑完的自动清理，两者判据不同（后者找不到主报告就不敢删）。

三类保护，命中任何一个都不删：
  * `flow_lids_013` —— 交接文档点名保留的参照数据
  * 名字在 `--protect` 里的
  * 最近 `--older-than-min` 分钟内动过的（默认 30）—— 防呆：别删掉正在跑的那个
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

MAPPING_ROOT = Path(__file__).resolve().parents[1]
RUNS_ROOT = MAPPING_ROOT / "working" / "seven_variable_trials"
REPORTS_DIR = MAPPING_ROOT / "working" / "_sample_reports"

#: 要留下的文件，和 `Run-Batch.py` 的 `KEEP_IN_RUN_DIR` 一致。
KEEP_IN_RUN_DIR = ("flow_sample.json", "training_sample.csv")

#: 绝不删 —— 交接文档点名保留的参照数据（开口识别的基准），且只有 5.8 MB。
PROTECTED = ("flow_lids_013",)


def classify(name: str) -> str:
    """按名字认来源。认不出来就归到「其它」，照样列出来给人看。"""
    if name.startswith("rangev"):
        return "范围验证"
    if name.startswith("sample_"):
        return "批量样本"
    if name.startswith(("alpha", "v_", "failpt", "probe_")):
        return "诊断扫描"
    if name.startswith("flow_"):
        return "早期样本"
    return "其它"


def dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def last_touch(path: Path) -> float:
    return max((f.stat().st_mtime for f in path.rglob("*") if f.is_file()),
               default=path.stat().st_mtime)


def salvage_reports(run_dir: Path) -> list[str]:
    """删之前把小报告搬到 `_sample_reports/`。返回搬走的文件名（可能为空）。"""
    moved = []
    for name in KEEP_IN_RUN_DIR:
        src = run_dir / name
        if src.is_file():
            REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            dest = REPORTS_DIR / f"{run_dir.name}.{name}"
            shutil.copy2(src, dest)
            moved.append(dest.name)
    return moved


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="清理 working/seven_variable_trials/ 下的历史 CAD 副本（默认只列出）")
    p.add_argument("--apply", action="store_true", help="真的删；不加就只列出")
    p.add_argument("--kind", default="", help="只处理这一类，如 范围验证 / 诊断扫描 / 批量样本")
    p.add_argument("--protect", default="", help="额外保护的名字，逗号分隔")
    p.add_argument("--older-than-min", type=float, default=30.0,
                   help="只清这么多分钟以前没动过的（默认 30）；**0 = 不限制**。"
                        "防呆用，别删掉正在跑的那个")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not RUNS_ROOT.is_dir():
        print(f"没有这个目录：{RUNS_ROOT}")
        return 0

    extra_protect = {s.strip() for s in args.protect.split(",") if s.strip()}
    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - args.older_than_min * 60

    rows = []
    for d in sorted(RUNS_ROOT.iterdir()):
        if not d.is_dir():
            continue
        kind = classify(d.name)
        touched = last_touch(d)
        if d.name in PROTECTED:
            why = "受保护（既定规则）"
        elif d.name in extra_protect:
            why = "受保护（--protect）"
        elif args.older_than_min > 0 and touched > cutoff:
            why = f"刚动过（{args.older_than_min:g} 分钟内）"
        elif args.kind and kind != args.kind:
            why = f"不在 --kind {args.kind} 里"
        else:
            why = ""
        rows.append({"dir": d, "name": d.name, "kind": kind,
                     "size_mb": dir_size(d) / 1048576, "skip": why,
                     "when": datetime.fromtimestamp(touched).strftime("%m-%d %H:%M")})

    rows.sort(key=lambda r: -r["size_mb"])
    total = sum(r["size_mb"] for r in rows)
    doomed = [r for r in rows if not r["skip"]]

    print(f"{RUNS_ROOT}")
    print(f"共 {len(rows)} 个目录，{total:.0f} MB\n")
    print(f"{'大小':>8}  {'最后改动':<12} {'类别':<10} {'目录名':<44} 处置")
    for r in rows:
        mark = r["skip"] or ("将删除" if args.apply else "可删")
        print(f"{r['size_mb']:7.1f}M  {r['when']:<12} {r['kind']:<10} {r['name']:<44} {mark}")

    print(f"\n可清理 {len(doomed)} 个，共 {sum(r['size_mb'] for r in doomed):.0f} MB"
          f"（占全部的 {sum(r['size_mb'] for r in doomed) / total * 100:.0f}%）")

    if not args.apply:
        print("\n（只列出，没删任何东西。确认清单后加 --apply）")
        return 0

    if not doomed:
        print("\n没有可清理的。")
        return 0

    print()
    for r in doomed:
        moved = salvage_reports(r["dir"])
        shutil.rmtree(r["dir"], ignore_errors=True)
        note = f"，报告留下：{', '.join(moved)}" if moved else ""
        print(f"  已删 {r['name']}（{r['size_mb']:.1f} MB）{note}")
    print(f"\n清理完毕：删了 {len(doomed)} 个，释放约 "
          f"{sum(r['size_mb'] for r in doomed):.0f} MB。报告在 {REPORTS_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
