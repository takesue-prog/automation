import logging
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional, Tuple

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

# 自動処理対象の報告種別
AUTO_PROCESS_TYPES = frozenset({"契約獲得", "オプション追加", "オプション解約", "解約", "課金前解約"})
BOT_REACTION = os.getenv("BOT_REACTION", "bot_済み")
REMINDER_USER_ID = os.getenv("REMINDER_USER_ID", "")
REMINDER_INTERVAL_DAYS = int(os.getenv("REMINDER_INTERVAL_DAYS", "3"))

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


_channel_id_cache: dict = {}

def get_channel_id(client, channel_name: str) -> Optional[str]:
    channel_name = channel_name.lstrip("#")
    if channel_name in _channel_id_cache:
        return _channel_id_cache[channel_name]
    cursor = None
    while True:
        kwargs = {"types": "public_channel,private_channel", "limit": 200}
        if cursor:
            kwargs["cursor"] = cursor
        response = client.conversations_list(**kwargs)
        for ch in response.get("channels", []):
            if ch["name"] == channel_name:
                _channel_id_cache[channel_name] = ch["id"]
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


def extract_store_name_plain(text: str) -> str:
    """対象店舗ブロックがない場合は先頭付近の店舗名行を返す"""
    result = extract_store_name(text)
    if result:
        return result
    lines = text.splitlines()
    for line in lines[1:6]:
        stripped = line.strip()
        if stripped and not stripped.startswith(":") and not stripped.startswith("＜") and not stripped.startswith("ID"):
            return stripped
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


def get_pending_review_reports(client, channel_id: str) -> list:
    """BOT済みはついているが武居_済み/無視がついていないメッセージを返す"""
    messages = fetch_channel_messages(client, channel_id)
    result = []
    for msg in messages:
        reactions = [r["name"] for r in msg.get("reactions", [])]
        has_bot = BOT_REACTION in reactions
        has_processed = any(r in reactions for r in PROCESSED_REACTIONS)
        if has_bot and not has_processed:
            result.append(msg)
    result.sort(key=lambda m: float(m["ts"]))
    return result


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


def parse_year_month(text: str) -> Tuple[Optional[int], Optional[int]]:
    """テキストから年月を抽出する（例: 2026年7月 → (2026, 7)）"""
    m = re.search(r'(\d{4})年(\d{1,2})月', text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r'(\d{4})[/\-](\d{1,2})', text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r'(\d{1,2})月', text)
    if m:
        return datetime.now().year, int(m.group(1))
    return None, None


def _reactions_add_with_retry(client, channel: str, timestamp: str, name: str, max_retries: int = 3) -> None:
    """Add a reaction with exponential backoff on rate limiting. Treats already_reacted as success."""
    for attempt in range(max_retries + 1):
        try:
            client.reactions_add(channel=channel, timestamp=timestamp, name=name)
            return
        except Exception as e:
            err_str = str(e).lower()
            if "already_reacted" in err_str:
                return  # Already there — counts as success
            if "ratelimited" in err_str or "rate_limited" in err_str or "429" in err_str:
                wait = 2 ** attempt
                logger.warning(f"reactions_add rate limited (attempt {attempt+1}); retrying in {wait}s")
                time.sleep(wait)
            else:
                raise
    # Final attempt after all waits
    client.reactions_add(channel=channel, timestamp=timestamp, name=name)


