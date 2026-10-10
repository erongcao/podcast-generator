#!/usr/bin/env python3
"""
MiniMax LLM 播客台词生成器

调用 MiniMax Open Platform chat completion API（M3 模型），
把主题/原文转写成多主持人对话脚本。

为什么不用豆包：
  - 豆包 action=0 是固定风格，填充词比例不可调，医学专业度一般
  - MiniMax M3 是通用旗舰模型，提示词完全可控，可针对患者教育定制
  - 用户已有 MiniMax 订阅账号，复用同一个 API key（与 TTS 同账号）

API 细节（cn region, 2026-09 验证）：
  POST https://api.minimaxi.com/v1/chat/completions
  Authorization: Bearer <MiniMax API key>
  Body: {"model": "MiniMax-M3", "messages": [...], "max_completion_tokens": N}
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import List, Dict, Optional

import requests

from exceptions import APIError

log = logging.getLogger("minimax_script")

DEFAULT_API_BASE = "https://api.minimaxi.com"
DEFAULT_MODEL = "MiniMax-M3"
DEFAULT_TIMEOUT = 240  # 秒；thinking 深度思考模式 + 长脚本可能需要 1-3 分钟

CHAT_URL = "{api_base}/v1/chat/completions"


# ============ 提示词模板 ============

# 医学播客对脚本生成的核心约束
# 设计原则（针对用户实际痛点）：
#   1. 风格克制自然：豆包"嗯啊过度"在医生→患者场景会显得不专业
#   2. 严格输出格式：直接对接现有 parser【speaker】\ntext\n\n---\n\n
#   3. 专业度对齐：可用术语，但首次出现要顺手解释一句
#   4. 长度可控：避免生成超长脚本导致 TTS 时长失控

SYSTEM_PROMPT_TEMPLATE = """你是「{show_name}」的播客编剧，负责把医学主题改写成两位主持人的口语对话脚本。

# 受众
普通患者和他们的家属，没有医学背景。他们想了解一个健康话题，但不想被术语淹没。

# 对话的本质：脚本里每一对「甲→乙」必须是「上一句引发下一句」

两个人在**真实对话**，不是两个人**轮流念稿**。具体来说：
- 主播乙这一句回答完之后，主播甲下一句必须**承接、追问、反应、质疑、复述、或补充**乙刚才说的具体内容
- **绝对禁止**主播甲从"提纲清单"里读下一个抽象问题——所有问题都必须**从乙刚说的那句话自然冒出来**
- 主播乙如果被主播甲打断/质疑，要**正面回应**，不能装作没听见

每一对甲乙写之前，先问自己：**"乙刚说了 X，作为真实听众，甲的下一句会怎么反应？"** —— 然后照这个反应写。

# 主持人（角色分工严格，不可互换）

**主播甲：主持人 / 对话者（不是采访记者）**

主播甲是一个**有血肉的真人**，会震惊、会跑题、会想起身边人、会被说服、也会质疑——不是提词器。

- 每轮字数：**20-60 字**（一到三句话）
- 语气：轻松自然，像跟朋友聊天，不是主持人念稿

**必用的 8 种互动模式**（每种至少出现 2-3 次）：

1. **承接式 follow-up**：乙刚说的某个具体概念/数字，立刻追问这个概念
   > 乙说"破裂会大出血" → 甲"那这出血来得突然吗？有前兆吗？"

2. **反应式回应**：乙说了让人震惊/困惑的事，先震惊/停顿/消化，再问
   > 乙说"会大出血" → 甲"啊？那这个风险到底多大？是说突然就要抢救那种？"

3. **打断式插入**：乙讲到某个细节，插嘴追问"等等，这个怎么测/怎么理解"
   > 乙说"脾脏硬度可以加测" → 甲"等等，脾脏硬度怎么测？跟肝硬度是同一台机器吗？"

4. **质疑/反驳**：对乙的话提出不同看法或现实情况
   > 乙说"低于阈值就可以不做" → 甲"但是不是所有医生都这样？有些基层医院可能没脾脏硬度这个检查吧？"

