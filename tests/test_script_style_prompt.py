"""
台词生成风格基线（2026-10-26）

用户要求合成出来的对话更生动活泼：多语气词、短句、设问、感叹号、口语化。
两条生成路径都要带这个风格基线：
  - MiniMax M3：写进 SYSTEM_PROMPT_TEMPLATE（该路径 prompt 完全可控）
  - 豆包 PodcastTTS：黑盒模型，只能写进输入文本的「附加要求」（
    DEFAULT_STYLE_REQUIREMENT，用户自定义叠加在后）

锁死的是"风格基线存在"，不是具体措辞——措辞可调，基线不能丢。
"""


def test_minimax_system_prompt_has_lively_style():
    from minimax_script_gen import build_messages

    msgs = build_messages(topic="测试主题", host_descriptions=["主播甲", "主播乙"])
    system = msgs[0]["content"]
    assert msgs[0]["role"] == "system"
    for marker in ("语气词", "短句", "设问", "感叹"):
        assert marker in system, f"system prompt 丢了风格要求: {marker}"
    # 综艺感语气词的禁令不能丢（用户只要自然语气词，不要"哈哈""哦豁"）
    assert "哦豁" in system


def test_doubao_default_style_requirement():
    from gui.web.api.script_gen import DEFAULT_STYLE_REQUIREMENT

    for marker in ("语气词", "短句", "设问", "感叹号", "口语化"):
        assert marker in DEFAULT_STYLE_REQUIREMENT, f"豆包风格基线丢了: {marker}"
