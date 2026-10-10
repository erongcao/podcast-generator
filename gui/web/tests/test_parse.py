"""
test_parse.py — parse 回归测试

用用户 2026-10-09 发来的 32 段真实样本（脾脏硬度播客脚本，16 对甲乙对话）做基准测试。

回归目标：
    1. parse 段数 = 32
    2. speaker 严格交替：甲 乙 甲 乙 ... × 16
    3. 任何 body 不为空
    4. parse 失败时**必须** raise（不再静默 fallback）

跑法：
    cd ~/Documents/podcast-generator
    ~/.podcast-automation/.venv/bin/python -m gui.web.tests.test_parse
"""
from __future__ import annotations

import sys
from pathlib import Path

# 把项目根加进 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gui.web.api.parse import parse_script_lines  # noqa: E402

# 失败时让 assert 错误更清楚
def _assert(cond, msg):
    if not cond:
        print(f"❌ FAIL: {msg}")
        sys.exit(1)
    print(f"  ✅ {msg}")


# ============================================================
# 用户 2026-10-09 发来的真实 22 段样本（脾脏硬度）
# 完整粘贴，逐字保留；只把正文里可能混淆的字符（tab 等）规范了一下
# ============================================================
USER_REAL_SAMPLE = """---

【主播甲】
上期我们聊了脾脏硬度能帮忙决定要不要做胃镜，结果好多听众追问——脾脏硬度除了这一招还能干嘛？今天咱们就继续深挖。

---

【主播乙】
这位听众问到点子上了。脾脏硬度的用途其实远比"要不要做胃镜"要广。从了解门静脉高压的严重程度，到预测未来出现腹水、出血这些并发症的风险，再到观察治疗效果、甚至发现一些"长得像肝硬化但其实不是"的特殊肝病，它都有用。今天我们一个一个聊。

---

【主播甲】
门静脉高压这词我听过好几次了，但一直没太太搞明白——到底是个什么东西？怎么肝硬化就把它"压"高了？

---

【主播乙】
这要先把门静脉搞清楚。你可以把它想象成一条连接胃肠道、脾脏、胰腺和肝脏的大血管，专门负责把这些器官的血液运到肝脏去处理。肝硬化的时候，肝脏里柔软的肝细胞被纤维疤痕挤压、血管通道被挤扁，血液流不过去，门静脉这头的压力自然就上去了——就像城市排水管堵了，上游的水位会越涨越高。

---

【主播甲】
等等，你前面说肝脏像"吸满血的海绵"，这个比喻我特别喜欢。能再展开讲讲吗？

---

【主播乙】
健康的肝脏确实像一块吸满水的海绵——里面有大量柔软的肝细胞、细小的血管和空隙，医学上叫"肝窦"。血液从门静脉进来顺畅流过、被肝脏处理后从另一侧流出。但肝硬化后，肝细胞反复坏死、修复，纤维疤痕越积越多，海绵孔隙被堵死，血管通道被挤扁。门静脉这头送来的血堵在"上游"，压力蹭蹭往上涨——这就是门静脉高压。

---

【主播甲】
那门静脉压力高了，血液会往哪儿去？总不能就在原地憋着吧？

---

【主播乙】
你这问到关键了。压力是会"上传"的。脾脏的血液本来是通过脾静脉流进门静脉、再进肝脏的。现在肝脏"出口"堵了、门静脉压力高，脾脏的血就流不出去——倒灌、淤积在里面。脾脏长期泡在高压血液里，会慢慢肿胀、纤维化，变得越来越硬。所以脾脏硬度升高，本质上是在替门静脉高压"代言"。

---

【主播甲】
也就是说，脾脏硬度其实是门静脉高压的一个"信号灯"？那医生平时怎么用这个信号？

---

【主播乙】
没错。测量脾脏硬度——比如用 FibroScan 这类仪器——可以帮助医生判断门静脉高压的严重程度。临床上医生通常会把脾脏硬度、肝脏硬度、血小板计数、超声影像这些指标放在一起综合看，对门静脉高压的病情做一个整体评估。

---

【主播甲】
我朋友他爸就是肝硬化，每次听他说"脾大"我就紧张。脾脏变硬变大，是不是身体已经在报警了？

---

【主播乙】
别紧张，但确实要重视。脾脏硬度升高反映的是门静脉高压的存在，但它本身不会直接让病人难受——真正让人担心的是门静脉高压带来的一系列并发症，比如腹水、消化道大出血、肝性脑病，这些才是医生真正警惕的。下期我们会专门聊这些并发症怎么提前识别。今天我们要说的是，脾脏硬度还有"预测未来"的能力。

---

【主播甲】
等等——预测未来？脾脏硬度还能告诉医生"以后会不会出事"？

---

【主播乙】
是的。越来越多的研究发现，脾脏硬度的高低与肝硬化患者未来发生失代偿事件的风险有关。比如一个 50 岁的乙肝肝硬化患者，最近脾脏硬度比较高，那医生就要在心里打个问号——他未来出现腹水、消化道出血、肝性脑病的风险会不会更高？是不是需要更密集的随访、更积极的预防治疗？这是脾脏硬度作为"风险评估工具"的意义。

---

【主播甲】
那是不是脾脏硬度越高，风险就越大？有没有什么切点或者具体数字？

---

【主播乙】
目前比较公认的一个参考切点是脾脏硬度 40 kPa 左右——低于这个数相对安全，明显高于这个数，未来发生失代偿的风险会显著升高。当然具体到每个人，还得结合肝脏硬度、血小板、肝功能分级、影像学表现综合判断。我经常跟病人打比方，脾脏硬度就像天气预报里的"气压计"——气压低可能预示暴风雨要来，但具体下不下雨、几点下，还得看其他气象数据。

---

【主播甲】
哎，这个类比好。不过我得追问一句——40 kPa 这个切点是不是全世界通用？不同仪器、不同人种会不会有差别？

---

【主播乙】
这是个好问题。40 kPa 是一个比较公认的参考值，但不是绝对的"金标准"。不同厂家的仪器、不同病因导致的肝硬化、不同人种甚至不同 BMI 的患者，脾脏硬度的数值都可能存在差异。所以医生用这个切点时，往往会留一个"安全余量"——低于某个数才放心，高于某个数才高度警惕，介于两者之间就叫"灰区"，需要结合其他检查综合判断。

---

【主播甲】
明白，"灰区"这个概念我之前没想到……那反过来——如果治疗之后脾脏硬度降下来了，是不是说明病情真的好转了？

---

【主播乙】
没错，这就是第三个用途——观察治疗效果。这几年医学界有两个热词——"肝纤维化逆转"和"肝硬化再代偿"。意思是有些患者通过积极的病因治疗，比如乙肝病毒长期抑制、戒酒、减重，肝脏纤维化可以一定程度改善，门静脉压力也可能下降。这个过程中，肝脏硬度下降，脾脏硬度往往也跟着降。所以医生会把它当成治疗反应的"风向标"，定期监测、动态观察。

---

【主播甲】
我妈就是慢性乙肝，她每次复查都紧张得要命。要是能看到脾脏硬度的数字一点点往下走，她估计能安心不少。

---

【主播乙】
这种心理我特别理解。对长期管理的患者来说，"看见数字变化"本身就是一种正向反馈——意味着治疗没白做、生活方式调整没白坚持。当然也要提醒一句：脾脏硬度短期内波动受很多因素影响——比如饮食、当天状态、是否有腹水——不能只看一次结果就下结论，要在长期随访中看趋势。

---

【主播甲】
对，听说有些指标"单次看不准，得看趋势"。那除了看趋势，还有什么情况是医生会特别盯脾脏硬度的？

---

【主播乙】
这就要说到第四个用途——发现一些"特殊的肝病"。临床上有一类情况，门静脉还没进肝脏就被堵了，比如门静脉本身有血栓、狭窄，或者脾静脉有问题。这种叫"窦前性门静脉高压"。这类病人有一个非常有意思的特征——脾脏明显增大、脾脏硬度明显升高，但肝脏硬度却基本正常。

---

【主播甲】
等等，这个逻辑我得消化一下——肝不硬但脾很硬，问题反而更复杂？医生看到这个组合会怎么想？

---

【主播乙】
你问到点子上了。看到这种"肝不硬、脾却很硬"的不匹配，医生第一反应就是——肝脏本身可能没事，问题出在门静脉或脾静脉本身。这类情况叫"非肝硬化性门静脉高压"。我有个印象深刻的案例，一个 40 多岁女性，偶然体检发现脾脏明显增大，但肝脏硬度完全正常，进一步检查才发现是门静脉海绵样变性——门静脉主干堵了，周围长出小血管代偿。

---

【主播甲】
这种"肝脾不匹配"的情况常见吗？是不是只有大医院才能查出来？

---

【主播乙】
并不罕见，只是容易被漏诊。在基层医院，如果只盯着肝脏那一套检查，很容易把这类病人当成"普通肝硬化"处理。所以现在越来越强调，肝硬化患者的评估要"肝脏+脾脏+血管"一起看。如果发现这种不匹配，最好到有经验的肝胆中心或血管介入科，进一步做增强 CT 或 MRI 血管成像，明确门静脉系统到底有没有狭窄或血栓。

---

【主播甲】
那总结一下，脾脏硬度到底是个什么样的"角色"？是核心检查还是辅助参考？

---

【主播乙】
这要划重点——脾脏硬度虽然用途越来越多，但它不是一项能单独判断病情的"万能检查"。医生通常需要把脾脏硬度、肝脏硬度、血小板、肝功能、超声或 CT 这些放在一起，才能对患者病情作出更准确的判断。用一句话概括：脾脏硬度是肝硬化管理工具箱里越来越重要的一件"工具"，但单靠这一件工具做决定，不够稳。

---

【主播甲】
好的，信息量有点大。最后帮我划一下重点？普通肝病患者和家属最该记住哪几件事？

---

【主播乙】
好，帮你梳四件事。第一，脾脏硬度升高反映的是门静脉高压，本质上是肝病严重程度的"信号灯"。第二，对肝硬化患者有三层意义——判断门静脉高压、预测未来并发症风险、观察治疗反应。第三，也是最容易漏诊的一点——"肝不硬、脾却很硬"的不匹配，要警惕非肝硬化性门静脉高压，需进一步查血管。最后，永远不要只看一个指标，必须结合肝脏硬度、血小板、肝功能、超声或 CT 综合判断。
"""


