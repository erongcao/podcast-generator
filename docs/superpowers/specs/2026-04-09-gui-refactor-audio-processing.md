# GUI 拆分重构与音频后处理改进设计

> **Goal:** 重构 GUI 代码结构，实现音量标准化和临时文件清理功能

**Architecture:** 将大型 GUI 文件拆分为独立模块，添加音频后处理和临时文件清理功能

**Tech Stack:** Python 3, customtkinter, ffmpeg

---

## 1. GUI 拆分重构

### 1.1 目标结构

```
podcast-generator/
├── gui/
│   ├── __init__.py
│   ├── main_window.py      # GhibliApp 主窗口
│   ├── panels/
│   │   ├── __init__.py
│   │   ├── input.py        # InputPanel - URL/文本输入
│   │   ├── intro_outro.py  # IntroOutroPanel - 片头片尾
│   │   ├── speakers.py     # SpeakerSelectionPanel - 音色选择
│   │   ├── credentials.py  # CredentialPanel - 凭证输入
│   │   └── output.py       # OutputPanel - 输出设置
│   └── theme.py            # [NEW] 共享主题/常量
├── podcast_generator.py     # [保持不变]
├── minimax_tts.py          # [保持不变]
├── minimax_music.py         # [保持不变]
├── config.py               # [保持不变]
├── exceptions.py           # [保持不变]
├── logger.py               # [保持不变]
└── output/                  # 输出目录
```

### 1.2 模块职责

| 模块 | 职责 |
|------|------|
| `main_window.py` | 主窗口整合、事件调度、生成流程控制 |
| `panels/input.py` | URL/文本输入、生成按钮 |
| `panels/intro_outro.py` | 片头片尾模式选择、AI BGM prompt |
| `panels/speakers.py` | 音色选择（豆包/MiniMax） |
| `panels/credentials.py` | API 凭证输入、MiniMax key |
| `panels/output.py` | 输出路径、后处理选项（音量标准化、临时文件清理） |
| `theme.py` | GHIBLI 配色、共享常量 |

### 1.3 关键接口

**Panel 通信：**
- 每个 Panel 实例通过 `get_value()` 方法返回当前值
- 主窗口在创建 Panel 时保存引用，通过 `self.panels["credentials"].get_credentials()` 访问

**Panel 注册表（main_window.py）：**
```python
class GhibliApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.panels = {}
        self._create_panels()

    def _create_panels(self):
        self.panels["credentials"] = CredentialPanel(self.content.left)
        self.panels["speakers"] = SpeakerSelectionPanel(self.content.left)
        # ...
```

**Panel 示例 (credentials.py)：**
```python
class CredentialPanel(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, ...)
        self.appid_var = ctk.StringVar()
        self.token_var = ctk.StringVar()
        self.minimax_key_var = ctk.StringVar(value=CONFIG.get_minimax("api_key", ""))
        # ... UI setup

    def get_credentials(self):
        return {
            "appid": self.appid_var.get().strip(),
            "token": self.token_var.get().strip(),
        }

    def get_minimax_key(self):
        return self.minimax_key_var.get().strip()
```

---

## 2. 音频后处理

### 2.1 音量标准化

**功能：** 使用 ffmpeg loudnorm 滤镜自动调整音量到标准水平

**目标响度：** I=-16, TP=-1.5, LRA=11 (播客标准)

**实现：**
```python
import shutil

def normalize_audio(input_path: str, output_path: str = None) -> str:
    """
    使用 ffmpeg loudnorm 标准化音量
    Returns: 标准化后文件路径
    Raises: AudioProcessingError if ffmpeg fails
    """
    # Check ffmpeg availability
    if not shutil.which('ffmpeg'):
        raise AudioProcessingError("ffmpeg not found in PATH")

    if output_path is None:
        output_path = input_path.replace(".mp3", "_normalized.mp3")

    result = subprocess.run([
        'ffmpeg', '-y', '-i', input_path,
        '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11',
        '-acodec', 'libmp3lame',
        output_path
    ], capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        raise AudioProcessingError(f"ffmpeg loudnorm failed: {result.stderr}")

    return output_path
```

**GUI 集成：**
- 在 `OutputPanel` 添加 "音量标准化" 复选框
- 默认勾选
- 生成完成后自动应用（如果勾选）

