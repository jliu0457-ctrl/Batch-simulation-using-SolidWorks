#!/usr/bin/env python3
"""检查 Flow Simulation 计算域收得够不够。

用法：
    python check_domain.py            # 默认读 .\\1
    python check_domain.py 1          # 指定项目目录
    python check_domain.py path/to/prj

只读 1.stdout，不改任何文件。改完计算域跑一次「仅生成网格」即可，几秒出结果。
"""
import re
import sys
from pathlib import Path

# 真实水道体积：入口管 2m + 阀 + 出口管 1m，内径 210mm
WATERWAY_M3 = 0.116


def read_text(path):
    """Flow Simulation 的日志常见「UTF-8 中混少量非法字节」，必须容错解码。"""
    data = path.read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    # 其余一律按 UTF-8 容错解；非法字节替换掉，ASCII 表头不受影响
    return data.decode("utf-8-sig", errors="replace")


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "1")
    log = root / "1.stdout"
    if not log.is_file():
        sys.exit(f"找不到 {log}")

    text = read_text(log)
    if text is None:
        sys.exit("日志编码无法识别")

    def grab(pattern, cast, default=None):
        m = re.search(pattern, text)
        return cast(m.group(1)) if m else default

    problem = grab(r"Problem type:\s*(\S+)", str, "?")
    res = grab(r"Result resolution:\s*(\d+)", int, 0)
    mingap = grab(r"Minimum gap size:\s*([\d.eE+-]+)", float)
    v_fluid = grab(r"GEOMSUBDOM \(0\):[^\n]*?V=([\d.eE+-]+)", float)
    cv_fluid = grab(r"GEOMSUBDOM \(0\):[^\n]*?cv=(\d+)", int)
    fw_s = grab(r"GEOMSUBDOM \(0\):fa_s=[\d.]+ fw_s=([\d.eE+-]+)", float)

    print(f"项目          {root}")
    print(f"分析类型      {problem}")
    print(f"分辨率级别    {res}")
    if mingap:
        print(f"最小间隙      {mingap:.6g} m")
    if v_fluid:
        print(f"流体域体积    {v_fluid:.4g} m3")
    if cv_fluid:
        print(f"流体单元数    {cv_fluid:,}")
    if v_fluid and cv_fluid:
        edge = (v_fluid / cv_fluid) ** (1 / 3) * 1000
        print(f"平均单元边长  {edge:.1f} mm")
        if mingap:
            print(f"间隙占几格    {mingap / (edge / 1000):.1f} 格")
    if fw_s:
        print(f"流体壁面积    {fw_s:.4g} m2")
    if v_fluid:
        print(f"水道占比      {WATERWAY_M3 / v_fluid * 100:.2f} %")

    print()
    print("=== 判定 ===")
    ok = True
    if v_fluid is None or fw_s is None:
        print("× 日志里没找到 GEOMSUBDOM 数据（可能是求解日志而非网格日志）")
        return
    if v_fluid < 0.25:
        print(f"√ 计算域体积 {v_fluid:.3g} m3 已收到位")
    elif v_fluid < 1.0:
        ok = False
        print(f"△ 计算域体积 {v_fluid:.3g} m3 还能再收，目标 0.1~0.2")
    else:
        ok = False
        print(f"× 计算域体积 {v_fluid:.3g} m3 仍然太大，管外死水还在算")
    if fw_s < 4:
        print(f"√ 流体壁面积 {fw_s:.3g} m2，流体基本不贴包围盒壁")
    else:
        ok = False
        print(f"× 流体壁面积 {fw_s:.3g} m2，流体仍贴着包围盒壁 → 还是外部流动")
    if cv_fluid and cv_fluid < 500:
        print(f"△ 单元数只有 {cv_fluid}，太少了，算不动阀口，需要提高分辨率")
    if fw_s and v_fluid:
        real_cells = cv_fluid * WATERWAY_M3 / v_fluid if cv_fluid else 0
        print(f"\n真实水道上的单元数约 {real_cells:,.0f} 个（占 {real_cells/cv_fluid*100:.1f}%）")
    print("\n结论：" + ("可以进入下一步" if ok else "先继续收计算域"))


if __name__ == "__main__":
    main()
