/**
 * 앱스토어 피쳐드 모니터링 — 관리 UI 로직
 * GitHub Pages에서 실행, GitHub API로 config.json 직접 업데이트
 */

// ─────────────────────────────────────────────
// 설정
// ─────────────────────────────────────────────
const REPO_OWNER = 'eshida-ai';
const REPO_NAME  = 'playstore-monitor';
const CONFIG_PATH_IN_REPO = 'config.json';

// 실제 배포 시 위 값을 채우거나, URL 파라미터로 받아도 됨
// 예: ?owner=myorg&repo=appstore-monitor

let config = null;
let configSha = null;  // GitHub API 업데이트용 파일 SHA

// ─────────────────────────────────────────────
// 초기화
// ─────────────────────────────────────────────
(async function init() {
  readUrlParams();
  await loadConfig();
  renderGameCards();
  renderRecipientSelect();
  renderDriveStatus();
  renderImageList();
})();

function readUrlParams() {
  const params = new URLSearchParams(location.search);
  if (params.get('owner')) window._repoOwner = params.get('owner');
  if (params.get('repo'))  window._repoName  = params.get('repo');
}

function getOwner() { return window._repoOwner || REPO_OWNER; }
function getRepo()  { return window._repoName  || REPO_NAME;  }

// ─────────────────────────────────────────────
// config.json 로드 (GitHub API)
// ─────────────────────────────────────────────
async function loadConfig() {
  try {
    const url = `https://api.github.com/repos/${getOwner()}/${getRepo()}/contents/${CONFIG_PATH_IN_REPO}`;
    const res = await fetch(url, { headers: githubHeaders() });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const data = await res.json();
    configSha = data.sha;
    config = JSON.parse(atob(data.content.replace(/\n/g, '')));
  } catch (e) {
    // 로컬 개발 폴백: 같은 디렉토리의 config.json fetch
    try {
      const res = await fetch('../config.json');
      config = await res.json();
    } catch (_) {
      config = defaultConfig();
    }
    console.warn('GitHub API 미연결 — 로컬 config 사용:', e.message);
  }
}

function defaultConfig() {
  return {
    schedule: { run_time_utc: '00:00', run_time_kst: '09:00' },
    drive: { folder_path: '피쳐드모니터링/수동추가', filename_format: '{country}_{tab}_{section}_{game}.png' },
    github: { owner: '', repo: '' },
    games: [],
    countries: ['kr', 'us', 'jp', 'gb', 'de'],
    tabs: ['today', 'games'],
    email: { sender: '', app_password: '${GMAIL_APP_PASSWORD}' },
  };
}

function githubHeaders() {
  const token = document.getElementById('github-token')?.value?.trim() || '';
  const h = { 'Accept': 'application/vnd.github+json' };
  if (token) h['Authorization'] = `Bearer ${token}`;
  return h;
}

// ─────────────────────────────────────────────
// 탭 전환
// ─────────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll('.tab-btn').forEach((b, i) => {
    b.classList.toggle('active', ['games','recipients','images'][i] === name);
  });
  document.querySelectorAll('.tab-content').forEach(el => {
    el.classList.toggle('active', el.id === `tab-${name}`);
  });
  if (name === 'recipients') renderRecipients();
  if (name === 'images')     renderImageList();
}

// ─────────────────────────────────────────────
// ══ 게임 관리 탭 ══
// ─────────────────────────────────────────────
const COUNTRY_LABELS = {
  kr:'🇰🇷 한국(KR)', us:'🇺🇸 미국(US)', jp:'🇯🇵 일본(JP)',
  gb:'🇬🇧 영국(GB)', de:'🇩🇪 독일(DE)', cn:'🇨🇳 중국(CN)',
  tw:'🇹🇼 대만(TW)', th:'🇹🇭 태국(TH)', fr:'🇫🇷 프랑스(FR)',
};

