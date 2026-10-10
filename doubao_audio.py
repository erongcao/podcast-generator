#!/usr/bin/env python3
"""
豆包播客大模型 - 音频后处理

Pure stdlib + subprocess operations:
  - add_intro_outro: ffmpeg concat for intro / main / outro
  - normalize_audio: ffmpeg loudnorm two-pass
  - cleanup_temp_files: remove temp chunks after final output is produced
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from exceptions import AudioProcessingError


# ============== 片头片尾合并 ==============

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

    # 验证主体文件存在
    if not os.path.exists(main_audio):
        raise FileNotFoundError(f"主体音频不存在: {main_audio}")

    # 收集要合并的文件
    files_to_concat = []
    if intro_path and os.path.exists(intro_path):
        files_to_concat.append(intro_path)
    files_to_concat.append(main_audio)
    if outro_path and os.path.exists(outro_path):
        files_to_concat.append(outro_path)

    if len(files_to_concat) == 1:
        return files_to_concat[0]

    # 创建合并列表文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        list_file = f.name
        for fp in files_to_concat:
            f.write(f"file '{os.path.abspath(fp)}'\n")

    try:
        result = subprocess.run(
            ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_file,
             '-acodec', 'libmp3lame', output_path],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg 合并失败: {result.stderr[:200]}")
    finally:
        os.unlink(list_file)

    return output_path


# ============== 音频后处理 ==============

def normalize_audio(input_path: str, output_path: str = None) -> str:
    """
    使用 ffmpeg loudnorm 标准化音量

    Args:
        input_path: 输入音频文件路径
        output_path: 输出音频文件路径，默认在原文件后加 _normalized

    Returns:
        标准化后文件路径

    Raises:
        AudioProcessingError if ffmpeg fails or not found
    """
    import shutil
    import subprocess

    # Check ffmpeg availability
    if not shutil.which('ffmpeg'):
        raise AudioProcessingError("ffmpeg not found in PATH, cannot normalize audio")

    if output_path is None:
        # 避免重复添加 _normalized 后缀
        if input_path.endswith("_normalized.mp3"):
            output_path = input_path
        else:
            output_path = input_path.replace(".mp3", "_normalized.mp3")

    result = subprocess.run([
        'ffmpeg', '-y', '-i', input_path,
        '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11',
        '-ar', '48000',   # 锁采样率（防止 ffmpeg 内部 resample 到低采样率）
        '-ac', '1',       # 单声道（与播客输出一致）
        '-acodec', 'libmp3lame',
        '-ab', '320k',    # 锁 320kbps（之前没指定，ffmpeg 默认给到 64kbps，
                          #           砍掉 >16kHz 高频，女声 sibilance 严重失真）
        output_path
    ], capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        raise AudioProcessingError(f"ffmpeg loudnorm failed: {result.stderr}")

    return output_path


def cleanup_temp_files(final_output_path: str = None):
    """
    清理临时文件

    Args:
        final_output_path: 最终输出文件路径（保留）
    """
    import re

    if final_output_path is None:
        final_output_path = "output/podcast_final.mp3"

    final_name = Path(final_output_path).name
    temp_indicators = [
        r'_\d+\.mp3$',             # chunk files: _0.mp3, _1.mp3
        r'_\d+\.txt$',             # concat lists: _0.txt, _1.txt
        r'intro_bgm\.mp3$',
        r'outro_bgm\.mp3$',
    ]

    output_dir = Path("output")
    for f in output_dir.iterdir():
        if f.name == final_name:
            continue
        if any(re.search(pattern, f.name) for pattern in temp_indicators):
            f.unlink(missing_ok=True)