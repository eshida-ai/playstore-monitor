"""
Google Play Store 피쳐드 모니터링
- 계정A → 계정B 순차 실행 (A가 발견한 국가는 B에서 스킵)
- config.google_play.sections 에 등록된 섹션 타입 기준으로 탐지
  (타입 예: events, new_games, preregistration, be_first)
- 게임별 국가/섹션 타입 설정 지원
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
MAX_SCROLL_STEPS = 20  # 무한 lazy-load 방지
PLAY_URL = "https://play.google.com/store/games?device=phone&hl={hl}&gl={country}"

# #fix4: tw/th 배너 라벨 추가
BANNER_LABEL = {
    "kr": "배너",
    "us": "Banner",
    "jp": "バナー",
    "tw": "橫幅廣告",
    "th": "แบนเนอร์",
}

# 세로 스크롤 중단 키워드 ("show more" 류 버튼 감지)
SHOW_MORE_KEYWORDS = [
    "더보기", "더 보기", "show more", "see more", "もっと見る",
    "查看更多", "แสดงเพิ่มเติม", "모두 보기", "see all games",
]


# ─────────────────────────────────────────────
# 유틸리티
# ─────────────────────────────────────────────
def rand_delay(min_ms: int = 500, max_ms: int = 1500):
    time.sleep(random.randint(min_ms, max_ms) / 1000)


def make_screenshot_path(country: str, section: str, game_id: str) -> str:
    """파일명: google_{country}_{safe_section}_{game_id}.png"""
    SCREENSHOTS_DIR.mkdir(exist_ok=True)
    safe_section = re.sub(r'[\\/:*?"<>|\s]', '_', section)
    return f"google_{country}_{safe_section}_{game_id}.png"


def get_target_sections_for_game_country(sections_map: dict, game: dict, country: str) -> list[str]:
    """
    게임의 섹션 타입 키를 기반으로 해당 국가의 현지화 섹션명 목록 반환.
    game.stores.google.sections 미설정 시 sections_map 전체 타입 사용.
    """
    game_section_types = game.get("stores", {}).get("google", {}).get("sections")
    if game_section_types is None:
        section_types = list(sections_map.keys())
    else:
        section_types = game_section_types
    return [sections_map[t][country] for t in section_types
            if sections_map.get(t, {}).get(country)]


# ─────────────────────────────────────────────
# show more 감지 (세로 스크롤 중단 트리거)
# ─────────────────────────────────────────────
def _is_show_more_visible(page) -> bool:
    """뷰포트 내에 'show more' 류 버튼이 보이면 True → 세로 스크롤 중단"""
    try:
        return page.evaluate("""(keywords) => {
            for (const el of document.querySelectorAll('a, button, [role="button"]')) {
                const text = (el.innerText || '').trim().toLowerCase();
                if (!text || text.length > 30) continue;
                if (keywords.some(k => text.includes(k))) {
                    const rect = el.getBoundingClientRect();
                    if (rect.top >= 0 && rect.top < window.innerHeight && rect.width > 0) {
                        return true;
                    }
                }
            }
            return false;
        }""", [k.lower() for k in SHOW_MORE_KEYWORDS])
    except Exception:
        return False


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
    - 외부 제목 없음(배너) → 국가별 배너 라벨 반환
    - 외부 제목은 있지만 target_sections에 없음 → None (스킵)
    """
    title = _get_section_title_outside(page, container_el)

    if title is None:
        # 외부 제목 없음 → 배너로 분류
        return BANNER_LABEL.get(country, "Banner")

    # #fix20: target→title 방향만 허용 (title in ts 방향은 오탐 원인)
    for ts in target_sections:
        if ts.strip() in title:
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
        steps = 0
        while steps < MAX_SCROLL_STEPS:
            prev = page.evaluate("(el) => el.scrollLeft", container_el)
            page.evaluate("(el) => { el.scrollLeft += 350; }", container_el)
            rand_delay(150, 300)  # 가로 스크롤은 짧게
            curr = page.evaluate("(el) => el.scrollLeft", container_el)
            if curr == prev:
                break
            steps += 1
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
    세로 스크롤 중 'show more' 버튼 감지 시 즉시 중단.
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

                # 가로 스크롤 끝까지 (관심 섹션 내부)
                scroll_horizontal_fully(page, container_el)

                apps = collect_apps_in_container(page, container_el)
                for app in apps:
                    for game in active_games:
                        if "google" not in game.get("stores", {}):
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

    # 세로 스크롤 루프: show more 감지 시 중단
    prev_height = 0
    scroll_count = 0
    while scroll_count < MAX_SCROLL_STEPS:
        process_containers()
        # show more 버튼 감지 → 스크롤 중단 (차트/랭킹 섹션 전 경계)
        if _is_show_more_visible(page):
            print(f"  [Google] 'show more' 버튼 감지 — 세로 스크롤 중단")
            break
        page.evaluate("window.scrollBy(0, 600)")
        rand_delay(500, 1000)
        curr_height = page.evaluate("document.body.scrollHeight")
        if curr_height == prev_height:
            break
        prev_height = curr_height
        scroll_count += 1

    process_containers()
    return results


