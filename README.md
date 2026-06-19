# Slack 自動返信 Bot

Claude AI を使って Slack のメンションやDMに自動返信するBotです。

## セットアップ

### 1. Slack App の作成

1. https://api.slack.com/apps にアクセスして「Create New App」
2. 「From scratch」を選択してアプリ名とワークスペースを設定
3. **OAuth & Permissions** → Bot Token Scopes に以下を追加:
   - `app_mentions:read`
   - `chat:write`
   - `channels:history`
   - `channels:read`
   - `groups:history`
   - `groups:read`
   - `im:history`
   - `im:read`
   - `im:write`
4. **Socket Mode** を有効化 → App-Level Token を生成 (`connections:write` スコープ)
5. **Event Subscriptions** を有効化 → Subscribe to bot events に以下を追加:
   - `app_mention`
   - `message.im`
6. アプリをワークスペースにインストール
7. ボットを `#repitte-beauty-contract` チャンネルに招待する (`/invite @Bot名`)

### 2. 環境変数の設定

```bash
cp .env.example .env
# .env を編集して各トークンを設定
```

| 変数名 | 説明 |
|--------|---------|
| `SLACK_BOT_TOKEN` | OAuth & Permissions → Bot User OAuth Token (`xoxb-...`) |
| `SLACK_APP_TOKEN` | Basic Information → App-Level Tokens (`xapp-...`) |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com |
| `CONTRACT_CHANNEL_NAME` | 契約報告チャンネル名（`#` 不要、デフォルト: `repitte-beauty-contract`） |
| `PROCESSED_REACTION` | 処理済みを示すリアクション名（コロンなし、デフォルト: `武居_済み`） |
| `UNPROCESSED_SEARCH_DAYS` | 未処理検索対象日数（デフォルト: `30`） |

### 3. 依存関係のインストールと起動

```bash
pip install -r requirements.txt
python main.py
```

## 動作

- **メンション** (`@Bot 質問`): スレッドに返信
- **DM**: ダイレクトメッセージに返信
- **未処理一覧** (`@Bot 未処理` または DM で `未処理`): `#repitte-beauty-contract` チャンネルで `:武居_済み:` リアクションがついていない報告を一覧表示

### 未処理一覧の起動方法

任意のチャンネルまたはDMで以下のいずれかを送信する:

```
@Bot 未処理
@Bot 一覧
@Bot 未済
@Bot リスト
```

表示例:
```
未処理の契約報告 3件（過去30日間）
• 2026/06/10 14:32　THERUBY　メッセージを見る
• 2026/06/15 09:10　サロン名　メッセージを見る
• 2026/06/19 11:45　別サロン　メッセージを見る
```

`SYSTEM_PROMPT` 環境変数でAIの返答スタイルをカスタマイズできます。
