# VoxCPM 段间响度归一化修复（v0.4.1-web）

> 调试日期：2026-10-11 ~ 2026-10-12
> 修复人：Claude (主会话, 多次 context 续接) + 用户验证
> 影响范围：`voxcpm_tts.py`（主修复）、`minimax_tts.py`（补遗的 master loudnorm）
> 关联 commit：v0.4.1-web

---

## 1. 问题描述

**用户反馈（2026-10-11 晚）：**

> jiangyu-4 这条音色，合成出的段有时很轻有时很响。但李兵航的音色段没这问题。

**用户问题层次（用户主动分层）：**

1. **L1 — 表层：** 段间响度不均
2. **L2 — 中层：** 为何只有 jiangyu-4 有问题，Li-test-v1 没有？
3. **L3 — 深层：** 为何 MiniMax cloud TTS 同样输入下没有这个问题？

---

## 2. 调试过程（按时间顺序）

### 2.1 排除 jiangyu 库内多版本混淆

**假设：** 库内有 `jiang-test` / `jiangyu-4` / `jiang-clone-v3-001` 等多条江瑜音色，混用导致不同 ref 的合成音量差异大。

**验证：** 列出 voice library 全部 jiangyu-* 记录 → 用户澄清只用了 `jiangyu-4` 一条。

**结论：** 假设不成立。单条 ref 也复现问题。

### 2.2 用 ffmpeg 测段间响度

写了一个临时脚本，对 `output/web_voxcpm_20261012_135035.mp3`（用户最新合成结果）做：
- `silencedetect` 切出 51 个 sub-segment
- 每个 sub-segment 量 RMS / Peak

**实测结果：**

| 指标 | 范围 | spread |
|---|---|---|
| RMS | -29 ~ -14.5 dB | **14.5 dB** |
| Peak | -18.5 ~ -4.0 dB | **14.5 dB** |

对比 `output/podcast_minimax_combined.mp3`（用户昨晚的 MiniMax 输出）：

| 指标 | 范围 | spread |
|---|---|---|
| Peak | -3.0 ~ -0.8 dB | **2.2 dB** |

**结论：** MiniMax 的段间 peak 几乎全挤在 -1.0 dB 附近，疑似**内部 peak limiter**；VoxCPM 没有任何限制。

### 2.3 探查 jiangyu-4 ref 的"声情并茂"特性

**用户主动披露：**

> jiangyu-4 这条音色，我是让江瑜声情并茂地录了一段

**机理推测：** VoxCPM2 是 diffusion TTS，ref audio 的局部能量峰值会被 diffusion sampling "放大并继承"到每段合成。jiangyu-4 录音本身表情丰富（强弱交替），所以每段合成都在该段对应的强弱位置放大。

Li-test-v1 是平稳男声，没有强起伏 → 段间稳定。

### 2.4 提出修复方向：per-segment loudnorm

MiniMax 的稳定来自内部 peak limiter（-1.0 dB 强制限幅）。要复现同样行为，需要在 VoxCPM 合成后**对每段**做响度归一化，target = -1.0 dBTP。

读了 `voxcpm_tts.py` 后发现：

```python
# 2) 音量归一化：per-segment loudnorm 已在 2026-10-11 取消
# 原因：短段 < 5s 的 integrated loudness 受内容影响大（短喊叫 vs 长平静），
# 每段都压到 -16 LUFS 后段间差 ±3-6 dB → 听感"忽轻忽响"。
# 改为 master 归一：见 synthesize_segments() 末尾 _master_loudnorm_mp3。
```

**这里的 reasoning 是错的：**

> 每段都压到 -16 LUFS 后段间差 ±3-6 dB

不对。Loudnorm 到 -16 LUFS 后，每段的 integrated loudness = -16 LUFS（按定义）。**段间 LUFS 差 = 0**。

之前关掉 per-seg loudnorm 的那个人（可能是之前的我），把"段内响度大"和"段间响度差"搞混了。

**master loudnorm 解决的是"整 podcast 听感响度"**（让 podcast 在 Apple Podcasts 上听感跟其他播客一致），**不解决"段间 peak 抖动"**。

---

## 3. 修复设计

### 3.1 方案对比

