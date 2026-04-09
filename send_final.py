"""
GitHub Issue close 이벤트 → 최종 이메일 발송
GitHub Actions 'send-final-on-approval' job에서 호출
환경변수: ISSUE_NUMBER, ISSUE_TITLE, CLOSED_BY, GMAIL_APP_PASSWORD
"""
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
LOGS_DIR = BASE_DIR / "logs"
RUN_LOG_PATH = LOGS_DIR / "run_log.json"
HISTORY_PATH = LOGS_DIR / "history.csv"
SCREENSHOTS_DIR = BASE_DIR / "screenshots"


def _load_recipients_from_env() -> dict:
    raw = os.environ.get("RECIPIENTS_CONFIG", "{}")
    try:
        return json.loads(raw)
    except Exception as e:
        print(f"[경고] RECIPIENTS_CONFIG 파싱 실패: {e}")
        return {}


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_run_log() -> dict:
    if RUN_LOG_PATH.exists():
        with open(RUN_LOG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_run_log(log: dict):
    LOGS_DIR.mkdir(exist_ok=True)
    existing = load_run_log()
    # 중첩 업데이트
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


def parse_issue_title(title: str) -> tuple[str | None, str | None]:
    """
    '[피쳐드 확인] {게임명} · {날짜}' 형식에서 게임명·날짜 파싱
    """
    m = re.match(r'\[피쳐드 확인\]\s+(.+?)\s+·\s+(\d{4}-\d{2}-\d{2})', title)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None, None


def find_game_by_name(game_name: str, config: dict) -> dict | None:
    for game in config["games"]:
        if game["default_name"] == game_name:
            return game
    return None


def _migrate_history_if_needed():
    """history.csv에 store 컬럼이 없으면 자동 추가 (구버전 호환)"""
    import csv
    if not HISTORY_PATH.exists():
        return
    with open(HISTORY_PATH, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows or "store" in rows[0]:
        return
    # store 컬럼을 game_name 뒤(index 3)에 삽입
    new_rows = []
    for i, row in enumerate(rows):
        if i == 0:
            new_rows.append(row[:3] + ["store"] + row[3:])
        else:
            new_rows.append(row[:3] + ["apple"] + row[3:])  # 기존 데이터는 apple로 간주
    with open(HISTORY_PATH, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(new_rows)
    print(f"[history.csv] store 컬럼 마이그레이션 완료 ({len(rows)-1}행)")


def append_to_history(date_str: str, game: dict, found_list: list,
                      approved_by: str, final_sent_at: str):
    import csv
    LOGS_DIR.mkdir(exist_ok=True)
    _migrate_history_if_needed()
    is_new = not HISTORY_PATH.exists()
    with open(HISTORY_PATH, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["date", "game_id", "game_name", "store",
                             "country", "tab", "section",
                             "approved_by", "final_sent_at"])
        if found_list:
            for r in found_list:
                writer.writerow([
                    date_str, game["id"], game["default_name"],
                    r.get("store", "apple"),
                    r.get("country", ""), r.get("tab", ""), r.get("section", ""),
                    approved_by, final_sent_at,
                ])
        else:
            writer.writerow([
                date_str, game["id"], game["default_name"],
                "all", "", "", "노출 없음", approved_by, final_sent_at,
            ])


def main():
    issue_number = os.environ.get("ISSUE_NUMBER", "")
    issue_title = os.environ.get("ISSUE_TITLE", "")
    closed_by = os.environ.get("CLOSED_BY", "")

    print(f"=== 최종 이메일 발송 트리거 ===")
    print(f"Issue #{issue_number}: {issue_title}")
    print(f"Close 처리자: {closed_by}")

    config = load_config()

    # 이슈 제목에서 게임명·날짜 파싱
    game_name, date_str = parse_issue_title(issue_title)
    if not game_name or not date_str:
        print(f"[스킵] 피쳐드 확인 이슈가 아님: {issue_title}")
        return

    game = find_game_by_name(game_name, config)
    if not game:
        print(f"[오류] config.json에서 게임 '{game_name}' 없음")
        sys.exit(1)

    # 수신자를 환경변수에서 주입
    recipients_map = _load_recipients_from_env()
    game["recipients"] = recipients_map.get(game["id"], {"draft": [], "final": []})

    # 실행 로그에서 해당 날짜·게임의 found_list 복원
    run_log = load_run_log()
    game_log = run_log.get(date_str, {}).get("games", {}).get(game["id"], {})

    # apple_found / google_found 분리 저장 구조 (신규)
    # 구버전 호환: "found" 키만 있는 경우 전부 apple로 처리
    apple_raw  = game_log.get("apple_found", game_log.get("found", []))
    google_raw = game_log.get("google_found", [])

    def _resolve_screenshot(pattern: str) -> str | None:
        matches = list(SCREENSHOTS_DIR.glob(pattern))
        return matches[0].name if matches else None

    # Apple found_list 재구성
    apple_found = []
    for r in apple_raw:
        pattern = f"{r['country']}_{r.get('tab', '*')}_*_{game['id']}.png"
        apple_found.append({
            "country":    r["country"],
            "tab":        r.get("tab", ""),
            "section":    r["section"],
            "store":      "apple",
            "game_id":    game["id"],
            "screenshot": _resolve_screenshot(pattern),
        })

    # Google found_list 재구성
    google_found = []
    for r in google_raw:
        safe_section = re.sub(r'[\s\\/:*?"<>|]', '_', r["section"])
        pattern = f"google_{r['country']}_{safe_section}_{game['id']}.png"
        google_found.append({
            "country":    r["country"],
            "section":    r["section"],
            "store":      "google",
            "game_id":    game["id"],
            "screenshot": _resolve_screenshot(pattern),
        })

    found_list = apple_found + google_found
    print(f"복원된 결과: Apple {len(apple_found)}건 / Google {len(google_found)}건")

    # 최종 이메일 발송
    from mailer import Mailer
    mailer = Mailer(
        sender=config["email"]["sender"],
        app_password=os.environ.get("GMAIL_APP_PASSWORD", ""),
        config=config,
    )
    final_sent_at = mailer.send_final(
        game=game,
        found_list=found_list,
        date_str=date_str,
    )

    # 로그 업데이트
    save_run_log({
        date_str: {
            "games": {
                game["id"]: {
                    "approved_by": closed_by,
                    "approved_at": datetime.utcnow().isoformat() + "Z",
                    "final_sent_at": final_sent_at,
                }
            }
        }
    })

    # 누적 내역 CSV 기록
    append_to_history(date_str, game, found_list, closed_by, final_sent_at)

    print(f"최종 이메일 발송 완료: {final_sent_at}")


if __name__ == "__main__":
    main()
