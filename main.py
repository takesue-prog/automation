import os
import anthropic
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from dotenv import load_dotenv

load_dotenv()

app = App(token=os.environ["SLACK_BOT_TOKEN"])
claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "あなたはSlackの自動返信アシスタントです。ユーザーの質問に簡潔かつ丁寧に日本語で回答してください。",
)


def ask_claude(user_message: str) -> str:
    message = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return message.content[0].text


@app.event("app_mention")
def handle_mention(event, say):
    # メンション文字列を除去してメッセージを取得
    text = event.get("text", "")
    # <@BOT_ID> の部分を除去
    user_text = " ".join(
        word for word in text.split() if not word.startswith("<@")
    ).strip()

    if not user_text:
        say("何かご質問はありますか？", thread_ts=event.get("ts"))
        return

    reply = ask_claude(user_text)
    say(reply, thread_ts=event.get("ts"))


@app.event("reaction_added")
def handle_reaction(event, client):
    reaction = event.get("reaction")
    if reaction not in ("memo", "globe_with_meridians"):
        return

    # リアクションされたメッセージを取得
    item = event.get("item", {})
    channel = item.get("channel")
    message_ts = item.get("ts")

    # チャンネル情報を確認してDMかどうかチェック
    try:
        info = client.conversations_info(channel=channel)
        if not info["channel"].get("is_im"):
            return
    except Exception:
        return

    # メッセージ本文を取得
    try:
        result = client.conversations_history(
            channel=channel, latest=message_ts, limit=1, inclusive=True
        )
        messages = result.get("messages", [])
        if not messages:
            return
        text = messages[0].get("text", "").strip()
    except Exception:
        return

    if not text:
        return

    if reaction == "memo":
        prompt = f"以下のメッセージを簡潔に要約してください:\n\n{text}"
        prefix = "📝 要約:"
    else:
        prompt = f"以下のメッセージを日本語に翻訳してください。すでに日本語の場合は英語に翻訳してください:\n\n{text}"
        prefix = "🌐 翻訳:"

    reply = ask_claude(prompt)
    client.chat_postMessage(
        channel=channel,
        text=f"{prefix}\n{reply}",
        thread_ts=message_ts,
    )


@app.event("message")
def handle_dm(event, say, client):
    # DMのみ返信 (チャンネルメッセージはメンションで対応)
    if event.get("channel_type") != "im":
        return
    # ボット自身のメッセージは無視
    if event.get("bot_id"):
        return

    text = event.get("text", "").strip()
    if not text:
        return

    reply = ask_claude(text)
    say(reply)


if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    print("⚡ Slack自動返信Bot起動中...")
    handler.start()