function renderGameCards() {
  const container = document.getElementById('game-cards');
  if (!config || !config.games.length) {
    container.innerHTML = '<div class="empty-msg">등록된 게임이 없습니다. 오른쪽 상단 [+ 게임 추가]를 눌러주세요.</div>';
    return;
  }

  container.innerHTML = config.games.map((game, idx) => {
    const today = new Date().toISOString().slice(0, 10);
    const isActive = game.active_from <= today && today <= game.active_until;
    const storeApple  = (game.stores || []).includes('apple')  ? 'on' : '';
    const storeGoogle = (game.stores || []).includes('google') ? 'on' : '';

    const nameFields = Object.entries(COUNTRY_LABELS).map(([code, label]) => `
      <div>
        <label>${label}</label>
        <input type="text" value="${esc(game.names?.[code] || '')}"
               onchange="setGameField(${idx},'names','${code}',this.value)">
      </div>`).join('');

    return `
    <div class="card" id="game-card-${idx}">
      <div class="card-header">
        <div class="collapsible-header" onclick="toggleCard(${idx})">
          <span class="chevron">▶</span>
          <h3>${esc(game.default_name || '(이름 없음)')}</h3>
          <span class="badge ${isActive ? 'active' : 'inactive'}">${isActive ? '모니터링 중' : '비활성'}</span>
        </div>
        <button class="btn-danger" onclick="removeGame(${idx})">삭제</button>
      </div>

      <div class="collapsible-body" id="card-body-${idx}">
        <label>게임 ID (영문, 변경 불가)</label>
        <input type="text" value="${esc(game.id)}" onchange="setGameField(${idx},'id',null,this.value)">

        <label>기본 게임명 (영문)</label>
        <input type="text" value="${esc(game.default_name)}" onchange="setGameField(${idx},'default_name',null,this.value)">

        <label>모니터링 기간</label>
        <div style="display:flex;gap:10px;">
          <input type="date" value="${game.active_from}" style="width:50%;" onchange="setGameField(${idx},'active_from',null,this.value)">
          <input type="date" value="${game.active_until}" style="width:50%;" onchange="setGameField(${idx},'active_until',null,this.value)">
        </div>

        <label>스토어 선택</label>
        <div class="stores-row">
          <button class="store-toggle ${storeApple}"  id="toggle-apple-${idx}"  onclick="toggleStore(${idx},'apple')"> Apple App Store</button>
          <button class="store-toggle ${storeGoogle}" id="toggle-google-${idx}" onclick="toggleStore(${idx},'google')">▶ Google Play (향후 확장)</button>
        </div>

        <label>Apple 번들 ID</label>
        <input type="text" value="${esc(game.bundle_ids?.apple || '')}"
               placeholder="com.company.gamename"
               onchange="setBundleId(${idx},'apple',this.value)">

        <label>Google 패키지명</label>
        <input type="text" value="${esc(game.bundle_ids?.google || '')}"
               placeholder="com.company.gamename"
               onchange="setBundleId(${idx},'google',this.value)">

        <hr class="sep">
        <label style="font-weight:600;">현지화 게임명</label>
        <div class="names-grid">${nameFields}</div>
      </div>
    </div>`;
  }).join('');
}

function toggleCard(idx) {
  const header = document.querySelector(`#game-card-${idx} .collapsible-header`);
  const body   = document.getElementById(`card-body-${idx}`);
  header.classList.toggle('open');
  body.classList.toggle('open');
}

function setGameField(idx, field, subfield, value) {
  if (subfield) {
    if (!config.games[idx][field]) config.games[idx][field] = {};
    config.games[idx][field][subfield] = value;
  } else {
    config.games[idx][field] = value;
  }
}

function setBundleId(idx, store, value) {
  if (!config.games[idx].bundle_ids) config.games[idx].bundle_ids = {};
  config.games[idx].bundle_ids[store] = value;
}

function toggleStore(idx, store) {
  const game = config.games[idx];
  if (!game.stores) game.stores = [];
  const i = game.stores.indexOf(store);
  if (i === -1) game.stores.push(store);
  else          game.stores.splice(i, 1);
  const btn = document.getElementById(`toggle-${store}-${idx}`);
  if (btn) btn.classList.toggle('on', game.stores.includes(store));
}

