#!/bin/bash
# Macのバックグラウンドサービスとして登録するスクリプト

PYTHON=$(which python3)
DIR=$(cd "$(dirname "$0")" && pwd)
PLIST="$HOME/Library/LaunchAgents/com.slack-automation.bot.plist"

echo "Python: $PYTHON"
echo "Dir:    $DIR"
echo "Plist:  $PLIST"

cat > "$PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.slack-automation.bot</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$DIR/main.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>30</integer>
    <key>StandardOutPath</key>
    <string>$DIR/bot.log</string>
    <key>StandardErrorPath</key>
    <string>$DIR/bot.log</string>
</dict>
</plist>
EOF

# 既存のサービスを停止してから再登録
launchctl unload "$PLIST" 2>/dev/null
launchctl load "$PLIST"

echo ""
echo "✅ バックグラウンドサービスとして登録しました"
echo "📋 ログ確認: tail -f $DIR/bot.log"
echo "🛑 停止する場合: launchctl unload $PLIST"
