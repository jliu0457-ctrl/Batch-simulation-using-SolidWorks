#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SolidWorks / Flow 会话：只附加、门禁、文档生命周期。

除清理已确认的 ``NVOGLDC invisible`` 孤儿进程和残留 ``~$`` 锁文件外，本模块不写
CAD/Flow 数据；正常或状态不明的 SolidWorks 进程绝不结束。

两条铁律（踩过坑，见交接文档 §10）：

  1. **不冷启动 SolidWorks。** 必须由人先手工启动、停在空白主界面，脚本只附加；
     附不上就退出并报告。没有已运行实例时创建 SolidWorks 会
     `CO_E_SERVER_EXEC_FAILURE (0x80080005)` 紧接着 `0x00000003` 崩溃。
     （`Run-FlowSingle.py:415` 的 `RunProduct2` 属于自启动分支，本模块不复用。）

  2. **同一时刻只允许一个 SolidWorks 实例、一个装配体窗口。**
     `flow_transfer.find_sldworks_pid()` 取 tasklist 的第一行 —— 有两个实例时会绑错，
     所以这里要求恰好一个，否则直接报错。

门禁分两段：``SwSession.gate_or_raise()`` 是不需要打开文档就能查的；
打开装配体之后还要调 ``assert_no_flow_project()`` —— 模板里残留着悬空的
Flow 工程注册（`assembly_batch_v6\\1` 被登记但目录不存在），那是弹模态框的引信。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

MAPPING_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MAPPING_ROOT.parent
sys.path.insert(0, str(REPO_ROOT))

MANIFEST_PATH = MAPPING_ROOT / "config" / "cad_template_manifest_v6.json"
TEMPLATE_DIR = MAPPING_ROOT / "working" / "assembly_batch_v6"
RUNS_ROOT = (MAPPING_ROOT / "working" / "seven_variable_trials").resolve()

REQUIRED_REVISION_PREFIX = "34."


class GateFailure(RuntimeError):
    """门禁不过。调用方必须就此停止，不得继续写入。"""


class ModalDialogBlocked(RuntimeError):
    """疑似被模态对话框卡住。调用方应告诉用户手动关掉后 --resume。"""


# ---------------------------------------------------------------- 进程与哈希

@dataclass(frozen=True)
class SolidWorksProcess:
    """`tasklist /V` 能可靠取得的 SolidWorks 进程信息。"""

    pid: int
    window_title: str = ""
    status: str = ""


