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

// 국가 코드 → { 라벨, Google Play locale }
// 여기에 없는 국가는 UI 드롭다운에 표시되지 않음
const KNOWN_COUNTRIES = {
  kr: { label: '🇰🇷 한국 (KR)',        locale: 'ko' },
  us: { label: '🇺🇸 미국 (US)',        locale: 'en' },
  jp: { label: '🇯🇵 일본 (JP)',        locale: 'ja' },
  gb: { label: '🇬🇧 영국 (GB)',        locale: 'en' },
  de: { label: '🇩🇪 독일 (DE)',        locale: 'de' },
  fr: { label: '🇫🇷 프랑스 (FR)',      locale: 'fr' },
  au: { label: '🇦🇺 호주 (AU)',        locale: 'en' },
  ca: { label: '🇨🇦 캐나다 (CA)',      locale: 'en' },
  sg: { label: '🇸🇬 싱가포르 (SG)',    locale: 'en' },
  tw: { label: '🇹🇼 대만 (TW)',        locale: 'zh' },
  cn: { label: '🇨🇳 중국 (CN)',        locale: 'zh' },
  th: { label: '🇹🇭 태국 (TH)',        locale: 'th' },
  id: { label: '🇮🇩 인도네시아 (ID)', locale: 'id' },
  mx: { label: '🇲🇽 멕시코 (MX)',      locale: 'es' },
  br: { label: '🇧🇷 브라질 (BR)',      locale: 'pt' },
  ru: { label: '🇷🇺 러시아 (RU)',      locale: 'ru' },
  in: { label: '🇮🇳 인도 (IN)',        locale: 'en' },
  tr: { label: '🇹🇷 터키 (TR)',        locale: 'tr' },
  sa: { label: '🇸🇦 사우디 (SA)',      locale: 'ar' },
};

// Google Play 섹션 타입 정의
const SECTION_TYPES = {
  events:          { label: '🎉 이벤트',     en: 'Events happening now' },
  new_games:       { label: '🆕 신규 출시',   en: 'Newly launched games' },
  preregistration: { label: '📋 사전등록',    en: 'Pre-registration games' },
  be_first:        { label: '🥇 최초 플레이', en: 'Be the first to play' },
};

let config = null;
let configSha = null;  // GitHub API 업데이트용 파일 SHA

// ─────────────────────────────────────────────
// 초기화
// ─────────────────────────────────────────────
(async function init() {
  readUrlParams();
  // localStorage에서 저장된 토큰 복원
  const saved = localStorage.getItem('gh_token');
  if (saved) {
    const el = document.getElementById('github-token');
    if (el) el.value = saved;
  }
  await loadConfig();
  renderGameCards();
  renderRecipientSelect();
  renderDriveStatus();
  renderImageList();
})();

async function reloadConfig() {
  const statusEl = document.getElementById('save-status');
  statusEl.textContent = '불러오는 중...';
  statusEl.style.color = '#888';
  await loadConfig();
  renderGameCards();
  renderRecipientSelect();
  statusEl.textContent = '✅ 불러오기 완료';
  statusEl.style.color = '#27ae60';
  setTimeout(() => { statusEl.textContent = ''; }, 2000);
}

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
    email: { sender: '', app_password: '${GMAIL_APP_PASSWORD}' },
  };
}

