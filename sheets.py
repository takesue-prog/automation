import os
import re
import logging
from datetime import datetime
from typing import Optional, List, Dict, Tuple

logger = logging.getLogger(__name__)

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "1dA7ByXoFeA74GQfVO0eoeFJY-tJ4mLTjD2Bthvv0_Tk")
CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "B実績")
DATA_COLUMN = int(os.getenv("GOOGLE_DATA_COLUMN", "2"))

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Section labels in column B of the spreadsheet
SECTION_B_INDIVIDUAL = "B個人"
SECTION_B_STORE = "B店舗"
SECTION_LMP = "LMP版"

# Item row labels (in the 科目 column)
LABEL_PLAN_B_INDIVIDUAL = "個人プラン"
LABEL_PLAN_B_STORE = "店舗プラン"
LABEL_PLAN_LMP_INDIVIDUAL = "個人"
LABEL_PLAN_LMP_STORE = "店舗"
LABEL_FEE = "初期費用"
LABEL_FEE_HALF = "初期費用（半額）"

OPTION_SHEET_LABELS: Dict[str, str] = {
    "システム連携": "システム連携",
    "WEB予約": "WEB予約",
    "チャット": "チャット",
    "セグメント配信": "セグメント配信",
    "カルテ機能": "カルテ機能",
}

OPTION_NAME_MAP: Dict[str, str] = {
    "HPB連携": "システム連携",
    "システム連携": "システム連携",
    "WEB予約機能": "WEB予約",
    "WEB予約": "WEB予約",
    "チャット機能": "チャット",
    "チャット": "チャット",
    "セグメント配信": "セグメント配信",
    "カルテ機能": "カルテ機能",
}

