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
SCREENSHOTS_DIR = BASE_DIR / "screenshots"


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

    # 실행 로그에서 해당 날짜·게임의 found_list 복원
    run_log = load_run_log()
    game_log = run_log.get(date_str, {}).get("games", {}).get(game["id"], {})
    found_raw = game_log.get("found", [])

    # found_list 재구성 (screenshot 파일 찾기)
    found_list = []
    for r in found_raw:
        screenshot = None
        # screenshots/ 에서 해당 국가·탭·섹션 파일 탐색
        pattern = f"{r['country']}_{r['tab']}_*_{game['id']}.png"
        matches = list(SCREENSHOTS_DIR.glob(pattern))
        if matches:
            screenshot = matches[0].name
        found_list.append({
            "country": r["country"],
            "tab": r["tab"],
            "section": r["section"],
            "game_id": game["id"],
            "screenshot": screenshot,
        })

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
    print(f"최종 이메일 발송 완료: {final_sent_at}")


if __name__ == "__main__":
    main()
