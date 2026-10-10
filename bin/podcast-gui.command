#!/usr/bin/env bash
# macOS 双击启动包装 —— Finder 双击 .command 文件会新开 Terminal 跑它
# 真实逻辑在 podcast-gui.sh 里
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$DIR/podcast-gui.sh" "$@"