5. **分享/补充**：分享自己/朋友/亲戚的见闻或知识
   > 乙说"肝硬化病人" → 甲"我有个朋友就是肝硬化，他说他每次做胃镜都紧张得不行，提前一晚睡不着"

6. **复述/确认**：用自己的话复述乙刚说的，确认理解
   > 乙说"三个指标" → 甲"等等我整理一下——肝硬度、血小板、脾脏大小，对吧？三个都低于阈值才算低风险？"

7. **听众代入喊话**：对着听众喊话，让甲自己也像在听
   > 乙说"20 kPa 以下" → 甲"听到没？肝硬度低于 20 的朋友们可以稍微松口气"

8. **停顿/犹豫痕迹**：让甲有"嗯……让我想想""这个我之前没想到"这种呼吸感
   > "嗯……这个我之前还真没想到……那如果是这样的话，是不是说……"

**听众代入**（必出现 3-4 次）：至少 3-4 轮使用"我妈/我爸/我朋友/我自己"作为具体听众代入。

**道别轮**：不是简单"再见"，而是要**预告下期话题 + 留钩子**：
> "好，今天就聊到这儿。下期我们想聊聊……你有什么想听的，评论区告诉我，拜拜。"

**绝对禁止**：
- ❌ "那现在有没有什么新办法？" 这种从提纲跳到下一题的抽象提问
- ❌ 每轮只问一个干净的学术问题，不反应、不承接
- ❌ 像机器人一样按"提纲顺序"提下一个问题

---

**主播乙：讲解者 / 信息来源（不是百科词条，是有人味的医生朋友）**

- 每轮字数：**60-150 字**（一段聚焦的内容）
- 语气：专业可信，像医生朋友跟你聊天
- 每轮只回答**一个问题**，不要把多个点塞进一轮

**必用技巧**：

- **举 1-2 个具体临床案例**（"我有个 50 岁的王先生，肝硬化 5 年了……"）——让听众有代入感，不要纯讲概念
- **讲到吓人的点（出血、风险、副作用），先用一句安抚**再讲医学细节：
  > "别紧张，这种情况完全可以提前发现。今天我们讲的就是怎么避免这种情况。"
- **每轮结尾给一个钩子**——"这个数字要记住：X" / "划重点：X ≠ Y" / "出现这个症状就别犹豫"
- 医学术语第一次出现要顺手用一句话解释（"肝硬度 LSM，简单说就是用 FibroScan 这种仪器测的肝脏软硬程度"）
- 必要时给具体数字/切点（"肝硬度低于 20 kPa" / "血小板高于 150×10⁹/L"）
- 提醒边界条件和误区（"三类人不适用"）
- **允许被甲打断/质疑**——如果甲提出"但是不是……"，要认真回应，不能装作没听见

# 风格硬约束
- **不限固定轮数**——把本期主题的所有医学关键点都讲完为止，**没有上限**（复杂主题可达 30-50 轮以上）
- **绝对不要"为了凑数加废话"**，**也绝对不要"还剩关键点没讲就匆忙道别"**
- 收尾三步固定：①主播甲抛出总结性问题（如"最后帮我划一下重点？"）→ ②主播乙给完整总结 + 关键数字 + 收尾建议 → ③主播甲预告下期话题 + 道别
- 段落分隔：用「---」空行隔开每一轮
- 填充词比例：≤ 3%（一句最多一个"嗯"或"对"），医学话题要显得专业可信
- **绝对禁止**用"哈哈""呵呵""哦豁"等综艺感语气词
- **绝对禁止**用 emoji、网络梗、营销话术（"快来评论区告诉我""点赞关注"）
- **绝对禁止**为角色编造真实姓名（不要用"李医生""张教授"等具体人名，统一用「主播甲」「主播乙」）

# 工作方式

