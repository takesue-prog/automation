import os
import time
from datetime import datetime, timedelta

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from dotenv import load_dotenv

load_dotenv()

app = App(token=os.environ["SLACK_BOT_TOKEN"])

_anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
if _anthropic_api_key:
    import anthropic
    claude = anthropic.Anthropic(api_key=_anthropic_api_key)
else:
    claude = None

SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "あなたはSlackの自動返信アシスタントです。ユーザーの質問に簡潔かつ丁寧に日本語で回答してください。",
)
CONTRACT_CHANNEL_NAME = os.getenv("CONTRACT_CHANNEL_NAME", "repitte-beauty-contract")
PROCESSED_REACTION = os.getenv("PROCESSED_REACTION", "武居_済み")
UNPROCESSED_SEARCH_DAYS = int(os.getenv("UNPROCESSED_SEARCH_DAYS", "30"))


def ask_claude(user_message: str) -> str:
    if not claude:
        return "AI返答機能は設定されていません（ANTHROPIC_API_KEY未設定）。"
    message = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return message.content[0].text


def get_channel_id(client, channel_name: str) -> str | None:
    channel_name = channel_name.lstrip("#")
    cursor = None
    while True:
        kwargs = {"types": "public_channel,private_channel", "limit": 200}
        if cursor:
            kwargs["cursor"] = cursor
        response = client.conversations_list(**kwargs)
        for ch in response.get("channels", []):
            if ch["name"] == channel_name:
                return ch["id"]
        meta = response.get("response_metadata", {})
        cursor = meta.get("next_cursor")
        if not cursor:
            break
    return None


def extract_store_name(text: str) -> str:
    """＜対象店舗＞ブロックから店舗名を抽出する"""
    lines = text.splitlines()
    in_store_block = False
    for line in lines:
        if "＜対象店舗＞" in line:
            in_store_block = True
            continue
        if in_store_block:
            stripped = line.strip()
            if stripped and not stripped.startswith("＜"):
                return stripped
            if stripped.startswith("＜"):
                break
    return ""


def get_unprocessed_reports(client, channel_id: str) -> list[dict]:
    """過去N日間のうち処理済みリアクションのないメッセージを返す"""
    oldest = (datetime.now() - timedelta(days=UNPROCESSED_SEARCH_DAYS)).timestamp()
    unprocessed = []
    cursor = None

    while True:
        kwargs = {"channel": channel_id, "oldest": str(oldest), "limit": 200}
        if cursor:
            kwargs["cursor"] = cursor

        response = client.conversations_history(**kwargs)
        for msg in response.get("messages", []):
            if msg.get("bot_id") or msg.get("subtype"):
                continue
            reactions = [r["name"] for r in msg.get("reactions", [])]
            if PROCESSED_REACTION not in reactions:
                unprocessed.append(msg)

        meta = response.get("response_metadata", {})
        cursor = meta.get("next_cursor")
        if not response.get("has_more") or not cursor:
            break

    # 古い順に並べ替え
    unprocessed.sort(key=lambda m: float(m["ts"]))
    return unprocessed


def format_unprocessed_list(channel_id: str, messages: list[dict]) -> str:
    if not messages:
        return f"✅ 未処理の契約報告はありません（過去{UNPROCESSED_SEARCH_DAYS}日間）"

    lines = [f"*未処理の契約報告 {len(messages)}件*（過去{UNPROCESSED_SEARCH_DAYS}日間）\n"]
    for msg in messages:
        ts = msg["ts"]
        dt = datetime.fromtimestamp(float(ts)).strftime("%Y/%m/%d %H:%M")
        store = extract_store_name(msg.get("text", ""))
        store_label = f"　{store}" if store else ""
        # Slack メッセージリンク形式
        ts_id = ts.replace(".", "")
        link = f"https://slack.com/archives/{channel_id}/p{ts_id}"
        lines.append(f"• {dt}{store_label}　<{link}|メッセージを見る>")

    return "\n".join(lines)


@app.event("app_mention")
def handle_mention(event, say, client):
    text = event.get("text", "")
    user_text = " ".join(
        word for word in text.split() if not word.startswith("<@")
    ).strip()

    # 未処理一覧コマンド（キーワードなし or 明示キーワード）
    listing_keywords = ["未処理", "一覧", "未済", "リスト", "list"]
    is_listing = not user_text or any(kw in user_text for kw in listing_keywords)

    if is_listing:
        channel_id = get_channel_id(client, CONTRACT_CHANNEL_NAME)
        # 契約報告チャンネルからのメンションのみ未処理一覧を返す
        if channel_id and event.get("channel") == channel_id:
            reports = get_unprocessed_reports(client, channel_id)
            reply = format_unprocessed_list(channel_id, reports)
            say(reply, thread_ts=event.get("ts"))
            return

    if not user_text:
        say("何かご質問はありますか？", thread_ts=event.get("ts"))
        return

    reply = ask_claude(user_text)
    say(reply, thread_ts=event.get("ts"))


@app.event("message")
def handle_dm(event, say, client):
    if event.get("channel_type") != "im":
        return
    if event.get("bot_id"):
        return

    text = event.get("text", "").strip()
    if not text:
        return

    listing_keywords = ["未処理", "一覧", "未済", "リスト", "list"]
    if any(kw in text for kw in listing_keywords):
        channel_id = get_channel_id(client, CONTRACT_CHANNEL_NAME)
        if not channel_id:
            say(f"チャンネル `#{CONTRACT_CHANNEL_NAME}` が見つかりませんでした。")
            return

        reports = get_unprocessed_reports(client, channel_id)
        reply = format_unprocessed_list(channel_id, reports)
        say(reply)
        return

    reply = ask_claude(text)
    say(reply)


if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    print("⚡ Slack Bot 起動中...")
    handler.start()