### 2.2 后处理流程

```
生成主音频
    ↓
检查是否需要音量标准化
    ↓ (是)
应用 loudnorm
    ↓
返回最终音频路径
```

---

## 3. 临时文件清理

### 3.1 临时文件来源

- `output/{speaker}_{round_id}.mp3` - 豆包 TTS 分段文件
- `output/minimax_{voice}_{i}.mp3` - MiniMax TTS 分段文件
- `output/intro_bgm.mp3` / `output/outro_bgm.mp3` - AI 生成的 BGM
- ffmpeg concat 临时列表文件

### 3.2 清理策略

**GUI 选项：** 在 `OutputPanel` 添加 "清理临时文件" 复选框
- 默认勾选（清理）
- 用户可取消

**清理时机：** 生成流程完成后

**清理范围：**
- 分段 chunk 文件（`*_0.mp3`, `*_1.mp3` 等）
- AI 生成的 BGM 文件
- ffmpeg 临时列表文件
- 保留最终输出文件和预览文件

**实现：**
```python
def cleanup_temp_files(final_output_path: str = None):
    """
    清理临时文件

    Args:
        final_output_path: 最终输出文件路径（保留），默认 "output/podcast_final.mp3"
    """
    import re

    if final_output_path is None:
        final_output_path = "output/podcast_final.mp3"

    final_name = Path(final_output_path).name
    temp_indicators = [
        r'_\\d+\\.mp3$',           # e.g., _0.mp3, _1.mp3 (chunk files)
        r'_\\d+\\.txt$',           # e.g., concat lists
        r'intro_bgm\\.mp3$',
        r'outro_bgm\\.mp3$',
    ]

    output_dir = Path("output")
    for f in output_dir.iterdir():
        if f.name == final_name:
            continue  # Keep final output
        if any(re.search(pattern, f.name) for pattern in temp_indicators):
            f.unlink(missing_ok=True)
```

---

## 4. 实现顺序

### Phase 1: GUI 拆分
1. 创建 `gui/` 目录结构
2. 创建 `theme.py` 移动 GHIBLI 配色
3. 拆分 `panels/credentials.py`
4. 拆分 `panels/speakers.py`
5. 拆分 `panels/intro_outro.py`
6. 拆分 `panels/input.py`
7. 拆分 `panels/output.py`
8. 创建 `main_window.py` 整合所有面板
9. **删除旧的 `podcast_gui.py`**（验证新 GUI 运行正常后）
10. 测试 GUI 运行

### Phase 2: 音频后处理
10. 在 `podcast_generator.py` 添加 `normalize_audio()`
11. 在 GUI 生成流程中集成音量标准化
12. 测试音量标准化

### Phase 3: 临时文件清理
13. 在 `podcast_generator.py` 添加 `cleanup_temp_files()`
14. 在 GUI 添加 "清理临时文件" 复选框
15. 在生成流程中集成清理
16. 整体测试

---

## 5. 向后兼容

- 所有新功能默认启用（用户无感知）
- 配置文件 `config.json` 添加新选项：
```json
{
  "post_processing": {
    "normalize_audio": true,
    "cleanup_temp_files": true
  }
}
```
- 如果配置缺失，使用默认值 true

### 5.1 GUI 配置持久化

- GUI 启动时从 `config.json` 读取后处理选项
- 用户修改选项后立即保存到 `config.json`
- `OutputPanel` 的复选框初始化时读取配置：
```python
class OutputPanel(ctk.CTkFrame):
    def __init__(self, parent):
        # ...
        self.normalize_var = ctk.BooleanVar(
            value=CONFIG.get("post_processing", "normalize_audio", default=True)
        )
        self.cleanup_var = ctk.BooleanVar(
            value=CONFIG.get("post_processing", "cleanup_temp_files", default=True)
        )
        # ...

    def _save_config(self):
        """保存选项到配置"""
        config_data = json.loads(CONFIG_FILE.read_text())
        config_data.setdefault("post_processing", {})
        config_data["post_processing"]["normalize_audio"] = self.normalize_var.get()
        config_data["post_processing"]["cleanup_temp_files"] = self.cleanup_var.get()
        CONFIG_FILE.write_text(json.dumps(config_data, indent=2))
```
