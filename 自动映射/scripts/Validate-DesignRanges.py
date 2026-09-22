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
的路径找出最近已验证可用值。若全上界失败，还会沿“基准值→全上界”做联合路径二分，随后在最近失败
点逐个把变量退回基准值，识别共同推高内部构造直径的变量。这些结果都是指定路径上的 CAD 边界，
不应误解为完整七维域证明。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
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
# C# 的 Marshal.GetActiveObject 在“SolidWorks 已启动但没有打开文档”时会取不到 ROT，
# 随后旧入口会错误 Activator.CreateInstance，拉起第二个 SLDWORKS.exe。这个参考零件
# 只用于建立已有实例的 COM 通道，名称不与 V6 六个核心零件冲突，也不参与参数化。
BOOTSTRAP_DOCUMENT = MAPPING_ROOT.parent / "封盖1.SLDPRT"

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
    for pair in data.get("paired_corner_tests") or []:
        if len(pair) != 2 or pair[0] not in FIELDS or pair[1] not in FIELDS or pair[0] == pair[1]:
            raise ValueError(f"paired_corner_tests 中的变量不合法：{pair!r}")
    for triple in data.get("triple_corner_tests") or []:
        if len(triple) != 3 or any(field not in FIELDS for field in triple) or len(set(triple)) != 3:
            raise ValueError(f"triple_corner_tests 中的变量不合法：{triple!r}")
    return data


def design_key(design: dict[str, float]) -> str:
    """设计点的规范化指纹，用于判断“这个点是不是已经测过”。"""
    return json.dumps({name: float(design[name]) for name in FIELDS}, sort_keys=True)


