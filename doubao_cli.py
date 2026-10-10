#!/usr/bin/env python3
"""
豆包播客大模型 - 命令行入口

`python -m doubao_cli` or `python doubao_cli.py` parses argparse args
and invokes `generate_podcast` from doubao_podcast.
"""

import argparse
import asyncio

from logger import PodcastTTS

from doubao_podcast import (
    DEFAULT_APP_ID,
    DEFAULT_ACCESS_TOKEN,
    SPEAKERS,
    generate_podcast,
)


# ============== 命令行入口 ==============

def main():
    parser = argparse.ArgumentParser(description="豆包播客 - 文档转语音")
    parser.add_argument("--url", "-u", type=str, help="公网访问的文档/网页 URL")
    parser.add_argument("--text", "-t", type=str, help="直接传入文本内容")
    parser.add_argument("--appid", "-a", type=str, default=DEFAULT_APP_ID, help="APP ID")
    parser.add_argument("--token", "-k", type=str, default=DEFAULT_ACCESS_TOKEN, help="Access Token")
    parser.add_argument("--output", "-o", type=str, default="output/podcast.mp3", help="输出路径")
    parser.add_argument("--speakers", nargs=2, type=str, default=SPEAKERS,
                        help="两位发音人")
    parser.add_argument("--preview", "-p", action="store_true",
                        help="预览模式：只生成台词文本，不生成音频")

    args = parser.parse_args()

    if not args.url and not args.text:
        PodcastTTS.error("[!] 请提供 --url 或 --text 参数")
        PodcastTTS.info("\n用法:")
        PodcastTTS.info("  python podcast_generator.py --url 'https://...' --appid xxx --token xxx")
        PodcastTTS.info("  python podcast_generator.py --text '分析一下...' --appid xxx --token xxx")
        return

    asyncio.run(generate_podcast(
        file_url=args.url,
        text=args.text,
        output_path=args.output,
        appid=args.appid,
        token=args.token,
        speakers=args.speakers,
        only_nlp_text=args.preview,
    ))


if __name__ == "__main__":
    main()