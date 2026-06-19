"""Googleカレンダー OAuth 初回認可スクリプト

ローカルPCで一度だけ実行し、ブラウザで認可すると token.json が生成される。
生成した token.json をBotの稼働環境に配置すれば、以降は自動でリフレッシュされる。

事前準備:
  1. Google Cloud Console でOAuthクライアント(デスクトップアプリ)を作成
  2. クライアントシークレットJSONを credentials.json として配置
  3. python authorize.py を実行
"""

import os

from google_auth_oauthlib.flow import InstalledAppFlow

from calendar_client import CREDENTIALS_FILE, SCOPES, TOKEN_FILE


def main() -> None:
    if not os.path.exists(CREDENTIALS_FILE):
        raise SystemExit(
            f"{CREDENTIALS_FILE} が見つかりません。"
            "Google Cloud ConsoleでOAuthクライアント(デスクトップ)を作成し、"
            "JSONをこのファイル名で配置してください。"
        )

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())
    print(f"✅ 認証完了: {TOKEN_FILE} を保存しました")


if __name__ == "__main__":
    main()
