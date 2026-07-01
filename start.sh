#!/bin/bash
# 癌薬物治療アップデートアプリの起動スクリプト

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "エラー: .venv がありません。先に pip install を実行してください。"
  exit 1
fi

source .venv/bin/activate

pick_port() {
  for port in 8000 8001 8002 8003; do
    if ! lsof -i :"$port" >/dev/null 2>&1; then
      echo "$port"
      return 0
    fi
  done
  return 1
}

PORT=$(pick_port)
if [ -z "$PORT" ]; then
  echo "エラー: ポート 8000〜8003 がすべて使用中です。"
  echo "他のアプリを終了するか、次のコマンドで空いているポートを確認してください:"
  echo "  lsof -i :8000"
  exit 1
fi

if [ "$PORT" != "8000" ]; then
  echo "ポート 8000 は使用中のため、ポート ${PORT} で起動します。"
fi

echo "サーバーを起動します: http://127.0.0.1:${PORT}"
exec uvicorn app.main:app --reload --host 127.0.0.1 --port "$PORT"