# ============================================================
# Test cases
# ============================================================
def test_user_real_sample():
    """用户 2026-10-09 发的脾脏硬度样本：32 段（16 对甲乙），必须 parse 对。"""
    print("\n=== test_user_real_sample ===")
    segments = parse_script_lines(USER_REAL_SAMPLE)
    _assert(len(segments) == 32, f"段数 = 32（实际 {len(segments)}）")

    # speaker 严格交替：甲 乙 甲 乙 ...（共 16 对）
    expected_speakers = ["主播甲", "主播乙"] * 16
    actual_speakers = [s["speaker"] for s in segments]
    _assert(actual_speakers == expected_speakers,
            f"speaker 交替模式正确（甲乙甲乙... × 16）")

    # 每段 body 都非空
    empty = [i for i, s in enumerate(segments) if not s["text"].strip()]
    _assert(len(empty) == 0, f"无空 body 段（空段: {empty}）")

    # 每段 body >= 10 字（不是只剥了 marker）
    too_short = [i for i, s in enumerate(segments) if len(s["text"]) < 10]
    _assert(len(too_short) == 0, f"无 < 10 字的段（短段: {too_short}）")

    # 至少 15 段正文 >= 50 字（说明正文真解析出来了，不是只剩 marker）
    long_count = sum(1 for s in segments if len(s["text"]) >= 50)
    _assert(long_count >= 15, f"15+ 段正文 >= 50 字（实际 {long_count}）")

    # 总字数 >= 2000（用户脚本正文 5000+ 字，解析至少要拿大头）
    total_chars = sum(len(s["text"]) for s in segments)
    _assert(total_chars >= 2000, f"总字数 >= 2000（实际 {total_chars}）")


