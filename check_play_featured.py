import os
import re
import time
import json
import smtplib
import hashlib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from datetime import datetime
import winreg
import mimetypes
from pathlib import Path

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException


# =========================
# 사용자 설정
# =========================
OUTPUT_DIR     = "playstore_screenshots"
GOOGLEPLAY_DIR = os.path.join(OUTPUT_DIR, "googleplay")
APPSTORE_DIR   = os.path.join(OUTPUT_DIR, "appstore")

ACCOUNTS = [
    {
        "name": "계정A",
        "profile_dir": "uc_profile_a",
        "cookie_file": "google_cookies_a.json",
        "save_cookie": True,
    },
    {
        "name": "계정B",
        "profile_dir": "uc_profile_b",
        "cookie_file": "google_cookies_b.json",
        "save_cookie": True,
    },
]

COUNTRIES = [
    {"name": "US", "url": "https://play.google.com/store/games?device=phone&hl=en&gl=US"},
    {"name": "JP", "url": "https://play.google.com/store/games?device=phone&hl=ja&gl=JP"},
    {"name": "TW", "url": "https://play.google.com/store/games?device=phone&hl=zh-TW&gl=TW"},
    {"name": "KR", "url": "https://play.google.com/store/games?device=phone&hl=ko&gl=KR"},
    {"name": "TH", "url": "https://play.google.com/store/games?device=phone&hl=th&gl=TH"},
]

SECTIONS_BY_COUNTRY = {
    "US": ["Newly launched games", "Events happening now", "Pre-registration games"],
    "KR": ["최근 신규 추천 게임", "진행 중인 이벤트", "출시 예정! 사전등록 하세요"],
    "JP": ["新規リリースのゲーム", "事前登録できるゲーム"],
    "TH": ["เกมเปิดใหม่ล่าสุด", "เกมที่ต้องลงทะเบียนล่วงหน้า"],
    "TW": ["新上架遊戲", "預先註冊的遊戲"],
}

SECTION_KO = {
    "Newly launched games":        "신규 출시 게임",
    "Events happening now":        "진행 중인 이벤트",
    "Pre-registration games":      "사전등록 게임",
    "新規リリースのゲーム":          "신규 출시 게임",
    "事前登録できるゲーム":          "사전등록 게임",
    "เกมเปิดใหม่ล่าสุด":           "신규 출시 게임",
    "เกมที่ต้องลงทะเบียนล่วงหน้า": "사전등록 게임",
    "新上架遊戲":                   "신규 출시 게임",
    "預先註冊的遊戲":                "사전등록 게임",
}

HEADLESS = True

# =========================
# 이메일 설정
# =========================
def _load_env():
    env_path = Path(__file__).parent / "emaillist.env"
    env = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    else:
        print(f"[경고] emaillist.env 파일을 찾을 수 없습니다: {env_path}")
    return env

_env = _load_env()

EMAIL_ENABLED             = True
EMAIL_SENDER              = _env.get("EMAIL_SENDER", "")
EMAIL_APP_PASSWORD        = _env.get("EMAIL_APP_PASSWORD", "")
EMAIL_RECIPIENTS_INTERNAL = [x.strip() for x in _env.get("EMAIL_RECIPIENTS_INTERNAL", "").split(",") if x.strip()]
EMAIL_RECIPIENTS_EXTERNAL = [x.strip() for x in _env.get("EMAIL_RECIPIENTS_EXTERNAL", "").split(",") if x.strip()]
EMAIL_RECIPIENTS          = EMAIL_RECIPIENTS_INTERNAL

COUNTRY_FLAGS = {
    "US": "\U0001F1FA\U0001F1F8",
    "JP": "\U0001F1EF\U0001F1F5",
    "TW": "\U0001F1F9\U0001F1FC",
    "KR": "\U0001F1F0\U0001F1F7",
    "TH": "\U0001F1F9\U0001F1ED",
}

# =========================
# Google Sheets / Drive
# =========================
ENABLE_GOOGLE_LOGGING          = True
GOOGLE_CREDENTIALS_PATH        = os.path.join(os.path.dirname(__file__), "credentials.json")
GOOGLE_SPREADSHEET_NAME        = "플레이스토어_모니터링"
GOOGLE_RESULT_SHEET_NAME       = "모니터링_결과"
GOOGLE_SETTINGS_SHEET_NAME     = "설정"
GOOGLE_LOCALIZATION_SHEET_NAME = "현지화"
GOOGLE_DRIVE_FOLDER_NAME       = "플레이스토어_스크린샷"
GOOGLE_PARENT_FOLDER_NAME      = "Store Featuring"
GOOGLE_SPREADSHEET_ID          = ""
GOOGLE_DRIVE_FOLDER_ID         = ""

RESULT_HEADERS   = ["확인 날짜", "스토어", "국가", "섹션명", "노출 여부", "스크린샷 링크"]
SETTINGS_HEADERS = ["게임 키워드", "체크 시작일", "체크 종료일", "모니터링 수신자", "최종 발송 수신자"]


# =========================
# 유틸
# =========================
def safe_filename(text):
    text = re.sub(r"[\\/:*?\"<>|]+", "_", text.strip())
    return re.sub(r"\s+", "_", text)

