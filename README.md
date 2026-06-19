# Slack カレンダー自動化 Bot

Claude AI を使って、Slack のメンションやDMから自然言語で Google カレンダーの
**空き状況の確認**と**予定の自動登録**を行う Bot です。

> 例: `@Bot 来週の空いている時間に1時間の打ち合わせを入れて`
> → Claude が空き枠を検索し、予定を作成してリンクを返します。

## 仕組み

Slack で受け取ったメッセージを Claude の **tool use (function calling)** で解釈し、
以下のツールを自動で呼び出します。

| ツール | 役割 |
|--------|------|
| `check_calendar_availability` | 指定期間の予定済み(busy)区間を取得 |
| `find_free_slots` | 営業時間内の空き枠を検索 |
| `list_calendar_events` | 予定一覧を取得 |
| `create_calendar_event` | 予定を作成 |

## セットアップ

### 1. Slack App の作成

1. https://api.slack.com/apps にアクセスして「Create New App」→「From scratch」
2. **OAuth & Permissions** → Bot Token Scopes に以下を追加:
   - `app_mentions:read`
   - `chat:write`
   - `im:history`
   - `im:read`
   - `im:write`
3. **Socket Mode** を有効化 → App-Level Token を生成 (`connections:write` スコープ)
4. **Event Subscriptions** を有効化 → Subscribe to bot events に以下を追加:
   - `app_mention`
   - `message.im`
5. アプリをワークスペースにインストール

### 2. Google カレンダー (OAuth) の準備

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成
2. **Google Calendar API** を有効化
3. **OAuth 同意画面** を設定 (テストユーザーに自分のアカウントを追加)
4. **認証情報** → OAuth クライアント ID を作成（アプリの種類: **デスクトップ**）
5. クライアントシークレットの JSON をダウンロードし、`credentials.json` として配置
6. 初回認可を実行（ブラウザが開くので自分のGoogleアカウントで許可）:

   ```bash
   python authorize.py
   ```

   → `token.json` が生成されます。以降は自動でリフレッシュされます。
   サーバーで常駐させる場合は、生成した `token.json` を稼働環境に配置してください。

### 3. 環境変数の設定

```bash
cp .env.example .env
# .env を編集して各トークンを設定
```

| 変数名 | 取得場所 / 既定値 |
|--------|------------------|
| `SLACK_BOT_TOKEN` | OAuth & Permissions → Bot User OAuth Token (`xoxb-...`) |
| `SLACK_APP_TOKEN` | Basic Information → App-Level Tokens (`xapp-...`) |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com |
| `CLAUDE_MODEL` | 使用するモデル (既定 `claude-opus-4-8`) |
| `TIMEZONE` | タイムゾーン (既定 `Asia/Tokyo`) |
| `GOOGLE_CALENDAR_ID` | 操作対象カレンダー (既定 `primary`) |

### 4. 依存関係のインストールと起動

```bash
pip install -r requirements.txt
python main.py
```

## 使い方

- **メンション** (`@Bot ...`): スレッドに返信
- **DM**: ダイレクトメッセージに返信

### 依頼例

- `明日の午後で空いている時間を教えて`
- `来週水曜の14時から1時間「定例MTG」を入れて`
- `今週中で30分の打ち合わせを空いてる枠に入れて`
- `6/25の予定を一覧で見せて`

予定作成に必要な情報（タイトル・日時）が不足している場合は、Bot が確認します。

## セキュリティ注意

- `credentials.json` と `token.json` は秘密情報です。リポジトリにコミットしないでください
  (`.gitignore` 推奨)。
- Bot は受け取ったメッセージに従ってカレンダーへ予定を作成します。共有チャンネルで
  使う場合は、誰が操作できるかに注意してください。