function addGame() {
  const today = new Date().toISOString().slice(0, 10);
  const until = new Date(Date.now() + 90 * 86400000).toISOString().slice(0, 10);
  config.games.push({
    id: `game_${Date.now()}`,
    default_name: '새 게임',
    stores: ['apple'],
    active_from: today,
    active_until: until,
    names: {},
    bundle_ids: { apple: '', google: '' },
    recipients: { draft: [], final: [] },
  });
  renderGameCards();
  renderRecipientSelect();
  // 새 카드 열기
  const lastIdx = config.games.length - 1;
  toggleCard(lastIdx);
}

function removeGame(idx) {
  if (!confirm(`"${config.games[idx].default_name}" 게임을 삭제하시겠습니까?`)) return;
  config.games.splice(idx, 1);
  renderGameCards();
  renderRecipientSelect();
}

// ─────────────────────────────────────────────
// ══ 수신자 관리 탭 ══
// ─────────────────────────────────────────────
function renderRecipientSelect() {
  const sel = document.getElementById('recipient-game-select');
  if (!sel) return;
  sel.innerHTML = config.games.map((g, i) =>
    `<option value="${i}">${esc(g.default_name)}</option>`
  ).join('');
  renderRecipients();
}

function renderRecipients() {
  const sel = document.getElementById('recipient-game-select');
  const container = document.getElementById('recipient-card');
  if (!sel || !container || !config.games.length) {
    if (container) container.innerHTML = '<div class="card empty-msg">등록된 게임이 없습니다.</div>';
    return;
  }
  const idx = parseInt(sel.value, 10);
  const game = config.games[idx];

  function recipientRows(type) {
    return (game.recipients?.[type] || []).map((email, ri) => `
      <div class="recipient-row">
        <input type="email" value="${esc(email)}"
               onchange="setRecipient(${idx},'${type}',${ri},this.value)">
        <button class="btn-sm danger" onclick="removeRecipient(${idx},'${type}',${ri})">삭제</button>
      </div>`).join('');
  }

  container.innerHTML = `
    <div class="card">
      <h3 style="margin:0 0 16px 0;font-size:16px;">${esc(game.default_name)} — 수신자 설정</h3>

      <label style="font-weight:600;color:#0066cc;">📋 초안 수신자 (검토·승인 담당자)</label>
      <p style="font-size:12px;color:#888;margin:2px 0 8px 0;">
        초안 이메일을 받고 [이상 없음] 버튼으로 최종 발송을 승인하는 담당자
      </p>
      <div id="draft-recipients-${idx}" class="recipient-list">${recipientRows('draft')}</div>
      <button class="btn-add" onclick="addRecipient(${idx},'draft')">+ 초안 수신자 추가</button>

      <hr class="sep">

      <label style="font-weight:600;color:#27ae60;">📨 최종 수신자 (전체 공유 대상)</label>
      <p style="font-size:12px;color:#888;margin:2px 0 8px 0;">
        승인 후 최종 이메일을 받는 모든 담당자
      </p>
      <div id="final-recipients-${idx}" class="recipient-list">${recipientRows('final')}</div>
      <button class="btn-add" onclick="addRecipient(${idx},'final')">+ 최종 수신자 추가</button>
    </div>`;
}

function setRecipient(gameIdx, type, recIdx, value) {
  config.games[gameIdx].recipients[type][recIdx] = value;
}

function addRecipient(gameIdx, type) {
  if (!config.games[gameIdx].recipients) config.games[gameIdx].recipients = {};
  if (!config.games[gameIdx].recipients[type]) config.games[gameIdx].recipients[type] = [];
  config.games[gameIdx].recipients[type].push('');
  renderRecipients();
}

function removeRecipient(gameIdx, type, recIdx) {
  config.games[gameIdx].recipients[type].splice(recIdx, 1);
  renderRecipients();
}

