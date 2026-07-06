# 医学論文検索

癌薬物治療に関連する重要論文20件を、日本語で読める形にまとめるWebアプリです。

## 表示内容

各論文について、次を表示します。

1. **英文タイトル**
2. **タイトル和訳**（化合物名・薬剤名は英語のまま）
3. **Abstract和文要約**（常時表示）
4. **Abstract全文和訳**（ボタンクリックで表示）

## 必要なもの

- Python 3.9以上
- **Anthropic Claude APIキー**（和訳・要約に使用）
- **メール送信設定**（SMTP）と送信先メールアドレス

## セットアップ

```bash
cd ~/Projects/cancer-drug-updates
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### APIキーの取得（ブラウザで行う）

1. https://console.anthropic.com/settings/keys を開く
2. Anthropicアカウントでログイン（なければ新規登録）
3. 「Create Key」でキーを作成
4. キーは **`sk-ant-` で始まります** — 表示されたらすぐコピー（再表示できないことがあります）
5. **Cursorのエディタ**で `.env` を開き、次のように貼り付け:

```
ANTHROPIC_API_KEY=sk-ant-（ここにコピーしたキー）
```

6. 課金設定が必要な場合: https://console.anthropic.com/settings/billing

### メール送信の設定（Cursorのエディタで `.env` に追加）

送信先メールアドレスは **Web画面で毎回入力** します。`.env` には SMTP（送信の仕組み）だけ設定します。

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=送信元のGmailアドレス
SMTP_PASSWORD=Gmailのアプリパスワード
```

Gmail を使う場合は、Googleアカウントで「アプリパスワード」を作成して `SMTP_PASSWORD` に設定してください。

処理完了後、論文一覧は **指定メールアドレスにコンパクトなテキスト** で送られます（英文タイトルのみ太字）。

## 起動

```bash
./start.sh
```

または:

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

ブラウザで http://127.0.0.1:8000 を開きます。

## メール送信が失敗するとき

以前は動いていたのに `535 Username and Password not accepted` が出る場合、**Google 側でアプリパスワードが無効になった**ことが多いです（Gmail のパスワード変更、セキュリティ通知など）。

### 確認コマンド（Cursor のターミナル）

```bash
cd ~/Projects/cancer-drug-updates
source .venv/bin/activate
python3 scripts/test_smtp.py
```

`OK` と出れば設定は正しいです。`NG` の場合は次を試してください。

1. https://myaccount.google.com/apppasswords を開く
2. 古いアプリパスワードを削除
3. 新しいアプリパスワードを作成
4. `.env` の `SMTP_PASSWORD` を新しい値に置き換え（スペース付きでも可）
5. `./start.sh` でアプリを再起動

Gmail のアプリパスワードは **スペースを除いて16文字** です。スペースなしで19文字になっている場合は、余分な文字が入っている可能性があります。

## 注意

- 要約・翻訳はAI（Anthropic Claude）が生成します。必ず原文も確認してください。
- 診療判断には使用しないでください。
