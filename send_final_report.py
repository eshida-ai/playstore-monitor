"""
모니터링_결과 시트에서 최신 날짜의 "노출됨" 데이터를 읽고,
appstore/ 폴더를 스캔하여 미기록 앱스토어 내역을 자동 등록한 뒤
게임별 최종 수신자에게 이메일을 발송합니다.

앱스토어 파일명 규칙: 날짜_국가코드_게임명_섹션명.png(jpg 등)
예) 2026-04-03_JP_스타세일러_오늘의게임.jpg

사용법: send_report.bat 더블클릭 또는 python send_final_report.py
"""
import os
import re
from datetime import datetime
import check_play_featured as cpf


# =========================
# 앱스토어 폴더 스캔
# =========================
def _find_local_screenshot(folder, date_str, country):
    """날짜+국가 기준으로 폴더에서 첫 번째 매칭 파일의 절대경로 반환."""
    folder_abs = os.path.abspath(folder)
    if not os.path.isdir(folder_abs):
        return ""
    prefix = f"{date_str}_{country}_"
    for fname in os.listdir(folder_abs):
        if fname.startswith(prefix):
            return os.path.join(folder_abs, fname)
    return ""


def scan_appstore_folder(appstore_dir, recorded_dates_by_country):
    """
    appstore/ 폴더에서 '날짜_국가코드_게임명_섹션명.확장자' 형식 파일 스캔.
    시트에 이미 기록된 (날짜, 국가) 조합은 건너뜀.
    반환: {게임명: {국가: {섹션명: {"path":..., "section_display":..., "date":..., "game_name":...}}}}
    """
    SUPPORTED_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")
    new_items = {}  # {game_name: {country: {section: meta}}}

    if not os.path.isdir(appstore_dir):
        return new_items

    for fname in os.listdir(appstore_dir):
        if not fname.lower().endswith(SUPPORTED_EXTS):
            continue

        name = os.path.splitext(fname)[0]
        parts = name.split("_", 3)
        if len(parts) < 4:
            print(f"  [앱스토어] 파일명 형식 불일치 (건너뜀, 형식: 날짜_국가_게임명_섹션명): {fname}")
            continue

        date_str    = parts[0]
        country     = parts[1].upper()
        game_name   = parts[2]
        section_raw = parts[3].replace("_", " ")

        parsed_date = cpf.parse_date_flexible(date_str)
        if not parsed_date:
            print(f"  [앱스토어] 날짜 파싱 실패 (건너뜀): {fname}")
            continue

        # 이미 시트에 기록된 날짜+국가 조합이면 스킵
        recorded = recorded_dates_by_country.get(country, set())
        if parsed_date in recorded:
            print(f"  [앱스토어] 이미 기록됨 (건너뜀): {fname}")
            continue

        new_items.setdefault(game_name, {})
        new_items[game_name].setdefault(country, {})
        new_items[game_name][country][section_raw] = {
            "path":            os.path.abspath(os.path.join(appstore_dir, fname)),
            "section_display": section_raw,
            "date":            parsed_date,
            "game_name":       game_name,
        }
        print(f"  [앱스토어] 신규 내역 발견: {game_name} / {country} / {section_raw} ({parsed_date})")

    return new_items


def get_recorded_appstore_dates(rows):
    """시트에서 스토어='앱스토어'인 행의 {국가: {날짜 집합}} 반환."""
    recorded = {}
    for row in rows[1:]:
        if len(row) < 3:
            continue
        date_raw = row[0].strip()
        store    = row[1].strip()
        country  = row[2].strip()
        if store == "앱스토어" and date_raw and country:
            parsed = cpf.parse_date_flexible(date_raw)
            if parsed:
                recorded.setdefault(country, set()).add(parsed)
    return recorded