def list_solidworks_processes() -> list[SolidWorksProcess]:
    """所有 SLDWORKS.exe 进程；保留窗口标题以识别已知的无界面残留。"""
    try:
        out = subprocess.run(["tasklist", "/V", "/FI", "IMAGENAME eq SLDWORKS.exe",
                              "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=30, errors="replace")
    except Exception as exc:  # noqa: BLE001
        raise GateFailure(f"tasklist 调用失败：{exc}") from exc
    if out.returncode:
        raise GateFailure(f"tasklist 调用失败（{out.returncode}）：{out.stderr or out.stdout}")
    processes: list[SolidWorksProcess] = []
    for parts in csv.reader(io.StringIO(out.stdout)):
        if len(parts) < 2 or parts[0].strip().lower() != "sldworks.exe":
            continue
        try:
            pid = int(parts[1].strip())
        except ValueError:
            continue
        processes.append(SolidWorksProcess(
            pid=pid,
            window_title=parts[-1].strip() if len(parts) >= 9 else "",
            status=parts[5].strip() if len(parts) >= 6 else "",
        ))
    return processes


def list_solidworks_pids() -> list[int]:
    """所有 SLDWORKS.exe 的 PID。**不用 flow_transfer 那一套「取第一行」。**"""
    return [p.pid for p in list_solidworks_processes()]


def cleanup_empty_solidworks_processes() -> dict:
    """结束能确定为 ``NVOGLDC invisible`` 的无界面残留进程。

    空标题也可能只是正在启动，不能据此强杀。结束前会重新核对 PID 和标题，防止 PID
    复用或状态变化；正常窗口和状态不明确的进程一律不碰。
    """
    marker = "nvogldc invisible"
    before = list_solidworks_processes()
    candidates = [p for p in before if p.window_title.casefold() == marker]
    removed: list[int] = []
    failed: list[dict[str, Any]] = []
    for candidate in candidates:
        current = {p.pid: p for p in list_solidworks_processes()}.get(candidate.pid)
        if current is None or current.window_title.casefold() != marker:
            failed.append({"pid": candidate.pid, "reason": "结束前复核时进程已消失或标题已变化"})
            continue
        result = subprocess.run(["taskkill", "/PID", str(candidate.pid), "/T", "/F"],
                                capture_output=True, text=True, timeout=30, errors="replace")
        if result.returncode == 0:
            removed.append(candidate.pid)
        else:
            failed.append({"pid": candidate.pid,
                           "reason": (result.stderr or result.stdout).strip()})
    after = list_solidworks_processes()
    return {
        "marker": "NVOGLDC invisible",
        "candidates": [p.pid for p in candidates],
        "removed": removed,
        "failed": failed,
        "remaining": [p.pid for p in after],
        "ambiguous_not_touched": [p.pid for p in before
                                  if p.window_title.casefold() != marker],
        "ok": not failed,
    }


def solver_running() -> bool:
    """EFDsolver.exe 是否在跑。求解阶段靠它判「求解器已退出」。"""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq EFDsolver.exe", "/NH"],
                             capture_output=True, text=True, timeout=30, errors="replace")
    except Exception:  # noqa: BLE001
        return False
    return "efdsolver.exe" in out.stdout.lower()


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def lock_files_under(folder: Path) -> list[Path]:
    """`~$*` 残留锁文件。崩溃或异常退出会留下它们。

    适配器里写的是 ``Directory.GetFiles(folder, "*.SLDASM").Single()``，
    匹配到两个就抛「序列包含一个以上的元素」—— 表现成适配器内部报错，看不出是文件问题。
    """
    return sorted(p for p in folder.rglob("~$*") if p.is_file())


def verify_template_manifest(manifest_path: Path = MANIFEST_PATH,
                             template_dir: Path = TEMPLATE_DIR) -> dict:
    """模板逐字节核对。跑完任何一环都要核一次 —— 靠它抓「什么时候被谁动了」。

    判据只有两条：清单里登记的文件都在、且哈希相符。
    **「多出来的文件」只报告、不判失败** —— 清单故意只登记 7 个 CAD 文件，
    而目录里的 `…_project_folders.html` 是 Flow 写的索引文件，不是 CAD 数据。
    真正要拦的是「装配体里还注册着 Flow 工程」，那要打开文档读
    `GetProjectNames()` 才知道，见 ``SwSession.assert_no_flow_project``。
    """
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    expected = manifest["files"]
    problems, checked = [], {}
    for name, want in expected.items():
        target = Path(template_dir) / name
        if not target.is_file():
            problems.append(f"模板缺文件 {name}")
            continue
        got = sha256_of(target)
        checked[name] = got
        if got != want:
            problems.append(f"{name} 哈希不符：期望 {want[:16]}… 实际 {got[:16]}…")
    extra = sorted(p.name for p in Path(template_dir).iterdir()
                   if p.is_file() and p.name not in expected)
    return {
        "template_id": manifest.get("template_id"),
        "checked": checked,
        "extra_files": extra,
        "extra_files_note": "未登记文件仅供参照；_project_folders.html 是 Flow 索引，"
                            "它出现说明装配体里可能还挂着工程注册 —— 以打开后的 "
                            "GetProjectNames() 为准",
        "problems": problems,
        "ok": not problems,
    }


# ---------------------------------------------------------------- 会话

@dataclass
class SwSession:
    """一个已附加的 SolidWorks + Flow API 会话。"""

    sw: Any
    api: Any
    interactive: Any
    pid: int | None = None
    residual_cleanup: dict[str, Any] = field(default_factory=dict)
    _closed: bool = field(default=False, repr=False)

    # -- 附加 --------------------------------------------------------

    @classmethod
    def attach(cls, *, retries: int = 20, delay: float = 3.0) -> "SwSession":
        import pythoncom
        import win32com.client as win32

        pythoncom.CoInitialize()

        cleanup = cleanup_empty_solidworks_processes()
        pids = list_solidworks_pids()
        if not pids:
            raise GateFailure("没有运行中的 SolidWorks。请先手工启动并停在空白主界面 —— "
                              "本流程不冷启动 SolidWorks。")
        if len(pids) > 1:
            raise GateFailure(
                f"检测到 {len(pids)} 个 SLDWORKS.exe 进程 {pids} —— 无法判定该附加哪一个。"
                "请只保留一个实例后重试。")

        sw = None
        last = None
        for _ in range(retries):
            try:
                sw = win32.GetActiveObject("SldWorks.Application")
                break
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(delay)
        if sw is None:
            raise GateFailure(
                f"无法附加到 SolidWorks（试了 {retries} 次）：{last}\n"
                "  `MK_E_UNAVAILABLE` 不一定代表没开 —— 也可能是当前权限看不到交互式桌面的"
                "COM 运行对象表；请在与桌面同一会话的权限下运行。")

        # flow_transfer.connect() 失败时直接 sys.exit()，不会解 COM —— 这里包一层。
        import flow_transfer as ft
        try:
            api = ft.connect()
        except SystemExit as exc:  # noqa: PERF203
            raise GateFailure(f"Flow 产品 API 加载失败（flow_transfer.connect 退出码 {exc.code}）") from exc
        interactive = ft.safe_get(api, "Attach2RunningObject")
        if interactive is None:
            try:
                api.UnloadProductAPI()
            except Exception:  # noqa: BLE001
                pass
            raise GateFailure("Flow API 无法附加到运行中的 SolidWorks 实例。")

        return cls(sw=sw, api=api, interactive=interactive, pid=pids[0],
                   residual_cleanup=cleanup)

    # -- 门禁（不需要打开文档的部分）------------------------------

    def gate_or_raise(self, *, run: Path | None = None,
                      template_dir: Path = TEMPLATE_DIR,
                      manifest_path: Path = MANIFEST_PATH) -> dict:
        """写任何东西之前必须过。只读，不启动任何东西。"""
        report: dict[str, Any] = {}
        problems: list[str] = []
        report["residual_process_cleanup"] = self.residual_cleanup

        revision = str(self.sw.RevisionNumber())
        report["revision"] = revision
        if not revision.startswith(REQUIRED_REVISION_PREFIX):
            problems.append(f"SolidWorks 版本 {revision}，期望 {REQUIRED_REVISION_PREFIX}x")

        visible = bool(self.sw.Visible)
        report["visible"] = visible
        if not visible:
            problems.append("SolidWorks 不可见（Visible=False）")

        active = self.sw.ActiveDoc
        report["active_doc"] = None if active is None else str(active.GetTitle)
        if active is not None:
            problems.append(f"有已打开的文档 {report['active_doc']!r} —— 门禁要求全部关闭")

        report["solidworks_pids"] = list_solidworks_pids()
        if len(report["solidworks_pids"]) != 1:
            problems.append(f"SLDWORKS.exe 实例数 {len(report['solidworks_pids'])}，期望恰好 1")

        if run is not None:
            run = Path(run).resolve()
            if RUNS_ROOT != run and RUNS_ROOT not in run.parents:
                problems.append(f"run 目录 {run} 不在 {RUNS_ROOT} 之下")
            report["run"] = str(run)
            # `<run>/1` 是 Flow 的工程目录。上一轮残留时**只记录、不判否** ——
            # 重建工程那条路（create_project）自己会先清掉它；
            # 在这里拦死会逼人手工删目录，而那正是要避免的手工步骤。
            project_dir = run / "1"
            report["pre_existing_project_dir"] = project_dir.is_dir()
            if project_dir.is_dir():
                report["pre_existing_project_dir_note"] = (
                    "已存在；重建工程时会先清掉。若本轮不重建工程而它又是残留，"
                    "S8 读 xmlconfig 可能读到旧配置。")
            # 残留的 `~$*` 锁文件：适配器的 `Single()` 会被它们搞炸
            # （「序列包含一个以上的元素」），但它们**只是簿记文件、不含数据**，
            # 交接文档已明确「可删除」。SolidWorks 异常退出/重启后会留下它们。
            # 既然上面已经确认没有文档打开，这里的锁就是残留的 —— 直接清掉并记录，
            # 而不是拦死让人手工删。
            locks = lock_files_under(run)
            report["lock_files_present"] = [str(p) for p in locks]
            if locks and active is None:
                removed = []
                for path in locks:
                    try:
                        path.unlink()
                        removed.append(path.name)
                    except Exception as exc:  # noqa: BLE001
                        problems.append(f"残留锁文件 {path.name} 删不掉：{type(exc).__name__}: {exc}")
                report["lock_files_removed"] = removed
                report["lock_files_note"] = "门禁时发现并清理的残留锁文件（簿记文件，不含数据）"
            elif locks:
                problems.append(f"发现锁文件 {[p.name for p in locks]}，但此刻有文档打开 —— "
                                "可能是别人正在用，不动它")

        manifest = verify_template_manifest(manifest_path, template_dir)
        report["template_manifest"] = manifest
        if not manifest["ok"]:
            problems.extend(manifest["problems"])

        report["problems"] = problems
        report["ok"] = not problems
        if problems:
            raise GateFailure("门禁不通过：\n  " + "\n  ".join(problems))
        return report

    # -- 门禁（打开文档之后）--------------------------------------

    def assert_no_flow_project(self, configuration, *, remove: bool = False,
                               keep_under: Path | None = None) -> dict:
        """模板里残留着悬空的 Flow 工程注册（`assembly_batch_v6\\1` 登记了但目录不存在）。

        它的边界条件引用的是四个封盖的面；一旦几何变了引用就解析不了，
        下一次重建会弹「面<1>@封盖1<1> 未在固体和流体区域之间的边界上」的模态框，
        之后所有 COM 调用全被堵住。

        remove=True 时在**副本**上删掉它（绝不在母版上做）。

        ⚠️ **`keep_under` 不能省。** 不加区分地全删会连**我们自己存进去的工程**一起删掉：
        停在求解前那一次已经把工程保存进装配体了，求解那一次开文档后本该直接激活它，
        删掉之后 S6 只能重建 —— 实测白花 ~20 秒，还要重写 9 个特征。
        判据用**工程目录落在哪**：在本 run 目录下的 = 我们自己存的，保留；
        指向 `assembly_batch_v6\\1` 之类外面的 = 继承来的悬空注册，删掉（那才是要防的）。
        """
        names = [str(n) for n in (configuration.GetProjectNames() or [])]
        report: dict[str, Any] = {"projects_before": names}
        keep: list[str] = []
        if remove and keep_under is not None:
            root = Path(keep_under).resolve()
            for name in names:
                if self._project_directory(configuration, name, root) is not None:
                    keep.append(name)
        if remove:
            removed, failed = [], []
            for name in names:
                if name in keep:
                    continue
                try:
                    (removed if configuration.RemoveProject(name) else failed).append(name)
                except Exception as exc:  # noqa: BLE001
                    failed.append(f"{name} ({type(exc).__name__}: {exc})")
            report["removed"] = removed
            report["kept"] = keep
            report["remove_failed"] = failed
            names = [str(n) for n in (configuration.GetProjectNames() or [])]
        report["projects_after"] = names
        if report.get("remove_failed"):
            # 交接文档 §7.2 记录过 RemoveProject 偶发 RPC_E_DISCONNECTED —— 失败即停，不硬闯。
            raise GateFailure(f"删除继承的 Flow 工程失败：{report['remove_failed']}")
        leftover = [n for n in names if n not in keep]
        if leftover:
            raise GateFailure(
                f"副本里仍挂着 Flow 工程 {leftover} —— 必须先清掉，"
                "否则重建时会弹模态框把后续所有 COM 调用堵死")
        report["ok"] = True
        return report

    @staticmethod
    def _project_directory(configuration, name: str, keep_under: Path) -> Path | None:
        """工程的落盘目录 —— **只有落在 `keep_under` 之下才返回它**，否则 None。

        没有直接读目录的 API，只能先 `ActivateProject` 再问 `ProjectFiles`。
        激活失败 / 目录字段为空 / 目录在别处 → 一律当「不是我们的」，交给调用方删掉。
        """
        try:
            project = configuration.ActivateProject(name, False)
        except Exception:  # noqa: BLE001
            return None
        if project is None:
            return None
        try:
            raw = str(getattr(project.ProjectFiles, "ProjectDirectory", "") or "")
        except Exception:  # noqa: BLE001
            return None
        if not raw:
            return None
        try:
            directory = Path(raw).resolve()
        except Exception:  # noqa: BLE001
            return None
        return directory if keep_under in directory.parents else None

    # -- 生命周期 ---------------------------------------------------

    def close_all_documents(self, *, limit: int = 30) -> int:
        """关掉全部文档。只关自己打开的 —— 调用方负责保证进来时没有用户的文档。"""
        closed = 0
        for _ in range(limit):
            doc = self.sw.ActiveDoc
            if doc is None:
                break
            try:
                self.sw.CloseDoc(doc.GetTitle)
                closed += 1
            except Exception:  # noqa: BLE001
                break
        return closed

    def unload(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.api.UnloadProductAPI()
        except Exception:  # noqa: BLE001
            pass
        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "SwSession":
        return self

    def __exit__(self, *exc) -> None:
        self.unload()


# ---------------------------------------------------------------- 廉价探活

def probe_alive(session: SwSession | None = None, *, timeout_s: float = 8.0) -> tuple[bool, str]:
    """模态对话框会堵住 COM 调用。调用前后各探一次，卡住就能及早发现。

    ⚠️ **必须在子线程里自己 `CoInitialize` 并重新 `GetActiveObject`。**
    COM 是单元线程的：把主线程拿到的对象直接丢给另一个线程调用，必定报
    `尚未调用 CoInitialize`（实测踩过）—— 那会被当成"SolidWorks 无响应"，
    于是**每一次都误判成模态框挡住**，比不检查还糟。

    返回 `(是否活着, 说明)`。超时、异常、非 34.x 都算不活。

    `session` 参数**不参与实现**（函数自己在子线程里重新 `GetActiveObject`），
    保留只是为了 `guarded()` 的调用签名稳定；批处理这类没有现成 session 的调用方
    可以直接 `probe_alive()`。
    """
    import threading
    result: dict[str, Any] = {"ok": False, "why": "未知"}

    def _call():
        import pythoncom
        import win32com.client as win32
        pythoncom.CoInitialize()
        try:
            sw = win32.GetActiveObject("SldWorks.Application")
            revision = str(sw.RevisionNumber())
            result["revision"] = revision
            result["ok"] = revision.startswith(REQUIRED_REVISION_PREFIX)
            result["why"] = "ok" if result["ok"] else f"版本 {revision}"
        except Exception as exc:  # noqa: BLE001
            result["why"] = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass

    thread = threading.Thread(target=_call, daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        return False, f"探活在 {timeout_s:.0f} 秒内没返回（COM 调用被堵住）"
    return bool(result["ok"]), str(result.get("why", "未知"))


def guarded(action, session: SwSession, *, step: str):
    """执行一个可能弹模态的动作：前探活、执行、后探活。

    卡住时抛 ``ModalDialogBlocked`` 并指明步骤 —— 让用户手动关掉对话框后 ``--resume``。
    **绝不重试**可能已经弹出模态的调用。
    """
    alive, why = probe_alive(session)
    if not alive:
        raise ModalDialogBlocked(f"{step}：调用前 SolidWorks 就无响应（{why}）")
    value = action()
    alive, why = probe_alive(session)
    if not alive:
        raise ModalDialogBlocked(
            f"{step}：调用后 SolidWorks 无响应（{why}）—— 很可能弹出了模态对话框。"
            "请手动关掉它，然后 --resume 从本步继续。")
    return value