function githubHeaders() {
  const token = document.getElementById('github-token')?.value?.trim() || '';
  if (token) localStorage.setItem('gh_token', token);
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

function _keepCardOpen(idx) {
  document.getElementById(`card-body-${idx}`)?.classList.add('open');
  document.querySelector(`#game-card-${idx} .collapsible-header`)?.classList.add('open');
}

function renderGameCards() {
  const container = document.getElementById('game-cards');
  if (!config || !config.games.length) {
    container.innerHTML = '<div class="empty-msg">등록된 게임이 없습니다. 오른쪽 상단 [+ 게임 추가]를 눌러주세요.</div>';
    return;
  }

  container.innerHTML = config.games.map((game, idx) => {
    const today = new Date().toISOString().slice(0, 10);
    const isActive = game.active_from <= today && today <= game.active_until;

    // stores가 배열(구버전)이면 객체로 간주하지 않음
    const stores = (!game.stores || Array.isArray(game.stores)) ? {} : game.stores;
    const storeApple  = stores.apple  ? 'on' : '';
    const storeGoogle = stores.google ? 'on' : '';

    const nameFields = Object.entries(COUNTRY_LABELS).map(([code, label]) => `
      <div>
        <label>${label}</label>
        <input type="text" value="${esc(game.names?.[code] || '')}"
               onchange="setGameField(${idx},'names','${code}',this.value)">
      </div>`).join('');

    // ── Apple 상세 설정 ──
    const appleSection = stores.apple ? (() => {
      const countries = stores.apple.countries || [];
      const tabs = stores.apple.tabs || [];

      const countryChips = countries.map(c => `
        <span class="chip chip-apple">${KNOWN_COUNTRIES[c]?.label || c}
          <button onclick="removeGameCountry(${idx},'apple','${c}')">×</button>
        </span>`).join('');

      const tabChips = tabs.map(t => `
        <span class="chip chip-tab">${t}
          <button onclick="removeGameTab(${idx},'${t}')">×</button>
        </span>`).join('');

      const countryOptions = Object.entries(KNOWN_COUNTRIES)
        .filter(([c]) => !countries.includes(c))
        .map(([c, i]) => `<option value="${c}">${i.label}</option>`).join('');

      return `
        <div class="store-detail-section">
          <p style="font-size:12px;font-weight:700;color:#0066cc;margin:12px 0 6px 0;">🍎 Apple 모니터링 국가</p>
          <div>
            ${countryChips || '<span style="color:#aaa;font-size:12px;">없음</span>'}
            <span style="display:inline-flex;align-items:center;gap:4px;margin:3px;">
              <select id="apple-country-sel-${idx}"
                      style="padding:4px 8px;border:1px solid #ddd;border-radius:4px;font-size:13px;">
                <option value="">국가 선택</option>
                ${countryOptions}
              </select>
              <button class="btn-sm primary" onclick="addGameCountry(${idx},'apple')">+ 추가</button>
            </span>
          </div>

          <p style="font-size:12px;font-weight:700;color:#0066cc;margin:12px 0 6px 0;">🍎 Apple 확인 탭</p>
          <div>
            ${tabChips || '<span style="color:#aaa;font-size:12px;">없음</span>'}
            ${!tabs.includes('today') ? `<button class="btn-sm" onclick="addGameTab(${idx},'today')">+ today</button>` : ''}
            ${!tabs.includes('games') ? `<button class="btn-sm" onclick="addGameTab(${idx},'games')">+ games</button>` : ''}
          </div>
        </div>`;
    })() : '';

    // ── Google Play 상세 설정 ──
    const googleSection = stores.google ? (() => {
      const countries = stores.google.countries || [];
      const selectedSections = stores.google.sections || Object.keys(SECTION_TYPES);

      const countryChips = countries.map(c => `
        <span class="chip chip-google">${KNOWN_COUNTRIES[c]?.label || c}
          <button onclick="removeGameCountry(${idx},'google','${c}')">×</button>
        </span>`).join('');

      const countryOptions = Object.entries(KNOWN_COUNTRIES)
        .filter(([c]) => !countries.includes(c))
        .map(([c, i]) => `<option value="${c}">${i.label}</option>`).join('');

      const sectionCheckboxes = Object.entries(SECTION_TYPES).map(([key, info]) => `
        <label style="display:inline-flex;align-items:center;gap:5px;margin:3px 8px 3px 0;font-size:13px;cursor:pointer;">
          <input type="checkbox" ${selectedSections.includes(key) ? 'checked' : ''}
                 onchange="toggleGameSection(${idx},'${key}',this.checked)">
          ${info.label}
          <span style="color:#aaa;font-size:11px;">(${info.en})</span>
        </label>`).join('');

      return `
        <div class="store-detail-section">
          <p style="font-size:12px;font-weight:700;color:#01875f;margin:12px 0 6px 0;">🎮 Google Play 모니터링 국가</p>
          <div>
            ${countryChips || '<span style="color:#aaa;font-size:12px;">없음</span>'}
            <span style="display:inline-flex;align-items:center;gap:4px;margin:3px;">
              <select id="google-country-sel-${idx}"
                      style="padding:4px 8px;border:1px solid #ddd;border-radius:4px;font-size:13px;">
                <option value="">국가 선택</option>
                ${countryOptions}
              </select>
              <button class="btn-sm" style="background:#01875f;color:#fff;border-color:#01875f;"
                      onclick="addGameCountry(${idx},'google')">+ 추가</button>
            </span>
          </div>

          <p style="font-size:12px;font-weight:700;color:#01875f;margin:12px 0 6px 0;">🎮 모니터링 섹션 선택</p>
          <div style="line-height:2;">${sectionCheckboxes}</div>
        </div>`;
    })() : '';

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
          <button class="store-toggle ${storeApple}"  id="toggle-apple-${idx}"  onclick="toggleStore(${idx},'apple')">🍎 Apple App Store</button>
          <button class="store-toggle ${storeGoogle}" id="toggle-google-${idx}" onclick="toggleStore(${idx},'google')">🎮 Google Play</button>
        </div>

        ${appleSection}
        ${googleSection}

        <hr class="sep">

        <label>Apple 앱스토어 숫자 ID (예: 1234567890)</label>
        <input type="text" value="${esc(game.bundle_ids?.apple || '')}"
               placeholder="1234567890 (앱스토어 URL의 /id 뒤 숫자)"
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
  // 구버전 배열 형식도 안전하게 처리
  if (!game.stores || Array.isArray(game.stores)) game.stores = {};
  if (game.stores[store]) {
    delete game.stores[store];
  } else {
    game.stores[store] = store === 'apple'
      ? { countries: [], tabs: [] }
      : { countries: [], sections: Object.keys(SECTION_TYPES) };
  }
  renderGameCards();
  _keepCardOpen(idx);
}

// ── 게임별 국가 추가/삭제 ──
function addGameCountry(idx, store) {
  const sel = document.getElementById(`${store}-country-sel-${idx}`);
  const val = sel?.value;
  if (!val) return;
  const game = config.games[idx];
  if (!game.stores || Array.isArray(game.stores)) game.stores = {};
  if (!game.stores[store]) game.stores[store] = store === 'apple' ? { countries: [], tabs: [] } : { countries: [], sections: Object.keys(SECTION_TYPES) };
  if (!game.stores[store].countries) game.stores[store].countries = [];
  if (!game.stores[store].countries.includes(val)) {
    game.stores[store].countries.push(val);
    // Google: locale_map 자동 업데이트
    if (store === 'google') {
      if (!config.google_play) config.google_play = {};
      if (!config.google_play.locale_map) config.google_play.locale_map = {};
      config.google_play.locale_map[val] = KNOWN_COUNTRIES[val]?.locale || 'en';
    }
    renderGameCards();
    _keepCardOpen(idx);
  }
}

function removeGameCountry(idx, store, code) {
  const arr = config.games[idx].stores?.[store]?.countries;
  if (!arr) return;
  const i = arr.indexOf(code);
  if (i !== -1) arr.splice(i, 1);
  // Google: 다른 게임에서도 안 쓰면 locale_map에서 제거
  if (store === 'google' && config.google_play?.locale_map) {
    const usedElsewhere = config.games.some(g =>
      g.stores?.google?.countries?.includes(code)
    );
    if (!usedElsewhere) delete config.google_play.locale_map[code];
  }
  renderGameCards();
  _keepCardOpen(idx);
}

// ── 게임별 Apple 탭 추가/삭제 ──
function addGameTab(idx, tab) {
  const game = config.games[idx];
  if (!game.stores?.apple) return;
  if (!game.stores.apple.tabs) game.stores.apple.tabs = [];
  if (!game.stores.apple.tabs.includes(tab)) {
    game.stores.apple.tabs.push(tab);
    renderGameCards();
    _keepCardOpen(idx);
  }
}

function removeGameTab(idx, tab) {
  const arr = config.games[idx].stores?.apple?.tabs;
  if (!arr) return;
  const i = arr.indexOf(tab);
  if (i !== -1) arr.splice(i, 1);
  renderGameCards();
  _keepCardOpen(idx);
}

// ── 게임별 Google 섹션 토글 ──
function toggleGameSection(idx, key, checked) {
  const game = config.games[idx];
  if (!game.stores?.google) return;
  if (!game.stores.google.sections) game.stores.google.sections = Object.keys(SECTION_TYPES);
  const arr = game.stores.google.sections;
  const i = arr.indexOf(key);
  if (checked && i === -1) arr.push(key);
  else if (!checked && i !== -1) arr.splice(i, 1);
  // 재렌더 없이 상태만 업데이트 (체크박스 자체가 이미 변경됨)
}

function addGame() {
  const today = new Date().toISOString().slice(0, 10);
  const until = new Date(Date.now() + 90 * 86400000).toISOString().slice(0, 10);
  config.games.push({
    id: `game_${Date.now()}`,
    default_name: '새 게임',
    stores: { apple: { countries: [], tabs: [] } },
    active_from: today,
    active_until: until,
    names: {},
    bundle_ids: { apple: '', google: '' },
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
  renderRecipients();
}

function renderRecipients() {
  const container = document.getElementById('recipient-card');
  if (!container) return;

  const owner = getOwner();
  const repo  = getRepo();
  const secretsUrl = `https://github.com/${owner}/${repo}/settings/secrets/actions`;

  const gameIds = (config?.games || []).map(g => g.id);
  const exampleJson = JSON.stringify(
    Object.fromEntries(gameIds.map(id => [id, { draft: ['담당자@company.com'], final: ['전체@company.com'] }])),
    null, 2
  );

  container.innerHTML = `
    <div class="card">
      <h3 style="margin:0 0 8px 0;font-size:16px;">📧 수신자 관리</h3>
      <p style="font-size:14px;color:#555;line-height:1.7;margin:0 0 16px 0;">
        수신자 이메일은 보안을 위해 <strong>GitHub Secrets</strong>에서 관리합니다.<br>
        아래 버튼을 눌러 <code>RECIPIENTS_CONFIG</code> Secret을 추가하거나 수정하세요.
      </p>
      <a href="${secretsUrl}" target="_blank"
         style="display:inline-block;padding:10px 20px;background:#0066cc;color:#fff;
                text-decoration:none;border-radius:6px;font-weight:600;font-size:14px;margin-bottom:20px;">
        🔐 GitHub Secrets 열기
      </a>

      <hr class="sep">

      <p style="font-size:13px;font-weight:600;margin:0 0 8px 0;">Secret 이름</p>
      <code style="background:#f5f5f5;padding:6px 12px;border-radius:4px;font-size:14px;display:block;margin-bottom:16px;">
        RECIPIENTS_CONFIG
      </code>

      <p style="font-size:13px;font-weight:600;margin:0 0 8px 0;">Secret 값 형식 (JSON)</p>
      <pre style="background:#f5f5f5;padding:12px 16px;border-radius:6px;font-size:13px;overflow-x:auto;margin:0 0 16px 0;">${esc(exampleJson)}</pre>

      <div style="background:#fffbf0;border:1px solid #f0c040;border-radius:6px;padding:12px 16px;font-size:13px;color:#856404;">
        💡 <strong>게임 추가 시</strong>: Secret 값의 JSON에 새 게임 항목을 추가하면 됩니다.<br>
        💡 <strong>key</strong>는 config.json의 <code>game.id</code>와 동일해야 합니다.
      </div>
    </div>`;
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
      // 신규 구조: apple_found / google_found 분리, 구버전 호환: found
      const appleFound  = gameLog.apple_found  || [];
      const googleFound = gameLog.google_found || [];
      const combined    = gameLog.found ? gameLog.found : [...appleFound, ...googleFound];
      for (const r of combined) {
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
