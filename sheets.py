import os
import re
import logging
from datetime import datetime
from typing import Optional, List, Dict, Tuple

logger = logging.getLogger(__name__)

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "1dA7ByXoFeA74GQfVO0eoeFJY-tJ4mLTjD2Bthvv0_Tk")
CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "")
DATA_COLUMN = int(os.getenv("GOOGLE_DATA_COLUMN", "2"))

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ── Spreadsheet section header labels ────────────────────────────────────
SECTION_ACQUISITION = "獲得件数詳細"
SECTION_CANCELLATION = "解約件数"

# ── Row labels (must match exact text in the spreadsheet) ────────────────
LABEL_FEE_INDIVIDUAL = "B個人初期費用"
LABEL_FEE_STORE_9800 = "B店舗初期費用（¥9,800）"
LABEL_FEE_STORE_4900 = "B店舗初期費用（半額）"

PLAN_LABEL: Dict[str, str] = {
    "B個人":   "B個人　個人プラン",
    "B店舗":   "B店舗　店舗プラン",
    "LMP個人": "LMP版　個人",
    "LMP店舗": "LMP版　店舗",
}

# (individual row label, store row label) per option
OPTION_LABELS: Dict[str, Tuple[str, str]] = {
    "システム連携":   ("B個人　システム連携",   "B店舗　システム連携"),
    "WEB予約":        ("B個人　WEB予約",         "B店舗　WEB予約"),
    "チャット":       ("B個人　チャット",         "B店舗　チャット"),
    "セグメント配信": ("B個人　セグメント配信",   "B店舗　セグメント配信"),
    "カルテ機能":     ("B個人　カルテ機能",       "B店舗　カルテ機能"),
}

# Monthly price increase per option for individual plan (used to distinguish B個人 vs B店舗)
# If actual price change <= threshold → individual, else → store
OPTION_INDIVIDUAL_PRICE: Dict[str, int] = {
    "チャット":       1000,
    "WEB予約":        2000,
    "システム連携":   2000,
    "セグメント配信": 1000,
    "カルテ機能":     1000,
}

OPTION_NAME_MAP: Dict[str, str] = {
    "HPB連携":       "システム連携",
    "システム連携":   "システム連携",
    "WEB予約機能":    "WEB予約",
    "WEB予約":        "WEB予約",
    "チャット機能":   "チャット",
    "チャット":       "チャット",
    "セグメント配信": "セグメント配信",
    "カルテ機能":     "カルテ機能",
}


# ── Google Sheets helpers ─────────────────────────────────────────────────

def _is_configured() -> bool:
    return os.path.exists(CREDENTIALS_PATH)


def _worksheet():
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    if SHEET_NAME:
        return spreadsheet.worksheet(SHEET_NAME)
    now = datetime.now()
    for name in (f"{now.year}年{now.month}月", f"{now.month}月", f"{now.year}-{now.month:02d}"):
        try:
            return spreadsheet.worksheet(name)
        except Exception:
            pass
    return spreadsheet.sheet1


def _build_index(ws) -> Dict[str, List[Tuple[int, int]]]:
    """Return {cell_text: [(row, col), ...]} for the whole sheet (1-indexed)."""
    index: Dict[str, List[Tuple[int, int]]] = {}
    for r, row in enumerate(ws.get_all_values(), start=1):
        for c, val in enumerate(row, start=1):
            v = val.strip()
            if v:
                index.setdefault(v, []).append((r, c))
    return index


def _section_bounds(index: Dict, header: str) -> Tuple[int, int]:
    """Return (start_row, end_row) exclusive for a section."""
    positions = index.get(header, [])
    start = positions[0][0] if positions else 0
    end = 9999
    for h in (SECTION_ACQUISITION, SECTION_CANCELLATION):
        for (r, _) in index.get(h, []):
            if r > start:
                end = min(end, r)
    return (start, end)


def _find_in_section(index: Dict, label: str, start: int, end: int) -> Optional[int]:
    """Row number for label within (start, end) exclusive, or first match anywhere."""
    for (r, _) in index.get(label, []):
        if start < r < end:
            return r
    positions = index.get(label, [])
    return positions[0][0] if positions else None


