#!/bin/zsh
# ====================================================
# MS リサーチツール 一発起動ボタン
# このファイルをダブルクリックするだけで起動します
# ====================================================

# このスクリプトのあるフォルダに移動
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   🚀  MS リサーチツール 起動中...   ║"
echo "╚══════════════════════════════════════╝"
echo ""

# 既に起動中かチェック（ポート8001が使用中の場合は終了）
if lsof -Pi :8001 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "⚠️  既にサーバーが起動しています。ブラウザを開きます..."
    open "http://localhost:8001"
    exit 0
fi

echo "📦 サーバーを起動しています..."
echo "   (起動後にブラウザが自動で開きます)"
echo ""

# バックグラウンドでサーバーを起動
python3 -m uvicorn app_main:app --host 0.0.0.0 --port 8001 --reload > server.log 2>&1 &
SERVER_PID=$!

echo "   サーバーPID: $SERVER_PID"

# サーバーが起動するまで待つ（最大20秒）
echo -n "   起動確認中"
for i in {1..20}; do
    sleep 1
    echo -n "."
    if curl -s http://localhost:8001 > /dev/null 2>&1; then
        echo " ✅"
        break
    fi
    if [ $i -eq 20 ]; then
        echo ""
        echo "❌ 起動に失敗しました。server.log を確認してください。"
        cat server.log | tail -20
        exit 1
    fi
done

echo ""
echo "✅ 起動完了！ブラウザを開きます..."
echo "   URL: http://localhost:8001"
echo ""
echo "   ※ このウィンドウを閉じるとサーバーが停止します"
echo "   ※ 停止するには Ctrl+C を押してください"
echo ""

# ブラウザを開く
open "http://localhost:8001"

# サーバーが止まるまで待機（このウィンドウを開き続ける）
wait $SERVER_PID
echo ""
echo "サーバーが停止しました。"
