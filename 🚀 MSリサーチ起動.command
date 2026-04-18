#!/bin/zsh
# ====================================================
# MS リサーチツール 一発起動ボタン
# このファイルをダブルクリックするだけで起動します
# ====================================================

# このスクリプトのあるフォルダに移動
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

APP_PORT="${APP_PORT:-8002}"
RESEARCH_DB_PATH="${RESEARCH_DB_PATH:-data/history_research_dev.db}"
USE_CAFFEINATE="${USE_CAFFEINATE:-1}"
APP_RELOAD="${APP_RELOAD:-0}"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   🚀  MS リサーチツール 起動中...   ║"
echo "╚══════════════════════════════════════╝"
echo ""

# 既に起動中かチェック
if lsof -Pi :"$APP_PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "⚠️  既にサーバーが起動しています。ブラウザを開きます..."
    open "http://localhost:$APP_PORT"
    exit 0
fi

echo "📦 サーバーを起動しています..."
echo "   (起動後にブラウザが自動で開きます)"
echo "   開発版ポート: $APP_PORT"
echo "   開発版DB: $RESEARCH_DB_PATH"
if [ "$USE_CAFFEINATE" = "1" ] && command -v caffeinate >/dev/null 2>&1; then
    echo "   スリープ防止: ON (画面は設定に応じて消灯可)"
else
    echo "   スリープ防止: OFF"
fi
if [ "$APP_RELOAD" = "1" ]; then
    echo "   自動リロード: ON"
else
    echo "   自動リロード: OFF (低発熱モード)"
fi
echo ""

# バックグラウンドでサーバーを起動
if [ "$APP_RELOAD" = "1" ]; then
    UVICORN_ARGS=(python3 -m uvicorn app_main:app --host 0.0.0.0 --port "$APP_PORT" --reload)
else
    UVICORN_ARGS=(python3 -m uvicorn app_main:app --host 0.0.0.0 --port "$APP_PORT")
fi

if [ "$USE_CAFFEINATE" = "1" ] && command -v caffeinate >/dev/null 2>&1; then
    caffeinate -i env APP_PORT="$APP_PORT" RESEARCH_DB_PATH="$RESEARCH_DB_PATH" \
        "${UVICORN_ARGS[@]}" > server.log 2>&1 &
    SERVER_PID=$!
else
    APP_PORT="$APP_PORT" RESEARCH_DB_PATH="$RESEARCH_DB_PATH" \
        "${UVICORN_ARGS[@]}" > server.log 2>&1 &
    SERVER_PID=$!
fi

echo "   サーバーPID: $SERVER_PID"

# サーバーが起動するまで待つ（最大20秒）
echo -n "   起動確認中"
for i in {1..20}; do
    sleep 1
    echo -n "."
    if curl -s "http://localhost:$APP_PORT" > /dev/null 2>&1; then
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
echo "   URL: http://localhost:$APP_PORT"
echo ""
echo "   ※ ふたを閉じるとMacはスリープするため、放置時はふたを開けたままにしてください"
echo "   ※ 画面はシステム設定どおり自動消灯しても処理は継続します"
echo "   ※ このウィンドウを閉じるとサーバーが停止します"
echo "   ※ 停止するには Ctrl+C を押してください"
echo ""

# ブラウザを開く
open "http://localhost:$APP_PORT"

# サーバーが止まるまで待機（このウィンドウを開き続ける）
wait $SERVER_PID
echo ""
echo "サーバーが停止しました。"