def load_prior_results(sources: list[str]) -> tuple[dict[tuple[str, str], dict], list[str], list[str]]:
    """读旧会话的用例，按 ``(标签, 设计点)`` 建索引，供本轮跳过已判定的点。

    键必须**同时**含标签和设计点：只按标签跳会漏掉“标签没变但设计点改了”的用例
    （例如改了某项下界之后，同名端点的意义已经不同）。

    只继承 ``pass`` / ``fail``。``inconclusive`` 是 COM/崩溃/超时这类环境问题，
    不是几何证据 —— 继承它等于把上一次的意外算成本次的结论。
    """
    index: dict[tuple[str, str], dict] = {}
    sessions: list[str] = []
    not_inherited: list[str] = []
    for raw in sources:
        path = Path(raw)
        if path.is_dir():
            path = path / "range_validation_summary.json"
        if not path.is_file():
            path = SESSIONS_ROOT / raw / "range_validation_summary.json"
        if not path.is_file():
            raise FileNotFoundError(f"找不到可续跑的会话汇总：{raw}")
        data = json.loads(path.read_text(encoding="utf-8"))
        session = str(data.get("session") or path.parent.name)
        sessions.append(session)
        for case in data.get("cases") or []:
            if case.get("outcome") not in ("pass", "fail"):
                not_inherited.append(f"{session}/{case.get('tag')}")
                continue
            index[(str(case.get("tag")), design_key(case.get("design") or {}))] = {
                "case": case,
                "session": session,
            }
    return index, sessions, not_inherited


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
        self.prior: dict[tuple[str, str], dict] = {}
        self.prior_sessions: list[str] = []
        self.prior_not_inherited: list[str] = []
        self.inherited: list[dict[str, Any]] = []
        if args.resume_from:
            self.prior, self.prior_sessions, self.prior_not_inherited = load_prior_results(args.resume_from)
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
            "resumed_from": list(self.prior_sessions),
            "prior_inconclusive_not_inherited": list(self.prior_not_inherited),
            "inherited_cases": [],
            "cases": [],
            "axis_results": {},
            "interaction_corner_results": [],
            "triple_corner_results": [],
            "conditional_interaction_boundaries": [],
            "global_corner_results": [],
            "global_upper_path_boundary": None,
            "global_upper_leave_one_results": [],
            "status": "planned",
        }
        self._bootstrap_app: Any | None = None
        self._bootstrap_title: str | None = None

    def save(self) -> None:
        self.summary["updated_utc"] = utc_now()
        json_dump(self.summary_path, self.summary)

    @staticmethod
    def _com_value(obj: Any, name: str) -> Any:
        """pywin32 下同一成员可能是属性或普通 Python 可调用对象。"""
        value = getattr(obj, name)
        return value() if callable(value) else value

    def bootstrap_existing_solidworks(self) -> dict[str, Any]:
        """附加用户手动开的空白实例并打开一个无冲突参考零件。

        这里刻意只用 ``GetActiveObject``，绝不 ``Dispatch`` / ``CreateObject``；附加失败
        就停止，不能为“尝试修复”而启动第二个 SolidWorks。参考零件会在本会话结束时关闭。
        """
        if not BOOTSTRAP_DOCUMENT.is_file():
            return {"ok": False, "error": f"找不到 COM 引导参考件：{BOOTSTRAP_DOCUMENT}"}
        try:
            import pythoncom
            import win32com.client as win32
            pythoncom.CoInitialize()
            # SolidWorks can take a few seconds to publish its ROT entry after the window is
            # visibly usable. Retrying GetActiveObject is harmless; crucially, we never fall
            # back to Dispatch/CreateObject, so this loop cannot create another instance.
            app = None
            last_error: Exception | None = None
            for _ in range(10):
                try:
                    app = win32.GetActiveObject("SldWorks.Application")
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    time.sleep(2)
            if app is None:
                raise RuntimeError(f"GetActiveObject 连续 10 次失败：{last_error}")
            active = app.ActiveDoc
            if active is not None:
                title = str(self._com_value(active, "GetTitle"))
                return {"ok": False, "error": f"SolidWorks 不是空白主界面，当前打开：{title}"}
            opened = app.OpenDoc6(str(BOOTSTRAP_DOCUMENT), 1, 1, "", 0, 0)
            if isinstance(opened, tuple):
                document = opened[0] if opened else None
                errors = int(opened[1]) if len(opened) > 1 and opened[1] is not None else 0
                warnings = int(opened[2]) if len(opened) > 2 and opened[2] is not None else 0
            else:
                document, errors, warnings = opened, 0, 0
            if document is None:
                return {"ok": False, "error": f"无法打开 COM 引导参考件，errors={errors}, warnings={warnings}"}
            title = str(self._com_value(document, "GetTitle"))
            self._bootstrap_app, self._bootstrap_title = app, title
            return {
                "ok": True,
                "method": "pywin32 GetActiveObject + OpenDoc6",
                "document": str(BOOTSTRAP_DOCUMENT),
                "title": title,
                "open_errors": errors,
                "open_warnings": warnings,
            }
        except Exception as exc:  # noqa: BLE001 - a COM attach failure is a hard preflight failure
            return {"ok": False, "error": f"无法附加到已启动的 SolidWorks：{type(exc).__name__}: {exc}"}

    def release_bootstrap_document(self) -> None:
        """只关闭本验证器自己打开的参考零件，绝不关闭用户文档或 CAD 副本。"""
        if self._bootstrap_app is None or not self._bootstrap_title:
            return
        result: dict[str, Any] = {"title": self._bootstrap_title}
        try:
            self._bootstrap_app.CloseDoc(self._bootstrap_title)
            result["closed"] = True
        except Exception as exc:  # noqa: BLE001
            result["closed"] = False
            result["error"] = f"{type(exc).__name__}: {exc}"
        self.summary["bootstrap_release"] = result
        self._bootstrap_app, self._bootstrap_title = None, None

    def plan(self):
        """按执行顺序产出全部**直接**用例（二分是嵌套的，不在这里）。

        预告和实际执行共用这一处 —— 两处各写一份的话，跳过集就会对不上真实序列。
        """
        yield {"tag": "baseline", "design": dict(self.baseline), "purpose": "baseline"}
        for field in FIELDS:
            for side in ("lower", "upper"):
                design = dict(self.baseline)
                design[field] = float(self.ranges[field][side])
                yield {"tag": f"axis_{field}_{side}", "design": design,
                       "purpose": "axis_endpoint", "field": field, "side": side}
        if not self.args.no_interaction_corners:
            for first, second in self.config.get("paired_corner_tests", []):
                for first_side in ("lower", "upper"):
                    for second_side in ("lower", "upper"):
                        design = dict(self.baseline)
                        design[first] = float(self.ranges[first][first_side])
                        design[second] = float(self.ranges[second][second_side])
                        yield {"tag": f"corner_{first}_{first_side}_{second}_{second_side}",
                               "design": design, "purpose": "paired_corner",
                               "pair": (first, first_side, second, second_side)}
        if not self.args.no_triple_corners:
            for first, second, third in self.config.get("triple_corner_tests", []):
                for first_side in ("lower", "upper"):
                    for second_side in ("lower", "upper"):
                        for third_side in ("lower", "upper"):
                            design = dict(self.baseline)
                            sides = {first: first_side, second: second_side, third: third_side}
                            for field, side in sides.items():
                                design[field] = float(self.ranges[field][side])
                            yield {
                                "tag": f"triple_{first}_{first_side}_{second}_{second_side}_{third}_{third_side}",
                                "design": design,
                                "purpose": "triple_corner",
                                "triple": (first, first_side, second, second_side, third, third_side),
                            }
        if self.config.get("global_corner_tests", True) and not self.args.no_global_corners:
            for side in ("lower", "upper"):
                yield {"tag": f"global_{side}",
                       "design": {field: float(self.ranges[field][side]) for field in FIELDS},
                       "purpose": "global_corner", "side": side}

    def planned_cases(self) -> list[dict[str, Any]]:
        return [dict(spec) for spec in self.plan()]

    def will_inherit(self, tag: str, design: dict[str, float], purpose: str) -> dict | None:
        """该用例这一轮会不会被跳过；返回旧记录或 None。

        预告和实际执行都走这一个判据。两边各写一份的话，一旦漂了，方向恰恰是最坏的那种：
        预告说"实跑"、实际复用 —— 没人会发现有个用例根本没跑。

        基线哨兵不复用：它证明的是"此刻这台 SolidWorks 能跑通整条 CAD 链路"，
        继承它等于把上一次的好运气当成本次的环境检查，那后面的通过就都不可信了。
        """
        if purpose == "baseline":
            return None
        return self.prior.get((tag, design_key(design)))

    def _run_name(self, tag: str) -> str:
        self.counter += 1
        clean = re.sub(r"[^A-Za-z0-9_-]", "_", tag)
        # Session stamp + counter make names unique, while keeping them below Run-OneDesign's limit.
        result = f"{self.session}_{self.counter:03d}_{clean}"
        if not SAFE_RUN.fullmatch(result):
            raise ValueError(f"内部生成的 run 名不安全：{result}")
        return result

    def cleanup_run_directory(self, run_dir: Path) -> dict[str, Any]:
        """删除本验证器刚创建的一次性 CAD 副本；报告和输入文件保留。

        删除前必须证明目标是 ``seven_variable_trials`` 的直接子目录，避免任何路径计算错误
        波及母版、工作区或历史目录。SolidWorks/Run-OneDesign 已退出后才调用；若文件锁尚未
        释放则短暂重试，并把失败原因写进会话汇总，不静默遗留。
        """
        resolved = run_dir.resolve()
        parent = RUNS_ROOT.resolve()
        result: dict[str, Any] = {"path": str(resolved), "removed": False}
        if resolved.parent != parent or not SAFE_RUN.fullmatch(resolved.name):
            result["error"] = "refused_unsafe_cleanup_target"
            return result
        if not resolved.exists():
            result["removed"] = True
            result["already_absent"] = True
            return result
        last_error: Exception | None = None
        for _ in range(5):
            try:
                shutil.rmtree(resolved)
                result["removed"] = True
                return result
            except OSError as exc:
                last_error = exc
                time.sleep(1)
        result["error"] = f"{type(last_error).__name__}: {last_error}"
        return result

    def run_case(self, tag: str, design: dict[str, float], *, purpose: str, field: str | None = None,
                 side: str | None = None, parent: str | None = None) -> dict[str, Any]:
        """执行单个隔离 CAD 副本；失败副本保留，判据只读 adapter 报告字段。"""
        prior = self.will_inherit(tag, design, purpose)
        if prior is not None:
            old = prior["case"]
            case = {
                **old,
                "purpose": purpose,
                "field": field,
                "side": side,
                "parent": parent,
                "inherited_from": old.get("run"),
                "inherited_session": prior["session"],
                "inherited_elapsed_seconds": old.get("elapsed_seconds"),
                # 本轮没有真的跑它，所以本轮耗时记 0 —— 不能让继承的秒数虚增外层的总计。
                "elapsed_seconds": 0.0,
            }
            self.summary["cases"].append(case)
            self.inherited.append({"tag": tag, "outcome": case.get("outcome"),
                                   "session": prior["session"], "run": old.get("run")})
            self.summary["inherited_cases"] = list(self.inherited)
            self.save()
            print(f"[{len(self.summary['cases'])}] {tag}: {case.get('outcome')} "
                  f"(继承 {prior['session']}，未重跑)", flush=True)
            return case
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
        if self.args.keep_run_folders:
            case["run_directory_cleanup"] = {"path": str(run_dir.resolve()), "removed": False,
                                             "reason": "--keep-run-folders"}
        else:
            case["run_directory_cleanup"] = self.cleanup_run_directory(run_dir)
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
        for spec in self.plan():
            if spec["purpose"] == "axis_endpoint":
                field, side = spec["field"], spec["side"]
                target = float(spec["design"][field])
                self.summary["axis_results"].setdefault(field, {})
                case = self.run_case(
                    spec["tag"], spec["design"],
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
        for spec in self.plan():
            if spec["purpose"] != "paired_corner":
                continue
            first, first_side, second, second_side = spec["pair"]
            design = spec["design"]
            case = self.run_case(spec["tag"], design, purpose="paired_corner", parent=None)
            self.summary["interaction_corner_results"].append({
                "fields": [first, second],
                "sides": {first: first_side, second: second_side},
                "values": {first: design[first], second: design[second]},
                "outcome": case["outcome"],
                "run": case["run"],
                "error": case.get("error"),
            })
            self.save()
            # 两个端点各自单独通过、合起来却失败时，沿两个方向各找一条条件一维边界。
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

    def run_triple_corners(self) -> None:
        """验证定向三变量八角点；失败时分别寻找三个条件一维边界。"""
        if self.args.no_triple_corners:
            return
        for spec in self.plan():
            if spec["purpose"] != "triple_corner":
                continue
            raw = spec["triple"]
            fields = [raw[0], raw[2], raw[4]]
            sides = {raw[0]: raw[1], raw[2]: raw[3], raw[4]: raw[5]}
            design = spec["design"]
            case = self.run_case(spec["tag"], design, purpose="triple_corner")
            self.summary["triple_corner_results"].append({
                "fields": fields,
                "sides": sides,
                "values": {field: design[field] for field in fields},
                "outcome": case["outcome"],
                "run": case["run"],
                "error": case.get("error"),
            })
            self.save()
            if case["outcome"] != "fail":
                continue
            # 对每个变量，从“另外两个仍固定在本角点、该变量回到基准”开始，向失败角点二分。
            # 只有对应单变量端点已通过时才有资格做这条条件边界；否则单变量二分已给出更基础的边界。
            for field in fields:
                if self.summary["axis_results"][field][sides[field]]["outcome"] != "pass":
                    continue
                anchor = dict(design)
                anchor[field] = self.baseline[field]
                anchor_tag = f"triple_anchor_{spec['tag']}_{field}"
                anchor_case = self.run_case(
                    anchor_tag, anchor, purpose="triple_conditional_anchor",
                    field=field, side=sides[field], parent=case["run"],
                )
                if anchor_case["outcome"] != "pass":
                    self.summary["conditional_interaction_boundaries"].append({
                        "field": field,
                        "side": sides[field],
                        "anchor_design": anchor,
                        "stopped_reason": "conditional_anchor_not_pass",
                        "anchor_run": anchor_case["run"],
                        "parent": case["run"],
                    })
                    self.save()
                    continue
                self.summary["conditional_interaction_boundaries"].append(self.bisect_endpoint(
                    field=field,
                    side=sides[field],
                    anchor=anchor,
                    failed_value=design[field],
                    purpose="triple_conditional_bisection",
                    parent=case["run"],
                ))
                self.save()

    def run_global_corners(self) -> None:
        if self.args.no_global_corners or not self.config.get("global_corner_tests", True):
            return
        for spec in self.plan():
            if spec["purpose"] != "global_corner":
                continue
            case = self.run_case(spec["tag"], spec["design"], purpose="global_corner")
            self.summary["global_corner_results"].append({
                "side": spec["side"],
                "design": spec["design"],
                "outcome": case["outcome"],
                "run": case["run"],
                "error": case.get("error"),
            })
            self.save()

    def design_on_upper_path(self, t: float) -> dict[str, float]:
        """返回基准点到七变量全上界连线上的设计点；``t`` 为无量纲路径比例。"""
        if not 0.0 <= t <= 1.0:
            raise ValueError(f"全上界路径比例必须在 0..1 内，实际为 {t}")
        return {
            field: self.baseline[field]
            + t * (float(self.ranges[field]["upper"]) - self.baseline[field])
            for field in FIELDS
        }

    def run_global_upper_path_boundary(self) -> None:
        """全上界失败时定位联合边界，并在失败侧做逐变量退回基准值测试。

        这里不能复用单变量 ``bisect_endpoint``：路径参数 ``t`` 同时改变七个变量，单位也不是
        mm/deg。二分只在已验证通过的基准点与已验证失败的全上界之间进行；遇到 inconclusive
        立即停止，避免把 COM、崩溃或超时误当成几何边界。
        """
        if self.args.no_global_boundary or self.args.no_global_corners \
                or not self.config.get("global_corner_tests", True):
            return

        upper_rows = [row for row in self.summary["global_corner_results"]
                      if row.get("side") == "upper"]
        if not upper_rows:
            self.summary["global_upper_path_boundary"] = {
                "status": "not_run", "reason": "global_upper_result_missing"
            }
            self.save()
            return
        upper = upper_rows[-1]
        if upper.get("outcome") != "fail":
            self.summary["global_upper_path_boundary"] = {
                "status": "not_needed" if upper.get("outcome") == "pass" else "not_run",
                "reason": f"global_upper_outcome={upper.get('outcome')}",
            }
            self.save()
            return

        success_t, failure_t = 0.0, 1.0
        attempts: list[str] = []
        stopped_reason = "tolerance_reached"
        for step in range(1, self.args.max_path_bisect_steps + 1):
            if failure_t - success_t <= self.args.tolerance_t:
                break
            midpoint = (success_t + failure_t) / 2.0
            case = self.run_case(
                f"global_upper_path_bisect_{step:02d}",
                self.design_on_upper_path(midpoint),
                purpose="global_upper_path_bisection",
                side="upper",
                parent=upper.get("run"),
            )
            attempts.append(case["run"])
            if case["outcome"] == "pass":
                success_t = midpoint
            elif case["outcome"] == "fail":
                failure_t = midpoint
            else:
                stopped_reason = "inconclusive_case"
                break
        else:
            stopped_reason = "max_steps_reached"

        boundary = {
            "status": "completed" if stopped_reason != "inconclusive_case" else "inconclusive",
            "anchor": "baseline",
            "target": "global_upper",
            "nearest_verified_t": success_t,
            "nearest_failed_t": failure_t,
            "nearest_verified_design": self.design_on_upper_path(success_t),
            "nearest_failed_design": self.design_on_upper_path(failure_t),
            "tolerance_t": self.args.tolerance_t,
            "attempt_runs": attempts,
            "stopped_reason": stopped_reason,
            "interpretation": "仅表示基准点到全上界连线上的 CAD 边界，不是完整七维可行域。",
        }
        self.summary["global_upper_path_boundary"] = boundary
        self.save()

        # 只有完成了几何二分，才在最近失败点做驱动因素筛查。每次仅将一个变量退回基准值。
        if boundary["status"] != "completed":
            return
        failed_design = boundary["nearest_failed_design"]
        results: list[dict[str, Any]] = []
        for field in FIELDS:
            design = dict(failed_design)
            design[field] = self.baseline[field]
            case = self.run_case(
                f"global_upper_leave_one_{field}", design,
                purpose="global_upper_leave_one",
                field=field,
                side="upper",
                parent=upper.get("run"),
            )
            results.append({
                "field_returned_to_baseline": field,
                "baseline_value": self.baseline[field],
                "design": design,
                "outcome": case["outcome"],
                "run": case["run"],
                "internal_s_mm": case.get("internal_s_mm"),
                "error": case.get("error"),
                "interpretation": (
                    "pass 表示把该变量单独退回基准值足以从最近失败点恢复；"
                    "它是联合约束的驱动因素之一，不代表只需收窄这一个变量。"
                ),
            })
            self.summary["global_upper_leave_one_results"] = list(results)
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
        self.summary["bootstrap"] = self.bootstrap_existing_solidworks()
        if not self.summary["bootstrap"]["ok"]:
            self.summary["status"] = "blocked_by_solidworks_attach"
            self.summary["finished_utc"] = utc_now()
            self.save()
            print("无法安全附加到用户已打开的 SolidWorks，已停止；不会冷启动第二个实例。", file=sys.stderr)
            return 4
        self.summary["status"] = "running"
        self.save()
        try:
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
            self.run_triple_corners()
            self.run_global_corners()
            self.run_global_upper_path_boundary()
            outcomes = [case["outcome"] for case in self.summary["cases"]]
            self.summary["status"] = "completed" if "inconclusive" not in outcomes else "completed_with_inconclusive_cases"
            self.summary["finished_utc"] = utc_now()
            self.save()
            print(f"\n完成。汇总：{self.summary_path}")
            return 0
        except Exception as exc:
            self.summary["status"] = "aborted_unexpected"
            self.summary["fatal_error"] = f"{type(exc).__name__}: {exc}"
            self.summary["finished_utc"] = utc_now()
            self.save()
            raise
        finally:
            self.release_bootstrap_document()
            # SolidWorks may release the last component handles only after the bootstrap document
            # is closed. Retry any per-case cleanup that lost the first race with WinError 32.
            if not self.args.keep_run_folders:
                for case in self.summary["cases"]:
                    cleanup = case.get("run_directory_cleanup") or {}
                    if cleanup.get("removed") is False and case.get("run_directory"):
                        case["run_directory_cleanup"] = self.cleanup_run_directory(
                            Path(case["run_directory"])
                        )
            self.save()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="验证七变量 CAD 范围；失败端点自动二分")
    p.add_argument("--config", type=Path, default=CONFIG_PATH, help="范围配置 JSON")
    p.add_argument("--execute", action="store_true", help="实际调用 SolidWorks；默认只打印计划")
    p.add_argument("--prefix", default="rangev1", help="生成的 run 名和会话目录前缀（ASCII，最长 18 字符）")
    p.add_argument("--timeout-sec", type=int, default=720, help="单个 CAD 参数化最大等待秒数")
    p.add_argument("--max-bisect-steps", type=int, default=12, help="一个失败端点最多二分次数")
    p.add_argument("--tolerance-mm", type=float, default=0.05, help="长度变量二分停止精度（mm）")
    p.add_argument("--tolerance-deg", type=float, default=0.05, help="角度变量二分停止精度（deg）")
    p.add_argument("--no-interaction-corners", action="store_true", help="不测两组二变量联合角点")
    p.add_argument("--no-triple-corners", action="store_true", help="不测配置中的定向三变量八角点")
    p.add_argument("--no-global-corners", action="store_true", help="不测全变量下限/上限两个组合")
    p.add_argument("--no-global-boundary", action="store_true",
                   help="全上界失败时不做基准→全上界路径二分及逐变量退回测试")
    p.add_argument("--max-path-bisect-steps", type=int, default=12,
                   help="全上界联合路径最多二分次数")
    p.add_argument("--tolerance-t", type=float, default=0.02,
                   help="全上界联合路径参数 t 的二分停止精度")
    p.add_argument("--resume-from", action="append", default=[], metavar="SESSION",
                   help="复用旧会话里已判定的用例（可重复）。取值可以是会话名、会话目录、"
                        "或 range_validation_summary.json 的路径。按「用例标签 + 设计点」匹配，"
                        "所以改了范围之后同名但设计点变了的端点会重跑；baseline 永远实跑。")
    p.add_argument("--keep-run-folders", action="store_true",
                   help="保留每个用例的 CAD 副本；默认在报告落盘后删除一次性副本")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,17}", args.prefix):
        raise SystemExit("--prefix 只能是 1～18 位 ASCII 字母/数字/_/-，且必须以字母开头")
    if args.timeout_sec <= 0 or args.max_bisect_steps <= 0 or args.max_path_bisect_steps <= 0:
        raise SystemExit("timeout 和 max-bisect-steps 必须为正数")
    if args.tolerance_mm <= 0 or args.tolerance_deg <= 0 or not 0 < args.tolerance_t < 1:
        raise SystemExit("二分精度必须为正数")
    config = load_config(args.config)
    validator = RangeValidator(config, args)
    if not args.execute:
        print("只读预检：不会复制 CAD、不会连接 SolidWorks。")
        print(f"配置：{args.config.resolve()}")
        specs = validator.planned_cases()
        if validator.prior_sessions:
            print(f"续跑自：{', '.join(validator.prior_sessions)}")
            for one in validator.prior_not_inherited:
                print(f"  （{one} 不是 pass/fail，按证据不足处理，不继承）")
        live = 0
        for spec in specs:
            prior = validator.will_inherit(spec["tag"], spec["design"], spec["purpose"])
            if prior is None:
                live += 1
                print(f"  [实跑] {spec['tag']}")
            else:
                print(f"  [复用] {spec['tag']}  ← {prior['session']} 判为 "
                      f"{prior['case'].get('outcome')}（run={prior['case'].get('run')}）")
        print(f"共 {len(specs)} 个直接 CAD 测试：实跑 {live} 个，复用 {len(specs) - live} 个。")
        print(f"若实跑的端点失败，会在该端点额外执行最多 {args.max_bisect_steps} 次一维二分。")
        if not args.no_global_boundary and not args.no_global_corners:
            print("若全上界判为失败，还会执行联合路径二分，并在最近失败点做 7 次逐变量退回测试。")
        return 0
    return validator.execute()


if __name__ == "__main__":
    raise SystemExit(main())
