"""
이메일 생성·발송 모듈
- 초안: draft 수신자 발송 + 4개 수정 버튼 + 승인/재검토 버튼
- 최종: final 수신자 전체 발송, 수정 버튼 없음
- 이미지: MIME multipart/related 인라인 삽입
- 승인: GitHub Issues 기반
"""
import base64
import json
import os
import smtplib
import traceback
from datetime import datetime
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

BASE_DIR = Path(__file__).parent
SCREENSHOTS_DIR = BASE_DIR / "screenshots"

COUNTRY_FLAGS = {
    "kr": "🇰🇷", "us": "🇺🇸", "jp": "🇯🇵",
    "gb": "🇬🇧", "de": "🇩🇪", "cn": "🇨🇳",
    "tw": "🇹🇼", "th": "🇹🇭", "fr": "🇫🇷", "sg": "🇸🇬",
}

TAB_LABELS = {"today": "Today", "games": "Games"}


class Mailer:
    def __init__(self, sender: str, app_password: str, config: dict):
        self.sender = sender
        self.app_password = app_password
        self.config = config

    # ─────────────────────────────────────────
    # GitHub Issue 생성
    # ─────────────────────────────────────────
    def create_github_issue(self, game: dict, found_list: list, date_str: str,
                             owner: str, repo: str) -> tuple[int | None, str]:
        """피쳐드 확인 건 GitHub Issue 생성. (issue_number, issue_url) 반환"""
        if not owner or not repo:
            print("  [GitHub] owner/repo 미설정 — Issue 생성 스킵")
            return None, ""

        try:
            from github import Github
            token = os.environ.get(self.config.get("github", {}).get("token_env", "GITHUB_TOKEN"), "")
            if not token:
                print("  [GitHub] GITHUB_TOKEN 없음 — Issue 생성 스킵")
                return None, ""

            gh = Github(token)
            repo_obj = gh.get_repo(f"{owner}/{repo}")

            found_summary = "\n".join(
                f"- {r['country'].upper()} / {TAB_LABELS.get(r['tab'], r['tab'])} / {r['section']}"
                for r in found_list
            ) or "- 노출 없음"

            body = (
                f"초안 이메일 발송 완료.\n\n"
                f"**게임:** {game['default_name']}\n"
                f"**날짜:** {date_str}\n"
                f"**노출 내역:**\n{found_summary}\n\n"
                f"담당자가 이메일의 **[이상 없음 — 최종 이메일 발송]** 버튼을 클릭하면 "
                f"이 Issue가 자동으로 close되고 최종 이메일이 발송됩니다."
            )
            issue = repo_obj.create_issue(
                title=f"[피쳐드 확인] {game['default_name']} · {date_str}",
                body=body,
                labels=["pending-approval", game["id"]],
            )
            return issue.number, issue.html_url
        except Exception as e:
            print(f"  [GitHub Issue 생성 실패] {e}")
            return None, ""

    # ─────────────────────────────────────────
    # 초안 이메일 발송
    # ─────────────────────────────────────────
    def send_draft(self, game: dict, found_list: list, date_str: str, issue_url: str) -> str:
        recipients = game["recipients"].get("draft", [])
        subject = f"[초안] 앱스토어 피쳐드 · {game['default_name']} · {date_str}"
        html = self._build_html(
            game=game,
            found_list=found_list,
            date_str=date_str,
            is_draft=True,
            issue_url=issue_url,
        )
        self._send(recipients=recipients, subject=subject, html=html, found_list=found_list)
        return datetime.utcnow().isoformat() + "Z"

    # ─────────────────────────────────────────
    # 최종 이메일 발송
    # ─────────────────────────────────────────
    def send_final(self, game: dict, found_list: list, date_str: str) -> str:
        recipients = game["recipients"].get("final", [])
        subject = f"[앱스토어 피쳐드] {game['default_name']} · {date_str} 모니터링 결과"
        html = self._build_html(
            game=game,
            found_list=found_list,
            date_str=date_str,
            is_draft=False,
            issue_url="",
        )
        self._send(recipients=recipients, subject=subject, html=html, found_list=found_list)
        return datetime.utcnow().isoformat() + "Z"

    # ─────────────────────────────────────────
    # 실패 알림 이메일
    # ─────────────────────────────────────────
    def send_error_email(self, recipients: list, error_message: str, traceback_str: str = ""):
        subject = "[오류] 앱스토어 피쳐드 모니터링 실행 실패"
        html = f"""
        <html><body style="font-family: sans-serif; color: #333;">
        <h2 style="color: #c0392b;">⚠️ 모니터링 실행 중 오류 발생</h2>
        <p><strong>시각:</strong> {datetime.utcnow().isoformat()}Z (UTC)</p>
        <p><strong>오류:</strong></p>
        <pre style="background:#f8f8f8;padding:12px;border-radius:4px;">{error_message}</pre>
        <p><strong>스택 트레이스:</strong></p>
        <pre style="background:#f8f8f8;padding:12px;border-radius:4px;font-size:12px;">{traceback_str}</pre>
        <hr>
        <p style="color:#888;font-size:12px;">본 메일은 GitHub Actions를 통해 자동 발송되었습니다.</p>
        </body></html>
        """
        try:
            self._send(recipients=recipients, subject=subject, html=html, found_list=[])
        except Exception as e:
            print(f"[실패 이메일 발송 오류] {e}")

    # ─────────────────────────────────────────
    # HTML 본문 빌더
    # ─────────────────────────────────────────
    def _build_html(self, game: dict, found_list: list, date_str: str,
                    is_draft: bool, issue_url: str) -> str:
        game_name = game["default_name"]
        countries_checked = " · ".join(c.upper() for c in self.config.get("countries", []))
        tabs_checked = " · ".join(TAB_LABELS.get(t, t) for t in self.config.get("tabs", []))

        # 노출 요약
        if found_list:
            exposure_badge = f'<span style="background:#27ae60;color:#fff;padding:2px 10px;border-radius:12px;font-size:14px;">{len(set(r["country"] for r in found_list))}개국 노출</span>'
        else:
            exposure_badge = '<span style="background:#e74c3c;color:#fff;padding:2px 10px;border-radius:12px;font-size:14px;">노출 없음</span>'

        # 노출 테이블
        table_rows = ""
        for r in found_list:
            flag = COUNTRY_FLAGS.get(r["country"].lower(), "")
            tab_label = TAB_LABELS.get(r["tab"], r["tab"])
            table_rows += f"""
            <tr>
              <td style="padding:8px 12px;border-bottom:1px solid #eee;">{flag} {r['country'].upper()}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #eee;">{tab_label} · {r['section']}</td>
            </tr>"""

        if table_rows:
            exposure_table = f"""
            <table style="border-collapse:collapse;width:100%;margin:12px 0;">
              <thead>
                <tr style="background:#f5f5f5;">
                  <th style="padding:8px 12px;text-align:left;font-size:13px;color:#666;">국가</th>
                  <th style="padding:8px 12px;text-align:left;font-size:13px;color:#666;">탭 · 섹션</th>
                </tr>
              </thead>
              <tbody>{table_rows}</tbody>
            </table>"""
        else:
            exposure_table = '<p style="color:#999;font-style:italic;">확인된 노출 섹션이 없습니다.</p>'

        # 인라인 이미지 섹션
        images_html = ""
        for r in found_list:
            if not r.get("screenshot"):
                continue
            flag = COUNTRY_FLAGS.get(r["country"].lower(), "")
            tab_label = TAB_LABELS.get(r["tab"], r["tab"])
            cid = _make_cid(r["screenshot"])
            images_html += f"""
            <div style="margin:16px 0;">
              <img src="cid:{cid}" style="max-width:100%;border-radius:8px;border:1px solid #ddd;" alt="{r['section']}">
              <p style="margin:4px 0;font-size:13px;color:#555;">
                {flag} {r['country'].upper()} · {tab_label} · {r['section']}
              </p>
            </div>"""

        # 초안 전용: 수정 버튼 + 승인 버튼
        draft_section = ""
        if is_draft:
            approve_href = issue_url if issue_url else "#"
            draft_section = f"""
            <div style="margin:24px 0;padding:16px;background:#fffbf0;border:1px solid #f0c040;border-radius:8px;">
              <p style="font-size:13px;color:#856404;margin:0 0 12px 0;font-weight:bold;">⚠️ 초안 검토 — 아래 버튼으로 수정하거나 최종 발송을 승인하세요</p>
              <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px;">
                <button style="{_btn_style('#6c757d')}">📋 항목 삭제</button>
                <button style="{_btn_style('#6c757d')}">✏️ 국가·섹션명 수정</button>
                <button style="{_btn_style('#6c757d')}">🖼️ 이미지 직접 추가</button>
                <button style="{_btn_style('#6c757d')}">👥 수신자 임시 변경*</button>
              </div>
              <p style="font-size:11px;color:#888;margin:0 0 16px 0;">
                * 수신자 임시 변경 = 이번 발송 건에만 적용. 영구 변경은 관리 페이지에서
              </p>
              <div style="display:flex;gap:12px;">
                <a href="{approve_href}"
                   style="display:inline-block;padding:10px 24px;background:#27ae60;color:#fff;
                          text-decoration:none;border-radius:6px;font-weight:bold;font-size:14px;">
                  ✅ 이상 없음 — 최종 이메일 발송
                </a>
                <a href="{approve_href}#comment"
                   style="display:inline-block;padding:10px 24px;background:#e67e22;color:#fff;
                          text-decoration:none;border-radius:6px;font-weight:bold;font-size:14px;">
                  🔄 수정 후 재검토 요청
                </a>
              </div>
            </div>"""

        html = f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:'Apple SD Gothic Neo',Arial,sans-serif;">
