import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
PROCESSED_REACTIONS = [
    r.strip()
    for r in os.getenv("PROCESSED_REACTIONS", "武居_済み,無視").split(",")
]
IGNORE_REACTION = os.getenv("IGNORE_REACTION", "無視")
UNPROCESSED_SEARCH_DAYS = int(os.getenv("UNPROCESSED_SEARCH_DAYS", "60"))

# 集計対象の報告種別（メッセージ内のキーワードで判定）
REPORT_TYPES = [
    ("契約獲得", "🎉"),
    ("課金前解約", "⛔"),
    ("解約", "⛔"),
    ("オプション追加", "⚙️"),
    ("オプション解約", "⚙️"),
]


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


def get_channel_id(client, channel_name: str) -> Optional[str]:
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
    lines = text.splitlines()
    in_store_block = False
    for line in lines:
        if "＜対象店舗＞" in line or "＜店舗名＞" in line:
            in_store_block = True
            continue
        if in_store_block:
            stripped = line.strip()
            if stripped and not stripped.startswith("＜"):
                return stripped
            if stripped.startswith("＜"):
                break
    return ""


def extract_field_value(text: str, field: str) -> str:
    """fieldキーワードの次の非空行の値を返す"""
    lines = text.splitlines()
    found = False
    for line in lines:
        if found:
            stripped = line.strip()
            if stripped:
                return stripped
        if field in line:
            found = True
    return ""


def classify_report(text: str) -> Optional[str]:
    """メッセージの種別を返す（判定できない場合はNone）"""
    first_lines = " ".join(text.splitlines()[:3])
    for label, _ in REPORT_TYPES:
        if label in first_lines:
            return label
    return None


def fetch_channel_messages(client, channel_id: str) -> list:
    """過去N日間のチャンネルメッセージを取得する"""
    oldest = (datetime.now() - timedelta(days=UNPROCESSED_SEARCH_DAYS)).timestamp()
    messages = []
    cursor = None

    while True:
        kwargs = {"channel": channel_id, "oldest": str(oldest), "limit": 200}
        if cursor:
            kwargs["cursor"] = cursor

        response = client.conversations_history(**kwargs)
        for msg in response.get("messages", []):
            if msg.get("text", "").startswith("<@"):
                continue
            messages.append(msg)

        meta = response.get("response_metadata", {})
        cursor = meta.get("next_cursor")
        if not response.get("has_more") or not cursor:
            break

    return messages


def get_unprocessed_reports(client, channel_id: str) -> list:
    """処理済みリアクションのないメッセージを返す"""
    messages = fetch_channel_messages(client, channel_id)
    unprocessed = []
    for msg in messages:
        reactions = [r["name"] for r in msg.get("reactions", [])]
        if not any(r in reactions for r in PROCESSED_REACTIONS):
            unprocessed.append(msg)
    unprocessed.sort(key=lambda m: float(m["ts"]))
    return unprocessed


def get_aggregation_reports(client, channel_id: str) -> list:
    """:無視:以外のメッセージを集計対象として返す"""
    messages = fetch_channel_messages(client, channel_id)
    result = []
    for msg in messages:
        reactions = [r["name"] for r in msg.get("reactions", [])]
        if IGNORE_REACTION not in reactions:
            result.append(msg)
    return result


def format_unprocessed_list(channel_id: str, messages: list) -> str:
    if not messages:
        return f"✅ 未処理の契約報告はありません（過去{UNPROCESSED_SEARCH_DAYS}日間）"

    lines = [f"*未処理の契約報告 {len(messages)}件*（過去{UNPROCESSED_SEARCH_DAYS}日間）\n"]
    for msg in messages:
        ts = msg["ts"]
        dt = datetime.fromtimestamp(float(ts)).strftime("%Y/%m/%d %H:%M")
        store = extract_store_name(msg.get("text", ""))
        store_label = f"　{store}" if store else ""
        ts_id = ts.replace(".", "")
        link = f"https://slack.com/archives/{channel_id}/p{ts_id}"
        lines.append(f"• {dt}{store_label}　<{link}|メッセージを見る>")

    return "\n".join(lines)


def format_aggregation(messages: list) -> str:
    type_counts = defaultdict(int)
    plan_counts = defaultdict(int)
    option_add_counts = defaultdict(int)
    option_cancel_counts = defaultdict(int)
    unknown_count = 0

    for msg in messages:
        text = msg.get("text", "")
        report_type = classify_report(text)
        if not report_type:
            unknown_count += 1
            continue
        type_counts[report_type] += 1

        if report_type == "契約獲得":
            plan = extract_field_value(text, "プラン")
            plan_counts[plan or "不明"] += 1
        elif report_type == "オプション追加":
            opt = extract_field_value(text, "対象オプション")
            option_add_counts[opt or "不明"] += 1
        elif report_type == "オプション解約":
            opt = extract_field_value(text, "対象オプション")
            option_cancel_counts[opt or "不明"] += 1

    total = sum(type_counts.values())
    lines = [f"*契約集計（過去{UNPROCESSED_SEARCH_DAYS}日間）*　合計 {total}件\n"]

    for label, emoji in REPORT_TYPES:
        count = type_counts.get(label, 0)
        if count == 0:
            continue
        lines.append(f"{emoji} *{label}*：{count}件")

        if label == "契約獲得" and plan_counts:
            for plan, n in sorted(plan_counts.items(), key=lambda x: -x[1]):
                lines.append(f"　　• {plan}：{n}件")
        elif label == "オプション追加" and option_add_counts:
            for opt, n in sorted(option_add_counts.items(), key=lambda x: -x[1]):
                lines.append(f"　　• {opt}：{n}件")
        elif label == "オプション解約" and option_cancel_counts:
            for opt, n in sorted(option_cancel_counts.items(), key=lambda x: -x[1]):
                lines.append(f"　　• {opt}：{n}件")

    return "\n".join(lines)


