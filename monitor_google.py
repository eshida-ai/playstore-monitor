"""
Google Play Store 피쳐드 모니터링
- 비로그인(시크릿) → 계정A 로그인 순서로 세션 2개 순차 실행
- config.google_play.sections 에 등록된 섹션명 기준으로 탐지
- 최상단 섹션명 없는 배너 영역은 "배너" / "Banner" 로 분류
- 결과: [{"game", "game_id", "country", "section", "screenshot", "store":"google"}]
"""

import json
import os
import random
import re
import time
import traceback
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_DIR = Path(__file__).parent
SCREENSHOTS_DIR = BASE_DIR / "screenshots"

# Google Play 기기 프리셋 (모바일 뷰)
VIEWPORT = {"width": 412, "height": 915}

USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.6312.80 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-G998B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
]

MAX_RETRIES = 3
PLAY_URL = "https://play.google.com/store/games?device=phone&hl={hl}&gl={country}"

BANNER_LABEL = {"kr": "배너", "us": "Banner", "jp": "バナー"}


# ─────────────────────────────────────────────
# 유틸리티
# ─────────────────────────────────────────────
def rand_delay(min_ms: int = 500, max_ms: int = 1500):
    time.sleep(random.randint(min_ms, max_ms) / 1000)


def load_google_account() -> dict | None:
    """GOOGLE_ACCOUNT_A Secret: {"email":"...","password":"..."}"""
    raw = os.environ.get("GOOGLE_ACCOUNT_A", "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception as e:
        print(f"  [Google 계정] GOOGLE_ACCOUNT_A 파싱 실패: {e}")
        return None


def make_screenshot_path(country: str, section: str, game_id: str) -> str:
    """파일명: google_{country}_{safe_section}_{game_id}.png"""
    SCREENSHOTS_DIR.mkdir(exist_ok=True)
    safe_section = re.sub(r'[\\/:*?"<>|\s]', '_', section)
    return f"google_{country}_{safe_section}_{game_id}.png"


# ─────────────────────────────────────────────
# 배너 판별
# ─────────────────────────────────────────────
def _get_section_title_outside(page, container_el) -> str | None:
    """
    container_el 바깥(형제·부모 방향)에서 섹션 제목 탐색.
    container 내부 라벨은 무시.
    """
    return page.evaluate("""(el) => {
        // 1) el 이전 형제 요소 중 제목 탐색
        let prev = el.previousElementSibling;
        while (prev) {
            const tag = prev.tagName.toLowerCase();
            // 제목 태그 직접 확인
            if (['h2', 'h3', 'h4'].includes(tag)) {
                const t = prev.innerText.trim();
                if (t.length > 0 && t.length < 80) return t;
            }
            // 제목 태그를 포함하는 래퍼 (div/section 등)
            const heading = prev.querySelector('h2, h3, h4');
            if (heading) {
                const t = heading.innerText.trim();
                if (t.length > 0 && t.length < 80) return t;
            }
            prev = prev.previousElementSibling;
        }

        // 2) 부모의 이전 형제에서도 탐색 (1단계 위)
        const parent = el.parentElement;
        if (parent) {
            let pp = parent.previousElementSibling;
            while (pp) {
                const tag = pp.tagName.toLowerCase();
                if (['h2', 'h3', 'h4'].includes(tag)) {
                    const t = pp.innerText.trim();
                    if (t.length > 0 && t.length < 80) return t;
                }
                const heading = pp.querySelector('h2, h3, h4');
                if (heading) {
                    const t = heading.innerText.trim();
                    if (t.length > 0 && t.length < 80) return t;
                }
                pp = pp.previousElementSibling;
            }
        }
        return null;
    }""", container_el)


def get_section_label(page, container_el, country: str,
                      target_sections: list[str]) -> str | None:
    """
    섹션 레이블 결정:
    - 외부 제목이 target_sections 중 하나와 일치 → 해당 섹션명 반환
    - 외부 제목 없음(배너) → "배너"/"Banner" 반환
    - 외부 제목은 있지만 target_sections에 없음 → None (스킵)
    """
    title = _get_section_title_outside(page, container_el)

    if title is None:
        # 외부 제목 없음 → 배너로 분류
        return BANNER_LABEL.get(country, "Banner")

    # target_sections 중 하나와 일치하는지 확인 (부분 포함 허용)
    for ts in target_sections:
        if ts.strip() in title or title in ts.strip():
            return ts

    return None  # 관심 없는 섹션 → 스킵


# ─────────────────────────────────────────────
# 게임 추출 (컨테이너 내 앱 링크)
# ─────────────────────────────────────────────
def collect_apps_in_container(page, container_el) -> list[dict]:
    """
    컨테이너 내 앱 링크에서 이름과 패키지명 수집
    Google Play URL: /store/apps/details?id={package_name}
    """
    apps = []
    try:
        links = container_el.query_selector_all('a[href*="/store/apps/details"]')
        for link in links:
            pkg = None
            href = link.get_attribute("href") or ""
            m = re.search(r'[?&]id=([^&]+)', href)
            if m:
                pkg = m.group(1)

            # 앱 이름: aria-label 우선, 없으면 innerText 첫 줄
            name = ""
            aria = link.get_attribute("aria-label")
            if aria:
                name = aria.strip()
            else:
                inner = link.inner_text().strip()
                if inner:
                    name = inner.split("\n")[0].strip()

            if name or pkg:
                apps.append({"name": name, "package": pkg})
    except Exception:
        pass
    return apps


# ─────────────────────────────────────────────
# 게임 판별
# ─────────────────────────────────────────────
def is_my_game_google(app: dict, country: str, game: dict) -> bool:
    """
    1순위: 패키지명 완전일치
    2순위: 현지화명 완전일치
    """
    if app.get("package") and game.get("bundle_ids", {}).get("google"):
        if app["package"] == game["bundle_ids"]["google"]:
            return True

    localized = game["names"].get(country, game["default_name"])
    if localized and app.get("name", "").strip() == localized.strip():
        return True

    return False


# ─────────────────────────────────────────────
# 스크롤 (가로)
# ─────────────────────────────────────────────
def scroll_horizontal_fully(page, container_el):
    """컨테이너 내 가로 스크롤을 끝까지 이동"""
    try:
        page.evaluate("(el) => { el.scrollLeft = 0; }", container_el)
        while True:
            prev = page.evaluate("(el) => el.scrollLeft", container_el)
            page.evaluate("(el) => { el.scrollLeft += 350; }", container_el)
            rand_delay(300, 600)
            curr = page.evaluate("(el) => el.scrollLeft", container_el)
            if curr == prev:
                break
    except Exception:
        pass


# ─────────────────────────────────────────────
# 단일 페이지 스캔
# ─────────────────────────────────────────────
def scan_google_page(page, country: str, active_games: list,
                     target_sections: list[str]) -> list[dict]:
    """
    Google Play 게임 페이지 스캔.
    배너 + target_sections 에 해당하는 컨테이너만 탐색.
    중복 기준: (game_id, section) 동일하면 1건
    """
    results = []
    seen = set()  # (game_id, section)

    def process_containers():
        # 스크롤 가능한 가로 컨테이너 또는 주요 섹션 컨테이너 탐색
        containers = page.query_selector_all(
            '[class*="VfPpkd"], [class*="Ktdaqe"], '   # Play Store 전용 클래스
            'c-wiz[jsrenderer], '
            '[data-uitype], '
            'section, [role="region"]'
        )
        for container_el in containers:
            try:
                section_label = get_section_label(
                    page, container_el, country, target_sections
                )
                if section_label is None:
                    continue  # 관심 없는 섹션

                # 가로 스크롤 끝까지
                scroll_horizontal_fully(page, container_el)

                apps = collect_apps_in_container(page, container_el)
                for app in apps:
                    for game in active_games:
                        if "google" not in game.get("stores", []):
                            continue
                        if not is_my_game_google(app, country, game):
                            continue

                        key = (game["id"], section_label)
                        if key in seen:
                            continue
                        seen.add(key)

                        print(f"  ✓ [Google] 발견: {game['default_name']} / "
                              f"{country.upper()} / {section_label}")

                        # 스크린샷
                        filename = make_screenshot_path(
                            country, section_label, game["id"]
                        )
                        try:
                            container_el.screenshot(
                                path=str(SCREENSHOTS_DIR / filename)
                            )
                        except Exception as e:
                            print(f"    [스크린샷 실패] {filename}: {e}")
                            filename = None

                        results.append({
                            "game": game["default_name"],
                            "game_id": game["id"],
                            "country": country,
                            "section": section_label,
                            "screenshot": filename,
                            "store": "google",
                        })
            except Exception as e:
                print(f"    [컨테이너 처리 오류] {e}")

    # 페이지 세로 스크롤하며 반복
    prev_height = 0
    while True:
        process_containers()
        page.evaluate("window.scrollBy(0, 600)")
        rand_delay(500, 1000)
        curr_height = page.evaluate("document.body.scrollHeight")
        if curr_height == prev_height:
            break
        prev_height = curr_height

    process_containers()
    return results


# ─────────────────────────────────────────────
# 로그인 헬퍼
# ─────────────────────────────────────────────
def login_google(page, account: dict) -> bool:
    """Google 계정 로그인. 성공 여부 반환."""
    try:
        print(f"  [Google 로그인] {account['email']}")
        page.goto("https://accounts.google.com/signin", timeout=30000,
                  wait_until="domcontentloaded")
        rand_delay(1000, 2000)

        # 이메일 입력
        page.fill('input[type="email"]', account["email"])
        page.click('#identifierNext, [jsname="LgbsSe"]')
        rand_delay(1500, 2500)

        # 비밀번호 입력
        page.fill('input[type="password"]', account["password"])
        page.click('#passwordNext, [jsname="LgbsSe"]')
        rand_delay(2000, 3000)

        # 로그인 확인 (URL이 accounts.google.com을 벗어나면 성공)
        page.wait_for_url(re.compile(r'^(?!.*accounts\.google\.com/signin)'),
                          timeout=10000)
        print(f"  [Google 로그인] 성공")
        return True
    except Exception as e:
        print(f"  [Google 로그인 실패] {e}")
        return False


# ─────────────────────────────────────────────
# 단일 국가 스캔 (재시도 포함)
# ─────────────────────────────────────────────
def scan_country_with_retry(pw, country: str, hl: str,
                            active_games: list, target_sections: list[str],
                            logged_in_context=None) -> list[dict]:
    """단일 국가 URL 탐색. 비로그인/로그인 컨텍스트 공용."""
    url = PLAY_URL.format(hl=hl, country=country.upper())

    for attempt in range(1, MAX_RETRIES + 1):
        page = None
        try:
            if logged_in_context:
                page = logged_in_context.new_page()
            else:
                # 시크릿(비로그인) 페이지
                page = pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox"],
                ).new_context(
                    user_agent=random.choice(USER_AGENTS),
                    viewport=VIEWPORT,
                ).new_page()

            page.set_extra_http_headers({"Accept-Language": f"{hl},{hl[:2]};q=0.9,en;q=0.8"})
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            rand_delay(1500, 2500)

            results = scan_google_page(page, country, active_games, target_sections)
            page.close()
            return results

        except PlaywrightTimeoutError as e:
            print(f"  [타임아웃 {attempt}/{MAX_RETRIES}] {url}: {e}")
            if page:
                try: page.close()
                except Exception: pass
            if attempt == MAX_RETRIES:
                return []
            rand_delay(3000, 5000)

        except Exception as e:
            print(f"  [오류 {attempt}/{MAX_RETRIES}] {url}: {e}")
            traceback.print_exc()
            if page:
                try: page.close()
                except Exception: pass
            if attempt == MAX_RETRIES:
                return []
            rand_delay(3000, 5000)

    return []


