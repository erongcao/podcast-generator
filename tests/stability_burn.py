#!/usr/bin/env python3
"""
Qt GUI 稳定性烧机脚本 —— 真实启动 gui.qt.main_window，模拟用户操作，
持续 N 分钟，捕捉任何 crash / Qt 警告 / 异常。

用法：
    python tests/stability_burn.py [--duration 1800] [--interval 5] [--headless]

参数：
    --duration  跑多久（秒），默认 1800（30 分钟）。生产部署建议 86400（24h）
    --interval  模拟用户操作的间隔（秒），默认 5
    --headless  用 offscreen 平台跑（适合 CI）
"""

from __future__ import annotations

import argparse
import os
import random
import signal
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--duration", type=int, default=1800)
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--headless", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    if args.headless:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.chdir(str(PROJECT))

    # 抓 Qt 警告到 stderr
    log_path = PROJECT / "tests" / "stability_burn.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = open(log_path, "a", encoding="utf-8")

    def log(msg: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line, flush=True)
        log_f.write(line + "\n")
        log_f.flush()

    log(f"=== stability_burn START (duration={args.duration}s, interval={args.interval}s, "
        f"headless={args.headless}) ===")

    # 捕获 PyQt 异常
    def excepthook(exc_type, exc_value, exc_tb):
        log("!!! UNCAUGHT EXCEPTION !!!")
        log("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
        log_f.flush()
        # 继续运行（不退出）
    sys.excepthook = excepthook

    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from gui.env_check import EnvReport, EngineStatus, CheckResult
        from gui.qt.main_window import GhibliApp

        app = QApplication.instance() or QApplication(sys.argv)
        app.setStyle("Fusion")

        report = EnvReport(
            python=sys.executable,
            python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
            platform=sys.platform,
            checks=[
                CheckResult(name="PySide6", available=True, required=True, fix_hint=""),
                CheckResult(name="requests", available=True, required=True, fix_hint=""),
                CheckResult(name="soundfile", available=True, required=True, fix_hint=""),
            ],
            engines=[
                EngineStatus(key="doubao", label="豆包", available=True, reason=""),
                EngineStatus(key="minimax", label="MiniMax", available=True, reason=""),
                EngineStatus(key="voxcpm", label="VoxCPM", available=True, reason=""),
            ],
        )
        win = GhibliApp(report)
        win.show()
        log("GhibliApp constructed + shown")

        # 操作计数器 + crash 计数
        ops = 0
        crashes = 0
        qt_warnings = 0
        start = time.time()
        end = start + args.duration

        def bump_qt_warnings():
            nonlocal qt_warnings
            qt_warnings += 1
            if qt_warnings <= 20:
                log(f"  Qt warning #{qt_warnings}")

        # 装一个 message handler 抓 Qt warning
        def msg_handler(mode, ctx, msg):
            if "warning" in mode.lower() or "qml" in mode.lower():
                bump_qt_warnings()
        app.installMessageHandler(msg_handler) if hasattr(app, "installMessageHandler") else None

        def random_action():
            nonlocal ops, crashes
            ops += 1
            try:
                choice = random.choice([
                    "switch_engine", "switch_tab", "edit_text",
                    "click_generate", "change_volume", "noop",
                ])
                if choice == "switch_engine":
                    engines = ["doubao", "minimax", "voxcpm"]
                    eng = random.choice(engines)
                    btn_id = engines.index(eng)
                    win.input_panel.cred._engine_group.button(btn_id).click()
                elif choice == "switch_tab":
                    right = win.splitter.widget(1)
                    right.setCurrentIndex(random.randint(0, right.count() - 1))
                elif choice == "edit_text":
                    box = random.choice([win.input_panel.url_box, win.input_panel.txt_box])
                    box.setPlainText(box.toPlainText() + f" [edit-{ops}]")
                elif choice == "click_generate":
                    win.output_panel.gen_audio_btn.click()
                elif choice == "change_volume":
                    # 切 normalize / cleanup checkbox
                    cb = random.choice([win.output_panel.normalize_cb, win.output_panel.cleanup_cb])
                    cb.setChecked(not cb.isChecked())
                # noop 也算一次操作（验证不操作也不崩）
            except Exception as e:
                crashes += 1
                log(f"!!! action crashed: {choice} → {type(e).__name__}: {e}")
                log(traceback.format_exc())

        timer = QTimer()
        timer.setInterval(int(args.interval * 1000))
        timer.timeout.connect(random_action)
        timer.start()

        # 进度条
        progress_timer = QTimer()
        progress_timer.setInterval(60_000)  # 1 min
        def report_progress():
            elapsed = int(time.time() - start)
            remaining = max(0, int(end - time.time()))
            log(f"  [progress] t={elapsed}s, remaining={remaining}s, ops={ops}, crashes={crashes}, qt_warnings={qt_warnings}")
        progress_timer.timeout.connect(report_progress)
        progress_timer.start()

        # 退出定时
        def quit_app():
            elapsed = int(time.time() - start)
            log(f"=== duration reached ({elapsed}s), shutting down ===")
            log(f"  final ops={ops}, crashes={crashes}, qt_warnings={qt_warnings}")
            timer.stop()
            progress_timer.stop()
            app.quit()

        QTimer.singleShot(int(args.duration * 1000), quit_app)

        exit_code = app.exec()
        log(f"=== app.exec() returned {exit_code} ===")
        log(f"  total ops={ops}, crashes={crashes}, qt_warnings={qt_warnings}")
        if crashes == 0:
            log("✅ STABILITY PASS")
            sys.exit(0)
        else:
            log(f"❌ STABILITY FAIL ({crashes} crashes)")
            sys.exit(1)
    except Exception as e:
        log(f"!!! harness crashed: {type(e).__name__}: {e}")
        log(traceback.format_exc())
        sys.exit(2)
    finally:
        log_f.close()


if __name__ == "__main__":
    # Ctrl+C 友好
    signal.signal(signal.SIGINT, lambda *_: sys.exit(130))
    main()
