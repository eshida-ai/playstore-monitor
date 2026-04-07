/**
 * 피쳐드 내역 수정 페이지 로직
 * URL 파라미터: date, game, issue
 */

const REPO_OWNER = 'eshida-ai';
const REPO_NAME  = 'playstore-monitor';

const COUNTRY_FLAGS = {
  kr:'🇰🇷', us:'🇺🇸', jp:'🇯🇵', gb:'🇬🇧',
  de:'🇩🇪', cn:'🇨🇳', tw:'🇹🇼', th:'🇹🇭',
};
const TAB_OPTIONS = ['today', 'games'];
const COUNTRY_OPTIONS = ['kr', 'us', 'jp', 'gb', 'de', 'cn', 'tw', 'th'];

// URL 파라미터
const params    = new URLSearchParams(location.search);
const DATE_STR  = params.get('date')  || '';
const GAME_ID   = params.get('game')  || '';
const ISSUE_NUM = params.get('issue') || '';

// 현재 편집 중인 entries
let entries = [];
let issueUrl = '';
let configSha = null;
let overrideSha = null;

// ─────────────────────────────────────────────
// 초기화
// ─────────────────────────────────────────────
(function init() {
  document.getElementById('header-subtitle').textContent =
    `${GAME_ID} · ${DATE_STR}`;
  if (ISSUE_NUM) {
    document.getElementById('approve-link').href =
      `https://github.com/${REPO_OWNER}/${REPO_NAME}/issues/${ISSUE_NUM}`;
    issueUrl = `https://github.com/${REPO_OWNER}/${REPO_NAME}/issues/${ISSUE_NUM}`;
  }
})();

function getToken() {
  return document.getElementById('github-token').value.trim();
}

function githubHeaders(extra = {}) {
  const h = { 'Accept': 'application/vnd.github+json', ...extra };
  const t = getToken();
  if (t) h['Authorization'] = `Bearer ${t}`;
  return h;
}

// ─────────────────────────────────────────────
// 데이터 불러오기
// ─────────────────────────────────────────────
async function loadData() {
  const statusEl = document.getElementById('token-status');
  statusEl.textContent = '불러오는 중...';

  // 1. override 파일 먼저 확인
  const safeId = GAME_ID.replace(/[^a-zA-Z0-9]/g, '_');
  const overridePath = `logs/override_${DATE_STR}_${safeId}.json`;

  try {
    const res = await fetch(
      `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/contents/${overridePath}`,
      { headers: githubHeaders() }
    );
    if (res.ok) {
      const data = await res.json();
      overrideSha = data.sha;
      const override = JSON.parse(atob(data.content.replace(/\n/g, '')));
      entries = override.found_list || [];
      if (override.issue_url) issueUrl = override.issue_url;
      renderEntries();
      statusEl.textContent = '✅ override 데이터 로드';
      return;
    }
  } catch (_) {}

  // 2. run_log.json에서 오늘 데이터 로드
  try {
    const res = await fetch(
      `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/contents/logs/run_log.json`,
      { headers: githubHeaders() }
    );
    if (!res.ok) throw new Error(`${res.status}`);
    const data = await res.json();
    const log = JSON.parse(atob(data.content.replace(/\n/g, '')));
    const gameLog = log[DATE_STR]?.games?.[GAME_ID];

    if (gameLog) {
      if (gameLog.github_issue_url) issueUrl = gameLog.github_issue_url;
      entries = (gameLog.found || []).map(r => ({
        country: r.country,
        tab: r.tab,
        section: r.section,
        image_url: '',
      }));
    } else {
      entries = [];
    }
    renderEntries();
    statusEl.textContent = '✅ 불러오기 완료';
  } catch (e) {
    statusEl.textContent = `❌ 오류: ${e.message}`;
    document.getElementById('entries-container').innerHTML =
      `<div class="empty-msg">데이터를 불러올 수 없습니다.<br>토큰을 확인해주세요.</div>`;
  }
}