<div style="max-width:640px;margin:20px auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08);">

  <!-- 헤더 -->
  <div style="background:#0066cc;padding:24px 32px;">
    <h1 style="margin:0;font-size:20px;color:#fff;font-weight:700;">
      {'[초안] ' if is_draft else ''}앱스토어 피쳐드 모니터링
    </h1>
    <p style="margin:4px 0 0 0;font-size:13px;color:rgba(255,255,255,.8);">{date_str}</p>
  </div>

  <!-- 본문 -->
  <div style="padding:28px 32px;">
    <p style="margin:0 0 16px 0;font-size:15px;line-height:1.7;">
      안녕하세요, 사업제휴팀입니다.<br>
      <strong>{game_name}</strong>의 금일 피쳐드 내역이 확인되어 공유드립니다.<br>
      주요 국가 기준으로 체크되었으며, 기기 또는 계정별 피쳐드 내역은
      다르게 노출될 수 있는 점 참고 바랍니다.
    </p>

    <hr style="border:none;border-top:1px solid #eee;margin:20px 0;">

    <!-- 게임 요약 -->
    <div style="margin-bottom:20px;">
      <h2 style="margin:0 0 8px 0;font-size:18px;">{game_name} &nbsp;{exposure_badge}</h2>
      {exposure_table}
    </div>

    <!-- 스크린샷 이미지 -->
    {images_html}

    <hr style="border:none;border-top:1px solid #eee;margin:20px 0;">

    <!-- 확인 정보 -->
    <p style="font-size:13px;color:#777;margin:0 0 4px 0;">확인 국가: {countries_checked}</p>
    <p style="font-size:13px;color:#777;margin:0 0 4px 0;">확인 탭: {tabs_checked}</p>
    <p style="font-size:13px;color:#aaa;margin:8px 0 0 0;">
      본 메일은 GitHub Actions를 통해 자동 발송되었습니다.
    </p>

    {draft_section}
  </div>
