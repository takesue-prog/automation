# Slack 自動返信 Bot

Claude AI を使って Slack のメンションやDMに自動返信するBotです。

## セットアップ

### 1. Slack App の作成

1. https://api.slack.com/apps にアクセスして「Create New App」
2. 「From scratch」を選択してアプリ名とワークスペースを設定
3. **OAuth & Permissions** → Bot Token Scopes に以下を追加:
   - `app_mentions:read`
   - `chat:write`
   - `im:history`
   - `im:read`
   - `im:write`
   - `channels:read`
   - `reactions:read`
4. **Socket Mode** を有効化 → App-Level Token を生成 (`connections:write` スコープ)
5. **Event Subscriptions** を有効化 → Subscribe to bot events に以下を追加:
   - `app_mention`
   - `message.im`
   - `reaction_added`
6. アプリをワークスペースにインストール

### 2. 環境変数の設定

```bash
cp .env.example .env
# .env を編集して各トークンを設定
```

| 変数名 | 取得場所 |
|--------|---------|
| `SLACK_BOT_TOKEN` | OAuth & Permissions → Bot User OAuth Token (`xoxb-...`) |
| `SLACK_APP_TOKEN` | Basic Information → App-Level Tokens (`xapp-...`) |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com |

### 3. 依存関係のインストールと起動

```bash
pip install -r requirements.txt
python main.py
```

## 動作

- **メンション** (`@Bot 質問`): スレッドに返信
- **DM**: ダイレクトメッセージに返信

`SYSTEM_PROMPT` 環境変数でAIの返答スタイルをカスタマイズできます。
