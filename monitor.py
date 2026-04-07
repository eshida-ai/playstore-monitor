"""
앱스토어 피쳐드 모니터링 — 메인 실행 모듈
매일 KST 09:00 (UTC 00:00) GitHub Actions에서 자동 실행
"""
import json
import os
import random
import re
import sys
import time
import traceback
from datetime import date, datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from drive_sync import collect_manual_images
from mailer import Mailer

# ─────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
SCREENSHOTS_DIR = BASE_DIR / "screenshots"
LOGS_DIR = BASE_DIR / "logs"
RUN_LOG_PATH = LOGS_DIR / "run_log.json"

APPLE_URLS = {
    "today": "https://apps.apple.com/{country}/iphone/today",
    "games": "https://apps.apple.com/{country}/charts/iphone/top-free-games",
}

USER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

MAX_RETRIES = 3


# ─────────────────────────────────────────────
# 유틸리티
# ─────────────────────────────────────────────
def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_run_log(log: dict):
    LOGS_DIR.mkdir(exist_ok=True)
    existing = {}
    if RUN_LOG_PATH.exists():
        with open(RUN_LOG_PATH, encoding="utf-8") as f:
            existing = json.load(f)
    existing.update(log)
    with open(RUN_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


def get_active_games(config: dict, today: date) -> list:
    """오늘 날짜가 active_from ~ active_until 범위에 있는 게임만 반환"""
    active = []
    for game in config["games"]:
        active_from = date.fromisoformat(game["active_from"])
        active_until = date.fromisoformat(game["active_until"])
        if active_from <= today <= active_until:
            active.append(game)
    return active


def rand_delay(min_ms: int = 500, max_ms: int = 1500):
    time.sleep(random.randint(min_ms, max_ms) / 1000)


# ─────────────────────────────────────────────
# 게임 판별
# ─────────────────────────────────────────────
def is_my_game(app_name: str, bundle_id: str | None, country: str, game: dict, store: str) -> bool:
    """
    1순위: 번들 ID 완전 일치
    2순위: 현지화명 완전 일치
    부분 일치 사용 안 함 → 오탐 방지
    """
    if bundle_id and store in game.get("bundle_ids", {}):
        if bundle_id == game["bundle_ids"][store]:
            return True

    localized_name = game["names"].get(country, game["default_name"])
    if app_name.strip() == localized_name.strip():
        return True

    return False


# ─────────────────────────────────────────────
# 스크롤 헬퍼
# ─────────────────────────────────────────────
def scroll_page_to_bottom(page):
    """전체 페이지 세로 스크롤 (새 콘텐츠 없을 때까지)"""
    prev_height = 0
    while True:
        page.evaluate("window.scrollBy(0, 600)")
        rand_delay(500, 1000)
        curr_height = page.evaluate("document.body.scrollHeight")
        if curr_height == prev_height:
            break
        prev_height = curr_height


def scroll_element_horizontal(page, element):
    """요소 내 가로 스크롤 끝까지"""
    try:
        page.evaluate("""(el) => {
            el.scrollLeft = 0;
        }""", element)
        while True:
            prev_scroll = page.evaluate("(el) => el.scrollLeft", element)
            page.evaluate("""(el) => {
                el.scrollLeft += 400;
            }""", element)
            rand_delay(300, 600)
            curr_scroll = page.evaluate("(el) => el.scrollLeft", element)
            if curr_scroll == prev_scroll:
                break
    except Exception:
        pass


def click_next_buttons(page, section_el):
    """섹션 내 Next 버튼 끝까지 클릭"""
    while True:
        try:
            btn = section_el.query_selector(
                '[aria-label="Next"], [aria-label="다음"], '
                '[class*="next"], [class*="arrow-right"], '
                'button[class*="forward"]'
            )
            if not btn:
                break
            if not btn.is_enabled() or not btn.is_visible():
                break
            btn.click()
            rand_delay(400, 800)
        except Exception:
            break


# ─────────────────────────────────────────────
# 앱 이름 수집
# ─────────────────────────────────────────────
def collect_app_names_in_section(section_el) -> list[dict]:
    """섹션 내 앱 이름과 번들ID 수집"""
    apps = []
    try:
        # 앱 링크 요소에서 이름 수집 (App Store 웹 구조 대응)
        app_links = section_el.query_selector_all('a[href*="/app/"]')
        for link in app_links:
            name = ""
            bundle_id = None

            # 앱 이름 추출 — aria-label 또는 텍스트 노드
            aria = link.get_attribute("aria-label")
            if aria:
                name = aria.strip()
            else:
                # 텍스트 자식 중 첫 번째 의미 있는 것
                inner = link.inner_text().strip()
                if inner:
                    name = inner.split("\n")[0].strip()

            # 번들 ID 추출 — href에서 파싱 또는 data 속성
            href = link.get_attribute("href") or ""
            bundle_match = re.search(r'/id(\d+)', href)
            if bundle_match:
                bundle_id = bundle_match.group(0)  # Apple은 숫자 ID, 번들ID와 다름
            bundle_attr = link.get_attribute("data-bundle-id")
            if bundle_attr:
                bundle_id = bundle_attr

            if name:
                apps.append({"name": name, "bundle_id": bundle_id})
    except Exception:
        pass
    return apps


def get_section_name(section_el) -> str:
    """섹션 제목 추출"""
    try:
        # 다양한 제목 셀렉터 시도
        for sel in ['h2', 'h3', '[class*="title"]', '[class*="header"]', 'header']:
            el = section_el.query_selector(sel)
            if el:
                text = el.inner_text().strip()
                if text:
                    return text[:50]  # 최대 50자
    except Exception:
        pass
    return "unknown_section"


# ─────────────────────────────────────────────
# 스크린샷
# ─────────────────────────────────────────────
def capture_section(section_el, country: str, tab: str, section_name: str, game_id: str) -> str | None:
    """내 게임이 발견된 섹션 전체 캡처. 파일명 반환"""
    SCREENSHOTS_DIR.mkdir(exist_ok=True)
    safe_section = re.sub(r'[\\/:*?"<>|]', '_', section_name)
    filename = f"{country}_{tab}_{safe_section}_{game_id}.png"
    path = SCREENSHOTS_DIR / filename
    try:
        section_el.screenshot(path=str(path))
        return filename
    except Exception as e:
        print(f"    [스크린샷 실패] {filename}: {e}")
        return None


# ─────────────────────────────────────────────
# 단일 URL 탐색
# ─────────────────────────────────────────────
def scan_page(page, url: str, country: str, tab: str, active_games: list, store: str) -> list[dict]:
    """
    단일 URL 탐색 → 발견된 피쳐드 항목 리스트 반환
    [{"game": ..., "country": ..., "tab": ..., "section": ..., "screenshot": ...}]
    """
    results = []
    processed_sections = set()

    def process_sections():
        try:
            sections = page.query_selector_all('section, [class*="section"], [role="region"]')
            for section_el in sections:
                section_id = section_el.get_attribute("id") or ""
                section_class = section_el.get_attribute("class") or ""
                key = f"{section_id}_{section_class[:40]}"

                if key in processed_sections:
                    continue
                processed_sections.add(key)

                # 섹션 내 가로/세로 스크롤 처리
                try:
                    scrollable_els = section_el.query_selector_all(
                        '[style*="overflow-x: scroll"], [style*="overflow-x: auto"], '
                        '[style*="overflow-y: scroll"], [style*="overflow-y: auto"], '
                        '[class*="scroll"], [class*="carousel"]'
                    )
                    for el in scrollable_els:
                        scroll_element_horizontal(page, el)
                except Exception:
                    pass

                # Next 버튼 클릭
                click_next_buttons(page, section_el)

                # 앱 이름 수집 및 판별
                apps = collect_app_names_in_section(section_el)
                section_name = get_section_name(section_el)

                for app in apps:
                    for game in active_games:
                        if store not in game.get("stores", []):
                            continue
                        if is_my_game(app["name"], app["bundle_id"], country, game, store):
                            # 이미 이 섹션에서 이 게임을 찾은 경우 스킵
                            dup_key = f"{game['id']}_{section_name}"
                            if dup_key in processed_sections:
                                continue
                            processed_sections.add(dup_key)

                            print(f"  ✓ 발견: {game['default_name']} / {country.upper()} / {tab} / {section_name}")
                            screenshot = capture_section(section_el, country, tab, section_name, game["id"])
                            results.append({
                                "game": game["default_name"],
                                "game_id": game["id"],
                                "country": country,
                                "tab": tab,
                                "section": section_name,
                                "screenshot": screenshot,
                            })
        except Exception as e:
            print(f"    [섹션 처리 오류] {e}")

    # 페이지 세로 스크롤하며 섹션 반복 처리
    prev_height = 0
    while True:
        process_sections()
        page.evaluate("window.scrollBy(0, 600)")
        rand_delay(500, 1000)
        curr_height = page.evaluate("document.body.scrollHeight")
        if curr_height == prev_height:
            break
        prev_height = curr_height

    # 마지막 한 번 더
    process_sections()
    return results


# ─────────────────────────────────────────────
# 국가·탭별 탐색 (재시도 포함)
# ─────────────────────────────────────────────
def scan_with_retry(browser_context, url: str, country: str, tab: str,
                    active_games: list, store: str) -> list[dict]:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            page = browser_context.new_page()
            page.set_extra_http_headers({
                "Accept-Language": f"{country},en;q=0.9",
            })
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            rand_delay(1000, 2000)
            results = scan_page(page, url, country, tab, active_games, store)
            page.close()
            return results
        except PlaywrightTimeoutError as e:
            print(f"  [타임아웃 {attempt}/{MAX_RETRIES}] {url}: {e}")
            try:
                page.close()
            except Exception:
                pass
            if attempt == MAX_RETRIES:
                return []
            rand_delay(2000, 4000)
        except Exception as e:
            print(f"  [오류 {attempt}/{MAX_RETRIES}] {url}: {e}")
            try:
                page.close()
            except Exception:
                pass
            if attempt == MAX_RETRIES:
                return []
            rand_delay(2000, 4000)
    return []


# ─────────────────────────────────────────────
# 수신자 로드 (GitHub Secrets)
# ─────────────────────────────────────────────
def load_recipients_from_env() -> dict:
    """RECIPIENTS_CONFIG 환경변수에서 수신자 목록 파싱
    형식: {"game_id": {"draft": [...], "final": [...]}}
    """
    raw = os.environ.get("RECIPIENTS_CONFIG", "{}")
    try:
        return json.loads(raw)
    except Exception as e:
        print(f"[경고] RECIPIENTS_CONFIG 파싱 실패: {e}")
        return {}


# ─────────────────────────────────────────────
# 재발송 모드 (수정 후 초안 재발송)
# ─────────────────────────────────────────────
def resend_draft(config: dict, mailer: "Mailer"):
    """
    RESEND_MODE 환경변수: "date|game_id|issue_number|issue_url"
    override 파일: logs/override_{date}_{safe_game_id}.json
    """
    resend_mode = os.environ.get("RESEND_MODE", "")
    if not resend_mode:
        return False

    parts = resend_mode.split("|", 3)
    if len(parts) < 3:
        print(f"[재발송] RESEND_MODE 형식 오류: {resend_mode}")
        return False

    date_str, game_id, issue_number_str = parts[0], parts[1], parts[2]
    issue_url = parts[3] if len(parts) > 3 else ""
    issue_number = int(issue_number_str) if issue_number_str.isdigit() else None

    print(f"=== 재발송 모드: {game_id} / {date_str} ===")

    # override 파일 로드
    safe_id = re.sub(r'[^a-zA-Z0-9]', '_', game_id)
    override_path = LOGS_DIR / f"override_{date_str}_{safe_id}.json"
    if not override_path.exists():
        print(f"[재발송] override 파일 없음: {override_path}")
        return False

    with open(override_path, encoding="utf-8") as f:
        override = json.load(f)

    found_list = override.get("found_list", [])

    # 게임 설정 찾기
    game = next((g for g in config["games"] if g["id"] == game_id), None)
    if not game:
        print(f"[재발송] 게임 없음: {game_id}")
        return False

    # 수신자 주입
    recipients_map = load_recipients_from_env()
    game["recipients"] = recipients_map.get(game["id"], {"draft": [], "final": []})

    mailer.send_draft(
        game=game,
        found_list=found_list,
        date_str=date_str,
        issue_url=issue_url,
        issue_number=issue_number,
    )
    print(f"[재발송] 초안 재발송 완료")
    return True


# ─────────────────────────────────────────────
# 메인 실행
# ─────────────────────────────────────────────
def main():
    today = date.today()
    today_str = today.isoformat()
    run_at = datetime.utcnow().isoformat() + "Z"
    print(f"=== 앱스토어 피쳐드 모니터링 시작: {today_str} ===")

    config = load_config()
    gmail_pw = os.environ.get("GMAIL_APP_PASSWORD", "")
    sender = config["email"]["sender"]
    mailer = Mailer(sender=sender, app_password=gmail_pw, config=config)

    # 재발송 모드 체크
    if resend_draft(config, mailer):
        return

    active_games = get_active_games(config, today)

    # 수신자를 환경변수에서 주입
    recipients_map = load_recipients_from_env()
    for game in active_games:
        game["recipients"] = recipients_map.get(
            game["id"], {"draft": [], "final": []}
        )

    if not active_games:
        print("오늘 활성 게임 없음. 종료.")
        save_run_log({today_str: {"status": "no_active_games", "run_at": run_at, "games": {}}})
        return

    print(f"활성 게임: {[g['default_name'] for g in active_games]}")

    # Google Drive 수동 이미지 수집
    manual_images = []
    try:
        manual_images = collect_manual_images(today_str.replace("-", ""))
        print(f"Drive 수동 이미지: {len(manual_images)}개")
    except Exception as e:
        print(f"[Drive 수집 실패] {e}")

    # 게임별 결과 집계
    game_results: dict[str, list] = {g["id"]: [] for g in active_games}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        context = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 390, "height": 844},  # iPhone 14 해상도
            locale="ko-KR",
        )

        for game in active_games:
            print(f"\n--- {game['default_name']} 탐색 시작 ---")
            for store in game.get("stores", ["apple"]):
                if store != "apple":
                    print(f"  [{store}] 향후 확장 예정 — 스킵")
                    continue

                for country in config["countries"]:
                    for tab in config["tabs"]:
                        url_template = APPLE_URLS.get(tab)
                        if not url_template:
                            continue
                        url = url_template.format(country=country)
                        print(f"  탐색: {country.upper()} / {tab} → {url}")

                        found = scan_with_retry(context, url, country, tab, [game], store)
                        game_results[game["id"]].extend(found)
                        rand_delay(500, 1500)

        context.close()
        browser.close()

    # 수동 이미지를 게임별로 분류하여 results에 병합
    for img in manual_images:
        game_id = img.get("game_id")
        if game_id and game_id in game_results:
            game_results[game_id].append(img)

    # 이메일 발송 및 로그 기록
    log_games = {}
    github_owner = config.get("github", {}).get("owner", "")
    github_repo = config.get("github", {}).get("repo", "")

    for game in active_games:
        gid = game["id"]
        found_list = game_results[gid]
        print(f"\n{game['default_name']}: {len(found_list)}개 노출 발견")

        try:
            issue_number, issue_url = mailer.create_github_issue(
                game=game,
                found_list=found_list,
                date_str=today_str,
                owner=github_owner,
                repo=github_repo,
            )
            draft_sent_at = mailer.send_draft(
                game=game,
                found_list=found_list,
                date_str=today_str,
                issue_url=issue_url,
                issue_number=issue_number,
            )
            log_games[gid] = {
                "found": [
                    {"country": r["country"], "tab": r["tab"], "section": r["section"]}
                    for r in found_list
                ],
                "draft_sent_at": draft_sent_at,
                "github_issue": issue_number,
                "approved_by": None,
                "approved_at": None,
                "final_sent_at": None,
            }
            print(f"  초안 발송 완료: {draft_sent_at}")
        except Exception as e:
            print(f"  [이메일 발송 실패] {game['default_name']}: {e}")
            traceback.print_exc()
            log_games[gid] = {"status": "email_error", "error": str(e)}

    save_run_log({
        today_str: {
            "status": "success",
            "run_at": run_at,
            "games": log_games,
        }
    })
    print(f"\n=== 완료: {today_str} ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # 치명적 오류 → 실패 이메일 발송 시도
        print(f"[치명적 오류] {e}")
        traceback.print_exc()
        try:
            config = load_config()
            mailer = Mailer(
                sender=config["email"]["sender"],
                app_password=os.environ.get("GMAIL_APP_PASSWORD", ""),
                config=config,
            )
            all_recipients = set()
            for g in config["games"]:
                all_recipients.update(g["recipients"].get("draft", []))
            mailer.send_error_email(
                recipients=list(all_recipients),
                error_message=str(e),
                traceback_str=traceback.format_exc(),
            )
        except Exception as mail_err:
            print(f"[실패 이메일 발송도 실패] {mail_err}")
        sys.exit(1)