def test_inline_legacy_format():
    """老格式 `[主播甲]: 你好` 仍能 parse（向后兼容）。"""
    print("\n=== test_inline_legacy_format ===")
    text = "[主播甲]: 大家好\n[主播乙]: 今天聊便秘"
    segs = parse_script_lines(text)
    _assert(len(segs) == 2, f"老格式解析 2 段（实际 {len(segs)}）")
    _assert(segs[0]["speaker"] == "主播甲" and segs[0]["text"] == "大家好",
            "老格式段 1 正确")
    _assert(segs[1]["speaker"] == "主播乙" and segs[1]["text"] == "今天聊便秘",
            "老格式段 2 正确")


def test_short_label_format():
    """短标签 `甲: / A: / 1:` 仍能 parse。"""
    print("\n=== test_short_label_format ===")
    text = "甲: 第一个\n乙: 第二个\nA: 第三个\n1: 第四个"
    segs = parse_script_lines(text)
    _assert(len(segs) == 4, f"4 段（实际 {len(segs)}）")
    _assert([s["speaker"] for s in segs] == ["甲", "乙", "A", "1"],
            "短标签 speaker 正确")


def test_full_width_bracket_only():
    """全角 `【】` 无冒号（用户真实格式）单独成行。"""
    print("\n=== test_full_width_bracket_only ===")
    text = """---

【主播甲】
第一段正文

---

【主播乙】
第二段正文"""
    segs = parse_script_lines(text)
    _assert(len(segs) == 2, f"2 段（实际 {len(segs)}）")
    _assert(segs[0]["text"] == "第一段正文", f"段 1 正文正确（{segs[0]['text']!r}）")
    _assert(segs[1]["text"] == "第二段正文", f"段 2 正文正确（{segs[1]['text']!r}）")