</div>
</body></html>"""
        return html

    # ─────────────────────────────────────────
    # SMTP 발송 (MIME multipart/related)
    # ─────────────────────────────────────────
    def _send(self, recipients: list, subject: str, html: str, found_list: list):
        if not recipients:
            print("  [발송 스킵] 수신자 없음")
            return

        # 최상위: mixed (이메일 구조)
        msg_mixed = MIMEMultipart("mixed")
        msg_mixed["Subject"] = subject
        msg_mixed["From"] = self.sender
        msg_mixed["To"] = ", ".join(recipients)

        # related (HTML + 인라인 이미지)
        msg_related = MIMEMultipart("related")

        # alternative (plain + html) — html만 사용
        msg_alternative = MIMEMultipart("alternative")
        msg_alternative.attach(MIMEText("이 이메일은 HTML을 지원하는 클라이언트에서 확인하세요.", "plain", "utf-8"))
        msg_alternative.attach(MIMEText(html, "html", "utf-8"))
        msg_related.attach(msg_alternative)

        # 스크린샷 인라인 첨부
        for r in found_list:
            screenshot = r.get("screenshot")
            if not screenshot:
                continue
            img_path = SCREENSHOTS_DIR / screenshot
            if not img_path.exists():
                continue
            try:
                with open(img_path, "rb") as f:
                    img_data = MIMEImage(f.read(), _subtype="png")
                cid = _make_cid(screenshot)
                img_data.add_header("Content-ID", f"<{cid}>")
                img_data.add_header("Content-Disposition", "inline", filename=screenshot)
                msg_related.attach(img_data)
            except Exception as e:
                print(f"  [이미지 첨부 실패] {screenshot}: {e}")

        msg_mixed.attach(msg_related)

        # 발송
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(self.sender, self.app_password)
            smtp.sendmail(self.sender, recipients, msg_mixed.as_string())
        print(f"  발송 완료 → {', '.join(recipients)}")


# ─────────────────────────────────────────────
# 유틸리티
# ─────────────────────────────────────────────
def _make_cid(filename: str) -> str:
    """파일명에서 Content-ID 생성 (특수문자 제거)"""
    import re
    return re.sub(r'[^a-zA-Z0-9_]', '_', filename)


def _btn_style(bg_color: str) -> str:
    return (
        f"background:{bg_color};color:#fff;border:none;padding:8px 16px;"
        f"border-radius:4px;font-size:13px;cursor:pointer;"
    )