# ─────────────────────────────────────────────
# 단일 국가 스캔 (재시도 포함)
# ─────────────────────────────────────────────
def scan_country_with_retry(context, country: str, hl: str,
                            active_games: list, target_sections: list[str]) -> list[dict]:
    """
    단일 국가 URL 탐색. context는 항상 외부에서 전달 (좀비 브라우저 방지).
    try/finally로 페이지 리소스 누수 차단.
    """
    url = PLAY_URL.format(hl=hl, country=country.upper())

    for attempt in range(1, MAX_RETRIES + 1):
        page = context.new_page()
        try:
            page.set_extra_http_headers({"Accept-Language": f"{hl},{hl[:2]};q=0.9,en;q=0.8"})
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            rand_delay(1500, 2500)
            results = scan_google_page(page, country, active_games, target_sections)
            return results

        except PlaywrightTimeoutError as e:
            print(f"  [타임아웃 {attempt}/{MAX_RETRIES}] {url}: {e}")
            if attempt == MAX_RETRIES:
                return []
            rand_delay(3000, 5000)

        except Exception as e:
            print(f"  [오류 {attempt}/{MAX_RETRIES}] {url}: {e}")
            traceback.print_exc()
            if attempt == MAX_RETRIES:
                return []
            rand_delay(3000, 5000)

        finally:
            try:
                page.close()
            except Exception:
                pass

    return []


# ─────────────────────────────────────────────
# 메인 진입점
# ─────────────────────────────────────────────
def run_google_monitoring(config: dict, active_games: list) -> dict[str, list]:
    """
    Google Play 모니터링 실행 (비로그인).
    반환: {game_id: [result, ...]}
    """
    gp_config = config.get("google_play", {})
    sections_map = gp_config.get("sections", {})
    locale_map = gp_config.get("locale_map", {})

    google_games = [g for g in active_games if "google" in g.get("stores", {})]
    if not google_games:
        print("  [Google Play] 모니터링 대상 게임 없음 (stores에 'google' 없음)")
        return {}

    game_results: dict[str, list] = {g["id"]: [] for g in google_games}
    seen_keys: dict[str, set] = {g["id"]: set() for g in google_games}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        context = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport=VIEWPORT,
        )
        try:
            for game in google_games:
                gid = game["id"]
                game_countries = game.get("stores", {}).get("google", {}).get("countries", [])
                if not game_countries:
                    continue

                print(f"\n--- {game['default_name']} 탐색 ---")
                for country in game_countries:
                    hl = locale_map.get(country, "en")
                    target_sections = get_target_sections_for_game_country(
                        sections_map, game, country
                    )
                    print(f"  탐색: {country.upper()} (hl={hl}, 섹션 {len(target_sections)}개)")
                    found = scan_country_with_retry(
                        context, country, hl, [game], target_sections,
                    )
                    for r in found:
                        key = (r["country"], r["section"], gid)
                        if key not in seen_keys[gid]:
                            seen_keys[gid].add(key)
                            game_results[gid].append(r)
                    rand_delay(1000, 2000)
        finally:
            context.close()
            browser.close()

    total = sum(len(v) for v in game_results.values())
    print(f"\n[Google Play] 총 {total}건 발견")
    return game_results