// ─────────────────────────────────────────────
// 항목 렌더링
// ─────────────────────────────────────────────
function renderEntries() {
  const container = document.getElementById('entries-container');
  if (!entries.length) {
    container.innerHTML = '<div class="empty-msg">피쳐드 내역이 없습니다. 아래 [+ 항목 추가]로 직접 입력하세요.</div>';
    return;
  }

  container.innerHTML = entries.map((e, i) => {
    const flag = COUNTRY_FLAGS[e.country] || '🌐';
    const countryOpts = COUNTRY_OPTIONS.map(c =>
      `<option value="${c}" ${c === e.country ? 'selected' : ''}>${c.toUpperCase()}</option>`
    ).join('');
    const tabOpts = TAB_OPTIONS.map(t =>
      `<option value="${t}" ${t === e.tab ? 'selected' : ''}>${t}</option>`
    ).join('');

    return `
    <div class="entry-row" id="entry-${i}">
      <div class="entry-flag">${flag}</div>
      <div>
        <label>국가</label>
        <select class="country-select" onchange="updateEntry(${i},'country',this.value);updateFlag(${i},this.value)">
          ${countryOpts}
        </select>
      </div>
      <div>
        <label>탭</label>
        <select class="tab-select" onchange="updateEntry(${i},'tab',this.value)">
          ${tabOpts}
        </select>
      </div>
      <div style="grid-column:2/-1;">
        <label>섹션명</label>
        <input type="text" value="${esc(e.section)}" placeholder="섹션명 입력"
               oninput="updateEntry(${i},'section',this.value)">
      </div>
      <div style="grid-column:2/-1;">
        <label>이미지 URL (Google Drive 공유 링크 또는 직접 링크)</label>
        <input type="url" value="${esc(e.image_url || '')}" placeholder="https://..."
               oninput="updateEntry(${i},'image_url',this.value)">
      </div>
      <div class="entry-delete">
        <button class="btn-del" onclick="deleteEntry(${i})">🗑 항목 삭제</button>
      </div>
    </div>`;
  }).join('');
}

function updateEntry(idx, field, value) {
  entries[idx][field] = value;
}

function updateFlag(idx, country) {
  const flagEl = document.querySelector(`#entry-${idx} .entry-flag`);
  if (flagEl) flagEl.textContent = COUNTRY_FLAGS[country] || '🌐';
}

function deleteEntry(idx) {
  entries.splice(idx, 1);
  renderEntries();
}

function addEntry() {
  entries.push({ country: 'kr', tab: 'games', section: '', image_url: '' });
  renderEntries();
  // 새 항목으로 스크롤
  const rows = document.querySelectorAll('.entry-row');
  if (rows.length) rows[rows.length - 1].scrollIntoView({ behavior: 'smooth' });
}

// ─────────────────────────────────────────────
// 저장 후 초안 재발송
// ─────────────────────────────────────────────
async function saveAndResend() {
  const statusEl = document.getElementById('status-msg');
  const token = getToken();
  if (!token) {
    statusEl.textContent = '❌ GitHub Token을 입력해주세요.';
    statusEl.style.color = '#e74c3c';
    return;
  }

  statusEl.textContent = '저장 중...';
  statusEl.style.color = '#888';

  // 1. override 파일 저장
  const safeId = GAME_ID.replace(/[^a-zA-Z0-9]/g, '_');
  const overridePath = `logs/override_${DATE_STR}_${safeId}.json`;
  const overrideData = {
    game_id: GAME_ID,
    date: DATE_STR,
    issue_number: ISSUE_NUM ? parseInt(ISSUE_NUM) : null,
    issue_url: issueUrl,
    found_list: entries,
  };

  const content = btoa(unescape(encodeURIComponent(JSON.stringify(overrideData, null, 2))));
  const body = {
    message: `[수정] ${GAME_ID} 피쳐드 내역 수정 — ${DATE_STR}`,
    content,
  };
  if (overrideSha) body.sha = overrideSha;

  try {
    const res = await fetch(
      `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/contents/${overridePath}`,
      {
        method: 'PUT',
        headers: { ...githubHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }
    );
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.message || res.statusText);
    }
    const saved = await res.json();
    overrideSha = saved.content.sha;
  } catch (e) {
    statusEl.textContent = `❌ 저장 실패: ${e.message}`;
    statusEl.style.color = '#e74c3c';
    return;
  }

  statusEl.textContent = '초안 재발송 중...';

  // 2. workflow_dispatch 트리거
  const resendMode = `${DATE_STR}|${GAME_ID}|${ISSUE_NUM}|${issueUrl}`;
  try {
    const res = await fetch(
      `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/actions/workflows/monitor.yml/dispatches`,
      {
        method: 'POST',
        headers: { ...githubHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ref: 'main',
          inputs: { resend_mode: resendMode },
        }),
      }
    );
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.message || res.statusText);
    }
    statusEl.textContent = '✅ 초안 재발송 요청 완료! 잠시 후 이메일을 확인하세요.';
    statusEl.style.color = '#27ae60';
  } catch (e) {
    statusEl.textContent = `❌ 재발송 실패: ${e.message}`;
    statusEl.style.color = '#e74c3c';
  }
}

// ─────────────────────────────────────────────
// 유틸
// ─────────────────────────────────────────────
function esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