先在 <think>...</think> 块里深入思考：
- 主题的医学核心是什么？患者最容易误解哪些点？
- **先列「主播乙要讲的 N 个关键点」**（按医学逻辑顺序）——这个 N 决定脚本长度，**不要凑数**
- **然后对每个关键点反推主播甲可能的反应**：震惊？质疑？想起身边人？复述？
- **最后把甲乙编织成对话**——确保 70% 以上的主播甲提问是承接乙刚说的具体内容，而不是抽象新题
- 哪些术语需要现场解释？解释时要类比日常经验

**重要：脚本长度由"主题复杂度"决定，不由固定轮数决定**。简单主题可以 10 几轮，复杂主题（如"Baveno 标准"）可能要 30-50 轮才能讲完。**没有上限**——讲完所有关键点即可，不要为了凑数加废话，也不要"还剩关键点没讲就匆忙道别"。

然后输出最终脚本。

# 输出格式（严格遵守，不要任何额外解释）

每个轮次两行：
【主播甲】
对话正文

轮次之间用空行 + --- + 空行隔开：

【主播甲】
对话正文

---

【主播乙】
对话正文

---

不要前言、不要"以下是..."、不要 markdown 代码块包裹。直接开始 <think>...</think> 然后是脚本。
"""


USER_PROMPT_TEMPLATE = """# 本期主题

{topic}

# 原始素材（可能为空、可能是文章/论文/新闻；如有请基于此改写）

{context}

# 特别要求（用户可填，没有就忽略）

{custom}

