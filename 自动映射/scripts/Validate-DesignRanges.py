#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在隔离 V6 副本上验证七变量的 CAD 可用范围，并在端点失败时二分逼近边界。

此脚本只调用 ``Run-OneDesign.exe``，因此它验证的是：写入七变量 → 重建 → Dmax
反解 → 保存 → 重开 → 几何回读 → 装配特征检查。它不会创建封盖、Flow 项目、网格或
训练数据行。所有成功和失败的 CAD 副本都会保留，便于复查；绝不修改 V6 母版。

用法::

    python scripts/Validate-DesignRanges.py                 # 只打印试验计划
    python scripts/Validate-DesignRanges.py --execute       # 真的运行（SolidWorks 须手工开在空白页）

端点按 "其余六个变量保持基准值" 验证。另会执行 `phi/alpha`、`Dmax/alpha` 联合角点
及全低/全高组合，避免把变量各自能取到误判为组合也能取到。若某一端点失败，二分法沿“基准值→该端点”
的路径找出最近已验证可用值；该结果是这一路径上的 CAD 边界，不应误解为完整七维域证明。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAPPING_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = MAPPING_ROOT / "scripts"
RUNS_ROOT = MAPPING_ROOT / "working" / "seven_variable_trials"
SESSIONS_ROOT = MAPPING_ROOT / "working" / "_range_validation"
ANALYSIS_ROOT = MAPPING_ROOT / "_analysis"
ONE_DESIGN_EXE = SCRIPTS / "Run-OneDesign.exe"
CONFIG_PATH = MAPPING_ROOT / "config" / "range_validation_v1.json"
MANIFEST_PATH = MAPPING_ROOT / "config" / "cad_template_manifest_v6.json"

FIELDS = ("c_mm", "e_mm", "phi_deg", "alpha_deg", "Dmax_mm", "bm_mm", "ds_mm")
SAFE_RUN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def session_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    baseline = data.get("baseline")
    ranges = data.get("ranges")
    if set(baseline or {}) != set(FIELDS) or set(ranges or {}) != set(FIELDS):
        raise ValueError("配置必须恰好包含七个基准值和七个变量范围")
    for field in FIELDS:
        lo = float(ranges[field]["lower"])
        hi = float(ranges[field]["upper"])
        value = float(baseline[field])
        if not lo < hi:
            raise ValueError(f"{field} 的范围无效：{lo}..{hi}")
        if not lo <= value <= hi:
            raise ValueError(f"{field} 的基准值 {value} 不在范围 {lo}..{hi} 内")
    return data


def json_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def tail(text: str, limit: int = 1400) -> str:
    text = text.strip()
    return text[-limit:] if len(text) > limit else text


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_template_manifest() -> dict[str, Any]:
    """阻止范围扫描在被意外改动过的 V6 母版上运行。

    ``Run-OneDesign.exe`` 的职责是复制和参数化，不检查母版哈希；这里补上该门禁，
    并只允许 manifest 声明的一套 1 个装配体 + 6 个外部零件。
    """
    if not MANIFEST_PATH.is_file():
        return {"ok": False, "error": f"缺少母版清单：{MANIFEST_PATH}"}
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    template = MAPPING_ROOT / "working" / "assembly_batch_v6"
    expected = manifest.get("files") or {}
    mismatches: list[dict[str, str]] = []
    for name, wanted_hash in expected.items():
        path = template / name
        if not path.is_file():
            mismatches.append({"file": name, "reason": "missing"})
            continue
        actual_hash = sha256_file(path)
        if actual_hash.lower() != str(wanted_hash).lower():
            mismatches.append({"file": name, "reason": "sha256_mismatch", "actual": actual_hash,
                               "expected": str(wanted_hash)})
    assemblies = sorted(path.name for path in template.glob("*.SLDASM"))
    parts = sorted(path.name for path in template.glob("*.SLDPRT"))
    expected_assemblies = int(manifest.get("expected_assemblies", 1))
    expected_parts = int(manifest.get("expected_external_parts", 6))
    if len(assemblies) != expected_assemblies:
        mismatches.append({"file": "*.SLDASM", "reason": f"count={len(assemblies)}, expected={expected_assemblies}"})
    if len(parts) != expected_parts:
        mismatches.append({"file": "*.SLDPRT", "reason": f"count={len(parts)}, expected={expected_parts}"})
    return {
        "ok": not mismatches,
        "manifest": str(MANIFEST_PATH),
        "template": str(template),
        "expected_files": sorted(expected),
        "actual_assemblies": assemblies,
        "actual_parts": parts,
        "mismatches": mismatches,
    }