def _data_col(index: Dict) -> int:
    """Column for current month's data, or fallback DATA_COLUMN."""
    now = datetime.now()
    for label in (f"{now.month}月", str(now.month), f"{now.year}/{now.month:02d}"):
        positions = index.get(label, [])
        if positions:
            return positions[0][1]
    return DATA_COLUMN


def _inc(ws, row: int, col: int, delta: int) -> None:
    cell = ws.cell(row, col)
    try:
        current = int(str(cell.value or "0").replace(",", "").strip() or "0")
    except (ValueError, TypeError):
        current = 0
    ws.update_cell(row, col, current + delta)
    logger.info(f"Sheet ({row},{col}): {current} → {current + delta}")


# ── Message parsing helpers ───────────────────────────────────────────────

def _normalize_plan(text: str) -> Optional[str]:
    is_lmp = "LMP" in text
    if "個人利用" in text:
        return "LMP個人" if is_lmp else "B個人"
    if "店舗利用" in text:
        return "LMP店舗" if is_lmp else "B店舗"
    return None


def _is_individual(plan_type: Optional[str]) -> bool:
    return plan_type in ("B個人", "LMP個人")


def _extract_plan(text: str) -> Optional[str]:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "プラン" not in line:
            continue
        m = re.search(r"プラン[：:]\s*(.+)", line)
        if m and m.group(1).strip():
            return _normalize_plan(m.group(1).strip())
        # Value on next non-empty line
        for j in range(i + 1, min(i + 4, len(lines))):
            v = lines[j].strip()
            if v and not v.startswith("＜") and not v.startswith("オプション"):
                plan = _normalize_plan(v)
                if plan:
                    return plan
    return None


