#!/usr/bin/env python3
"""lab_exec.py — 在 lab 容器内执行一段脚本并留存证据（验证会工具）。

用法:
    python scripts/lab_exec.py <container> <evidence-name> < timeout-in-sec < script.sh
    python scripts/lab_exec.py ntbx-lab-ubuntu "01-ss" 60 < entry.sh

行为:
  - 从 stdin 读 bash 脚本，`docker exec -i <container> bash -s` 执行；
  - 超时默认 60s，超时按失败记录（杀掉进程组）；
  - 证据写 <repo>/../nettoolbox-data/docs/verify/<session>/evidence/<name>.txt
    （会话目录可用 --outdir 覆盖；证据仅进伴生仓，公开仓零真实回显）；
  - 证据文件含：时间戳 / 容器 / 退出码 / 脚本原文 / 完整回显。

单写者纪律：本脚本只读执行、只写证据文件，不碰 db；回填走单独流程。
"""
import argparse
import datetime
import os
import subprocess
import sys

DEFAULT_OUTDIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "..", "nettoolbox-data", "docs", "verify", "20261009-lab-session1", "evidence",
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("container", help="docker 容器名，如 ntbx-lab-ubuntu")
    ap.add_argument("name", help="证据文件名（不含扩展名），如 01-ss")
    ap.add_argument("--timeout", type=int, default=60, help="超时秒数（默认 60）")
    ap.add_argument("--outdir", default=os.path.normpath(DEFAULT_OUTDIR))
    args = ap.parse_args()

    script = sys.stdin.read()
    os.makedirs(args.outdir, exist_ok=True)
    path = os.path.join(args.outdir, f"{args.name}.txt")

    started = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        proc = subprocess.run(
            ["docker", "exec", "-i", args.container, "bash", "-s"],
            input=script.encode("utf-8"), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=args.timeout,
        )
        out = proc.stdout.decode("utf-8", errors="replace")
        code = proc.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", errors="replace")
        out += f"\n*** TIMEOUT after {args.timeout}s ***\n"
        code = 124

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# evidence: {args.name}\n# container: {args.container}\n")
        f.write(f"# started: {started}\n# exit_code: {code}\n")
        f.write("# " + "-" * 60 + "\n--- script ---\n" + script)
        f.write("# " + "-" * 60 + "\n--- output ---\n" + out)
    sys.stdout.write(out)
    print(f"\n[lab_exec] exit={code} evidence={path}")
    return 0 if code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
