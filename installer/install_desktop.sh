#!/usr/bin/env bash
# ============================================================
# Install desktop shortcut + icon for LAN File Transfer (Linux)
# Run:  bash installer/install_desktop.sh
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ICON_SRC="$SCRIPT_DIR/icon.jpg"
ICON_DST="$HOME/.local/share/icons/lan-transfer.jpg"
APPS_DIR="$HOME/.local/share/applications"

# ---- Check icon ----
if [ ! -f "$ICON_SRC" ]; then
    echo "❌ 未找到图标: $ICON_SRC"
    exit 1
fi

# ---- Install icon ----
mkdir -p "$(dirname "$ICON_DST")"
cp "$ICON_SRC" "$ICON_DST"
echo "✅ 图标已安装: $ICON_DST"

# ---- Make start.sh executable ----
chmod +x "$PROJECT_DIR/start.sh"

# ---- Create .desktop file ----
cat > /tmp/lan-transfer.desktop << EOF
[Desktop Entry]
Name=LAN File Transfer
Name[zh_CN]=局域网文件传输
Comment=Transfer files over LAN via web browser
Comment[zh_CN]=通过浏览器在局域网内传输文件
Exec=bash -c "cd '$PROJECT_DIR' && python3 transfer.py; read -p 'Enter to exit...'"
Path=$PROJECT_DIR
Icon=$ICON_DST
Terminal=true
Type=Application
Categories=Network;FileTransfer;
Keywords=lan;transfer;file;share;局域网;传输;文件;分享
EOF

# ---- Install to Desktop ----
if [ -d "$HOME/Desktop" ]; then
    DESKTOP_FILE="$HOME/Desktop/lan-transfer.desktop"
elif [ -d "$HOME/桌面" ]; then
    DESKTOP_FILE="$HOME/桌面/lan-transfer.desktop"
else
    mkdir -p "$HOME/Desktop"
    DESKTOP_FILE="$HOME/Desktop/lan-transfer.desktop"
fi

cp /tmp/lan-transfer.desktop "$DESKTOP_FILE"
chmod +x "$DESKTOP_FILE"
echo "✅ 桌面快捷方式: $DESKTOP_FILE"

# ---- Install to Applications menu ----
mkdir -p "$APPS_DIR"
cp /tmp/lan-transfer.desktop "$APPS_DIR/lan-transfer.desktop"
echo "✅ 应用菜单已注册 (搜索 '局域网' 或 'LAN Transfer')"

rm /tmp/lan-transfer.desktop

echo ""
echo "🎉 安装完成！"
echo "   桌面快捷方式 → 双击启动"
echo "   应用菜单     → 搜索 '局域网'"
