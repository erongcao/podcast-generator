# 播客生成器音频增强功能设计

> **Goal:** 为播客生成器添加音频预览、片头片尾、音量标准化、多音轨导出功能

**Architecture:** 在现有 `podcast_generator.py` 和 `podcast_gui.py` 基础上扩展，核心逻辑在 generator 中，GUI 添加相应控件。预览使用截断文本（取前 500 字符）实现；片头片尾通过 ffmpeg concat 合并；音量标准化使用 ffmpeg loudnorm 滤镜；多音轨导出通过分别保存分轨文件实现。

**Tech Stack:** Python 3, ffmpeg, customtkinter, websockets

---

## 1. 音频预览

### 功能描述
生成前先创建 30 秒预览片段，用户确认满意后再生成完整音频。

### API 扩展
在 `podcast_generator.py` 添加:

```python
async def generate_preview(
    text: str,
    speakers: list,
    appid: str = None,
    token: str = None,
) -> str:
    """
    生成 30 秒预览音频
    Returns: 预览音频文件路径
    """
    # 截断文本至约 500 字符（约 30 秒）
    preview_text = text[:500]
    result = await generate_podcast(
        text=preview_text,
        speakers=speakers,
        appid=appid,
        token=token,
        output_path="output/preview.mp3",
        only_nlp_text=False,
    )
    return result.get("output_path")
```

### GUI 变更
在 `InputPanel` 添加:
- 预览按钮（预览模式勾选后显示）
- 内置音频播放器（使用 `pygame` 或 `playsound`）

工作流:
1. 用户输入文本，勾选"预览模式"
2. 点击"预览"按钮 → 生成 30 秒音频 → 自动播放
3. 用户满意后点击"生成完整音频"

---

## 2. 片头片尾

### 功能描述
支持三种片头片尾模式，最终与主体音频合并。

### API 扩展
```python
import subprocess
import os

def add_intro_outro(
    main_audio: str,
    intro_path: str = None,
    outro_path: str = None,
    output_path: str = None,
) -> str:
    """
    合并片头 + 主体 + 片尾音频
    Returns: 合并后文件路径
    """
    if output_path is None:
        output_path = main_audio.replace(".mp3", "_with_intro_outro.mp3")

    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        list_file = f.name
        if intro_path:
            f.write(f"file '{intro_path}'\n")
        f.write(f"file '{main_audio}'\n")
        if outro_path:
            f.write(f"file '{outro_path}'\n")

    subprocess.run([
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
        '-i', list_file, '-acodec', 'libmp3lame', output_path
    ], capture_output=True)

    os.unlink(list_file)
    return output_path
```

### GUI 变更
在 `InputPanel` 添加片头片尾设置面板:
- 模式选择：纯音乐 / AI BGM / 用户上传
- 纯音乐：内置几首默认 BGM 供选择
- AI BGM：调用另一个 API 生成（暂用占位符）
- 用户上传：文件选择按钮

### 内置 BGM
使用免费授权的背景音乐文件，放在 `assets/bgm/` 目录。

---

## 3. 音量标准化

### 功能描述
使用 ffmpeg loudnorm 滤镜自动调整音量到标准水平。

### API 扩展
```python
def normalize_audio(input_path: str, output_path: str = None) -> str:
    """
    使用 ffmpeg loudnorm 标准化音量
    Returns: 标准化后文件路径
    """
    if output_path is None:
        output_path = input_path.replace(".mp3", "_normalized.mp3")

    # loudnorm 标准化的目标响度
    subprocess.run([
        'ffmpeg', '-y', '-i', input_path,
        '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11',
        '-acodec', 'libmp3lame', output_path
    ], capture_output=True)

    return output_path
```

### GUI 变更
- 添加"音量标准化"复选框（默认勾选）
- 在音频生成后自动应用（如果勾选）

---

## 4. 多音轨导出

### 功能描述
同时导出分轨版本（男声/女声各自独立）和最终合并版本。

### API 扩展
```python
def export_multitrack(round_files: list, output_dir: str = "output") -> dict:
    """
    导出多音轨版本
    Returns: {
        "combined": 合并版路径,
        "male": 男声轨道路径,
        "female": 女声轨道路径,
    }
    """
    os.makedirs(output_dir, exist_ok=True)

    # 按发言人分组
    male_files = [f for f in round_files if "male" in f]
    female_files = [f for f in round_files if "female" in f]

    combined = os.path.join(output_dir, "podcast_combined.mp3")
    male_track = os.path.join(output_dir, "podcast_male.mp3")
    female_track = os.path.join(output_dir, "podcast_female.mp3")

    # 合并所有
    if round_files:
        concat_files(combined, round_files)

    # 男声分轨
    if male_files:
        concat_files(male_track, male_files)

    # 女声分轨
    if female_files:
        concat_files(female_track, female_files)

    return {
        "combined": combined,
        "male": male_track if male_files else None,
        "female": female_track if female_files else None,
    }

def concat_files(output: str, files: list):
    """使用 ffmpeg 合并多个音频文件"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        list_file = f.name
        for f_path in files:
            f.write(f"file '{f_path}'\n")

    subprocess.run([
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
        '-i', list_file, '-acodec', 'libmp3lame', output
    ], capture_output=True)

    os.unlink(list_file)
```

### GUI 变更
- 添加"多音轨导出"复选框（默认关闭）
- 勾选后生成界面显示三个文件：
  - `podcast_combined.mp3` - 合并版
  - `podcast_male.mp3` - 男声分轨
  - `podcast_female.mp3` - 女声分轨
- 打开文件夹按钮直接打开 output 目录

---

## 文件结构

```
podcast-generator/
├── podcast_generator.py      # 核心生成逻辑
│   ├── generate_preview()    # [NEW] 30秒预览
│   ├── add_intro_outro()     # [NEW] 片头片尾合并
│   ├── normalize_audio()      # [NEW] 音量标准化
│   └── export_multitrack()   # [NEW] 多音轨导出
├── podcast_gui.py            # GUI界面
│   ├── [修改] InputPanel      # 添加片头片尾设置、预览按钮
│   ├── [修改] OutputPanel     # 添加音量标准化、多音轨选项
│   └── [NEW] AudioPlayer     # 内置音频播放器
├── assets/
│   └── bgm/                  # 内置背景音乐
│       ├── calm.mp3
│       ├── energetic.mp3
│       └── minimal.mp3
└── docs/
    └── specs/
        └── 2026-04-07-podcast-audio-enhancement-design.md
```

---

## 实现顺序

1. `generate_preview()` - 最简单，先实现
2. 内置 BGM 资源准备
3. `add_intro_outro()` - 片头片尾合并
4. GUI 片头片尾设置面板
5. `normalize_audio()` - 音量标准化
6. `export_multitrack()` - 多音轨导出
7. GUI 多音轨选项
8. GUI 预览播放器
9. 集成测试

---

## 依赖

- ffmpeg（已有）
- Python 标准库（已有）
- 无需新增 Python 包