def test_mixed_format():
    """混合格式：用户用 `【】` 但部分老段用了 `[]:`。"""
    print("\n=== test_mixed_format ===")
    text = """---

【主播甲】
新格式正文

---

[主播乙]: 老格式正文"""
    segs = parse_script_lines(text)
    _assert(len(segs) == 2, f"混合格式 2 段（实际 {len(segs)}）")
    _assert(segs[0]["speaker"] == "主播甲" and segs[0]["text"] == "新格式正文",
            "新格式段正确")
    _assert(segs[1]["speaker"] == "主播乙" and segs[1]["text"] == "老格式正文",
            "老格式段正确")


def test_no_marker_raises():
    """完全没 marker 时必须 raise，不静默 fallback。"""
    print("\n=== test_no_marker_raises ===")
    text = "这是一段完全没有 marker 的文字。主播甲主播乙 这些都不会被识别。"
    try:
        parse_script_lines(text)
        _assert(False, "应该 raise 但没 raise")
    except ValueError as e:
        _assert("marker" in str(e).lower() or "解析失败" in str(e),
                f"parse 失败时 raise ValueError（msg: {str(e)[:60]}...）")


def test_empty_raises():
    """空脚本 / None 必须 raise。"""
    print("\n=== test_empty_raises ===")
    for empty in ["", "   ", "\n\n\n"]:
        try:
            parse_script_lines(empty)
            _assert(False, f"空脚本 {empty!r} 应该 raise 但没 raise")
        except ValueError:
            pass
    _assert(True, "空脚本 / None 都 raise")


def test_leading_text_raises():
    """marker 之前有未标记文字时必须 raise。"""
    print("\n=== test_leading_text_raises ===")
    text = "前面有一段没有 marker 的文字\n【主播甲】\n正文"
    try:
        parse_script_lines(text)
        _assert(False, "应该 raise 但没 raise")
    except ValueError as e:
        _assert("开头" in str(e) or "未标记" in str(e),
                f"未标记开头文字时 raise（msg: {str(e)[:60]}...）")


# ============================================================
# Runner
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("parse.py 回归测试")
    print("=" * 60)
    tests = [
        test_user_real_sample,        # 重点：用户的 22 段样本
        test_full_width_bracket_only,  # 用户格式（无冒号）
        test_inline_legacy_format,     # 老格式兼容
        test_short_label_format,       # 短标签兼容
        test_mixed_format,             # 混合格式
        test_no_marker_raises,         # 失败 raise
        test_empty_raises,             # 空 raise
        test_leading_text_raises,      # 漏 marker raise
    ]
    failed = []
    for t in tests:
        try:
            t()
        except SystemExit:
            failed.append(t.__name__)
        except Exception as e:
            print(f"❌ EXCEPTION in {t.__name__}: {e}")
            failed.append(t.__name__)

    print("\n" + "=" * 60)
    if failed:
        print(f"❌ {len(failed)} 个测试失败: {failed}")
        sys.exit(1)
    else:
        print(f"✅ 全部 {len(tests)} 个测试通过")
        sys.exit(0)
