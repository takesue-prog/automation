import json
import os

import anthropic
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import calendar_client as cal

load_dotenv()

app = App(token=os.environ["SLACK_BOT_TOKEN"])
claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-8")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Tokyo")

# Claudeに渡すツール定義 (Googleカレンダー操作)
TOOLS = [
    {
        "name": "check_calendar_availability",
        "description": (
            "指定した期間にすでに入っている予定 (busy区間) を取得する。"
            "空いているかどうかを確認したいときに使う。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "期間の開始 (ISO 8601, 例 2026-06-20T00:00:00)"},
                "end": {"type": "string", "description": "期間の終了 (ISO 8601)"},
            },
            "required": ["start", "end"],
        },
    },
    {
        "name": "find_free_slots",
        "description": (
            "指定期間・営業時間内で、指定の長さの予定を入れられる空き枠を検索する。"
            "「空いている時間に入れて」と頼まれたら、まずこれで候補を探す。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "range_start": {"type": "string", "description": "検索範囲の開始 (ISO 8601)"},
                "range_end": {"type": "string", "description": "検索範囲の終了 (ISO 8601)"},
                "duration_minutes": {"type": "integer", "description": "予定の長さ(分)。既定60"},
                "day_start_hour": {"type": "integer", "description": "1日の業務開始時刻(時)。既定9"},
                "day_end_hour": {"type": "integer", "description": "1日の業務終了時刻(時)。既定18"},
                "include_weekends": {"type": "boolean", "description": "土日を含めるか。既定false"},
            },
            "required": ["range_start", "range_end"],
        },
    },
    {
        "name": "list_calendar_events",
        "description": "指定した期間の予定一覧を取得する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "期間の開始 (ISO 8601)"},
                "end": {"type": "string", "description": "期間の終了 (ISO 8601)"},
            },
            "required": ["start", "end"],
        },
    },
    {
        "name": "create_calendar_event",
        "description": (
            "カレンダーに予定を作成する。タイトルと開始・終了時刻が必須。"
            "作成前に必要な情報が揃っていることを確認すること。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "予定のタイトル"},
                "start": {"type": "string", "description": "開始日時 (ISO 8601)"},
                "end": {"type": "string", "description": "終了日時 (ISO 8601)"},
                "description": {"type": "string", "description": "予定の説明 (任意)"},
                "location": {"type": "string", "description": "場所 (任意)"},
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "参加者のメールアドレス一覧 (任意)",
                },
            },
            "required": ["summary", "start", "end"],
        },
    },
]


def _build_system_prompt() -> str:
    return (
        f"今日は {cal.now_iso()} ({TIMEZONE}) です。"
        "あなたはGoogleカレンダーを操作できるSlackアシスタントです。"
        "ユーザーの依頼に応じて空き時間を確認し、予定を作成してください。\n"
        "- 「明日」「来週」などの相対的な日付は現在時刻を基準に解決してください。\n"
        f"- 日時は ISO 8601 形式で指定し、タイムゾーンは {TIMEZONE} を前提とします。\n"
        "- 予定作成に必要な情報(タイトル・開始/終了時刻)が不足している場合は、"
        "勝手に決めず簡潔に確認してください。\n"
        "- 空き枠を提案するときは候補を箇条書きで分かりやすく示してください。\n"
        "- 予定を作成したら、必ず内容(タイトル・日時・リンク)を要約して報告してください。\n"
        "日本語で簡潔に回答してください。"
    )


def _dispatch_tool(name: str, tool_input: dict) -> dict:
    if name == "check_calendar_availability":
        return {"busy": cal.check_availability(tool_input["start"], tool_input["end"])}
    if name == "find_free_slots":
        return {
            "free_slots": cal.find_free_slots(
                tool_input["range_start"],
                tool_input["range_end"],
                duration_minutes=tool_input.get("duration_minutes", 60),
                day_start_hour=tool_input.get("day_start_hour", 9),
                day_end_hour=tool_input.get("day_end_hour", 18),
                include_weekends=tool_input.get("include_weekends", False),
            )
        }
    if name == "list_calendar_events":
        return {"events": cal.list_events(tool_input["start"], tool_input["end"])}
    if name == "create_calendar_event":
        return {
            "created": cal.create_event(
                summary=tool_input["summary"],
                start=tool_input["start"],
                end=tool_input["end"],
                description=tool_input.get("description"),
                location=tool_input.get("location"),
                attendees=tool_input.get("attendees"),
            )
        }
    raise ValueError(f"未知のツール: {name}")


def run_agent(user_text: str) -> str:
    """Claudeのtool useループでカレンダー操作を実行し、最終回答テキストを返す。"""
    messages: list[dict] = [{"role": "user", "content": user_text}]
    system = _build_system_prompt()

    for _ in range(10):  # ツール呼び出しの最大反復回数
        response = claude.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=system,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            return "".join(
                b.text for b in response.content if b.type == "text"
            ).strip()

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            try:
                output = _dispatch_tool(block.name, block.input)
                content = json.dumps(output, ensure_ascii=False, default=str)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": content}
                )
            except Exception as e:  # noqa: BLE001 - ツール失敗をClaudeに伝える
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"エラー: {e}",
                        "is_error": True,
                    }
                )

        messages.append({"role": "user", "content": tool_results})

    return "処理が完了しませんでした。もう一度お試しください。"


@app.event("app_mention")
def handle_mention(event, say):
    text = event.get("text", "")
    # <@BOT_ID> のメンション部分を除去
    user_text = " ".join(
        word for word in text.split() if not word.startswith("<@")
    ).strip()

    if not user_text:
        say("カレンダーの予定確認や登録をお手伝いします。ご用件をどうぞ。", thread_ts=event.get("ts"))
        return

    reply = run_agent(user_text)
    say(reply, thread_ts=event.get("ts"))


@app.event("message")
def handle_dm(event, say):
    # DMのみ対応 (チャンネルはメンションで対応)
    if event.get("channel_type") != "im":
        return
    if event.get("bot_id"):  # ボット自身のメッセージは無視
        return

    text = event.get("text", "").strip()
    if not text:
        return

    say(run_agent(text))


if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    print("⚡ Slackカレンダー自動化Bot起動中...")
    handler.start()
