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
from monitor_google import run_google_monitoring

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
MAX_SCROLL_STEPS = 20  # 무한 lazy-load 방지

# #fix6: 국가별 Apple Store 로케일 매핑
APPLE_LOCALE_MAP = {
    "kr": "ko-KR",
    "us": "en-US",
    "jp": "ja-JP",
    "tw": "zh-TW",
    "th": "th-TH",
    "gb": "en-GB",
    "de": "de-DE",
    "fr": "fr-FR",
    "ca": "en-CA",
    "au": "en-AU",
    "sg": "en-SG",
    "mx": "es-MX",
    "br": "pt-BR",
    "ru": "ru-RU",
    "in": "en-IN",
    "id": "id-ID",
    "sa": "ar-SA",
    "ae": "ar-AE",
    "cn": "zh-CN",
}


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
        try:
            with open(RUN_LOG_PATH, encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = {}
    # 중첩 병합: 동일 날짜 키의 games 딕셔너리는 덮어쓰지 않고 병합
    for date_key, date_val in log.items():
        if date_key in existing:
            if "games" in date_val and "games" in existing[date_key]:
                existing[date_key]["games"].update(date_val["games"])
            else:
                existing[date_key].update(date_val)
        else:
            existing[date_key] = date_val
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
def scroll_element_horizontal(page, element):
    """요소 내 가로 스크롤 끝까지"""
    try:
        page.evaluate("""(el) => {
            el.scrollLeft = 0;
        }""", element)
        steps = 0
        while steps < MAX_SCROLL_STEPS:
            prev_scroll = page.evaluate("(el) => el.scrollLeft", element)
            page.evaluate("""(el) => {
                el.scrollLeft += 400;
            }""", element)
            rand_delay(150, 300)  # 가로 스크롤 딜레이 단축
            curr_scroll = page.evaluate("(el) => el.scrollLeft", element)
            if curr_scroll == prev_scroll:
                break
            steps += 1
    except Exception:
        pass


def click_next_buttons(page, section_el):
    """섹션 내 Next 버튼 끝까지 클릭 (무한루프 방지: MAX_SCROLL_STEPS 제한)"""
    steps = 0
    while steps < MAX_SCROLL_STEPS:
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
            steps += 1
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

            # 앱스토어 숫자 ID 추출 — href의 /id{숫자} 부분
            # config의 bundle_ids.apple 에는 이 숫자 ID를 저장해야 함
            # 예: https://apps.apple.com/kr/app/game-name/id1234567890 → "1234567890"
            href = link.get_attribute("href") or ""
            bundle_match = re.search(r'/id(\d+)', href)
            if bundle_match:
                bundle_id = bundle_match.group(1)
            # data-bundle-id 속성이 있으면 우선 사용 (일부 구간에서 역DNS 형식 제공)
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
    processed_sections = set()   # 섹션 중복 처리 방지
    seen_game_sections = set()   # (game_id, section_name) 중복 결과 방지

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
                        if store not in game.get("stores", {}):
                            continue
                        if is_my_game(app["name"], app["bundle_id"], country, game, store):
                            # 이미 이 섹션에서 이 게임을 찾은 경우 스킵
                            dup_key = (game["id"], section_name)
                            if dup_key in seen_game_sections:
                                continue
                            seen_game_sections.add(dup_key)

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

    # #fix5: MAX_SCROLL_STEPS 제한으로 무한루프 방지
    prev_height = 0
    scroll_count = 0
    while scroll_count < MAX_SCROLL_STEPS:
        process_sections()
        page.evaluate("window.scrollBy(0, 600)")
        rand_delay(500, 1000)
        curr_height = page.evaluate("document.body.scrollHeight")
        if curr_height == prev_height:
            break
        prev_height = curr_height
        scroll_count += 1

    # 마지막 한 번 더
    process_sections()
    return results


# ─────────────────────────────────────────────
# 국가·탭별 탐색 (재시도 포함)
# ─────────────────────────────────────────────
def scan_with_retry(browser_context, url: str, country: str, tab: str,
                    active_games: list, store: str) -> list[dict]:
    for attempt in range(1, MAX_RETRIES + 1):
        # #fix9: try/finally로 페이지 리소스 누수 차단
        page = browser_context.new_page()
        try:
            page.set_extra_http_headers({
                "Accept-Language": f"{country},en;q=0.9",
            })
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            rand_delay(1000, 2000)
            results = scan_page(page, url, country, tab, active_games, store)
            return results
        except PlaywrightTimeoutError as e:
            print(f"  [타임아웃 {attempt}/{MAX_RETRIES}] {url}: {e}")
            if attempt == MAX_RETRIES:
                return []
            rand_delay(2000, 4000)
        except Exception as e:
            print(f"  [오류 {attempt}/{MAX_RETRIES}] {url}: {e}")
            if attempt == MAX_RETRIES:
                return []
            rand_delay(2000, 4000)
        finally:
            try:
                page.close()
            except Exception:
                pass
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
    print(f"=== 피쳐드 모니터링 시작 (Apple + Google Play): {today_str} ===")

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

    # Apple 게임이 있을 때만 Drive 수동 이미지 수집
    manual_images = []
    apple_games_active = [g for g in active_games if "apple" in g.get("stores", {})]
    if apple_games_active:
        try:
            manual_images = collect_manual_images(today_str.replace("-", ""))
            print(f"Drive 수동 이미지: {len(manual_images)}개")
        except Exception as e:
            print(f"[Drive 수집 실패] {e}")

    # ── Apple App Store 스캔 ────────────────────
    apple_results: dict[str, list] = {g["id"]: [] for g in active_games}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )

        for game in active_games:
            game_stores = game.get("stores", {})
            if "apple" not in game_stores:
                continue
            apple_cfg = game_stores["apple"]
            apple_countries = apple_cfg.get("countries", [])
            apple_tabs = apple_cfg.get("tabs", [])
            if not apple_countries or not apple_tabs:
                print(f"\n--- {game['default_name']} Apple 탐색 스킵 (국가 또는 탭 미설정) ---")
                continue
            print(f"\n--- {game['default_name']} Apple 탐색 ---")
            for country in apple_countries:
                # 국가별 로케일로 컨텍스트 생성
                locale = APPLE_LOCALE_MAP.get(country, "en-US")
                context = browser.new_context(
                    user_agent=random.choice(USER_AGENTS),
                    viewport={"width": 390, "height": 844},
                    locale=locale,
                )
                try:
                    for tab in apple_tabs:
                        url_template = APPLE_URLS.get(tab)
                        if not url_template:
                            continue
                        url = url_template.format(country=country)
                        print(f"  탐색: {country.upper()} / {tab} → {url}")

                        found = scan_with_retry(context, url, country, tab, [game], "apple")
                        for r in found:
                            r["store"] = "apple"
                        apple_results[game["id"]].extend(found)
                        rand_delay(500, 1500)
                finally:
                    context.close()

        browser.close()

    # ── Google Play 스캔 ────────────────────────
    print("\n=== Google Play 모니터링 시작 ===")
    try:
        google_results = run_google_monitoring(config, active_games)
    except Exception as e:
        print(f"[Google Play 스캔 실패] {e}")
        traceback.print_exc()
        google_results = {}

    # ── 수동 이미지 병합 (Apple 결과에 추가) ────
    for img in manual_images:
        game_id = img.get("game_id")
        if game_id and game_id in apple_results:
            img.setdefault("store", "apple")
            apple_results[game_id].append(img)

    # ── 이메일 발송 및 로그 기록 ────────────────
    log_games = {}
    github_owner = config.get("github", {}).get("owner", "")
    github_repo = config.get("github", {}).get("repo", "")

    for game in active_games:
        gid = game["id"]
        a_found = apple_results.get(gid, [])
        g_found = google_results.get(gid, [])
        found_list = a_found + g_found  # 이메일용 통합 목록

        print(f"\n{game['default_name']}: Apple {len(a_found)}건 / Google {len(g_found)}건")

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
                "apple_found": [
                    {"country": r["country"], "tab": r.get("tab", ""), "section": r["section"], "store": "apple"}
                    for r in a_found
                ],
                "google_found": [
                    {"country": r["country"], "section": r["section"], "store": "google"}
                    for r in g_found
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
            # #fix3: config["games"]에는 recipients 키 없음 → env에서 직접 로드
            recipients_map = load_recipients_from_env()
            all_recipients = set()
            for g in config["games"]:
                rcp = recipients_map.get(g["id"], {"draft": []})
                all_recipients.update(rcp.get("draft", []))
            mailer.send_error_email(
                recipients=list(all_recipients),
                error_message=str(e),
                traceback_str=traceback.format_exc(),
            )
        except Exception as mail_err:
            print(f"[실패 이메일 발송도 실패] {mail_err}")
        sys.exit(1)