请生成脚本。
"""


def build_messages(
    topic: str,
    *,
    host_descriptions: List[str],
    context: str = "",
    custom: str = "",
    show_name: str = "医学科普播客",
) -> List[Dict[str, str]]:
    """组装 chat completion messages。

    Args:
        topic: 本期主题（如"高血压患者能不能吃鸡蛋"）
        host_descriptions: 主持人描述列表，每条是一行（如 ["李医生：心血管内科主治医师",
                  风格沉稳，喜欢举临床例子", "虞阳：医学科普博主，擅长把术语翻译成大白话"]）
        context: 原始素材（文章/论文/笔记）。可为空。
        custom: 用户额外要求（如"风格再幽默一点""加入一个病人提问环节"）
        show_name: 节目名（影响 system prompt 中的称呼）
    """
    label_example = re.sub(r"[：:].*$", "", host_descriptions[0]).strip() or "甲"
    label_example2 = (
        re.sub(r"[：:].*$", "", host_descriptions[1]).strip() if len(host_descriptions) > 1 else "乙"
    )
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        show_name=show_name,
        host_descriptions="\n".join(f"- {h}" for h in host_descriptions),
        label_example=label_example,
        label_example2=label_example2,
    )
    user_prompt = USER_PROMPT_TEMPLATE.format(
        topic=topic.strip() or "（未提供主题）",
        context=context.strip() or "（无）",
        custom=custom.strip() or "（无）",
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


# ============ 脚本解析 ============

SEGMENT_SEP_RE = re.compile(r"\n\s*---\s*\n", re.MULTILINE)
LABEL_RE = re.compile(r"^【(.+?)】\s*$", re.MULTILINE)


def parse_script(text: str) -> List[Dict[str, str]]:
    """从 LLM 输出里解析出 [{"speaker": str, "text": str}, ...]。

    兼容两种标签格式：
      A. 【角色名】\\n台词正文（标准格式，多行）
      B. 角色名：台词正文（thinking 模式下 LLM 偏好的自然对话格式）

    还兼容 LLM 偶尔犯的格式错误：
      - 开头带 <think>...</think> 推理块
      - 包在 ``` ``` markdown 代码块里
      - 多余的前言/后语（自动剔除）
      - 标签前后有空格
      - 末尾被截断（max_tokens 不够）
    """
    text = text.strip()

    # 1) 剥 <think>...</think> 思维链
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    # 2) 剥 markdown 代码块
    if text.startswith("```"):
        m = re.search(r"```(?:[a-zA-Z]*)?\n?(.*?)```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()

    # 3) 探测标签格式
    has_bracket = "【" in text and "】" in text
    # 格式 B: 行首是 2-12 个汉字 + 全角冒号（"虞阳：" / "李医生："）
    # 限定长度避免误匹配"提示：用户..."
    COLON_LABEL_RE = re.compile(r"^([一-龥]{2,12})：", re.MULTILINE)
    has_colon = bool(COLON_LABEL_RE.search(text))

    if has_bracket:
        # 格式 A: 截取第一个【到最后一个】之间
        first = text.find("【")
        last = text.rfind("】")
        if last > first:
            text = text[first:last + 1]
        label_pat = LABEL_RE  # ^【(.+?)】\s*$
    elif has_colon:
        # 格式 B: 保留全文
        label_pat = COLON_LABEL_RE
    else:
        return []

    segments: List[Dict[str, str]] = []
    chunks = SEGMENT_SEP_RE.split(text)
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        m = label_pat.search(chunk)
        if not m:
            continue
        speaker = m.group(1).strip()
        # 标签之后到下一轮或结尾 = 正文
        content = chunk[m.end():].strip()
        if not content:
            continue
        segments.append({"speaker": speaker, "text": content})
    return segments


# ============ HTTP 客户端 ============

class MiniMaxScriptGenerator:
    """MiniMax LLM 脚本生成器（线程安全，可重入）。"""

    def __init__(
        self,
        api_key: str,
        *,
        api_base: str = DEFAULT_API_BASE,
        model: str = DEFAULT_MODEL,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        if not api_key:
            raise ValueError("MiniMax API key 不能为空")
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.timeout = timeout

    def _post(self, messages: List[Dict[str, str]], max_tokens: int) -> str:
        url = CHAT_URL.format(api_base=self.api_base)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # 让 LLM 启用 thinking 深入思考，用户是 Token Plan 订阅用户，
        # 不做 token 节流，宁可让模型多想多写以提高脚本质量。
        # thinking 默认会通过 <think>...</think> 块返回（或 reasoning_content 字段），
        # parse_script 第一步就是剥离 <think> 块。
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 1.0,  # thinking 模型推荐 1.0
        }
        # 不传 max_completion_tokens → 用 API 默认上限（用户要求"无限"）
        if max_tokens is not None:
            payload["max_completion_tokens"] = max_tokens
        log.info(f"[MiniMax LLM] POST {url} model={self.model} max_tokens={max_tokens or 'unlimited'} thinking=on")
        t0 = time.time()
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        except requests.Timeout:
            raise APIError("MiniMax-LLM", f"请求超时 ({self.timeout}s)")
        except requests.RequestException as e:
            raise APIError("MiniMax-LLM", f"网络错误: {e}")
        dt = time.time() - t0
        if resp.status_code != 200:
            raise APIError("MiniMax-LLM", f"HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except ValueError as e:
            raise APIError("MiniMax-LLM", f"非 JSON 响应: {e}")
        # OpenAI-compatible 格式
        try:
            msg = data["choices"][0]["message"]
            content = msg.get("content") or ""
        except (KeyError, IndexError, TypeError):
            raise APIError("MiniMax-LLM", f"响应结构异常: {json.dumps(data)[:200]}")
        # 兜底：如果服务端忽略 thinking 参数又返回 reasoning_content，
        # 把它当作 thinking 块塞进 content 前面，让 parse_script 的
        # `<think>...</think>` 正则能剥掉
        reasoning = msg.get("reasoning_content") or ""
        if reasoning and "<think>" not in content:
            content = f"<think>{reasoning}</think>\n{content}"
        # usage 信息（如果有）记到 log，便于调试 token 消耗
        usage = data.get("usage", {})
        if usage:
            log.info(
                f"[MiniMax LLM] OK in {dt:.1f}s, "
                f"completion_tokens={usage.get('completion_tokens')}/{max_tokens}, "
                f"output={len(content)} chars"
            )
        else:
            log.info(f"[MiniMax LLM] OK in {dt:.1f}s, output={len(content)} chars")
        return content

    def generate(
        self,
        topic: str,
        *,
        host_descriptions: List[str],
        context: str = "",
        custom: str = "",
        show_name: str = "医学科普播客",
        max_tokens: int = None,
    ) -> List[Dict[str, str]]:
        """生成脚本。

        max_tokens=None 表示不传 max_completion_tokens 给 API，用 MiniMax 默认上限。
        用户是 Token Plan 订阅用户，明确要求不限制 token 预算。

        Returns:
            list of {"speaker": str, "text": str}
        """
        messages = build_messages(
            topic=topic,
            host_descriptions=host_descriptions,
            context=context,
            custom=custom,
            show_name=show_name,
        )
        # 不限轮数：脚本长度由 LLM 自由决定（主题复杂度决定），不预设 token 上限。
        text = self._post(messages, max_tokens=max_tokens)
        segments = parse_script(text)
        if not segments:
            raise APIError(
                "MiniMax-LLM",
                f"解析失败，LLM 输出无有效段落。原始输出片段：\n{text[:500]}",
            )
        log.info(f"[MiniMax LLM] parsed {len(segments)} segments")
        return segments


# ============ 默认主持人预设 ============

# 通用 2 主持人预设：与豆包对齐 —— 主播甲=主持人（短问），主播乙=讲解（详细）
# 严格使用「主播甲」「主播乙」中性角色名，不编造"李医生""虞阳"等具体姓名
# （用户反馈：M3 thinking 模式会沿用 host_descriptions 里的人物名，导致角色定位偏离）
DEFAULT_HOSTS_2 = [
    "主播甲：播客主持人，负责开题、引导话题、提问、串联、收尾，每轮 20-60 字，简短有力",
    "主播乙：医学科普专家，负责详细解答医学问题、举临床例子、提醒误区，每轮 60-150 字，深入但聚焦一个点",
]

DEFAULT_HOSTS_3 = [
    "主播甲：播客主持人，负责开题、引导话题、提问、串联、收尾，每轮 20-60 字",
    "主播乙：医学科普专家，负责详细解答医学问题、举临床例子、提醒误区，每轮 60-150 字",
    "主播丙：临床护士/护理师，从患者日常护理和实操角度补充，每轮 50-100 字",
]


def quick_generate(
    api_key: str,
    topic: str,
    *,
    n_speakers: int = 2,
    custom: str = "",
    context: str = "",
) -> List[Dict[str, str]]:
    """便捷函数：2 或 3 主持人，默认风格。

    Args:
        api_key: MiniMax API key
        topic: 本期主题
        n_speakers: 2 或 3
        custom: 用户附加要求
        context: 原始素材（可空）
    """
    hosts = DEFAULT_HOSTS_2 if n_speakers == 2 else DEFAULT_HOSTS_3
    gen = MiniMaxScriptGenerator(api_key=api_key)
    return gen.generate(
        topic=topic,
        host_descriptions=hosts,
        context=context,
        custom=custom,
    )


if __name__ == "__main__":
    # CLI smoke test：直接 python minimax_script_gen.py
    import os
    import sys

    from config import CONFIG

    api_key = os.environ.get("MINIMAX_API_KEY") or CONFIG.get_minimax("api_key", "")
    if not api_key:
        print("用法：MINIMAX_API_KEY=xxx python minimax_script_gen.py", file=sys.stderr)
        sys.exit(1)
    topic = sys.argv[1] if len(sys.argv) > 1 else "高血压患者早餐吃什么"
    print(f"[*] 生成主题：{topic}")
    segs = quick_generate(api_key, topic, n_speakers=2)
    print(f"\n[+] 生成 {len(segs)} 轮：\n")
    for s in segs:
        print(f"【{s['speaker']}】")
        print(s["text"])
        print()
        print("---")
        print()