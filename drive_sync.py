"""
Google Drive 이미지 수집 모듈
- 지정 폴더/{YYYYMMDD}/ 에서 수동 추가 이미지 수집
- 파일명 규칙: {국가}_{탭}_{섹션명}_{게임명}.png
- 규칙 불일치 파일은 경고 이메일 발송 후 제외
"""
import json
import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
SCREENSHOTS_DIR = BASE_DIR / "screenshots"

VALID_TABS = {"today", "games"}
VALID_COUNTRIES_DEFAULT = {"kr", "us", "jp", "gb", "de", "cn", "tw", "th", "fr", "sg"}

# 파일명 정규식: {country}_{tab}_{section}_{game}.png
FILENAME_RE = re.compile(
    r'^([a-zA-Z]{2,3})_([a-zA-Z]+)_(.+)_(.+)\.png$',
    re.IGNORECASE
)


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def validate_filename(filename: str, config: dict) -> bool:
    """파일명 규칙 검증"""
    m = FILENAME_RE.match(filename)
    if not m:
        return False
    country, tab = m.group(1).lower(), m.group(2).lower()
    valid_countries = set(config.get("countries", list(VALID_COUNTRIES_DEFAULT)))
    valid_tabs = set(config.get("tabs", list(VALID_TABS)))
    return country in valid_countries and tab in valid_tabs


def parse_filename(filename: str, config: dict) -> dict | None:
    """파일명에서 메타데이터 추출"""
    m = FILENAME_RE.match(filename)
    if not m:
        return None
    country = m.group(1).lower()
    tab = m.group(2).lower()
    section = m.group(3)
    game_part = m.group(4)

    # game_id 매핑 (default_name 또는 현지화명으로 역매핑)
    game_id = _find_game_id(game_part, country, config)

    return {
        "country": country,
        "tab": tab,
        "section": section,
        "game_id": game_id,
        "screenshot": filename,
        "source": "drive_manual",
    }


def _find_game_id(game_part: str, country: str, config: dict) -> str | None:
    """게임명 문자열로 game_id 역매핑"""
    for game in config.get("games", []):
        # default_name 비교
        if game_part.lower() == game["default_name"].lower():
            return game["id"]
        # 현지화명 비교
        localized = game.get("names", {}).get(country, "")
        if localized and game_part == localized:
            return game["id"]
        # id 직접 일치
        if game_part.lower() == game["id"].lower():
            return game["id"]
    return game_part.lower()  # 매핑 실패 시 원본 소문자 사용


def collect_manual_images(date_str_yyyymmdd: str) -> list[dict]:
    """
    Google Drive에서 오늘 날짜 폴더의 수동 이미지 수집.
    Drive 연동이 안 된 경우 로컬 screenshots/ 폴더에서 수집 (개발/테스트용).
    반환: 유효 이미지 메타 리스트
    """
    config = load_config()
    creds_json = os.environ.get("GOOGLE_DRIVE_CREDENTIALS", "")

    if creds_json:
        return _collect_from_drive(date_str_yyyymmdd, config, creds_json)
    else:
        print("  [Drive] GOOGLE_DRIVE_CREDENTIALS 없음 — 로컬 screenshots/ 폴더 탐색")
        return _collect_from_local(date_str_yyyymmdd, config)


def _collect_from_drive(date_str: str, config: dict, creds_json: str) -> list[dict]:
    """Google Drive API로 이미지 수집"""
    try:
        import json as _json
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseDownload
        import io

        creds_data = _json.loads(creds_json)
        creds = Credentials.from_service_account_info(
            creds_data,
            scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
        service = build("drive", "v3", credentials=creds)

        folder_path = config["drive"]["folder_path"]
        target_folder = f"{folder_path}/{date_str}"

        # 폴더 ID 탐색
        folder_id = _find_drive_folder_id(service, target_folder)
        if not folder_id:
            print(f"  [Drive] 폴더 없음: {target_folder}")
            return []

        # 파일 목록
        results = service.files().list(
            q=f"'{folder_id}' in parents and mimeType='image/png' and trashed=false",
            fields="files(id, name)",
            pageSize=200,
        ).execute()

        files = results.get("files", [])
        print(f"  [Drive] {len(files)}개 파일 발견")

        valid, invalid = [], []
        SCREENSHOTS_DIR.mkdir(exist_ok=True)

        for f in files:
            name = f["name"]
            if validate_filename(name, config):
                # 로컬에 다운로드
                request = service.files().get_media(fileId=f["id"])
                local_path = SCREENSHOTS_DIR / name
                fh = io.FileIO(str(local_path), "wb")
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                meta = parse_filename(name, config)
                if meta:
                    valid.append(meta)
            else:
                invalid.append(name)

        if invalid:
            _warn_invalid_files(invalid, config)

        return valid

    except Exception as e:
        print(f"  [Drive 수집 오류] {e}")
        return []


def _find_drive_folder_id(service, folder_path: str) -> str | None:
    """슬래시 구분 경로로 Drive 폴더 ID 탐색"""
    parts = folder_path.strip("/").split("/")
    parent_id = "root"
    for part in parts:
        query = (
            f"name='{part}' and '{parent_id}' in parents "
            f"and mimeType='application/vnd.google-apps.folder' and trashed=false"
        )
        result = service.files().list(q=query, fields="files(id)").execute()
        files = result.get("files", [])
        if not files:
            return None
        parent_id = files[0]["id"]
    return parent_id


def _collect_from_local(date_str: str, config: dict) -> list[dict]:
    """로컬 screenshots/ 폴더에서 이미 있는 이미지 수집 (테스트용)"""
    SCREENSHOTS_DIR.mkdir(exist_ok=True)
    valid, invalid = [], []

    for p in SCREENSHOTS_DIR.glob("*.png"):
        name = p.name
        if validate_filename(name, config):
            meta = parse_filename(name, config)
            if meta:
                valid.append(meta)
        else:
            invalid.append(name)

    if invalid:
        _warn_invalid_files(invalid, config)

    return valid


def _warn_invalid_files(invalid_files: list, config: dict):
    """파일명 오류 경고 — 콘솔 출력 및 이메일 발송"""
    print(f"  [경고] 파일명 규칙 불일치로 제외된 파일 {len(invalid_files)}개:")
    for f in invalid_files:
        print(f"    - {f}")

    # 경고 이메일 발송
    # 수신자는 config.json이 아닌 환경변수 RECIPIENTS_CONFIG에서 로드 (보안 설계)
    try:
        import json as _json
        import os
        from mailer import Mailer
        mailer = Mailer(
            sender=config["email"]["sender"],
            app_password=os.environ.get("GMAIL_APP_PASSWORD", ""),
            config=config,
        )
        raw = os.environ.get("RECIPIENTS_CONFIG", "{}")
        try:
            recipients_map = _json.loads(raw)
        except Exception:
            recipients_map = {}
        all_draft_recipients = list({
            email
            for rcp in recipients_map.values()
            for email in rcp.get("draft", [])
        })
        if all_draft_recipients:
            invalid_list = "\n".join(f"  - {f}" for f in invalid_files)
            mailer.send_error_email(
                recipients=all_draft_recipients,
                error_message=(
                    f"아래 파일은 파일명 규칙 불일치로 이번 이메일에서 제외되었습니다.\n\n"
                    f"파일명 규칙: {{국가}}_{{탭}}_{{섹션명}}_{{게임명}}.png\n"
                    f"예시: KR_Games_신규출시_StarBlast.png\n\n"
                    f"제외된 파일:\n{invalid_list}"
                ),
            )
    except Exception as e:
        print(f"  [경고 이메일 발송 실패] {e}")