@app.event("app_mention")
def handle_mention(event, say, client):
    text = event.get("text", "")
    user_text = " ".join(
        word for word in text.split() if not word.startswith("<@")
    ).strip()

    listing_keywords = ["未処理", "一覧", "未済", "リスト", "list"]
    aggregation_keywords = ["集計", "summary", "サマリー"]

    is_listing = not user_text or any(kw in user_text for kw in listing_keywords)
    is_aggregation = any(kw in user_text for kw in aggregation_keywords)

    channel_id = get_channel_id(client, CONTRACT_CHANNEL_NAME)
    is_contract_channel = channel_id and event.get("channel") == channel_id

    if is_aggregation and is_contract_channel:
        messages = get_aggregation_reports(client, channel_id)
        reply = format_aggregation(messages)
        say(reply, thread_ts=event.get("ts"))
        return

    if is_listing and is_contract_channel:
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
    aggregation_keywords = ["集計", "summary", "サマリー"]

    channel_id = get_channel_id(client, CONTRACT_CHANNEL_NAME)

    if any(kw in text for kw in aggregation_keywords):
        if not channel_id:
            say(f"チャンネル `#{CONTRACT_CHANNEL_NAME}` が見つかりませんでした。")
            return
        messages = get_aggregation_reports(client, channel_id)
        reply = format_aggregation(messages)
        say(reply)
        return

    if any(kw in text for kw in listing_keywords):
        if not channel_id:
            say(f"チャンネル `#{CONTRACT_CHANNEL_NAME}` が見つかりませんでした。")
            return
        reports = get_unprocessed_reports(client, channel_id)
        reply = format_unprocessed_list(channel_id, reports)
        say(reply)
        return

    reply = ask_claude(text)
    say(reply)


@app.event("reaction_added")
def handle_reaction_added(event, client):
    if event.get("reaction") != "武居_済み":
        return

    item = event.get("item", {})
    if item.get("type") != "message":
        return

    react_channel = item.get("channel")
    ts = item.get("ts")

    contract_channel_id = get_channel_id(client, CONTRACT_CHANNEL_NAME)
    if not contract_channel_id or react_channel != contract_channel_id:
        return

    try:
        resp = client.conversations_history(
            channel=react_channel,
            latest=ts,
            oldest=ts,
            inclusive=True,
            limit=1,
        )
        messages = resp.get("messages", [])
        if not messages:
            return
        text = messages[0].get("text", "")
    except Exception as e:
        logger.error(f"Failed to fetch message ts={ts}: {e}")
        return

    if not text:
        return

    try:
        from sheets import update_spreadsheet
        msg_type, labels = update_spreadsheet(text, msg_ts=ts)
        if msg_type:
            logger.info(f"Sheets updated [{msg_type}]: {labels}")
    except Exception as e:
        logger.error(f"Sheets update failed: {e}")


@app.event("reaction_removed")
def handle_reaction_removed(event, client):
    logger.info(f"reaction_removed received: reaction={event.get('reaction')}")
    if event.get("reaction") != "武居_済み":
        logger.info(f"reaction_removed: skipped (not 武居_済み)")
        return

    item = event.get("item", {})
    if item.get("type") != "message":
        logger.info(f"reaction_removed: skipped (item type={item.get('type')})")
        return

    react_channel = item.get("channel")
    ts = item.get("ts")

    contract_channel_id = get_channel_id(client, CONTRACT_CHANNEL_NAME)
    logger.info(f"reaction_removed: react_channel={react_channel}, contract_channel_id={contract_channel_id}")
    if not contract_channel_id or react_channel != contract_channel_id:
        logger.info(f"reaction_removed: skipped (channel mismatch)")
        return

    try:
        resp = client.conversations_history(
            channel=react_channel,
            latest=ts,
            oldest=ts,
            inclusive=True,
            limit=1,
        )
        messages = resp.get("messages", [])
        if not messages:
            return
        text = messages[0].get("text", "")
    except Exception as e:
        logger.error(f"Failed to fetch message ts={ts}: {e}")
        return

    if not text:
        return

    try:
        from sheets import update_spreadsheet
        msg_type, labels = update_spreadsheet(text, msg_ts=ts, reverse=True)
        if msg_type:
            logger.info(f"Sheets reversed [{msg_type}]: {labels}")
    except Exception as e:
        logger.error(f"Sheets reverse failed: {e}")


if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    print("⚡ Slack Bot 起動中...")
    handler.start()