// ─────────────────────────────────────────────
// ══ 이미지 추가 탭 ══
// ─────────────────────────────────────────────
function renderDriveStatus() {
  const folderEl = document.getElementById('drive-folder-path');
  if (folderEl && config) {
    const today = new Date().toISOString().slice(0, 10).replace(/-/g, '');
    folderEl.textContent = `${config.drive?.folder_path || ''}/${today}/`;
  }

  // Drive 연결 실제 확인은 백엔드(Python)가 담당
  // 여기서는 GitHub Actions 최신 실행 결과로 상태 표시
  const statusEl = document.getElementById('drive-status');
  if (statusEl) {
    statusEl.className = 'drive-status ok';
    statusEl.innerHTML = `<div class="dot"></div><span>GitHub Actions 실행 시 자동 확인됩니다</span>`;
  }
}

async function renderImageList() {
  const container = document.getElementById('image-list');
  if (!container) return;

  // GitHub Actions 최신 실행 로그에서 이미지 정보 가져오기 시도
  try {
    const logRes = await fetch('../logs/run_log.json');
    if (!logRes.ok) throw new Error('로그 없음');
    const log = await logRes.json();
    const today = new Date().toISOString().slice(0, 10);
    const todayLog = log[today];

    if (!todayLog) {
      container.innerHTML = '<div class="empty-msg">오늘 실행 기록이 없습니다.</div>';
      return;
    }

    const allFound = [];
    for (const [gameId, gameLog] of Object.entries(todayLog.games || {})) {
      const game = config?.games?.find(g => g.id === gameId);
      for (const r of (gameLog.found || [])) {
        allFound.push({ ...r, game_name: game?.default_name || gameId });
      }
    }

    if (!allFound.length) {
      container.innerHTML = '<div class="empty-msg">오늘 발견된 피쳐드 없음 (또는 수동 이미지 없음)</div>';
      return;
    }

    container.innerHTML = allFound.map(r => `
      <div class="image-item">
        <div class="img-name">${r.country?.toUpperCase()}_${r.tab}_${r.section}_${r.game_name}.png</div>
        <div class="img-meta">${r.game_name} · ${r.country?.toUpperCase()} · ${r.tab} · ${r.section}</div>
      </div>`).join('');
  } catch (e) {
    container.innerHTML = `
      <div class="empty-msg">
        실행 로그를 불러올 수 없습니다.<br>
        GitHub Actions 실행 후 <a href="../logs/run_log.json">run_log.json</a>이 생성됩니다.
      </div>`;
  }
}

// ─────────────────────────────────────────────
// ══ config.json 저장 (GitHub API) ══
// ─────────────────────────────────────────────
async function saveConfig() {
  const statusEl = document.getElementById('save-status');
  const token = document.getElementById('github-token')?.value?.trim();
  if (!token) {
    statusEl.textContent = '❌ GitHub Token 필요';
    statusEl.style.color = '#e74c3c';
    return;
  }
  if (!getOwner() || !getRepo()) {
    statusEl.textContent = '❌ config-editor.js에 REPO_OWNER / REPO_NAME 설정 필요';
    statusEl.style.color = '#e74c3c';
    return;
  }

  statusEl.textContent = '저장 중...';
  statusEl.style.color = '#888';

  try {
    const content = btoa(unescape(encodeURIComponent(
      JSON.stringify(config, null, 2)
    )));
    const body = {
      message: `[관리 페이지] config.json 업데이트 — ${new Date().toISOString().slice(0,10)}`,
      content,
    };
    if (configSha) body.sha = configSha;

    const res = await fetch(
      `https://api.github.com/repos/${getOwner()}/${getRepo()}/contents/${CONFIG_PATH_IN_REPO}`,
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
    const data = await res.json();
    configSha = data.content.sha;

    statusEl.textContent = '✅ 저장 완료!';
    statusEl.style.color = '#27ae60';
    setTimeout(() => { statusEl.textContent = ''; }, 3000);
  } catch (e) {
    statusEl.textContent = `❌ 저장 실패: ${e.message}`;
    statusEl.style.color = '#e74c3c';
  }
}

// ─────────────────────────────────────────────
// 유틸리티
// ─────────────────────────────────────────────
function esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