def process_past_messages(client, channel_id: str, year: Optional[int] = None, month: Optional[int] = None) -> str:
    """未処理の過去メッセージを一括でスプシ更新 + BOT済みスタンプ付与する"""
    from sheets import (
        classify_report as sheets_classify,
        update_spreadsheet,
        _worksheet,
        _build_index,
        _is_configured,
    )

    # ワークシートとインデックスを一括処理の前に1回だけ読み込む（API呼び出しを大幅削減）
    ws = None
    index = None
    if _is_configured():
        try:
            ws = _worksheet()
            index = _build_index(ws)
            logger.info("Worksheet and index pre-built for batch (single read)")
        except Exception as e:
            logger.error(f"Failed to pre-build worksheet: {e}")

    messages = fetch_channel_messages(client, channel_id)
    logger.info(f"Batch start: fetched {len(messages)} messages from channel {channel_id}")
    processed = 0
    errors = 0
    skipped_already = 0
    skipped_period = 0
    skipped_type = 0

    period_label = f"{year}年{month}月" if year and month else "全期間"

    for msg in messages:
        reactions = [r["name"] for r in msg.get("reactions", [])]

        # BOT済み・武居済み・無視のいずれかがあればスキップ
        if BOT_REACTION in reactions or any(r in reactions for r in PROCESSED_REACTIONS):
            skipped_already += 1
            continue

        text = msg.get("text", "")
        ts = msg.get("ts")

        if not text or text.startswith("<@"):
            continue

        # 年月フィルタ
        if year and month:
            msg_dt = datetime.fromtimestamp(float(ts))
            if msg_dt.year != year or msg_dt.month != month:
                skipped_period += 1
                continue

        report_type = sheets_classify(text)
        if report_type not in AUTO_PROCESS_TYPES:
            skipped_type += 1
            logger.info(f"Skipping ts={ts}: type={report_type!r} not in AUTO_PROCESS_TYPES")
            continue

        logger.info(f"Processing ts={ts} type={report_type}")
        try:
            msg_type, labels = update_spreadsheet(text, msg_ts=ts, ws=ws, index=index)
            if msg_type:
                try:
                    _reactions_add_with_retry(client, channel_id, ts, BOT_REACTION)
                    logger.info(f"Past processed [{msg_type}] ts={ts}: {labels}")
                except Exception as re:
                    logger.error(f"Failed to add :{BOT_REACTION}: ts={ts}: {re}")
                processed += 1
                time.sleep(2)  # レートリミット対策（Google Sheets + Slack）
            else:
                logger.warning(f"update_spreadsheet returned None for ts={ts}")
        except Exception as e:
            logger.error(f"Past processing failed ts={ts}: {e}")
            errors += 1

    logger.info(
        f"Batch done ({period_label}): processed={processed}, errors={errors}, "
        f"skipped_already={skipped_already}, skipped_period={skipped_period}, skipped_type={skipped_type}"
    )
    if errors:
        return f"一括処理完了（{period_label}）：*{processed}件* 処理しました（エラー {errors}件）"
    return f"✅ 一括処理完了（{period_label}）：*{processed}件* 処理しました"


def setup_reminder_scheduler():
    if not REMINDER_USER_ID:
        logger.info("REMINDER_USER_ID not set; reminder scheduler not started")
        return None

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning("APScheduler not installed; reminder scheduler not started. Run: pip install APScheduler")
        return None

    scheduler = BackgroundScheduler()

    def send_pending_reminder():
        channel_id = get_channel_id(app.client, CONTRACT_CHANNEL_NAME)
        if not channel_id:
            logger.error("Cannot find contract channel for reminder")
            return
        reports = get_pending_review_reports(app.client, channel_id)
        if not reports:
            logger.info("No pending reports; skipping reminder")
            return
        header = f"<@{REMINDER_USER_ID}> *定期リマインド*：BOT処理済みで未確認の契約報告が *{len(reports)}件* あります。確認をお願いします。\n\n"
        body = format_unprocessed_list(channel_id, reports)
        try:
            app.client.chat_postMessage(channel=channel_id, text=header + body)
            logger.info(f"Reminder posted to #{CONTRACT_CHANNEL_NAME}: {len(reports)} reports")
        except Exception as e:
            logger.error(f"Failed to post reminder: {e}")

    scheduler.add_job(send_pending_reminder, "cron", day_of_week="tue", hour=11, minute=0)
    scheduler.add_job(send_pending_reminder, "cron", day_of_week="fri", hour=11, minute=0)
    scheduler.start()
    logger.info(f"Reminder scheduler started: tue/fri 11:00 → #{CONTRACT_CHANNEL_NAME}")
    return scheduler


