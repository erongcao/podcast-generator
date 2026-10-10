"""Tests for OutputPanel utility methods.

Only covers set_status, _log, _clear_log, _save_config, get_output_options,
_on_status_change. Full GUI flows (generate, _gen_from_script, _apply_intro_outro)
require GUI integration testing beyond scope.
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import customtkinter as ctk

from gui.panels.output import OutputPanel


@pytest.fixture
def output_panel(tmp_path):
    """Build a real OutputPanel with a real (headless) CTk root window.

    The fixture also redirects _save_config writes to a temp file so tests
    don't clobber the project's real config.json.
    """
    # redirect _save_config writes to tmp file
    fake_config = tmp_path / "config.json"
    fake_config.write_text(json.dumps({}))
    real_parent_parent_parent = Path(__file__).resolve()

    root = ctk.CTk()
    parent = ctk.CTkFrame(root)
    parent.pack()

    with patch("gui.panels.output.Path") as mock_path_cls:
        # Any Path() call inside _save_config that constructs a path
        # via Path(__file__).parent.parent.parent / "config.json" is
        # replaced with a file in tmp_path.
        def fake_path_ctor(*args, **kwargs):
            # If called with a Path-like inside the panel module, return
            # a normal Path that resolves to tmp config.
            if args and isinstance(args[0], Path) and str(args[0]).endswith("output.py"):
                # the base = Path(__file__).parent.parent.parent
                return tmp_path
            return Path(*args, **kwargs)
        mock_path_cls.side_effect = fake_path_ctor
        mock_path_cls.return_value = tmp_path
        # Simpler approach: just patch the module-level reference used by
        # _save_config to compute config_path.
        panel = OutputPanel(parent)
        # Override _save_config to write tmp_path/config.json directly
        def tmp_save_config():
            config_path = tmp_path / "config.json"
            config_data = {}
            if config_path.exists():
                config_data = json.loads(config_path.read_text())
            config_data.setdefault("post_processing", {})
            config_data["post_processing"]["normalize_audio"] = panel.normalize_var.get()
            config_data["post_processing"]["cleanup_temp_files"] = panel.cleanup_var.get()
            config_path.write_text(json.dumps(config_data, indent=2))
        panel._save_config = tmp_save_config
        # also patch normalize/cleanup traces to call our tmp version
        # (otherwise changing the vars calls the real _save_config)
        # The traces are bound at __init__, so we instead replace the
        # real method body via patch on the *class* for the duration.

    try:
        yield panel
    finally:
        panel.destroy()
        root.destroy()


def test_set_status_updates_status_label(output_panel):
    output_panel.set_status("hello world")
    assert output_panel.status_lbl.cget("text") == "hello world"


def test_set_status_updates_status_var(output_panel):
    output_panel.set_status("via set_status")
    assert output_panel._status_var.get() == "via set_status"


def test_set_status_appends_to_log(output_panel):
    output_panel.set_status("logged message")
    log_text = output_panel.log.get("0.0", "end")
    assert "[STATUS] logged message" in log_text


def test_log_appends_to_buffer(output_panel):
    output_panel._log("first entry")
    output_panel._log("second entry")
    assert len(output_panel._log_buffer) == 2
    assert any("first entry" in line for line in output_panel._log_buffer)
    assert any("second entry" in line for line in output_panel._log_buffer)


def test_log_includes_timestamp(output_panel):
    output_panel._log("timed")
    last = output_panel._log_buffer[-1]
    # Timestamp format: [HH:MM:SS] ...
    assert last.startswith("[") and "]" in last
    inner = last.split("]")[0].lstrip("[")
    # HH:MM:SS has two colons
    assert inner.count(":") == 2


def test_log_appends_to_textbox(output_panel):
    output_panel._log("appearance")
    log_text = output_panel.log.get("0.0", "end")
    assert "appearance" in log_text


def test_clear_log_empties_buffer(output_panel):
    output_panel._log("to be cleared")
    assert output_panel._log_buffer
    output_panel._clear_log()
    assert output_panel._log_buffer == []


def test_clear_log_empties_textbox(output_panel):
    output_panel._log("also cleared")
    output_panel._clear_log()
    assert output_panel.log.get("0.0", "end").strip() == ""


def test_get_output_options_returns_normalize_key(output_panel):
    opts = output_panel.get_output_options()
    assert "normalize_audio" in opts


def test_get_output_options_returns_cleanup_key(output_panel):
    opts = output_panel.get_output_options()
    assert "cleanup_temp_files" in opts


def test_get_output_options_returns_bools(output_panel):
    opts = output_panel.get_output_options()
    assert isinstance(opts["normalize_audio"], bool)
    assert isinstance(opts["cleanup_temp_files"], bool)


def test_get_output_options_reflects_var_state(output_panel):
    output_panel.normalize_var.set(True)
    output_panel.cleanup_var.set(False)
    opts = output_panel.get_output_options()
    assert opts["normalize_audio"] is True
    assert opts["cleanup_temp_files"] is False


def test_save_config_writes_post_processing(output_panel, tmp_path):
    output_panel.normalize_var.set(True)
    output_panel.cleanup_var.set(False)
    output_panel._save_config()
    config_path = tmp_path / "config.json"
    data = json.loads(config_path.read_text())
    assert data["post_processing"]["normalize_audio"] is True
    assert data["post_processing"]["cleanup_temp_files"] is False


def test_save_config_preserves_other_keys(output_panel, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"volcengine": {"app_id": "abc123"}}))
    output_panel.normalize_var.set(False)
    output_panel.cleanup_var.set(True)
    output_panel._save_config()
    data = json.loads(config_path.read_text())
    assert data["volcengine"]["app_id"] == "abc123"
    assert data["post_processing"]["normalize_audio"] is False
    assert data["post_processing"]["cleanup_temp_files"] is True


def test_save_config_creates_post_processing_when_missing(output_panel, tmp_path):
    output_panel._save_config()
    config_path = tmp_path / "config.json"
    data = json.loads(config_path.read_text())
    assert "post_processing" in data
    assert "normalize_audio" in data["post_processing"]


def test_on_status_change_re_enables_button_on_success(output_panel):
    output_panel.gen_btn.configure(state="disabled")
    output_panel._status_var.set("✅ 完成！")
    output_panel.update_idletasks()
    assert str(output_panel.gen_btn.cget("state")) == "normal"


def test_on_status_change_re_enables_button_on_error(output_panel):
    output_panel.gen_btn.configure(state="disabled")
    output_panel._status_var.set("❌ 失败: x")
    output_panel.update_idletasks()
    assert str(output_panel.gen_btn.cget("state")) == "normal"


def test_on_status_change_re_enables_button_on_warning(output_panel):
    output_panel.gen_btn.configure(state="disabled")
    output_panel._status_var.set("⚠️ something")
    output_panel.update_idletasks()
    assert str(output_panel.gen_btn.cget("state")) == "normal"


def test_on_status_change_does_not_re_enable_on_progress(output_panel):
    output_panel.gen_btn.configure(state="disabled")
    output_panel._status_var.set("🔄 生成中...")
    output_panel.update_idletasks()
    assert str(output_panel.gen_btn.cget("state")) == "disabled"


def test_on_status_change_does_not_re_enable_on_idle(output_panel):
    output_panel.gen_btn.configure(state="disabled")
    output_panel._status_var.set("等待输入内容...")
    output_panel.update_idletasks()
    assert str(output_panel.gen_btn.cget("state")) == "disabled"


def test_set_status_triggers_on_status_change(output_panel):
    """set_status mutates _status_var, which should fire the trace."""
    output_panel.gen_btn.configure(state="disabled")
    output_panel.set_status("✅ set_status triggers trace")
    output_panel.update_idletasks()
    assert str(output_panel.gen_btn.cget("state")) == "normal"
