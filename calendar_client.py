"""Googleカレンダー操作クライアント (OAuth / 個人アカウント)

空き状況の確認・空き枠の検索・予定の作成・予定一覧を提供する。
初回の認可は `python authorize.py` を実行して token.json を生成すること。
"""

import datetime as dt
import os
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# カレンダーの読み書き (freebusy 含む) に必要なスコープ
SCOPES = ["https://www.googleapis.com/auth/calendar"]

CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.json")
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Tokyo")

_tz = ZoneInfo(TIMEZONE)


def _load_credentials() -> Credentials:
    """token.json から認証情報を読み込み、必要なら自動リフレッシュする。"""
    if not os.path.exists(TOKEN_FILE):
        raise RuntimeError(
            f"{TOKEN_FILE} が見つかりません。"
            "`python authorize.py` を実行して初回認可を行ってください。"
        )

    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_FILE, "w") as f:
                f.write(creds.to_json())
        else:
            raise RuntimeError(
                "Google認証の有効期限が切れています。"
                "`python authorize.py` を再実行してください。"
            )
    return creds


def get_service():
    """Google Calendar API のサービスオブジェクトを返す。"""
    return build(
        "calendar", "v3", credentials=_load_credentials(), cache_discovery=False
    )


def _parse_dt(value: str) -> dt.datetime:
    """ISO 8601 文字列を datetime に変換する。タイムゾーン未指定なら設定TZを補う。"""
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_tz)
    return parsed


def check_availability(start: str, end: str, calendar_id: str | None = None) -> list[dict]:
    """指定期間の予定済み (busy) 区間を返す。

    戻り値: [{"start": ISO8601, "end": ISO8601}, ...]
    """
    cal = calendar_id or CALENDAR_ID
    body = {
        "timeMin": _parse_dt(start).isoformat(),
        "timeMax": _parse_dt(end).isoformat(),
        "timeZone": TIMEZONE,
        "items": [{"id": cal}],
    }
    resp = get_service().freebusy().query(body=body).execute()
    return resp["calendars"][cal]["busy"]


def find_free_slots(
    range_start: str,
    range_end: str,
    duration_minutes: int = 60,
    day_start_hour: int = 9,
    day_end_hour: int = 18,
    include_weekends: bool = False,
    calendar_id: str | None = None,
) -> list[dict]:
    """指定期間・営業時間内で、指定の長さが確保できる空き枠を返す。

    戻り値: [{"start": ISO8601, "end": ISO8601}, ...]
    """
    start = _parse_dt(range_start)
    end = _parse_dt(range_end)

    busy = check_availability(start.isoformat(), end.isoformat(), calendar_id)
    busy_intervals = sorted(
        (_parse_dt(b["start"]), _parse_dt(b["end"])) for b in busy
    )

    duration = dt.timedelta(minutes=duration_minutes)
    slots: list[dict] = []

    day = start.date()
    while day <= end.date():
        # 土日を除外 (Mon=0 .. Sun=6)
        if not include_weekends and day.weekday() >= 5:
            day += dt.timedelta(days=1)
            continue

        win_start = max(
            dt.datetime.combine(day, dt.time(day_start_hour), tzinfo=_tz), start
        )
        win_end = min(
            dt.datetime.combine(day, dt.time(day_end_hour), tzinfo=_tz), end
        )

        cursor = win_start
        for b_start, b_end in busy_intervals:
            if b_end <= cursor or b_start >= win_end:
                continue
            if b_start > cursor and (min(b_start, win_end) - cursor) >= duration:
                slots.append({"start": cursor.isoformat(), "end": min(b_start, win_end).isoformat()})
            cursor = max(cursor, b_end)

        if win_end - cursor >= duration:
            slots.append({"start": cursor.isoformat(), "end": win_end.isoformat()})

        day += dt.timedelta(days=1)

    return slots


def create_event(
    summary: str,
    start: str,
    end: str,
    description: str | None = None,
    location: str | None = None,
    attendees: list[str] | None = None,
    calendar_id: str | None = None,
) -> dict:
    """カレンダーに予定を作成し、作成結果を返す。"""
    cal = calendar_id or CALENDAR_ID
    event: dict = {
        "summary": summary,
        "start": {"dateTime": _parse_dt(start).isoformat(), "timeZone": TIMEZONE},
        "end": {"dateTime": _parse_dt(end).isoformat(), "timeZone": TIMEZONE},
    }
    if description:
        event["description"] = description
    if location:
        event["location"] = location
    if attendees:
        event["attendees"] = [{"email": e} for e in attendees]

    created = (
        get_service()
        .events()
        .insert(
            calendarId=cal,
            body=event,
            sendUpdates="all" if attendees else "none",
        )
        .execute()
    )
    return {
        "id": created["id"],
        "summary": created.get("summary"),
        "start": created["start"],
        "end": created["end"],
        "htmlLink": created.get("htmlLink"),
    }


def list_events(
    start: str, end: str, calendar_id: str | None = None, max_results: int = 20
) -> list[dict]:
    """指定期間の予定一覧を返す。"""
    cal = calendar_id or CALENDAR_ID
    resp = (
        get_service()
        .events()
        .list(
            calendarId=cal,
            timeMin=_parse_dt(start).isoformat(),
            timeMax=_parse_dt(end).isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=max_results,
        )
        .execute()
    )
    return [
        {
            "id": item["id"],
            "summary": item.get("summary", "(タイトルなし)"),
            "start": item["start"],
            "end": item["end"],
            "htmlLink": item.get("htmlLink"),
        }
        for item in resp.get("items", [])
    ]


def now_iso() -> str:
    """設定タイムゾーンでの現在時刻 (ISO 8601) を返す。"""
    return dt.datetime.now(_tz).isoformat()