def _extract_options(text: str) -> List[str]:
    options: List[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "オプション" not in line:
            continue
        m = re.search(r"オプション[：:]\s*(.+)", line)
        raw = m.group(1).strip() if (m and m.group(1).strip()) else ""
        if not raw and i + 1 < len(lines):
            raw = lines[i + 1].strip()
        for token in re.split(r"[,、，]+\s*", raw):
            token = token.strip()
            norm = OPTION_NAME_MAP.get(token)
            if norm and norm not in options:
                options.append(norm)
    return options


def _extract_initial_fee(text: str) -> Optional[int]:
    m = re.search(r"初期費用[：:]\s*([0-9,]+)円", text)
    return int(m.group(1).replace(",", "")) if m else None


def _is_sns_in_contract(text: str) -> bool:
    return "SNSシェア適用" in text and bool(re.search(r"初期費用[：:]\s*0円", text))


def _next_line_value(text: str, field: str) -> str:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if field in line:
            for j in range(i + 1, min(i + 4, len(lines))):
                v = lines[j].strip()
                if v:
                    return v
    return ""


def _option_price_change(text: str) -> Optional[int]:
    """Return abs(after - before) from 月額（変更前/後） fields."""
    before_str = _next_line_value(text, "月額（変更前）")
    after_str = _next_line_value(text, "月額（変更後）")
    if not before_str:
        bm = re.search(r"月額変更前[：:]\s*([0-9,]+)", text)
        am = re.search(r"月額変更後[：:]\s*([0-9,]+)", text)
        before_str = bm.group(1) if bm else ""
        after_str = am.group(1) if am else ""
    if before_str and after_str:
        try:
            return abs(int(after_str.replace(",", "")) - int(before_str.replace(",", "")))
        except ValueError:
            pass
    return None


def _is_individual_by_price(opt_name: str, price_change: int) -> bool:
    threshold = OPTION_INDIVIDUAL_PRICE.get(opt_name, 1500)
    return price_change <= threshold


# ── Public API ────────────────────────────────────────────────────────────

def classify_report(text: str) -> Optional[str]:
    head = " ".join(text.splitlines()[:5])
    for label in ("課金前解約", "解約", "契約獲得", "オプション追加", "オプション解約"):
        if label in head:
            return label
    if "SNSシェアキャンペーン適用" in text:
        return "SNSシェア"
    return None


def update_spreadsheet(text: str) -> Tuple[Optional[str], List[str]]:
    """
    Classify the Slack message and update the spreadsheet.
    Returns (report_type, list_of_updated_labels).
    Does nothing if Google credentials are not configured.
    """
    if not _is_configured():
        logger.info("Google credentials not found; skipping sheets update")
        return (None, [])

    report_type = classify_report(text)
    if not report_type:
        return (None, [])

    ws = _worksheet()
    index = _build_index(ws)
    col = _data_col(index)

    acq_start, acq_end = _section_bounds(index, SECTION_ACQUISITION)
    cancel_start, cancel_end = _section_bounds(index, SECTION_CANCELLATION)

    updated: List[str] = []

    def apply(section_start: int, section_end: int, label: str, delta: int = 1) -> None:
        row = _find_in_section(index, label, section_start, section_end)
        if row:
            _inc(ws, row, col, delta)
            updated.append(f"{label}({'+' if delta > 0 else ''}{delta})")
        else:
            logger.warning(f"Label '{label}' not found in spreadsheet")

    plan_type = _extract_plan(text)
    is_ind = _is_individual(plan_type)

    if report_type == "契約獲得":
        if plan_type and plan_type in PLAN_LABEL:
            apply(acq_start, acq_end, PLAN_LABEL[plan_type])

        if not _is_sns_in_contract(text):
            fee = _extract_initial_fee(text)
            if fee is not None:
                if is_ind:
                    apply(acq_start, acq_end, LABEL_FEE_INDIVIDUAL)
                elif fee >= 9800:
                    apply(acq_start, acq_end, LABEL_FEE_STORE_9800)
                else:
                    apply(acq_start, acq_end, LABEL_FEE_STORE_4900)

        for opt in _extract_options(text):
            if opt in OPTION_LABELS:
                apply(acq_start, acq_end, OPTION_LABELS[opt][0 if is_ind else 1])

    elif report_type == "解約":
        if plan_type and plan_type in PLAN_LABEL:
            apply(cancel_start, cancel_end, PLAN_LABEL[plan_type])
        for opt in _extract_options(text):
            if opt in OPTION_LABELS:
                apply(cancel_start, cancel_end, OPTION_LABELS[opt][0 if is_ind else 1])

    elif report_type == "課金前解約":
        if plan_type and plan_type in PLAN_LABEL:
            apply(acq_start, acq_end, PLAN_LABEL[plan_type], delta=-1)
        fee = _extract_initial_fee(text)
        if fee and fee > 0:
            if is_ind:
                apply(acq_start, acq_end, LABEL_FEE_INDIVIDUAL, delta=-1)
            elif fee >= 9800:
                apply(acq_start, acq_end, LABEL_FEE_STORE_9800, delta=-1)
            else:
                apply(acq_start, acq_end, LABEL_FEE_STORE_4900, delta=-1)
        for opt in _extract_options(text):
            if opt in OPTION_LABELS:
                apply(acq_start, acq_end, OPTION_LABELS[opt][0 if is_ind else 1], delta=-1)

    elif report_type in ("オプション追加", "オプション解約"):
        opt_text = _next_line_value(text, "対象オプション").strip()
        opt_name = OPTION_NAME_MAP.get(opt_text)
        if opt_name and opt_name in OPTION_LABELS:
            change = _option_price_change(text)
            if change is not None:
                opt_is_ind = _is_individual_by_price(opt_name, change)
                label = OPTION_LABELS[opt_name][0 if opt_is_ind else 1]
                if report_type == "オプション追加":
                    apply(acq_start, acq_end, label)
                else:
                    apply(cancel_start, cancel_end, label)
            else:
                logger.warning("Could not determine price change for option message")
        else:
            logger.warning(f"Unknown option: '{opt_text}'")

    elif report_type == "SNSシェア":
        bm = re.search(r"変更前[：:]\s*([0-9,]+)", text)
        am = re.search(r"変更後[：:]\s*([0-9,]+)", text)
        if bm:
            before = int(bm.group(1).replace(",", ""))
            after = int(am.group(1).replace(",", "")) if am else 0
            lbl_before = LABEL_FEE_STORE_9800 if before >= 9800 else LABEL_FEE_STORE_4900
            apply(acq_start, acq_end, lbl_before, delta=-1)
            if after > 0:
                lbl_after = LABEL_FEE_STORE_9800 if after >= 9800 else LABEL_FEE_STORE_4900
                apply(acq_start, acq_end, lbl_after, delta=1)

    return (report_type, updated)