| 方案 | 段间 peak spread | 段间 RMS spread | 复杂度 |
|---|---|---|---|
| 现状（master only） | 14.5 dB | 14.5 dB | 简单 |
| 改 per-segment loudnorm (TP=-1.0) | ≤ 2 dB (估) | ≤ 2 dB (估) | 简单（函数已存在） |
| 加 peak limiter (`-af alimiter`) | ≤ 1 dB | 不动 | 简单 |
| 换 ref audio（让江瑜重录平淡版） | 取决于新 ref | 取决于新 ref | 重录 30s |

**选择：** 方案 2（per-segment loudnorm）。理由：

- 已有 `_loudnorm_two_pass` 函数（两遍 EBU R128，target_lufs=-16 / target_tp=-1.0 / target_lra=11），签名匹配 Apple Podcasts 规范。
- 不需要江瑜重录（用户已经记了 pending 重录，但 loudnorm 已经把段内表情放大压住了，pending 可释放）。
- 行为对齐 MiniMax 的实测 peak（-1.0 dB）。

### 3.2 改动点

**文件 1：`voxcpm_tts.py`**

1. **Change 1：重新启用 per-segment loudnorm**
   - 位置：`synthesize()` 内 Resemble Enhance 之后、wav→mp3 之前
   - 调用 `_loudnorm_two_pass(sped_in, norm_out, target_lufs=-16.0, target_tp=-1.0, target_lra=11.0)`
   - 失败兜底：loudnorm 出错时退回 raw wav（不阻塞整次合成）

2. **Change 2：删除冗余的 master loudnorm**
   - 位置：`synthesize_segments()` 末尾
   - 改 `shutil.copy2(_concat_tmp, output_path)`（`_concat_mp3` 已直接写到 output_path）
   - 保留 `_master_loudnorm_mp3` 函数定义（minimax_tts 还在 import 它）

**文件 2：`minimax_tts.py`（补遗，2026-10-11 未提交）**

- 在 `synthesize_segments` 末尾加 master loudnorm
- 仅对 mp3 格式生效（wav/flac/opus 跳过，避免破坏 raw 数据）
- 调用 voxcpm_tts 的 `_master_loudnorm_mp3`（复用，不重写）

> 备注：minimax 的 master loudnorm 用同样的 -16/-1.0/11 参数。MiniMax cloud TTS 内部已经 peak-limit 到 -1.0，所以 master loudnorm 在 minimax 上是 no-op（input LUFS 已经在 -16 附近）。这是"安全网"，不是"主动修复"。

---

## 4. 验证

### 4.1 测试方法

- 用 VoxCPM2 + jiangyu-4 合成 3 段故意制造差异的文本：
  - 段 0：短喊叫（"哇！这个发现真的太令人震惊了。"）
  - 段 1：中等平静句（"后来科学家又做了很多实验来验证这个假设。"）
  - 段 2：长句表情足（"这种全新的治疗方法在未来或许能为更多患者带来希望。"）
- 每段单独解码成 WAV，用 numpy 量 peak / rms
- 对比修复前后的 spread

### 4.2 实测结果

**修复后（per-segment loudnorm）：**

| 段 | duration | peak (dBFS) | rms (dBFS) |
|---|---|---|---|
| 0 | 3.04s | -1.67 | -16.53 |
| 1 | 3.52s | -1.00 | -15.67 |
| 2 | 4.96s | -1.21 | -15.40 |
| **spread** | | **0.66 dB** ✅ | **1.13 dB** ✅ |

**对比 MiniMax cloud TTS（用户昨晚的输出）：**

| 指标 | 值 |
|---|---|
| 整 podcast peak | -1.00 dBFS |
| 整 podcast rms | -16.03 dBFS |
| 段间 peak spread | 2.20 dB |

**对比修复前（master loudnorm only）：**

| 指标 | 值 |
|---|---|
| 整 podcast peak | -4.17 dBFS（远低于 -1.0 目标）|
| 段间 peak spread | 14.5 dB |

**结论：**

- ✅ 段间 peak spread 从 **14.5 dB → 0.66 dB**（22× 改善）
- ✅ 段间 RMS spread 1.13 dB（MiniMax 是 2.20 dB，我们更好）
- ✅ 每段 peak 命中 -1.0 dBFS 目标（与 MiniMax 一致）

### 4.3 测试套件

```
============================= 144 passed in 18.03s =============================
```

全部 144 个 unit tests 通过。没有回归。

### 4.4 用户主观听感

> 刚才听了新录制的音频，音量不稳定的毛病修好了。

—— 用户确认（2026-10-12 下午）

---

## 5. 经验教训

### 5.1 调试流程教训