# ─────────────────────────────────────────────
# 메인 진입점
# ─────────────────────────────────────────────
def run_google_monitoring(config: dict, active_games: list) -> dict[str, list]:
    """
    Google Play 모니터링 실행.
    반환: {game_id: [result, ...]}
    """
    gp_config = config.get("google_play", {})
    countries = gp_config.get("countries", [])
    sections_map = gp_config.get("sections", {})
    locale_map = gp_config.get("locale_map", {})

    google_games = [g for g in active_games if "google" in g.get("stores", [])]
    if not google_games:
        print("  [Google Play] 모니터링 대상 게임 없음 (stores에 'google' 없음)")
        return {}

    game_results: dict[str, list] = {g["id"]: [] for g in google_games}
    account = load_google_account()

    with sync_playwright() as pw:
        # ── 세션 1: 비로그인 (시크릿) ──────────────────
        print("\n=== Google Play 세션 1: 비로그인 ===")
        for country in countries:
            hl = locale_map.get(country, "en")
            target_sections = sections_map.get(country, [])
            print(f"  탐색: {country.upper()} (hl={hl})")

            found = scan_country_with_retry(
                pw, country, hl, google_games, target_sections,
                logged_in_context=None,
            )
            for r in found:
                gid = r["game_id"]
                if gid in game_results:
                    # 중복 제거: (country, section, game_id) 기준
                    existing_keys = {
                        (x["country"], x["section"], x["game_id"])
                        for x in game_results[gid]
                    }
                    if (r["country"], r["section"], r["game_id"]) not in existing_keys:
                        game_results[gid].append(r)

            rand_delay(1000, 2000)

        # ── 세션 2: 계정A 로그인 ───────────────────────
        if account:
            print("\n=== Google Play 세션 2: 계정A 로그인 ===")
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            context = browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport=VIEWPORT,
            )
            login_page = context.new_page()
            login_ok = login_google(login_page, account)
            login_page.close()

            if login_ok:
                for country in countries:
                    hl = locale_map.get(country, "en")
                    target_sections = sections_map.get(country, [])
                    print(f"  탐색: {country.upper()} (hl={hl}, 로그인)")

                    found = scan_country_with_retry(
                        pw, country, hl, google_games, target_sections,
                        logged_in_context=context,
                    )
                    for r in found:
                        gid = r["game_id"]
                        if gid in game_results:
                            existing_keys = {
                                (x["country"], x["section"], x["game_id"])
                                for x in game_results[gid]
                            }
                            if (r["country"], r["section"], r["game_id"]) not in existing_keys:
                                game_results[gid].append(r)

                    rand_delay(1000, 2000)
            else:
                print("  [Google Play] 로그인 실패 — 세션2 스킵")

            context.close()
            browser.close()
        else:
            print("\n=== Google Play 세션 2: GOOGLE_ACCOUNT_A 없음 — 스킵 ===")

    total = sum(len(v) for v in game_results.values())
    print(f"\n[Google Play] 총 {total}건 발견")
    return game_results
