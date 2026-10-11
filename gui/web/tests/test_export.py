"""
test_export.py — export endpoint 回归测试

测试范围：
    1. render_markdown 基本输出 / 空输入
    2. sanitize_filename 各种 unsafe 字符替换
    3. export_impl 的安全校验（path traversal、prefix、format）
    4. export_impl 的 404 行为
    5. export_impl raw / rendered 两种 format 的实际内容

跑法：
    cd ~/Documents/podcast-generator
    ~/.podcast-automation/.venv/bin/python -m gui.web.tests.test_export
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 把项目根加进 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gui.web.api.editor import (  # noqa: E402
    sanitize_filename,
    render_markdown,
    export_impl,
)
from gui.web.api.script_gen import SCRIPT_DIR, _format_for_save  # noqa: E402

# 失败时让 assert 错误更清楚
def _assert(cond, msg):
    if not cond:
        print(f"❌ FAIL: {msg}")
        sys.exit(1)
    print(f"  ✅ {msg}")


# ============================================================
# render_markdown
# ============================================================
def test_render_markdown_basic():
    """2 段 segments → `## 主播甲` + `---` + body。"""
    print("\n=== test_render_markdown_basic ===")
    segs = [
        {"speaker": "主播甲", "text": "第一段正文"},
        {"speaker": "主播乙", "text": "第二段正文"},
    ]
    out = render_markdown(segs)
    _assert("## 主播甲" in out, "含 ## 主播甲 heading")
    _assert("## 主播乙" in out, "含 ## 主播乙 heading")
    _assert("---" in out, "含 --- 分隔符")
    _assert("第一段正文" in out, "含段 1 body")
    _assert("第二段正文" in out, "含段 2 body")
    _assert(out.startswith("---"), "开头是 ---")  # spec: ---\n\n## 主播甲


def test_render_markdown_empty():
    """空列表 → 空字符串。"""
    print("\n=== test_render_markdown_empty ===")
    out = render_markdown([])
    _assert(out == "", f"空列表 → ''（实际 {out!r}）")


# ============================================================
# sanitize_filename
# ============================================================
def test_sanitize_filename_strips_unsafe():
    """unsafe 字符替换 + 空字符串 fallback。"""
    print("\n=== test_sanitize_filename_strips_unsafe ===")
    # 1. 中文 + 普通字符不动
    _assert(sanitize_filename("脾硬化") == "脾硬化",
            "中文文件名保留不变")
    # 2. < > 替换
    _assert(sanitize_filename("a<b>c") == "a_b_c",
            "a<b>c → a_b_c")
    # 3. 全部 unsafe 字符都替换
    _assert(sanitize_filename('a"b/c\\d|e?f*g') == "a_b_c_d_e_f_g",
            "全部 unsafe 字符 → a_b_c_d_e_f_g")
    # 4. 半角冒号替换（spec: '<>:"/\\|?*' → '_'，全角冒号不在内）
    _assert(sanitize_filename("a:b") == "a_b",
            "半角冒号替换")
    # 5. 空 / 纯空白 fallback
    _assert(sanitize_filename("") == "未命名脚本",
            "空字符串 → 未命名脚本")
    _assert(sanitize_filename("   ") == "未命名脚本",
            "纯空白 → 未命名脚本")


# ============================================================
# export_impl 安全校验
# ============================================================
def test_export_impl_rejects_path_traversal():
    """path traversal 攻击全部 raise ValueError。"""
    print("\n=== test_export_impl_rejects_path_traversal ===")
    bad_names = [
        "../etc/passwd",
        "foo/bar.json",
        "script_../x.json",
        "/etc/passwd",
        "\\windows\\system32",
    ]
    for bad in bad_names:
        try:
            export_impl(bad, "raw")
            _assert(False, f"非法 name {bad!r} 应该 raise 但没 raise")
        except ValueError:
            pass
    _assert(True, f"全部 {len(bad_names)} 个 path traversal 输入都拒绝")


def test_export_impl_rejects_bad_prefix():
    """非法 prefix（不是 script_*.json）也拒绝。"""
    print("\n=== test_export_impl_rejects_bad_prefix ===")
    bad_names = [
        "notscript_xxx.json",
        "voice_xxx.json",
        "",
    ]
    for bad in bad_names:
        try:
            export_impl(bad, "raw")
            _assert(False, f"非法 prefix {bad!r} 应该 raise 但没 raise")
        except ValueError:
            pass
    _assert(True, f"全部 {len(bad_names)} 个 bad prefix 输入都拒绝")


def test_export_impl_rejects_bad_format():
    """format 非法 → ValueError。"""
    print("\n=== test_export_impl_rejects_bad_format ===")
    # 用合法 name（即使不存在，因为 format 校验先于文件读取）
    try:
        export_impl("script_fake.json", "html")
        _assert(False, "format=html 应该 raise 但没 raise")
    except ValueError:
        pass
    _assert(True, "format=html 被拒绝")

    try:
        export_impl("script_fake.json", "")
        _assert(False, "format='' 应该 raise 但没 raise")
    except ValueError:
        pass
    _assert(True, "format='' 被拒绝")


def test_export_impl_404_on_missing():
    """name 校验通过但文件不存在 → FileNotFoundError。"""
    print("\n=== test_export_impl_404_on_missing ===")
    try:
        export_impl("script_definitely_does_not_exist_2026_10_11.json", "raw")
        _assert(False, "不存在的文件应该 raise FileNotFoundError")
    except FileNotFoundError:
        pass
    _assert(True, "不存在的文件 → FileNotFoundError")


# ============================================================
# export_impl 实际内容（用 _format_for_save 创建真实 fixture + 清理）
# ============================================================
_FIXTURES_TO_CLEAN = []  # 创建后清理的临时文件路径


def _save_fixture(segments, topic, editor_text=""):
    """调 _format_for_save 创建 fixture，记下来用于清理。"""
    p = _format_for_save(segments, topic, editor_text)
    _FIXTURES_TO_CLEAN.append(p)
    return Path(p).name  # 只要文件名


def _cleanup_fixtures():
    for p in _FIXTURES_TO_CLEAN:
        try:
            os.unlink(p)
        except FileNotFoundError:
            pass
    _FIXTURES_TO_CLEAN.clear()


def test_export_impl_raw_returns_editor_text():
    """raw format → 直接返回 editor_text。"""
    print("\n=== test_export_impl_raw_returns_editor_text ===")
    topic = "测试主题 raw export"
    segs = [{"speaker": "主播甲", "text": "raw 正文"}]
    custom_editor_text = "--- CUSTOM_EDITOR_TEXT_MARKER_xyz ---\n\n【主播甲】\n这段是用户在编辑器里手改的"
    name = _save_fixture(segs, topic, editor_text=custom_editor_text)

    md, filename = export_impl(name, "raw")
    _assert(md == custom_editor_text, "raw 返回 editor_text 原样（不被 render）")
    _assert("CUSTOM_EDITOR_TEXT_MARKER_xyz" in md, "raw 包含用户编辑过的标记")
    _assert(filename.endswith(".md"), f"filename 是 .md（实际 {filename!r}）")
    _assert(topic in filename, f"filename 包含 topic（实际 {filename!r}）")


def test_export_impl_rendered_uses_segments():
    """rendered format + 有 segments → 用 segments 渲染。"""
    print("\n=== test_export_impl_rendered_uses_segments ===")
    topic = "测试主题 rendered export"
    segs = [
        {"speaker": "主播甲", "text": "第一段渲染正文"},
        {"speaker": "主播乙", "text": "第二段渲染正文"},
    ]
    # editor_text 与 segments 不一致（模拟用户编辑后但没重新 parse）
    inconsistent_editor = "--- OLD EDITOR TEXT ---"
    name = _save_fixture(segs, topic, editor_text=inconsistent_editor)

    md, filename = export_impl(name, "rendered")
    _assert("## 主播甲" in md, "rendered 含 ## 主播甲 heading")
    _assert("## 主播乙" in md, "rendered 含 ## 主播乙 heading")
    _assert("第一段渲染正文" in md, "rendered 含段 1 body")
    _assert("第二段渲染正文" in md, "rendered 含段 2 body")
    _assert("OLD EDITOR TEXT" not in md,
            "rendered 用 segments 而非 editor_text（seg 是新的）")
    _assert(filename.endswith(".md"), "filename 是 .md")


# ============================================================
# Runner
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("editor.py export 回归测试")
    print("=" * 60)
    tests = [
        test_render_markdown_basic,
        test_render_markdown_empty,
        test_sanitize_filename_strips_unsafe,
        test_export_impl_rejects_path_traversal,
        test_export_impl_rejects_bad_prefix,
        test_export_impl_rejects_bad_format,
        test_export_impl_404_on_missing,
        test_export_impl_raw_returns_editor_text,
        test_export_impl_rendered_uses_segments,
    ]
    failed = []
    try:
        for t in tests:
            try:
                t()
            except SystemExit:
                failed.append(t.__name__)
            except Exception as e:
                import traceback
                print(f"❌ EXCEPTION in {t.__name__}: {e}")
                traceback.print_exc()
                failed.append(t.__name__)
    finally:
        # 清理测试创建的 fixture 文件 — 即使中途异常也跑（try/finally），
        # 避免污染用户的 ~/Library/Application Support/PodcastGenerator/scripts/
        _cleanup_fixtures()

    print("\n" + "=" * 60)
    if failed:
        print(f"❌ {len(failed)} 个测试失败: {failed}")
        sys.exit(1)
    else:
        print(f"✅ 全部 {len(tests)} 个测试通过")
        sys.exit(0)