class RangeValidator:
    def __init__(self, config: dict[str, Any], args: argparse.Namespace) -> None:
        self.config = config
        self.args = args
        self.baseline = {field: float(config["baseline"][field]) for field in FIELDS}
        self.ranges = config["ranges"]
        self.session = f"{args.prefix}_{session_stamp()}"
        self.session_dir = SESSIONS_ROOT / self.session
        self.inputs_dir = self.session_dir / "inputs"
        self.summary_path = self.session_dir / "range_validation_summary.json"
        self.counter = 0
        self.summary: dict[str, Any] = {
            "schema_version": 1,
            "purpose": "V6 CAD-only range validation; no Flow project, mesh, solver, or training row.",
            "session": self.session,
            "started_utc": utc_now(),
            "config": str(args.config.resolve()),
            "template": str(MAPPING_ROOT / "working" / "assembly_batch_v6"),
            "baseline": self.baseline,
            "requested_ranges": self.ranges,
            "acceptance": "Run-OneDesign report has completed=true and physical_mapping_verified=true",
            "cases": [],
            "axis_results": {},
            "interaction_corner_results": [],
            "conditional_interaction_boundaries": [],
            "global_corner_results": [],
            "status": "planned",
        }

    def save(self) -> None:
        self.summary["updated_utc"] = utc_now()
        json_dump(self.summary_path, self.summary)

    def planned_cases(self) -> list[str]:
        out = ["baseline"]
        for field in FIELDS:
            out.extend((f"{field}:lower", f"{field}:upper"))
        if not self.args.no_interaction_corners:
            for first, second in self.config.get("paired_corner_tests", []):
                for first_side in ("lower", "upper"):
                    for second_side in ("lower", "upper"):
                        out.append(f"{first}:{first_side} + {second}:{second_side}")
        if self.config.get("global_corner_tests", True) and not self.args.no_global_corners:
            out.extend(("all_variables:lower", "all_variables:upper"))
        return out

    def _run_name(self, tag: str) -> str:
        self.counter += 1
        clean = re.sub(r"[^A-Za-z0-9_-]", "_", tag)
        # Session stamp + counter make names unique, while keeping them below Run-OneDesign's limit.
        result = f"{self.session}_{self.counter:03d}_{clean}"
        if not SAFE_RUN.fullmatch(result):
            raise ValueError(f"内部生成的 run 名不安全：{result}")
        return result

    def run_case(self, tag: str, design: dict[str, float], *, purpose: str, field: str | None = None,
                 side: str | None = None, parent: str | None = None) -> dict[str, Any]:
        """执行单个隔离 CAD 副本；失败副本保留，判据只读 adapter 报告字段。"""
        run = self._run_name(tag)
        run_dir = RUNS_ROOT / run
        input_path = self.inputs_dir / f"{run}.json"
        json_dump(input_path, {name: float(design[name]) for name in FIELDS})
        report_path = ANALYSIS_ROOT / f"e2e_{run}.json"
        started = utc_now()
        began = time.monotonic()
        command = [str(ONE_DESIGN_EXE), ".", run, str(input_path)]
        try:
            proc = subprocess.run(
                command,
                cwd=MAPPING_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.args.timeout_sec,
            )
            returncode = proc.returncode
            stdout_tail = tail(proc.stdout)
            stderr_tail = tail(proc.stderr)
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            returncode = None
            stdout_tail = tail((exc.stdout or "").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""))
            stderr_tail = tail((exc.stderr or "").decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or ""))
            timed_out = True

        report: dict[str, Any] = {}
        report_read_error = None
        if report_path.is_file():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - preserve an inspectable outcome
                report_read_error = repr(exc)

        completed = report.get("completed") is True
        verified = report.get("physical_mapping_verified") is True
        if completed and verified:
            outcome = "pass"
        elif report:
            outcome = "fail"
        else:
            # A missing report generally means COM attach, crash, modal dialog, or timeout;
            # it is not geometric evidence and must not steer bisection.
            outcome = "inconclusive"

        case = {
            "tag": tag,
            "purpose": purpose,
            "field": field,
            "side": side,
            "parent": parent,
            "run": run,
            "run_directory": str(run_dir),
            "input_file": str(input_path),
            "design": {name: float(design[name]) for name in FIELDS},
            "started_utc": started,
            "finished_utc": utc_now(),
            "elapsed_seconds": round(time.monotonic() - began, 3),
            "command": command,
            "process_returncode": returncode,
            "timed_out": timed_out,
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
            "report_file": str(report_path),
            "report_present": bool(report),
            "report_read_error": report_read_error,
            "outcome": outcome,
            "completed": completed,
            "physical_mapping_verified": verified,
            "internal_s_mm": report.get("internal_s_mm"),
            "error": report.get("error"),
        }
        self.summary["cases"].append(case)
        self.save()
        print(
            f"[{len(self.summary['cases'])}] {tag}: {outcome} "
            f"({case['elapsed_seconds']:.1f}s)  run={run}",
            flush=True,
        )
        return case

    def bisect_endpoint(self, *, field: str, side: str, anchor: dict[str, float], failed_value: float,
                        purpose: str, parent: str) -> dict[str, Any]:
        """从已验证 anchor 到失败端点做一维二分；遇到非几何性不确定结果立即停。"""
        success_value = float(anchor[field])
        failure_value = float(failed_value)
        tolerance = self.args.tolerance_deg if field.endswith("_deg") else self.args.tolerance_mm
        attempts: list[str] = []
        stopped_reason = "tolerance_reached"
        for step in range(1, self.args.max_bisect_steps + 1):
            if abs(failure_value - success_value) <= tolerance:
                break
            midpoint = (success_value + failure_value) / 2.0
            design = dict(anchor)
            design[field] = midpoint
            case = self.run_case(
                f"bisect_{field}_{side}_{step:02d}", design,
                purpose=purpose, field=field, side=side, parent=parent,
            )
            attempts.append(case["run"])
            if case["outcome"] == "pass":
                success_value = midpoint
            elif case["outcome"] == "fail":
                failure_value = midpoint
            else:
                stopped_reason = "inconclusive_case"
                break
        else:
            stopped_reason = "max_steps_reached"
        return {
            "field": field,
            "side": side,
            "anchor_design": anchor,
            "nearest_verified_value": success_value,
            "nearest_failed_value": failure_value,
            "tolerance": tolerance,
            "attempt_runs": attempts,
            "stopped_reason": stopped_reason,
            "interpretation": "一维路径结果：其余变量固定为 anchor_design；不是完整七维可行域证明。",
        }

    def run_axis_tests(self) -> None:
        for field in FIELDS:
            self.summary["axis_results"][field] = {}
            for side, key in (("lower", "lower"), ("upper", "upper")):
                design = dict(self.baseline)
                target = float(self.ranges[field][key])
                design[field] = target
                case = self.run_case(
                    f"axis_{field}_{side}", design,
                    purpose="axis_endpoint", field=field, side=side,
                )
                endpoint = {
                    "requested_value": target,
                    "outcome": case["outcome"],
                    "run": case["run"],
                    "internal_s_mm": case.get("internal_s_mm"),
                    "error": case.get("error"),
                }
                if case["outcome"] == "fail":
                    endpoint["bisection"] = self.bisect_endpoint(
                        field=field,
                        side=side,
                        anchor=dict(self.baseline),
                        failed_value=target,
                        purpose="axis_endpoint_bisection",
                        parent=case["run"],
                    )
                self.summary["axis_results"][field][side] = endpoint
                self.save()

    def run_interaction_corners(self) -> None:
        """验证两两端点组合；失败后给出条件一维边界，而非捏造二维矩形结论。"""
        if self.args.no_interaction_corners:
            return
        for first, second in self.config.get("paired_corner_tests", []):
            if first not in FIELDS or second not in FIELDS or first == second:
                raise ValueError(f"paired_corner_tests 中的变量不合法：{first!r}, {second!r}")
            for first_side in ("lower", "upper"):
                for second_side in ("lower", "upper"):
                    design = dict(self.baseline)
                    design[first] = float(self.ranges[first][first_side])
                    design[second] = float(self.ranges[second][second_side])
                    tag = f"corner_{first}_{first_side}_{second}_{second_side}"
                    case = self.run_case(tag, design, purpose="paired_corner", parent=None)
                    result = {
                        "fields": [first, second],
                        "sides": {first: first_side, second: second_side},
                        "values": {first: design[first], second: design[second]},
                        "outcome": case["outcome"],
                        "run": case["run"],
                        "error": case.get("error"),
                    }
                    self.summary["interaction_corner_results"].append(result)
                    self.save()
                    # If both isolated endpoints pass yet their combination fails, find
                    # conditional one-dimensional limits in each direction.
                    if case["outcome"] == "fail":
                        first_axis = self.summary["axis_results"][first][first_side]["outcome"]
                        second_axis = self.summary["axis_results"][second][second_side]["outcome"]
                        if second_axis == "pass":
                            anchor = dict(self.baseline)
                            anchor[second] = design[second]
                            self.summary["conditional_interaction_boundaries"].append(self.bisect_endpoint(
                                field=first, side=first_side, anchor=anchor,
                                failed_value=design[first], purpose="conditional_interaction_bisection",
                                parent=case["run"],
                            ))
                        if first_axis == "pass":
                            anchor = dict(self.baseline)
                            anchor[first] = design[first]
                            self.summary["conditional_interaction_boundaries"].append(self.bisect_endpoint(
                                field=second, side=second_side, anchor=anchor,
                                failed_value=design[second], purpose="conditional_interaction_bisection",
                                parent=case["run"],
                            ))
                        self.save()

    def run_global_corners(self) -> None:
        if self.args.no_global_corners or not self.config.get("global_corner_tests", True):
            return
        for side in ("lower", "upper"):
            design = {field: float(self.ranges[field][side]) for field in FIELDS}
            case = self.run_case(f"global_{side}", design, purpose="global_corner")
            self.summary["global_corner_results"].append({
                "side": side,
                "design": design,
                "outcome": case["outcome"],
                "run": case["run"],
                "error": case.get("error"),
            })
            self.save()

    def execute(self) -> int:
        if not ONE_DESIGN_EXE.is_file():
            raise FileNotFoundError(f"找不到 {ONE_DESIGN_EXE}")
        if not (MAPPING_ROOT / "working" / "assembly_batch_v6").is_dir():
            raise FileNotFoundError("找不到 V6 只读母版 working/assembly_batch_v6")
        self.session_dir.mkdir(parents=True, exist_ok=False)
        self.summary["template_manifest"] = verify_template_manifest()
        if not self.summary["template_manifest"]["ok"]:
            self.summary["status"] = "blocked_by_template_manifest"
            self.summary["finished_utc"] = utc_now()
            self.save()
            print("V6 母版与冻结清单不一致，已停止；不会拿未知母版做范围试验。", file=sys.stderr)
            return 3
        self.summary["status"] = "running"
        self.save()

        baseline_case = self.run_case("baseline", self.baseline, purpose="baseline")
        self.summary["baseline_result"] = {
            "outcome": baseline_case["outcome"],
            "run": baseline_case["run"],
            "error": baseline_case.get("error"),
        }
        if baseline_case["outcome"] != "pass":
            self.summary["status"] = "blocked_by_baseline"
            self.summary["finished_utc"] = utc_now()
            self.save()
            print("基准设计未通过，已停止；不会把环境/连接错误当作范围失败。", file=sys.stderr)
            return 2

        self.run_axis_tests()
        self.run_interaction_corners()
        self.run_global_corners()
        outcomes = [case["outcome"] for case in self.summary["cases"]]
        self.summary["status"] = "completed" if "inconclusive" not in outcomes else "completed_with_inconclusive_cases"
        self.summary["finished_utc"] = utc_now()
        self.save()
        print(f"\n完成。汇总：{self.summary_path}")
        return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="验证七变量 CAD 范围；失败端点自动二分")
    p.add_argument("--config", type=Path, default=CONFIG_PATH, help="范围配置 JSON")
    p.add_argument("--execute", action="store_true", help="实际调用 SolidWorks；默认只打印计划")
    p.add_argument("--prefix", default="rangev1", help="生成的 run 名和会话目录前缀（ASCII，最长 18 字符）")
    p.add_argument("--timeout-sec", type=int, default=720, help="单个 CAD 参数化最大等待秒数")
    p.add_argument("--max-bisect-steps", type=int, default=12, help="一个失败端点最多二分次数")
    p.add_argument("--tolerance-mm", type=float, default=0.01, help="长度变量二分停止精度（mm）")
    p.add_argument("--tolerance-deg", type=float, default=0.01, help="角度变量二分停止精度（deg）")
    p.add_argument("--no-interaction-corners", action="store_true", help="不测两组二变量联合角点")
    p.add_argument("--no-global-corners", action="store_true", help="不测全变量下限/上限两个组合")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,17}", args.prefix):
        raise SystemExit("--prefix 只能是 1～18 位 ASCII 字母/数字/_/-，且必须以字母开头")
    if args.timeout_sec <= 0 or args.max_bisect_steps <= 0:
        raise SystemExit("timeout 和 max-bisect-steps 必须为正数")
    if args.tolerance_mm <= 0 or args.tolerance_deg <= 0:
        raise SystemExit("二分精度必须为正数")
    config = load_config(args.config)
    validator = RangeValidator(config, args)
    if not args.execute:
        print("只读预检：不会复制 CAD、不会连接 SolidWorks。")
        print(f"配置：{args.config.resolve()}")
        print(f"计划执行 {len(validator.planned_cases())} 个直接 CAD 测试：")
        for item in validator.planned_cases():
            print(f"  - {item}")
        print("若端点失败，会在该端点额外执行最多 12 次一维二分。")
        return 0
    return validator.execute()


if __name__ == "__main__":
    raise SystemExit(main())
