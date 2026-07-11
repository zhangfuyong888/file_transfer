#!/usr/bin/env bash
# ============================================================
# LAN File Transfer - Linux Launcher
# Double-click this script or run from terminal to start.
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ---- Check Python ----
if ! command -v python3 &>/dev/null; then
    echo "❌ 需要 Python 3，请先安装：sudo apt install python3"
    read -p "按回车键退出..."
    exit 1
fi

# ---- Install Flask if needed ----
if ! python3 -c "import flask" 2>/dev/null; then
    echo "📦 正在安装 Flask..."
    pip install flask --quiet
fi

# ---- Start ----
echo ""
echo "🚀 启动 LAN File Transfer..."
python3 transfer.py "$@"

# Keep window open if launched by double-click
read -p "按回车键退出..."
