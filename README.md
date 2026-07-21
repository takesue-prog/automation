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
   - `reactions:read`
4. **Socket Mode** を有効化 → App-Level Token を生成 (`connections:write` スコープ)
5. **Event Subscriptions** を有効化 → Subscribe to bot events に以下を追加:
   - `app_mention`
   - `message.im`
   - `reaction_added`
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

### 3. Google Sheets 連携の設定（任意）

`:武居_済み:` スタンプを押した際にスプレッドシートを自動更新する機能です。

#### 3-1. GCPサービスアカウントの作成

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成（または既存を使用）
2. **APIとサービス** → **ライブラリ** → 「Google Sheets API」を有効化
3. **APIとサービス** → **認証情報** → 「サービスアカウントを作成」
4. 作成したサービスアカウントの **キー** タブ → 「鍵を追加」→「JSONキーを作成」
5. ダウンロードしたJSONファイルを `credentials.json` という名前でプロジェクトフォルダに配置

#### 3-2. スプレッドシートの共有

1. スプレッドシートを開き「共有」ボタンをクリック
2. サービスアカウントのメールアドレス（`xxx@xxx.iam.gserviceaccount.com`）を追加
3. 権限を「編集者」に設定

#### 3-3. スプレッドシートの構成

シートの行ラベルが以下と一致していることを確認してください（異なる場合は `sheets.py` の定数を修正）：

| 定数名 | デフォルト値（シート内の行ラベル） |
|--------|-------------------------------|
| `LABEL_FEE_INDIVIDUAL` | `B個人初期費用` |
| `LABEL_FEE_STORE_9800` | `B店舗初期費用（¥9,800）` |
| `LABEL_FEE_STORE_4900` | `B店舗初期費用（半額）` |
| `PLAN_LABEL["B個人"]` | `B個人　個人プラン` |
| `PLAN_LABEL["B店舗"]` | `B店舗　店舗プラン` |
| `PLAN_LABEL["LMP個人"]` | `LMP版　個人` |
| `PLAN_LABEL["LMP店舗"]` | `LMP版　店舗` |
| `OPTION_LABELS["チャット"]` | `B個人　チャット` / `B店舗　チャット` |
| …（他オプションも同様）| … |

セクションのヘッダーラベル（`獲得件数詳細` / `解約件数`）でセクションを自動識別します。

#### 3-4. 環境変数の設定

`.env` に以下を追加：

```
GOOGLE_CREDENTIALS_PATH=credentials.json
SPREADSHEET_ID=1dA7ByXoFeA74GQfVO0eoeFJY-tJ4mLTjD2Bthvv0_Tk
GOOGLE_SHEET_NAME=           # 空白 = 今月のシートタブを自動検索（例: 「7月」）
GOOGLE_DATA_COLUMN=2         # データ列の番号（1=A, 2=B, …）
```

### 4. 依存関係のインストールと起動

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