def make_cid(store_label, country, key):
    """store_label+country+key → MD5 해시 8자리 → 완전 ASCII, 순서 무관하게 항상 동일"""
    raw = f"{store_label}_{country}_{key}"
    h = hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]
    return f"img_{h}"

def section_display_name(section_raw, country_code):
    if country_code == "KR":
        return section_raw
    ko = SECTION_KO.get(section_raw, "")
    return f"{section_raw} ({ko})" if ko else section_raw

def build_screenshot_path(date_str, country_code, section_display, matched_keyword="", account_name=""):
    parts = [date_str, country_code, safe_filename(section_display)]
    if matched_keyword:
        parts.append(safe_filename(matched_keyword))
    if account_name:
        parts.append(safe_filename(account_name))
    return os.path.join(GOOGLEPLAY_DIR, "_".join(parts) + ".png")

def parse_date_flexible(date_str):
    date_str = date_str.strip()
    if not date_str:
        return ""
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m/%d", "%m-%d",
                "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(date_str, fmt)
            if parsed.year == 1900:
                parsed = parsed.replace(year=datetime.now().year)
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str

def parse_emails(raw):
    return [x.strip() for x in raw.split(",") if x.strip()]


# =========================
# Google 연동
# =========================
def init_google_services():
    if not ENABLE_GOOGLE_LOGGING or not os.path.exists(GOOGLE_CREDENTIALS_PATH):
        return None
    try:
        import gspread
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from google.oauth2.service_account import Credentials
    except ImportError:
        print("[Google] 라이브러리 없음")
        return None

    scopes = ["https://www.googleapis.com/auth/spreadsheets",
              "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_PATH, scopes=scopes)
    print(f"[Google] 서비스 계정: {getattr(creds, 'service_account_email', '')}")
    drive_service = build("drive", "v3", credentials=creds)
    gs_client = gspread.authorize(creds)

    def find_id(mime, name, parent=None):
        q = f"name='{name}' and mimeType='{mime}' and trashed=false"
        if parent:
            q += f" and '{parent}' in parents"
        r = drive_service.files().list(q=q, fields="files(id)", pageSize=5,
            supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        f = r.get("files", [])
        return f[0]["id"] if f else None

    def get_parent():
        if GOOGLE_PARENT_FOLDER_NAME:
            pid = find_id("application/vnd.google-apps.folder", GOOGLE_PARENT_FOLDER_NAME)
            if pid:
                return pid
        return ""

    def get_folder():
        if GOOGLE_DRIVE_FOLDER_ID:
            return GOOGLE_DRIVE_FOLDER_ID
        p = get_parent()
        fid = find_id("application/vnd.google-apps.folder", GOOGLE_DRIVE_FOLDER_NAME, p or None)
        if fid:
            return fid
        meta = {"name": GOOGLE_DRIVE_FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"}
        if p:
            meta["parents"] = [p]
        return drive_service.files().create(body=meta, fields="id", supportsAllDrives=True).execute()["id"]

    def get_spreadsheet():
        if GOOGLE_SPREADSHEET_ID:
            return gs_client.open_by_key(GOOGLE_SPREADSHEET_ID)
        p = get_parent()
        sid = find_id("application/vnd.google-apps.spreadsheet", GOOGLE_SPREADSHEET_NAME, p or None)
        if sid:
            return gs_client.open_by_key(sid)
        ss = gs_client.create(GOOGLE_SPREADSHEET_NAME)
        if p:
            try:
                drive_service.files().update(fileId=ss.id, addParents=p, fields="id").execute()
            except Exception:
                pass
        return ss

    def ensure_sheet(ss, name, headers=None, init_data=None):
        try:
            ws = ss.worksheet(name)
        except Exception:
            ws = ss.add_worksheet(title=name, rows="1000", cols="10")
            if headers:
                ws.update("A1", [headers])
            if init_data:
                ws.update("A2", init_data)
            print(f"[Google] '{name}' 시트 생성 완료")
        return ws

    fid = get_folder()
    ss  = get_spreadsheet()

    result_ws = ensure_sheet(ss, GOOGLE_RESULT_SHEET_NAME, RESULT_HEADERS)
    try:
        v = result_ws.get_all_values()
        if not v or v[0][:len(RESULT_HEADERS)] != RESULT_HEADERS:
            result_ws.update("A1", [RESULT_HEADERS])
            print("[Google] '모니터링_결과' 헤더 업데이트 완료")
    except Exception:
        pass

    try:
        from gspread_formatting import set_data_validation_for_cell_range, DataValidationRule, BooleanCondition
        rule = DataValidationRule(BooleanCondition("ONE_OF_LIST", ["노출됨", "미노출"]), showCustomUi=True)
        set_data_validation_for_cell_range(result_ws, "E2:E1000", rule)
    except Exception as e:
        print(f"[Google] 드롭다운 설정 참고: {e}")

    settings_ws = ensure_sheet(ss, GOOGLE_SETTINGS_SHEET_NAME,
        headers=SETTINGS_HEADERS,
        init_data=[["소울 스트라이크", "", "", "biz_dev@com2us.com", "biz_dev@com2us.com"]])
    try:
        eh = settings_ws.row_values(1)
        if len(eh) < len(SETTINGS_HEADERS):
            settings_ws.update("A1", [SETTINGS_HEADERS])
            print("[Google] '설정' 시트 헤더 업데이트 완료")
    except Exception:
        pass

    localization_ws = ensure_sheet(ss, GOOGLE_LOCALIZATION_SHEET_NAME,
        headers=["한글명", "EN", "JA", "ZH-TW", "TH"],
        init_data=[["소울 스트라이크", "Soul Strike", "ソウルストライク", "靈魂衝擊", "Soul Strike"]])

    return {
        "drive_service":          drive_service,
        "result_worksheet":       result_ws,
        "settings_worksheet":     settings_ws,
        "localization_worksheet": localization_ws,
        "folder_id":              fid,
        "spreadsheet":            ss,
        "MediaFileUpload":        MediaFileUpload,
    }


def read_settings(ctx):
    game_names    = []
    game_settings = {}
    try:
        rows = ctx["settings_worksheet"].get_all_values()
        for i, row in enumerate(rows):
            if i == 0:
                continue
            kw    = row[0].strip() if len(row) > 0 else ""
            start = row[1].strip() if len(row) > 1 else ""
            end   = row[2].strip() if len(row) > 2 else ""
            mon_r = row[3].strip() if len(row) > 3 else ""
            fin_r = row[4].strip() if len(row) > 4 else ""
            if not kw:
                continue
            game_names.append(kw)
            game_settings[kw] = {
                "start":              parse_date_flexible(start),
                "end":                parse_date_flexible(end),
                "monitor_recipients": parse_emails(mon_r) if mon_r else EMAIL_RECIPIENTS_INTERNAL,
                "final_recipients":   parse_emails(fin_r) if fin_r else EMAIL_RECIPIENTS_EXTERNAL,
            }
    except Exception as e:
        print(f"[Google] 설정 읽기 실패: {e}")

    for g, s in game_settings.items():
        period = f"{s['start']} ~ {s['end']}" if s["start"] and s["end"] else "미설정"
        print(f"[설정] {g} | 체크 기간: {period} | 모니터링 수신자: {', '.join(s['monitor_recipients'])}")

    return game_names, game_settings


def read_localization(ctx):
    loc_map = {}
    try:
        rows = ctx["localization_worksheet"].get_all_values()
        for i, row in enumerate(rows):
            if i == 0:
                continue
            kr = row[0].strip() if row else ""
            if not kr:
                continue
            names = {kr}
            for j in range(1, len(row)):
                v = row[j].strip()
                if v:
                    names.add(v)
            loc_map[kr] = list(names)
    except Exception as e:
        print(f"[Google] 현지화 읽기 실패: {e}")
    return loc_map


def build_keywords(game_names, loc_map):
    keywords, seen = [], set()
    for name in game_names:
        for kw in loc_map.get(name, [name]):
            if kw.lower() not in seen:
                seen.add(kw.lower())
                keywords.append(kw)
    return keywords


def upload_screenshot(ctx, path):
    for attempt in range(3):
        try:
            ds, MFU = ctx["drive_service"], ctx["MediaFileUpload"]
            mime = mimetypes.guess_type(path)[0] or "image/png"
            media = MFU(path, mimetype=mime, resumable=False)
            meta = {"name": os.path.basename(path), "parents": [ctx["folder_id"]]}
            c = ds.files().create(body=meta, media_body=media, fields="id,webViewLink",
                                  supportsAllDrives=True).execute()
            link = c.get("webViewLink") or f"https://drive.google.com/file/d/{c['id']}/view"
            print(f"  [Google] 업로드: {link}")
            return link
        except Exception as e:
            print(f"  [Google] 업로드 실패 (시도 {attempt+1}/3): {e}")
            if attempt < 2:
                time.sleep(3)
    return ""


def append_result_row(ctx, row):
    try:
        ctx["result_worksheet"].insert_row(row, index=2, value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"  [Google] 시트 기록 실패: {e}")


# =========================
# 이메일 HTML 빌더
# =========================
def _build_store_section_html(store_label, merged_r, merged_s, country_list, failed_countries):
    html = []
    has_exposed = False

    html.append(f"""
<tr><td style="padding:16px 40px 4px;">
  <div style="font-size:15px;font-weight:700;color:#444;border-left:4px solid #1a73e8;padding-left:10px;">
    {store_label}
  </div>
</td></tr>
""")

    for c in country_list:
        cn = c["name"] if isinstance(c, dict) else c
        if cn in failed_countries:
            continue
        cr = merged_r.get(cn, {})
        cs = merged_s.get(cn, {})

        exposed_items = []
        if cr.get("top_banner") == "노출됨":
            exposed_items.append(("상단 배너", "top_banner"))
        for label in SECTIONS_BY_COUNTRY.get(cn, []):
            if cr.get(label) == "노출됨":
                display = section_display_name(label, cn)
                exposed_items.append((display, label))
        for key, status in cr.items():
            if status == "노출됨" and key != "top_banner" and key not in SECTIONS_BY_COUNTRY.get(cn, []):
                exposed_items.append((key, key))

        if not exposed_items:
            continue

        has_exposed = True
        flag = COUNTRY_FLAGS.get(cn, "")
        html.append(f"""
<tr><td style="padding:12px 40px 4px;">
  <h2 style="margin:0;font-size:16px;color:#1a1a2e;border-bottom:1px solid #e0e0e0;padding-bottom:6px;">
    {flag} {cn}
  </h2>
</td></tr>
""")
        for display_name, key in exposed_items:
            # make_cid 로 항상 동일한 ASCII cid 생성
            cid_key = make_cid(store_label, cn, key)
            meta = cs.get(key, {})
            path = meta.get("path", "")

            html.append(f"""
<tr><td style="padding:4px 40px 4px 56px;">
  <span style="font-size:14px;color:#333;">{display_name}</span>
  <span style="display:inline-block;background:#2e7d32;color:#fff;font-size:11px;font-weight:600;
        padding:2px 10px;border-radius:12px;margin-left:8px;">노출됨</span>
</td></tr>
""")
            if path and os.path.exists(path):
                html.append(f"""
<tr><td style="padding:6px 40px 10px 56px;">
  <img src="cid:{cid_key}" style="max-width:520px;width:100%;border-radius:8px;border:1px solid #e0e0e0;" />
</td></tr>
""")
            else:
                link = meta.get("link", "")
                if link:
                    html.append(f"""
<tr><td style="padding:2px 40px 10px 56px;">
  <a href="{link}" style="font-size:12px;color:#1a73e8;">스크린샷 보기</a>
</td></tr>
""")

    if not has_exposed:
        label_name = "구글플레이" if "Google" in store_label else "앱스토어"
        html.append(f"""
<tr><td style="padding:8px 40px 8px 56px;">
  <p style="font-size:13px;color:#999;margin:0;">{label_name} 피쳐드 내역 없음</p>
</td></tr>
""")

    return "".join(html), has_exposed


def send_email_report(today_str, merged_r_gp, merged_s_gp, keywords, gctx=None,
                      recipients=None, game_title=None,
                      merged_r_as=None, merged_s_as=None,
                      include_spreadsheet_btn=True,
                      include_failed_countries=True):
    to_list = recipients if recipients else EMAIL_RECIPIENTS
    if not EMAIL_ENABLED or not EMAIL_SENDER or not EMAIL_APP_PASSWORD or not to_list:
        print("[이메일] 발신 정보 미설정 또는 수신자 없음. 건너뜀.")
        return

    title_kr = game_title if game_title else "게임"
    subject  = f"{title_kr} — 플레이스토어 피쳐링 현황 {today_str}"

    exposed_sections  = 0
    exposed_countries = set()
    failed_countries  = []

    for c in COUNTRIES:
        cn = c["name"]
        cr = merged_r_gp.get(cn, {})
        all_unknown = all(v == "섹션 확인 안됨" for k, v in cr.items() if k != "top_banner") if cr else True
        banner_miss = cr.get("top_banner", "미노출") == "미노출"
        if all_unknown and banner_miss and len(cr) <= 1:
            failed_countries.append(cn)
            continue
        for key, status in cr.items():
            if status == "노출됨":
                exposed_sections += 1
                exposed_countries.add(cn)

    spreadsheet_url = ""
    if gctx and include_spreadsheet_btn:
        try:
            ss = gctx.get("spreadsheet")
            if ss:
                spreadsheet_url = ss.url
        except Exception:
            pass

    html = []
    html.append(f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:'Malgun Gothic','Apple SD Gothic Neo',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;">
<tr><td align="center" style="padding:20px 0;">
<table width="640" cellpadding="0" cellspacing="0"
  style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">

<tr><td style="background:#1a1a2e;padding:32px 40px;">
  <h1 style="margin:0;font-size:22px;color:#fff;font-weight:700;">{title_kr} — 플레이스토어 피쳐링 현황</h1>
  <p style="margin:8px 0 0;font-size:14px;color:#a0a0c0;">{today_str} 기준 모니터링 결과</p>
</td></tr>

<tr><td style="padding:24px 40px 8px;">
  <p style="margin:0;font-size:14px;color:#333;line-height:1.8;">
    안녕하세요 사업제휴팀입니다.<br>
    <strong>{title_kr}</strong> 의 금주 피쳐드 내역이 확인되어 공유 드립니다.<br>
    주요 국가 기준으로 체크 되었으며 기기 또는 계정별 피쳐드 내역은 다르게 노출될 수 있는 점 참고 바랍니다.
  </p>
</td></tr>

<tr><td style="padding:16px 40px;">
  <table width="100%" cellpadding="0" cellspacing="0"><tr>
    <td width="50%" style="padding-right:8px;">
      <div style="background:#f0faf0;border:1px solid #c8e6c9;border-radius:8px;padding:16px;text-align:center;">
        <div style="font-size:30px;font-weight:700;color:#2e7d32;">{exposed_sections}</div>
        <div style="font-size:12px;color:#555;margin-top:4px;">총 노출 섹션 수</div>
      </div>
    </td>
    <td width="50%" style="padding-left:8px;">
      <div style="background:#f0faf0;border:1px solid #c8e6c9;border-radius:8px;padding:16px;text-align:center;">
        <div style="font-size:30px;font-weight:700;color:#2e7d32;">{len(exposed_countries)}</div>
        <div style="font-size:12px;color:#555;margin-top:4px;">노출 확인 국가 수</div>
      </div>
    </td>
  </tr></table>
</td></tr>
""")

    if failed_countries and include_failed_countries:
        flags = " ".join(f"{COUNTRY_FLAGS.get(fc,'')}{fc}" for fc in failed_countries)
        html.append(f"""
<tr><td style="padding:0 40px 12px;">
  <div style="background:#fff8e1;border:1px solid #ffe082;border-radius:8px;padding:12px 16px;">
    <span style="font-size:15px;">⚠️</span>
    <span style="font-size:13px;color:#6d4c00;margin-left:6px;">페이지 접속 실패: <strong>{flags}</strong></span>
  </div>
</td></tr>
""")

    html.append('<tr><td style="padding:8px 40px 0;"><hr style="border:none;border-top:1px solid #eee;"></td></tr>')

    gp_html, _ = _build_store_section_html("🎮 Google Play", merged_r_gp, merged_s_gp, COUNTRIES, failed_countries)
    html.append(gp_html)

    html.append('<tr><td style="padding:8px 40px 0;"><hr style="border:none;border-top:1px solid #eee;"></td></tr>')

    if merged_r_as is not None:
        as_html, _ = _build_store_section_html("🍎 App Store", merged_r_as, merged_s_as or {}, COUNTRIES, [])
        html.append(as_html)
    else:
        html.append("""
<tr><td style="padding:16px 40px 4px;">
  <div style="font-size:15px;font-weight:700;color:#444;border-left:4px solid #1a73e8;padding-left:10px;">
    🍎 App Store
  </div>
</td></tr>
<tr><td style="padding:8px 40px 8px 56px;">
  <p style="font-size:13px;color:#999;margin:0;">앱스토어 피쳐드 내역 없음</p>
</td></tr>
""")

    if spreadsheet_url:
        html.append(f"""
<tr><td style="padding:20px 40px 8px;text-align:center;">
  <a href="{spreadsheet_url}" target="_blank"
     style="display:inline-block;background:#1a73e8;color:#fff;font-size:14px;font-weight:600;
            padding:12px 32px;border-radius:8px;text-decoration:none;">
    스프레드시트 전체 결과 보기
  </a>
</td></tr>
""")

    html.append("""
<tr><td style="padding:20px 40px 28px;text-align:center;">
  <p style="font-size:11px;color:#aaa;margin:0;">이 메일은 Play Store 모니터링 스크립트에 의해 자동 생성되었습니다.</p>
</td></tr>
</table></td></tr></table>
</body></html>
""")

    html_body = "".join(html)

    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = ", ".join(to_list)

    msg_alt = MIMEMultipart("alternative")
    msg.attach(msg_alt)
    msg_alt.attach(MIMEText(f"Play Store 피쳐링 현황 ({today_str})", "plain", "utf-8"))
    msg_alt.attach(MIMEText(html_body, "html", "utf-8"))

    # 이미지 첨부 — make_cid 로 HTML과 동일한 cid 보장
    def attach_images(m_s, store_label):
        for cn, cs in m_s.items():
            for key, meta in cs.items():
                path = meta.get("path", "")
                if not path or not os.path.exists(path):
                    continue
                cid_key = make_cid(store_label, cn, key)
                ext = os.path.splitext(path)[1].lower().lstrip(".")
                subtype = "jpeg" if ext in ("jpg", "jpeg") else ext if ext else "png"
                try:
                    with open(path, "rb") as f:
                        img_data = f.read()
                    img_part = MIMEImage(img_data, _subtype=subtype)
                    img_part.add_header("Content-ID", f"<{cid_key}>")
                    img_part.add_header("Content-Disposition", "inline",
                                        filename=os.path.basename(path))
                    msg.attach(img_part)
                    print(f"  [이메일] 이미지 첨부: {os.path.basename(path)} (cid:{cid_key})")
                except Exception as e:
                    print(f"  [이메일] 이미지 첨부 실패 ({path}): {e}")

    attach_images(merged_s_gp, "🎮 Google Play")
    if merged_s_as:
        attach_images(merged_s_as, "🍎 App Store")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_APP_PASSWORD)
            server.sendmail(EMAIL_SENDER, to_list, msg.as_string())
        print(f"[이메일] 발송 완료 → {', '.join(to_list)}")
    except Exception as e:
        print(f"[이메일] 발송 실패: {e}")


# =========================
# Chrome
# =========================
def get_chrome_major():
    for rp in [r"SOFTWARE\Google\Chrome\BLBeacon", r"SOFTWARE\WOW6432Node\Google\Chrome\BLBeacon"]:
        for rk in [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]:
            try:
                with winreg.OpenKey(rk, rp) as k:
                    v, _ = winreg.QueryValueEx(k, "version")
                    m = re.match(r"^(\d+)\.", str(v))
                    if m:
                        return int(m.group(1))
            except Exception:
                pass
    return None

def try_click_any(driver, xpaths, timeout=2):
    for xp in xpaths:
        try:
            el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.XPATH, xp)))
            el.click()
            return True
        except Exception:
            pass
    return False

def is_login_page(driver):
    try:
        return len(driver.find_elements(By.ID, "identifierId")) > 0
    except Exception:
        return "signin" in driver.current_url.lower()

def try_login_cookies(driver, cookie_file):
    if not cookie_file or not os.path.exists(cookie_file):
        return False
    driver.get("https://accounts.google.com/signin")
    time.sleep(2)
    driver.delete_all_cookies()
    with open(cookie_file, "r", encoding="utf-8") as f:
        cookies = json.load(f)
    for c in cookies:
        try:
            driver.add_cookie(c)
        except Exception:
            pass
    driver.refresh()
    time.sleep(3)
    return not is_login_page(driver)

def save_cookies(driver, cookie_file):
    os.makedirs(os.path.dirname(os.path.abspath(cookie_file)), exist_ok=True)
    with open(cookie_file, "w", encoding="utf-8") as f:
        json.dump(driver.get_cookies(), f, ensure_ascii=False, indent=2)

def create_driver(profile_dir):
    opts = uc.ChromeOptions()
    if HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--disable-notifications")
    p = os.path.abspath(profile_dir)
    os.makedirs(p, exist_ok=True)
    opts.add_argument(f"--user-data-dir={p}")
    cm = get_chrome_major()
    try:
        return uc.Chrome(options=opts, version_main=cm) if cm else uc.Chrome(options=opts)
    except Exception:
        return uc.Chrome(options=opts)

def login_account(driver, account):
    name = account["name"]
    cf   = account.get("cookie_file", "")
    if cf and try_login_cookies(driver, cf):
        print(f"[{name}] 쿠키 자동 로그인 성공")
        return
    if HEADLESS:
        print(f"[{name}] 쿠키 로그인 실패 → HEADLESS 모드에서는 수동 로그인 불가. 쿠키 파일을 갱신하세요.")
        raise RuntimeError(f"[{name}] 헤드리스 모드 쿠키 로그인 실패")
    print(f"[{name}] 쿠키 로그인 실패 → 브라우저에서 Google 수동 로그인을 진행해주세요.")
    driver.get("https://accounts.google.com/signin")
    input(f"[{name}] 수동 로그인 완료 후 Enter를 누르세요...")
    if account.get("save_cookie") and cf:
        save_cookies(driver, cf)
        print(f"[{name}] 쿠키 저장 완료 → 다음부터 자동 로그인됩니다.")

def cleanup_account(driver, account):
    if not account.get("save_cookie", False):
        try:
            driver.delete_all_cookies()
        except Exception:
            pass
        print(f"[{account['name']}] 쿠키 삭제 완료 (보안)")


# =========================
# 페이지 스크롤 & 검색
# =========================
def scroll_full_page(driver):
    try:
        last_h = driver.execute_script("return document.body.scrollHeight;")
        for _ in range(15):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)
            new_h = driver.execute_script("return document.body.scrollHeight;")
            if new_h == last_h:
                break
            last_h = new_h
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.5)
    except Exception:
        pass

def search_keyword_in_body(driver, keywords):
    try:
        body = driver.execute_script("return document.body.innerText || '';").lower()
    except Exception:
        return None
    for kw in keywords:
        if kw.lower() in body:
            return kw
    return None

def scroll_to_keyword_and_screenshot(driver, keyword, date_str, country_code, section_name, account_name):
    kw_js = keyword.replace("\\", "\\\\").replace("'", "\\'")
    try:
        found = driver.execute_script(f"""
            var kw='{kw_js}'.toLowerCase();
            var all=document.querySelectorAll('span,a,div,p,h1,h2,h3,h4');
            var best=null,bestLen=999999;
            for(var i=0;i<all.length;i++){{
                var el=all[i],t=(el.textContent||'').toLowerCase();
                if(t.indexOf(kw)>=0&&el.offsetWidth>0&&el.offsetHeight>0&&t.length<bestLen){{
                    bestLen=t.length;best=el;
                }}
            }}
            if(best){{best.scrollIntoView({{block:'center',inline:'center'}});return true;}}
            return false;
        """)
        if found:
            time.sleep(1.5)
            path = os.path.abspath(build_screenshot_path(date_str, country_code, section_name, keyword, account_name))
            driver.save_screenshot(path)
            print(f"  [스크린샷] '{keyword}' → {section_name} 저장: {os.path.basename(path)}")
            return path
    except Exception as e:
        print(f"  [스크린샷] 오류: {e}")
    return ""


# =========================
# 섹션별 검사
# =========================
def find_section_text_range(driver, section_title, keywords):
    kw_js    = json.dumps([k.lower() for k in keywords])
    title_js = section_title.replace("\\", "\\\\").replace("'", "\\'")
    return driver.execute_script(f"""
        var st='{title_js}',stl=st.toLowerCase(),kws={kw_js};
        var all=document.querySelectorAll('span,div,h2,h3'),titleEl=null;
        for(var i=0;i<all.length;i++){{
            var el=all[i],t=(el.textContent||'').trim();
            if(el.offsetWidth>0&&(t===st||t.toLowerCase()===stl)&&t.length<st.length*3){{titleEl=el;break;}}
        }}
        if(!titleEl){{
            for(var i=0;i<all.length;i++){{
                var el=all[i],t=(el.textContent||'').trim();
                if(el.offsetWidth>0&&t.toLowerCase().indexOf(stl)>=0&&t.length<200){{titleEl=el;break;}}
            }}
        }}
        if(!titleEl) return {{found:false,reason:'no_title'}};
        var container=null,cur=titleEl.parentElement;
        for(var d=0;d<15;d++){{
            if(!cur) break;
            if(cur.offsetWidth>500&&cur.offsetHeight>150){{container=cur;break;}}
            cur=cur.parentElement;
        }}
        if(!container) return {{found:false,reason:'no_container'}};
        var ct=(container.innerText||'').toLowerCase();
        for(var k=0;k<kws.length;k++) if(ct.indexOf(kws[k])>=0) return {{found:true,keyword:kws[k],scrolled:false}};
        var scrollEl=null,bestDiff=0,divs=container.querySelectorAll('div');
        for(var i=0;i<divs.length;i++){{
            var diff=divs[i].scrollWidth-divs[i].clientWidth;
            if(diff>50&&diff>bestDiff){{bestDiff=diff;scrollEl=divs[i];}}
        }}
        if(!scrollEl) return {{found:false,reason:'not_found'}};
        for(var step=0;step<30;step++){{
            var old=scrollEl.scrollLeft;
            scrollEl.scrollLeft+=800;
            var nt=(container.innerText||'').toLowerCase();
            for(var k=0;k<kws.length;k++) if(nt.indexOf(kws[k])>=0) return {{found:true,keyword:kws[k],scrolled:true,steps:step+1}};
            if(scrollEl.scrollLeft<=old) break;
            if(scrollEl.scrollLeft+scrollEl.clientWidth>=scrollEl.scrollWidth-5) break;
        }}
        return {{found:false,reason:'not_found_after_scroll'}};
    """)

def check_section(driver, section_title, keywords, date_str, country_code, account_name):
    try:
        result = find_section_text_range(driver, section_title, keywords)
    except Exception as e:
        print(f"  [섹션] '{section_title}' → 오류: {e}")
        return "섹션 확인 안됨", None, ""

    if not result or not result.get("found"):
        reason = (result or {}).get("reason", "unknown")
        if reason in ("no_title", "no_container"):
            print(f"  [섹션] '{section_title}' → 섹션 확인 안됨 ({reason})")
            return "섹션 확인 안됨", None, ""
        print(f"  [섹션] '{section_title}' → 미노출")
        return "미노출", None, ""

    matched = next((kw for kw in keywords if kw.lower() == result["keyword"]), result["keyword"])
    scroll_info = f" [스크롤 {result.get('steps',0)}회]" if result.get("scrolled") else ""
    print(f"  [섹션] '{section_title}' → 노출됨 ({matched}){scroll_info}")
    path = scroll_to_keyword_and_screenshot(driver, matched, date_str, country_code, section_title, account_name)
    return "노출됨", matched, path


# =========================
# 국가별 전체 검사
# =========================
def check_country(driver, country_code, keywords, date_str, account_name):
    results, screenshots = {}, {}
    print(f"  페이지 전체 스크롤 (lazy-load 로드 중)...")
    scroll_full_page(driver)

    body_match = search_keyword_in_body(driver, keywords)
    print(f"  [페이지] body에 '{body_match}' 존재" if body_match else "  [페이지] body에 키워드 없음")

    section_found = set()
    for label in SECTIONS_BY_COUNTRY.get(country_code, []):
        status, matched, path = check_section(driver, label, keywords, date_str, country_code, account_name)
        results[label] = status
        if status == "노출됨" and matched:
            section_found.add(matched.lower())
            if path:
                screenshots[label] = {
                    "path": path,
                    "section_display": section_display_name(label, country_code),
                    "matched_keyword": matched,
                }

    if body_match and body_match.lower() not in section_found:
        print(f"  [배너/기타] '{body_match}' → 상단 배너/기타 영역 노출")
        results["top_banner"] = "노출됨"
        path = scroll_to_keyword_and_screenshot(driver, body_match, date_str, country_code, "상단_배너", account_name)
        if path:
            screenshots["top_banner"] = {"path": path, "section_display": "상단 배너", "matched_keyword": body_match}
    elif body_match and body_match.lower() in section_found:
        results["top_banner"] = "확인 불가 (섹션에서 발견)"
    else:
        results["top_banner"] = "미노출"

    return results, screenshots

def detect_page_accessible(driver, country_code):
    try:
        text = driver.execute_script("return document.body.innerText || '';")
        if len(text) > 500:
            return True
        for label in SECTIONS_BY_COUNTRY.get(country_code, []):
            if label.lower() in text.lower():
                return True
    except Exception:
        pass
    return False

def run_account(account, countries, keywords, date_str):
    name = account["name"]
    print(f"\n{'='*50}\n[{name}] 체크 시작\n{'='*50}")
    driver = create_driver(account["profile_dir"])
    all_results, all_screenshots = {}, {}
    try:
        login_account(driver, account)
        for c in countries:
            cn = c["name"]
            print(f"\n[{name}][{cn}] 접속: {c['url']}")
            driver.get(c["url"])
            try_click_any(driver, [
                "//button[contains(.,'동의') or contains(.,'Accept') or contains(.,'I agree') or contains(.,'同意')]",
                "//button[@id='L2AGLb']",
            ], timeout=3)
            time.sleep(3)
            for _ in range(8):
                if detect_page_accessible(driver, cn):
                    break
                time.sleep(2)
            results, screenshots = check_country(driver, cn, keywords, date_str, name)
            all_results[cn] = results
            all_screenshots[cn] = screenshots
            print(f"\n  [{cn}] 상단 배너/기타: {results.get('top_banner', '-')}")
            for label in SECTIONS_BY_COUNTRY.get(cn, []):
                print(f"  [{cn}] {label}: {results.get(label, '미노출')}")
        cleanup_account(driver, account)
    finally:
        driver.quit()
    return all_results, all_screenshots

def merge_results(results_list, screenshots_list):
    merged_r, merged_s = {}, {}
    for results, screenshots in zip(results_list, screenshots_list):
        for country, sdict in results.items():
            if country not in merged_r:
                merged_r[country] = {}
                merged_s[country] = {}
            for key, status in sdict.items():
                if status == "노출됨":
                    merged_r[country][key] = "노출됨"
                    if key not in merged_s[country]:
                        ss = screenshots.get(country, {}).get(key)
                        if ss:
                            merged_s[country][key] = ss
                elif key not in merged_r[country]:
                    merged_r[country][key] = status
                    ss = screenshots.get(country, {}).get(key)
                    if ss and key not in merged_s[country]:
                        merged_s[country][key] = ss
    return merged_r, merged_s


# =========================
# 메인
# =========================
def main():
    os.makedirs(GOOGLEPLAY_DIR, exist_ok=True)
    os.makedirs(APPSTORE_DIR,   exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    gctx = init_google_services()
    if not gctx:
        print("[스킵] Google 연동 실패 → 실행을 종료합니다.")
        return

    game_names, game_settings = read_settings(gctx)
    loc_map = read_localization(gctx)

    if not game_names:
        print("\n[스킵] 설정 시트에 게임 키워드가 없습니다.")
        return

    # 오늘 체크 대상 게임만 필터링
    active_games = []
    for g in game_names:
        s = game_settings[g]
        start, end = s["start"], s["end"]
        if not start or not end:
            print(f"[스킵] {g} → 체크 기간 미설정")
            continue
        if not (start <= today <= end):
            print(f"[스킵] {g} → 오늘({today})은 체크 기간({start}~{end}) 외")
            continue
        active_games.append(g)
        print(f"[실행] {g} → 체크 기간 내 ({start}~{end})")

    if not active_games:
        print(f"\n[스킵] 오늘({today}) 체크할 게임이 없습니다.")
        return

    all_keywords = build_keywords(active_games, loc_map)
    print(f"\n[실행] 오늘({today}) 체크 시작")
    print(f"[실행] 대상 게임: {active_games}")
    print(f"[실행] 최종 키워드: {all_keywords}")

    all_r, all_s = [], []
    for account in ACCOUNTS:
        r, s = run_account(account, COUNTRIES, all_keywords, today)
        all_r.append(r)
        all_s.append(s)

    merged_r, merged_s = merge_results(all_r, all_s)

    print(f"\n{'='*50}\n최종 결과 (두 계정 합산, 중복 제거)\n{'='*50}")
    for c in COUNTRIES:
        cn = c["name"]
        cr = merged_r.get(cn, {})
        print(f"\n[{cn}] 상단 배너/기타: {cr.get('top_banner', '-')}")
        for label in SECTIONS_BY_COUNTRY.get(cn, []):
            print(f"[{cn}] {label}: {cr.get(label, '미노출')}")

    # 시트 기록 + Drive 업로드
    for c in COUNTRIES:
        cn = c["name"]
        for key, meta in merged_s.get(cn, {}).items():
            if merged_r.get(cn, {}).get(key) != "노출됨":
                continue
            p = meta.get("path", "")
            if not p or not os.path.exists(p):
                continue
            link = upload_screenshot(gctx, p)
            if link:
                meta["link"] = link
            append_result_row(gctx, [today, "구글플레이", cn, meta["section_display"], "노출됨", link])

    # 게임별로 모니터링 이메일 발송
    for game_name in active_games:
        recipients    = game_settings[game_name]["monitor_recipients"]
        game_keywords = build_keywords([game_name], loc_map)
        print(f"\n[이메일] '{game_name}' 모니터링 결과 발송 → {', '.join(recipients)}")
        send_email_report(
            today, merged_r, merged_s, game_keywords, gctx,
            recipients=recipients, game_title=game_name,
            merged_r_as=None, merged_s_as=None,
            include_spreadsheet_btn=True,
            include_failed_countries=True,
        )

    print(f"\n완료. 스크린샷 폴더: {os.path.abspath(OUTPUT_DIR)}")


if __name__ == "__main__":
    main()