@app.event("app_mention")
def handle_mention(event, say, client):
    text = event.get("text", "")
    user_text = " ".join(
        word for word in text.split() if not word.startswith("<@")
    ).strip()

    listing_keywords = ["未処理", "一覧", "未済", "リスト", "list"]
    aggregation_keywords = ["集計", "summary", "サマリー"]
    past_keywords = ["過去分処理", "一括処理", "過去分"]

    is_listing = not user_text or any(kw in user_text for kw in listing_keywords)
    is_aggregation = any(kw in user_text for kw in aggregation_keywords)
    is_past = any(kw in user_text for kw in past_keywords)

    channel_id = get_channel_id(client, CONTRACT_CHANNEL_NAME)
    is_contract_channel = channel_id and event.get("channel") == channel_id

    if is_past and is_contract_channel:
        year, month = parse_year_month(user_text)
        period = f"{year}年{month}月" if year and month else "全期間"
        say(f"{period} の過去分を処理中です。しばらくお待ちください...", thread_ts=event.get("ts"))
        result = process_past_messages(client, channel_id, year=year, month=month)
        say(result, thread_ts=event.get("ts"))
        return

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
def handle_message(event, say, client):
    # ── DM handling ──────────────────────────────────────────────────────────
    if event.get("channel_type") == "im":
        if event.get("bot_id"):
            return
        text = event.get("text", "").strip()
        if not text:
            return

        listing_keywords = ["未処理", "一覧", "未済", "リスト", "list"]
        aggregation_keywords = ["集計", "summary", "サマリー"]
        past_keywords = ["過去分処理", "一括処理", "過去分"]

        channel_id = get_channel_id(client, CONTRACT_CHANNEL_NAME)

        if any(kw in text for kw in past_keywords):
            logger.info(f"DM batch command received: {text!r}, channel_id={channel_id}")
            if not channel_id:
                say(f"チャンネル `#{CONTRACT_CHANNEL_NAME}` が見つかりませんでした。")
                return
            year, month = parse_year_month(text)
            period = f"{year}年{month}月" if year and month else "全期間"
            say(f"{period} の過去分を処理中です。しばらくお待ちください...")
            result = process_past_messages(client, channel_id, year=year, month=month)
            say(result)
            return

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
        return

    # ── Contract channel: auto-process new messages ───────────────────────────
    if event.get("bot_id") or event.get("subtype"):
        return

    contract_channel_id = get_channel_id(client, CONTRACT_CHANNEL_NAME)
    if not contract_channel_id or event.get("channel") != contract_channel_id:
        return

    text = event.get("text", "")
    ts = event.get("ts")

    if not text or text.startswith("<@"):
        return

    from sheets import classify_report as sheets_classify, update_spreadsheet
    report_type = sheets_classify(text)
    if report_type not in AUTO_PROCESS_TYPES:
        return

    logger.info(f"Auto-processing [{report_type}] ts={ts}")

    try:
        msg_type, labels = update_spreadsheet(text, msg_ts=ts)
        if msg_type:
            logger.info(f"Auto sheets updated [{msg_type}]: {labels}")
    except Exception as e:
        logger.error(f"Auto sheets update failed: {e}")
        return

    try:
        client.reactions_add(channel=event["channel"], timestamp=ts, name=BOT_REACTION)
        logger.info(f"Added :{BOT_REACTION}: to ts={ts}")
    except Exception as e:
        logger.error(f"Failed to add :{BOT_REACTION}: reaction: {e}")


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
        msg = messages[0]
        text = msg.get("text", "")
        reactions = [r["name"] for r in msg.get("reactions", [])]
    except Exception as e:
        logger.error(f"Failed to fetch message ts={ts}: {e}")
        return

    if not text:
        return

    # BOT済みがついていればBOTが既にスプシ更新済み → 武居済みは確認マーカーのみ
    if BOT_REACTION in reactions:
        logger.info(f"ts={ts}: BOT already processed; 武居済み = verified only (no sheet update)")
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
        msg = messages[0]
        text = msg.get("text", "")
        reactions = [r["name"] for r in msg.get("reactions", [])]
    except Exception as e:
        logger.error(f"Failed to fetch message ts={ts}: {e}")
        return

    if not text:
        return

    # BOT済みがついていれば武居済みの取り外しはスプシに影響しない
    if BOT_REACTION in reactions:
        logger.info(f"ts={ts}: BOT processed; 武居済み removal is not a reversal")
        return

    try:
        from sheets import update_spreadsheet
        msg_type, labels = update_spreadsheet(text, msg_ts=ts, reverse=True)
        if msg_type:
            logger.info(f"Sheets reversed [{msg_type}]: {labels}")
    except Exception as e:
        logger.error(f"Sheets reverse failed: {e}")


if __name__ == "__main__":
    setup_reminder_scheduler()
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    print("⚡ Slack Bot 起動中...")
    handler.start()