# =========================
# 메인
# =========================
def main():
    print("=" * 50)
    print("최종 리포트 발송 시작")
    print("=" * 50)

    # ① Google 서비스 초기화
    gctx = cpf.init_google_services()
    if not gctx:
        print("[오류] Google 연동 실패")
        input("\nEnter를 누르면 종료합니다...")
        return

    # ② 설정 읽기
    game_names, game_settings = cpf.read_settings(gctx)
    loc_map = cpf.read_localization(gctx)
    if not game_names:
        print("[오류] 키워드 없음")
        input("\nEnter를 누르면 종료합니다...")
        return

    # ③ 모니터링_결과 시트 전체 읽기
    result_ws = gctx["result_worksheet"]
    rows = result_ws.get_all_values()
    if len(rows) < 2:
        print("[오류] 모니터링 결과가 없습니다.")
        input("\nEnter를 누르면 종료합니다...")
        return

    # ④ 최신 날짜 찾기 (구글플레이 기준)
    all_dates = []
    for row in rows[1:]:
        if len(row) >= 2 and row[1].strip() == "구글플레이" and row[0].strip():
            parsed = cpf.parse_date_flexible(row[0])
            if parsed:
                all_dates.append(parsed)

    if not all_dates:
        print("[오류] 구글플레이 날짜 데이터가 없습니다.")
        input("\nEnter를 누르면 종료합니다...")
        return

    latest_date = max(all_dates)
    print(f"[확인] 최신 날짜: {latest_date}")

    # ⑤ 최신 날짜의 구글플레이 "노출됨" 행 수집
    # 헤더: [확인 날짜, 스토어, 국가, 섹션명, 노출 여부, 스크린샷 링크]
    merged_r_gp, merged_s_gp = {}, {}
    for row in rows[1:]:
        if len(row) < 6:
            continue
        date_raw = row[0].strip()
        store    = row[1].strip()
        country  = row[2].strip()
        section  = row[3].strip()
        status   = row[4].strip()
        link     = row[5].strip()

        if cpf.parse_date_flexible(date_raw) != latest_date:
            continue
        if store != "구글플레이" or status != "노출됨":
            continue

        merged_r_gp.setdefault(country, {})
        merged_s_gp.setdefault(country, {})
        key_clean = re.sub(r"\s*\(.*?\)\s*$", "", section).strip()
        key = "top_banner" if "배너" in key_clean else key_clean
        if key in merged_r_gp[country]:
            continue  # 중복 방지

        # googleplay/ 폴더에서 로컬 파일 탐색 → 인라인 이미지용
        local_path = _find_local_screenshot(cpf.GOOGLEPLAY_DIR, latest_date, country)

        merged_r_gp[country][key] = "노출됨"
        merged_s_gp[country][key] = {
            "path":            local_path,
            "link":            link,
            "section_display": section,
            "matched_keyword": "",
        }

    # ⑥ 앱스토어 폴더 스캔 → 게임별로 신규 내역 처리
    recorded_as = get_recorded_appstore_dates(rows)
    print(f"\n[앱스토어] 폴더 스캔 중: {cpf.APPSTORE_DIR}")
    new_as_items = scan_appstore_folder(cpf.APPSTORE_DIR, recorded_as)

    # 게임별 앱스토어 결과 딕셔너리
    # {game_name: {country: {section: {...}}}}
    game_r_as = {}  # {game_name: merged_r_as}
    game_s_as = {}  # {game_name: merged_s_as}

    for file_game, country_dict in new_as_items.items():
        # 파일명의 게임명을 설정 시트 게임명과 매칭
        matched_game = next(
            (g for g in game_names if file_game in g or g in file_game), None
        )
        if not matched_game:
            print(f"  [앱스토어] 게임명 불일치, 스킵: {file_game}")
            continue

        game_r_as.setdefault(matched_game, {})
        game_s_as.setdefault(matched_game, {})

        for country, sections in country_dict.items():
            game_r_as[matched_game].setdefault(country, {})
            game_s_as[matched_game].setdefault(country, {})

            for section_raw, meta in sections.items():
                path        = meta["path"]
                parsed_date = meta["date"]

                # Drive 업로드
                link = cpf.upload_screenshot(gctx, path) if os.path.exists(path) else ""

                # 시트에 자동 기록 (최신 내역 상단)
                cpf.append_result_row(gctx, [parsed_date, "앱스토어", country, section_raw, "노출됨", link])
                print(f"  [앱스토어] 시트 기록 완료: {matched_game} / {country} / {section_raw}")

                game_r_as[matched_game][country][section_raw] = "노출됨"
                game_s_as[matched_game][country][section_raw] = {
                    "path":            path,
                    "link":            link,
                    "section_display": section_raw,
                    "matched_keyword": "",
                    "game_name":       matched_game,
                }

    # ⑦ 노출 현황 출력
    print(f"\n[발송 내용] {latest_date} 기준 노출 항목 (구글플레이):")
    for country, sdict in merged_r_gp.items():
        for key, status in sdict.items():
            display = merged_s_gp.get(country, {}).get(key, {}).get("section_display", key)
            print(f"  {country} - {display}: {status}")

    for gname, r_as in game_r_as.items():
        print(f"\n[발송 내용] 앱스토어 신규 내역 ({gname}):")
        for country, sdict in r_as.items():
            for key in sdict:
                print(f"  {country} - {key}: 노출됨")

    if not merged_r_gp and not game_r_as:
        print(f"\n[결과] 발송할 노출 항목이 없습니다.")
        input("\nEnter를 누르면 종료합니다...")
        return

    # ⑧ 게임별 최종 수신자에게 발송
    print()
    for game_name in game_names:
        recipients    = game_settings[game_name]["final_recipients"]
        game_keywords = cpf.build_keywords([game_name], loc_map)

        # 해당 게임의 앱스토어 내역
        r_as = game_r_as.get(game_name, {})
        s_as = game_s_as.get(game_name, {})

        # 구글플레이 또는 앱스토어 중 하나라도 노출 내역 있어야 발송
        has_gp = bool(merged_r_gp)
        has_as = bool(r_as)

        # 구글플레이는 공통이므로 게임이 여러 개일 때도 모두 포함
        # (게임별 구분이 필요하다면 섹션명 기반으로 필터 추가 가능)
        if not has_gp and not has_as:
            print(f"[{game_name}] 노출 내역 없음 → 발송 스킵")
            continue

        if not recipients:
            print(f"[{game_name}] 최종 발송 수신자 없음. 설정 시트 확인 필요.")
            continue

        print(f"[{game_name}] 최종 발송 대상: {', '.join(recipients)}")
        confirm = input(f"[{game_name}] 발송하시겠습니까? (y/n): ").strip().lower()
        if confirm != "y":
            print(f"[{game_name}] 발송 취소")
            continue

        cpf.send_email_report(
            latest_date,
            merged_r_gp, merged_s_gp,
            game_keywords, gctx,
            recipients=recipients,
            game_title=game_name,
            merged_r_as=r_as if r_as else None,
            merged_s_as=s_as if s_as else None,
            include_spreadsheet_btn=False,
            include_failed_countries=False,
        )

    input("\nEnter를 누르면 종료합니다...")


if __name__ == "__main__":
    main()