OPTION_INDIVIDUAL_PRICE: Dict[str, int] = {
    "チャット": 1000,
    "WEB予約": 2000,
    "システム連携": 2000,
    "セグメント配信": 1000,
    "カルテ機能": 1000,
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


def _section_start(index: Dict, label: str) -> int:
    """Return the first row where label appears, 0 if not found."""
    positions = index.get(label, [])
    return positions[0][0] if positions else 0


def _find_in_range(index: Dict, label: str, start_row: int, end_row: int) -> Optional[int]:
    """Return the row of label within [start_row, end_row), or None."""
    for (r, c) in index.get(label, []):
        if start_row <= r < end_row:
            return r
    return None


def _data_col(index: Dict) -> int:
    """Find the column for the current month's data."""
    now = datetime.now()
    for label in (
        f"{now.year}年{now.month}月",
        f"{now.month}月",
        str(now.month),
        f"{now.year}/{now.month:02d}",
    ):
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


def _extract_plan(text: str) -> Optional[str]:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "プラン" not in line:
            continue
        m = re.search(r"プラン[：:]\s*(.+)", line)
        if m and m.group(1).strip():
            return _normalize_plan(m.group(1).strip())
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
    logger.info(f"Using data column: {col}")

    # Locate section start rows
    b_ind_start = _section_start(index, SECTION_B_INDIVIDUAL)
    b_store_start = _section_start(index, SECTION_B_STORE)
    lmp_start = _section_start(index, SECTION_LMP)

    b_ind_end = b_store_start if b_store_start > b_ind_start else 9999
    b_store_end = lmp_start if lmp_start > b_store_start else 9999
    lmp_end = 9999
    for lbl in ("OEM初期費用", "OEM　個人", "OEM個人"):
        pos = index.get(lbl, [])
        if pos and pos[0][0] > lmp_start:
            lmp_end = min(lmp_end, pos[0][0])

    logger.info(
        f"Sections — B個人:{b_ind_start}-{b_ind_end}, "
        f"B店舗:{b_store_start}-{b_store_end}, LMP:{lmp_start}-{lmp_end}"
    )

    updated: List[str] = []
    plan_type = _extract_plan(text)

    def apply(sec_start: int, sec_end: int, label: str, delta: int = 1) -> None:
        row = _find_in_range(index, label, sec_start, sec_end)
        if row:
            _inc(ws, row, col, delta)
            updated.append(f"{label}({'+' if delta > 0 else ''}{delta})")
        else:
            logger.warning(f"Label '{label}' not found in rows {sec_start}-{sec_end}")

    def b_ind(label: str, delta: int = 1) -> None:
        apply(b_ind_start, b_ind_end, label, delta)

    def b_store(label: str, delta: int = 1) -> None:
        apply(b_store_start, b_store_end, label, delta)

    def lmp(label: str, delta: int = 1) -> None:
        apply(lmp_start, lmp_end, label, delta)

    is_b_ind = plan_type == "B個人"
    is_b_store = plan_type == "B店舗"
    is_lmp_ind = plan_type == "LMP個人"
    is_lmp_store = plan_type == "LMP店舗"

    if report_type == "契約獲得":
        if is_b_ind:
            b_ind(LABEL_PLAN_B_INDIVIDUAL)
            if not _is_sns_in_contract(text):
                fee = _extract_initial_fee(text)
                if fee is not None:
                    b_ind(LABEL_FEE)
            for opt in _extract_options(text):
                lbl = OPTION_SHEET_LABELS.get(opt)
                if lbl:
                    b_ind(lbl)
        elif is_b_store:
            b_store(LABEL_PLAN_B_STORE)
            if not _is_sns_in_contract(text):
                fee = _extract_initial_fee(text)
                if fee is not None:
                    fee_label = LABEL_FEE_HALF if fee < 9800 else LABEL_FEE
                    b_store(fee_label)
            for opt in _extract_options(text):
                lbl = OPTION_SHEET_LABELS.get(opt)
                if lbl:
                    b_store(lbl)
        elif is_lmp_ind:
            lmp(LABEL_PLAN_LMP_INDIVIDUAL)
        elif is_lmp_store:
            lmp(LABEL_PLAN_LMP_STORE)

    elif report_type == "解約":
        if is_b_ind:
            b_ind(LABEL_PLAN_B_INDIVIDUAL, delta=-1)
            for opt in _extract_options(text):
                lbl = OPTION_SHEET_LABELS.get(opt)
                if lbl:
                    b_ind(lbl, delta=-1)
        elif is_b_store:
            b_store(LABEL_PLAN_B_STORE, delta=-1)
            for opt in _extract_options(text):
                lbl = OPTION_SHEET_LABELS.get(opt)
                if lbl:
                    b_store(lbl, delta=-1)
        elif is_lmp_ind:
            lmp(LABEL_PLAN_LMP_INDIVIDUAL, delta=-1)
        elif is_lmp_store:
            lmp(LABEL_PLAN_LMP_STORE, delta=-1)

    elif report_type == "課金前解約":
        if is_b_ind:
            b_ind(LABEL_PLAN_B_INDIVIDUAL, delta=-1)
            fee = _extract_initial_fee(text)
            if fee and fee > 0:
                b_ind(LABEL_FEE, delta=-1)
            for opt in _extract_options(text):
                lbl = OPTION_SHEET_LABELS.get(opt)
                if lbl:
                    b_ind(lbl, delta=-1)
        elif is_b_store:
            b_store(LABEL_PLAN_B_STORE, delta=-1)
            fee = _extract_initial_fee(text)
            if fee and fee > 0:
                fee_label = LABEL_FEE_HALF if fee < 9800 else LABEL_FEE
                b_store(fee_label, delta=-1)
            for opt in _extract_options(text):
                lbl = OPTION_SHEET_LABELS.get(opt)
                if lbl:
                    b_store(lbl, delta=-1)

    elif report_type in ("オプション追加", "オプション解約"):
        delta = 1 if report_type == "オプション追加" else -1
        opt_text = _next_line_value(text, "対象オプション").strip()
        opt_name = OPTION_NAME_MAP.get(opt_text)
        if opt_name and opt_name in OPTION_SHEET_LABELS:
            lbl = OPTION_SHEET_LABELS[opt_name]
            change = _option_price_change(text)
            if change is not None:
                if _is_individual_by_price(opt_name, change):
                    b_ind(lbl, delta=delta)
                else:
                    b_store(lbl, delta=delta)
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
            lbl_before = LABEL_FEE_HALF if before < 9800 else LABEL_FEE
            b_store(lbl_before, delta=-1)
            if after > 0:
                lbl_after = LABEL_FEE_HALF if after < 9800 else LABEL_FEE
                b_store(lbl_after, delta=1)

    return (report_type, updated)
