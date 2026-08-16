"""3台(studio/mini/asus)の変換ワーカーを、常に最新DNで動く状態に揃える。

ユーザーの鉄則: 「使う前にDNのバージョンを確認。古ければ更新。時代遅れで回さない」。
これを自動化する。各ワーカーで:
  1. DN clone の HEAD と origin/main を比較（古ければ reset --hard で更新）
  2. 更新した／バイナリが無ければ cargo build --release で再ビルド
  3. 準備確認: バイナリ・YomiToku venv・演算デバイス(cuda/mps)
使ったDNコミットを来歴として返し、ジョブに記録できるようにする。

使い方:
    uv run python scripts/orchestration/ensure_worker.py            # 全台を整合＋確認
    uv run python scripts/orchestration/ensure_worker.py --check    # 確認のみ(更新/ビルドしない)
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass

REPO = "~/Projects/Rust_DN_SuperBook_PDF_Converter"
CRATE = f"{REPO}/superbook-pdf"
VENV = f"{CRATE}/ai_bridge/ai_venv"
BIN = f"{CRATE}/target/release/superbook-pdf"


@dataclass
class Worker:
    name: str
    ssh: "str | None"  # None = ローカル(studio)
    device: str = "mps"  # mps | cuda | cpu
    ocr_batch: str = "normal"  # asus は VRAM 4GB なので small
    repo: str = REPO


WORKERS = [
    Worker("studio", None, device="mps"),
    Worker("mini", "ablaze-mac-mini@100.82.31.61", device="mps"),
    Worker("asus", "ablaze@100.107.30.86", device="cuda", ocr_batch="small"),
]


# ---- 純関数（決定論・テスト対象） -------------------------------------------


def parse_versions(rev_output: str) -> "tuple[str, str]":
    """`<local_sha> <remote_sha>` の1行を (local, remote) に分解。"""
    parts = rev_output.split()
    if len(parts) < 2:
        return ("", "")
    return (parts[0][:12], parts[1][:12])


def decide_action(local: str, remote: str, binary_exists: bool) -> dict:
    """更新/再ビルドの要否を決める（純関数）。"""
    if not local or not remote:
        return {"update": False, "rebuild": False, "reason": "版取得失敗"}
    if local != remote:
        return {"update": True, "rebuild": True, "reason": "DNが古い→更新+再ビルド"}
    if not binary_exists:
        return {"update": False, "rebuild": True, "reason": "バイナリ欠落→再ビルド"}
    return {"update": False, "rebuild": False, "reason": "最新・ビルド済み"}


def is_ready(report: dict) -> bool:
    """準備完了か（バイナリ有・venv有・デバイスOK）。"""
    return bool(report.get("binary") and report.get("venv") and report.get("device_ok"))


# ---- I/O（ssh/ローカル実行） ------------------------------------------------


def _run(worker: Worker, cmd: str, timeout: int = 60) -> "tuple[int, str]":
    """worker上でシェルコマンドを実行し (rc, stdout+stderr) を返す。"""
    full = f'export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"; {cmd}'
    if worker.ssh:
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", worker.ssh, full]
    else:
        argv = ["bash", "-lc", full]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return (p.returncode, (p.stdout or "") + (p.stderr or ""))
    except subprocess.TimeoutExpired:
        return (124, "timeout")


def _device_check_cmd(device: str) -> str:
    # activate 経由の `python` 解決に依存せず、venvのpythonを直接叩く（堅牢）。
    attr = "torch.cuda.is_available()" if device == "cuda" else "torch.backends.mps.is_available()"
    return f'{VENV}/bin/python -c "import torch; print({attr})" 2>/dev/null'


def ensure(worker: Worker, do_fix: bool) -> dict:
    """1台を整合＋準備確認して report を返す。"""
    report: dict = {"name": worker.name, "device": worker.device, "ocr_batch": worker.ocr_batch}

    rc, out = _run(worker, f"cd {worker.repo} && git fetch -q origin 2>/dev/null; "
                           f"echo $(git rev-parse HEAD) $(git rev-parse origin/main)")
    if rc != 0:
        report.update(reachable=False, error=out.strip()[:80])
        return report
    report["reachable"] = True
    local, remote = parse_versions(out)
    report["dn_commit"], report["dn_remote"] = local, remote

    rc_b, out_b = _run(worker, f"test -f {BIN} && echo yes || echo no")
    binary_exists = "yes" in out_b
    action = decide_action(local, remote, binary_exists)
    report["action"] = action["reason"]

    if do_fix and action["update"]:
        _run(worker, f"cd {worker.repo} && git reset --hard origin/main 2>&1 | tail -1", timeout=60)
        rc2, out2 = _run(worker, f"cd {worker.repo} && git rev-parse HEAD")
        report["dn_commit"] = out2.split()[0][:12] if out2.split() else local
    if do_fix and action["rebuild"]:
        rc3, out3 = _run(
            worker,
            f"cd {CRATE} && cargo build --release 2>&1 | tail -2",
            timeout=600,
        )
        report["rebuilt"] = "Finished" in out3 or rc3 == 0

    # 準備確認
    rc_b2, out_b2 = _run(worker, f"test -f {BIN} && echo yes || echo no")
    report["binary"] = "yes" in out_b2
    rc_v, out_v = _run(worker, f"test -d {VENV} && echo yes || echo no")
    report["venv"] = "yes" in out_v
    rc_d, out_d = _run(worker, _device_check_cmd(worker.device), timeout=90)
    report["device_ok"] = "True" in out_d
    report["ready"] = is_ready(report)
    return report


def main(check_only: bool) -> None:
    reports = [ensure(w, do_fix=not check_only) for w in WORKERS]
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    ready = [r["name"] for r in reports if r.get("ready")]
    print(f"\n準備OK: {ready}  / 全{len(reports)}台")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="確認のみ（更新/ビルドしない）")
    args = ap.parse_args()
    main(args.check)