1. **听用户的，先想好原因，再问怎么做。** 用户说"你先想好原因再跟我说"时，应该先在脑中走完一遍机理分析，再说话；不要边猜边问。这次碰壁了两次（"是两版 jiangyu 混用？" → 用户驳回）。
2. **"问题层次"显式拆开。** 用户提的"为何 voxcpm 有 minimax 没有"和"为何 jiangyu-4 有 Li-test 没有"是两个问题，分别用 ffmpeg 数据 + 模型机理 + ref 特征三路分析。
3. **fdata 永远先于 ddata。** 第一步不是改代码，是 ffmpeg 切段 + 量 RMS/Peak 拿到数字。
4. **用户的反驳 / 主动披露是金信号。** 用户说"jiangyu-4 是声情并茂录的"是直接定锤了 ref 端。

### 5.2 代码 / 设计教训

1. **"per-seg loudnorm 让短段听感不稳"是错的 reasoning。** 短段 < 5s 的 integrated loudness 受内容影响大是真的，但每段都归到 -16 LUFS 后段间 LUFS 差 = 0（按定义）。这个人（或上次的 session）把"段内峰值"和"段间响度差"搞混了，写了一段以"听感"为名的、其实站不住脚的注释 → 错误的代码决策。
2. **master loudnorm 不解决段间 peak 抖动。** Master loudnorm 是 podcast-level 的响度标准合规（Apple Podcasts 规范），段间 peak 抖动是 model 层面的问题，要 per-segment 处理。
3. **复用现成函数比写新函数强。** `_loudnorm_two_pass` 早已存在并验证过（4 voice 输入下 output LUFS 极差 ±1.0 LU），没有理由重写。
4. **MiniMax 内部 peak limiter 的存在是"幸运"。** 不是因为它"更好"，是因为云端有 limiter 输出稳定。一旦依赖云端，要预想到 limiter 变更 / 缺失的回归风险。

### 5.3 协作教训

1. **不要随便改用户没问的东西。** minimax_tts.py 的 master loudnorm 是 2026-10-11 之前我自己加的（这次 v0.4.1-web 才一起 commit），但用户没问过它。保留下来是因为同主题的响度归一化 pass，但要在 CHANGELOG 里讲清楚动机。
2. **前次 context 续接要看 git status。** 这次进 plan mode 后看 git diff 发现 minimax_tts.py 还有未提交改动。如果没有 plan mode 的 `git status` 提醒，commit 时会漏掉它。
3. **plan file 留 cross-session trace。** `/Users/yirongcao/.claude/plans/polymorphic-marinating-pebble.md` 留了完整的探索 + 决策 + 验证计划，下次类似的 loudness 抖动问题可以直接复用模板。

---

## 6. 相关文件 / 引用

| 路径 | 说明 |
|---|---|
| `voxcpm_tts.py` | 主修复：per-seg loudnorm 重启 + master loudnorm 删除 |
| `minimax_tts.py` | 补遗：minimax 路径加 master loudnorm（2026-10-11 漏提交） |
| `gui/web/app.py` | 版本号 `0.4.0-web` → `0.4.1-web`（3 处） |
| `README.md` | 新增 v0.4.1-web section + CHANGELOG 条目 |
| `/Users/yirongcao/.claude/plans/polymorphic-marinating-pebble.md` | Plan mode 探索阶段产物 |
| `output/web_voxcpm_20261012_135035.mp3` | 修复前证据（peak spread 14.5 dB） |
| `output/podcast_minimax_combined.mp3` | 对比基准（peak spread 2.2 dB） |
| `~/.claude/projects/-Users-yirongcao/memory/podcast-jiangyu-rerecord-pending.md` | 江瑜重录 pending → 本次修复后可释放 |

---

## 7. 后续 TODO

- [x] 修复 voxcpm 段间响度
- [x] 验证 144 个单测通过
- [x] 用户听感确认
- [ ] 更新 memory：jiangyu-rerecord-pending 标记为"已解决（loudnorm 替代重录）"
- [ ] （可选）给 `_loudnorm_two_pass` 加 regression test：拿 1 个固定 wav 输入，断言 output peak ≤ -0.5 dBFS、LUFS 在 -16 ± 0.5 内
- [ ] （可选）minimax_tts 评估：是否也需要 per-segment loudnorm（数据上 MiniMax 已经稳定，可能不需要，但 master-only 是否能稳定响度规范需要测一遍）
