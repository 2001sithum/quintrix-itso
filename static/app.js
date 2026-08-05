// Quintrix ITSO — vanilla JS console. No framework.
const S = { token: localStorage.getItem('q_token'), role: null, user: null };
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[<>&]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c]));

async function api(path, opts = {}) {
  opts.headers = Object.assign({ 'x-session': S.token || '' }, opts.headers || {});
  const r = await fetch('/api' + path, opts);
  if (r.status === 401) { logout(); throw new Error('unauthorized'); }
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if (!r.ok) throw new Error(data.detail || data || 'error');
  return data;
}
function toast(msg) {
  const t = document.createElement('div'); t.className = 'toast'; t.textContent = msg;
  document.body.appendChild(t); setTimeout(() => t.remove(), 3500);
}

// ---------- AUTH ----------
$$('.tab').forEach(b => b.onclick = () => {
  $$('.tab').forEach(x => x.classList.toggle('active', x === b));
  $('#login-form').classList.toggle('hidden', b.dataset.auth !== 'login');
  $('#register-form').classList.toggle('hidden', b.dataset.auth !== 'register');
});

$('#login-form').onsubmit = async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  try {
    const d = await api('/login', { method: 'POST', body: fd });
    S.token = d.token; S.role = d.role; S.user = d.username;
    localStorage.setItem('q_token', d.token);
    boot();
  } catch (err) { $('#auth-msg').style.color = 'var(--hi)'; $('#auth-msg').textContent = err.message; }
};

$('#reg-pass').addEventListener('input', async e => {
  const fd = new FormData(); fd.append('password', e.target.value);
  if (!e.target.value) { $('#str-bar').style.width = '0'; $('#str-label').textContent = 'Enter a password'; return; }
  const d = await api('/password-strength', { method: 'POST', body: fd });
  const map = { red: 'var(--hi)', yellow: 'var(--warn)', green: 'var(--ok)' };
  $('#str-bar').style.width = (d.score / 5 * 100) + '%';
  $('#str-bar').style.background = map[d.color];
  $('#str-label').textContent = `Strength: ${d.level}`;
  $('#str-label').style.color = map[d.color];
});

$('#register-form').onsubmit = async e => {
  e.preventDefault();
  try {
    const d = await api('/register', { method: 'POST', body: new FormData(e.target) });
    $('#auth-msg').style.color = 'var(--ok)'; $('#auth-msg').textContent = d.message;
    e.target.reset(); $('#str-bar').style.width = '0';
  } catch (err) { $('#auth-msg').style.color = 'var(--hi)'; $('#auth-msg').textContent = err.message; }
};

function logout() {
  api('/logout', { method: 'POST' }).catch(() => {});
  S.token = null; localStorage.removeItem('q_token');
  $('#console').classList.add('hidden'); $('#auth').classList.remove('hidden');
}
$('#logout').onclick = logout;

// ---------- NAV / ROUTING ----------
const NAV = [
  { id: 'dashboard', label: 'Dashboard', roles: ['Administrator', 'SecurityOperator', 'User'] },
  { id: 'projects', label: 'Projects', roles: ['Administrator', 'SecurityOperator', 'User'] },
  { id: 'search', label: 'Search', roles: ['Administrator', 'SecurityOperator', 'User'] },
  { id: 'alerts', label: 'Alerts', roles: ['Administrator', 'SecurityOperator'], dot: true },
  { id: 'config', label: 'Configuration', roles: ['Administrator'] },
  { id: 'logs', label: 'Logs', roles: ['Administrator'] },
  { id: 'users', label: 'Users', roles: ['Administrator'] },
  { id: 'docs', label: 'Docs', roles: ['Administrator', 'SecurityOperator', 'User'] },
];
let current = 'dashboard';

function renderNav() {
  $('#nav').innerHTML = NAV.filter(n => n.roles.includes(S.role)).map(n =>
    `<button class="navbtn ${n.id === current ? 'active' : ''}" data-nav="${n.id}">
       ${n.dot ? '<span class="dot" id="alert-dot" style="display:none"></span>' : ''}${n.label}
     </button>`).join('');
  $$('#nav .navbtn').forEach(b => b.onclick = () => go(b.dataset.nav));
}
function go(id) { current = id; renderNav(); VIEWS[id](); }

async function boot() {
  try { const m = await api('/me'); S.role = m.role; S.user = m.username;
    $('#who').textContent = `${m.full_name || m.username} · ${m.role}`; }
  catch { return logout(); }
  $('#auth').classList.add('hidden'); $('#console').classList.remove('hidden');
  renderNav(); go('dashboard'); pollAlerts();
}

// ---------- VIEWS ----------
const V = $('#view');
const VIEWS = {};

VIEWS.dashboard = async () => {
  V.innerHTML = `<h2>Analytics Dashboard</h2>
    <p class="sub">Real-time intelligence across all projects, tiers, and storage.</p>
    <div class="row" id="stats"></div>
    <div class="grid2" style="margin-top:16px">
      <div class="card"><h3 style="margin:0 0 12px;font-size:14px">Storage by tier</h3><div id="tierbox"></div></div>
      <div class="card"><h3 style="margin:0 0 12px;font-size:14px">Threat distribution</h3><div id="threatbox"></div></div>
    </div>
    <div class="card" style="margin-top:14px"><h3 style="margin:0 0 12px;font-size:14px">Recommendations</h3><div id="recs"></div></div>`;
  const a = await api('/analytics');
  const st = [
    ['Projects', Object.values(a.projects).reduce((x, y) => x + y, 0)],
    ['Segments', a.segments_total],
    ['Motion segments', a.segments_motion],
    ['Events', a.events],
    ['Open alerts', a.open_alerts, a.open_alerts ? 'var(--hi)' : 'var(--ok)'],
    ['Storage saved', a.storage.storage_savings_percent + '%', 'var(--ok)'],
    ['Compute', a.device.toUpperCase(), 'var(--info)'],
  ];
  $('#stats').innerHTML = st.map(([k, v, c]) =>
    `<div class="card stat"><div class="k">${k}</div><div class="v" style="color:${c || 'var(--text)'}">${v}</div></div>`).join('');
  const tiers = a.tiers, tt = Object.values(tiers).reduce((x, y) => x + y, 0) || 1;
  $('#tierbox').innerHTML = ['HIGH', 'MEDIUM', 'LOW'].map(t => {
    const n = tiers[t] || 0;
    return `<div style="margin-bottom:10px"><div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px">
      <span class="pill ${t}">${t}</span><span>${n}</span></div>
      <div class="bar"><i style="width:${n / tt * 100}%;background:var(--${t === 'HIGH' ? 'hi' : t === 'MEDIUM' ? 'warn' : 'ok'})"></i></div></div>`;
  }).join('');
  const th = a.threats, tn = Object.values(th).reduce((x, y) => x + y, 0) || 1;
  $('#threatbox').innerHTML = ['high', 'medium', 'low'].map(l => {
    const n = th[l] || 0, col = l === 'high' ? 'hi' : l === 'medium' ? 'warn' : 'ok';
    return `<div style="margin-bottom:10px"><div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px">
      <span style="text-transform:uppercase;color:var(--${col})">${l}</span><span>${n}</span></div>
      <div class="bar"><i style="width:${n / tn * 100}%;background:var(--${col})"></i></div></div>`;
  }).join('');
  const recs = await api('/recommendations');
  $('#recs').innerHTML = recs.length ? recs.map(r =>
    `<div style="padding:8px 0;border-bottom:1px solid var(--line);font-size:13px">
       <span class="pill LOW" style="margin-right:8px">${esc(r.kind)}</span>${esc(r.message)}</div>`).join('')
    : '<div style="color:var(--dimmer);font-size:13px">No recommendations yet.</div>';
};

VIEWS.projects = async () => {
  V.innerHTML = `<h2>Projects</h2><p class="sub">Upload surveillance footage — the 5-stage ITSO pipeline runs automatically.</p>
    <label class="dropzone" id="dz">
      <input type="file" id="file" accept=".mp4,.avi,.mov,.mkv" hidden>
      <div id="dztext"><b>Upload footage</b><br><span style="color:var(--dim);font-size:12px">MP4 · AVI · MOV · MKV — max 500MB</span></div>
    </label>
    <div id="plist" style="margin-top:18px"></div>
    <div id="pdetail"></div>`;
  const dz = $('#dz'), fi = $('#file');
  dz.ondragover = e => { e.preventDefault(); dz.classList.add('drag'); };
  dz.ondragleave = () => dz.classList.remove('drag');
  dz.ondrop = e => { e.preventDefault(); dz.classList.remove('drag'); if (e.dataTransfer.files[0]) doUpload(e.dataTransfer.files[0]); };
  fi.onchange = () => fi.files[0] && doUpload(fi.files[0]);
  loadProjects();
};

async function doUpload(file) {
  $('#dztext').innerHTML = '<span class="spin">◐</span> Uploading…';
  const fd = new FormData(); fd.append('file', file);
  try {
    await api('/projects', { method: 'POST', body: fd });
    toast('Upload accepted — pipeline started');
  } catch (e) { toast('Upload failed: ' + e.message); }
  $('#dztext').innerHTML = '<b>Upload footage</b><br><span style="color:var(--dim);font-size:12px">MP4 · AVI · MOV · MKV — max 500MB</span>';
  loadProjects();
}

async function loadProjects() {
  const rows = await api('/projects');
  if (!$('#plist')) return;
  const canManage = ['Administrator', 'SecurityOperator'].includes(S.role);
  $('#plist').innerHTML = rows.length ? `<table><thead><tr>
    <th>Project</th><th>File</th><th>Status</th><th>Segments</th><th>Saved</th><th></th></tr></thead><tbody>${
    rows.map(p => {
      const saved = p.original_bytes ? Math.round((1 - p.stored_bytes / p.original_bytes) * 100) : 0;
      return `<tr>
        <td>
          <span class="pname" id="pname-${p.id}">${esc(p.name || p.filename)}</span>
          ${canManage ? `<button class="btn sm ghost-icon" title="Rename" onclick="renameProject('${p.id}')">✎</button>` : ''}
        </td>
        <td style="color:var(--dim);font-size:12px">${esc(p.filename)}</td>
        <td><span class="pill ${p.status}">${p.status}</span></td>
        <td>${p.segs}</td>
        <td>${saved > 0 ? saved + '%' : '—'}</td>
        <td><button class="btn sm" onclick="openProject('${p.id}')">Open</button>
        ${canManage ? `<button class="btn sm danger" onclick="delProject('${p.id}')">Delete</button>` : ''}</td>
      </tr>`;
    }).join('')}</tbody></table>` : '<div class="empty">No projects yet. Upload footage to begin.</div>';
  if (rows.some(p => p.status === 'processing')) setTimeout(loadProjects, 2500);
}

window.renameProject = async id => {
  const el = $('#pname-' + id);
  const current = el.textContent;
  el.outerHTML = `<span id="pname-${id}">
    <input id="pname-input-${id}" value="${esc(current)}" style="width:200px;padding:4px 8px;
      background:var(--bg);border:1px solid var(--line2);border-radius:6px;color:var(--text);font-size:13px">
    <button class="btn sm" onclick="saveProjectName('${id}')">Save</button>
  </span>`;
  const input = $('#pname-input-' + id);
  input.focus(); input.select();
  input.onkeydown = e => { if (e.key === 'Enter') saveProjectName(id); if (e.key === 'Escape') loadProjects(); };
};
window.saveProjectName = async id => {
  const name = $('#pname-input-' + id).value.trim();
  if (!name) { toast('Name cannot be empty'); return; }
  const fd = new FormData(); fd.append('name', name);
  try { await api('/projects/' + id, { method: 'PATCH', body: fd }); toast('Project renamed'); }
  catch (e) { toast(e.message); }
  loadProjects();
};
window.delProject = async id => {
  if (!confirm('Delete project and all segments/files?')) return;
  await api('/projects/' + id, { method: 'DELETE' }); toast('Project deleted'); loadProjects(); $('#pdetail').innerHTML = '';
};

let _curProjectId = null;
window.openProject = async id => {
  _curProjectId = id;
  const d = await api('/projects/' + id);
  const p = d.project;
  const isProcessing = p.status === 'processing';
  $('#pdetail').innerHTML = `<div class="card" style="margin-top:14px">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:6px">
      <h3 style="margin:0">${esc(p.name || p.filename)}</h3>
      <span class="pill ${p.status}">${p.status}</span></div>
    <p class="sub">${esc(p.filename)} · <span style="font-family:var(--mono)">${p.id.slice(0, 12)}</span> · ${(p.duration || 0).toFixed(1)}s · ${p.width}×${p.height} · ${(p.fps || 0).toFixed(0)}fps</p>
    ${isProcessing ? pipelineTrackerHTML() : ''}
    ${!isProcessing && d.segments.length ? analysisShellHTML(d.segments, p.duration) : ''}
    ${!isProcessing && !d.segments.length ? '<div class="empty">No segments.</div>' : ''}
  </div>`;
  if (isProcessing) startPipelineTracker(id);
  else if (d.segments.length) selectSegment(0);
};

// ---------- video analysis: interactive timeline scrubber + inspector (FR32/38/39) ----------
let _curSegments = [];
function fmtTime(sec) { sec = Math.max(0, Math.round(sec)); return Math.floor(sec / 60) + ':' + String(sec % 60).padStart(2, '0'); }
function analysisShellHTML(segments, duration) {
  _curSegments = segments;
  const total = duration || segments[segments.length - 1]?.end_time || 1;
  return `<div class="analysis-shell" style="margin-top:16px">
    <div class="scrub">
      <div class="scrub-head">
        <h3 style="margin:0;font-size:13px">Video Analysis Timeline</h3>
        <div class="scrub-legend">
          <span><span class="pill HIGH">HIGH</span> lossless</span>
          <span><span class="pill MEDIUM">MEDIUM</span> re-encoded</span>
          <span><span class="pill LOW">LOW</span> keyframe / static</span>
        </div>
      </div>
      <div class="scrub-track" id="scrub-track">${segments.map((s, i) => {
        const w = Math.max(0.4, (s.end_time - s.start_time) / total * 100).toFixed(3);
        const col = s.tier === 'HIGH' ? 'hi' : s.tier === 'MEDIUM' ? 'warn' : s.motion ? 'ok' : 'dimmer';
        return `<div class="scrub-seg" data-idx="${i}" style="width:${w}%;background:var(--${col});--ssig:${Math.round((s.ssig || 0) * 100)}%"
          title="#${String(s.idx).padStart(3, '0')} · ${s.tier || 'static'} · Ssig ${s.ssig.toFixed(2)}"
          onclick="selectSegment(${i})"><div class="sb"></div></div>`;
      }).join('')}</div>
      <div class="scrub-ruler"><span>0:00</span><span>${fmtTime(total)}</span></div>
    </div>
    <div class="inspector" id="inspector"></div>
  </div>`;
}
window.selectSegment = i => {
  const s = _curSegments[i];
  if (!s) return;
  $$('.scrub-seg').forEach(el => el.classList.toggle('selected', +el.dataset.idx === i));
  const insp = $('#inspector');
  if (insp) insp.innerHTML = inspectorHTML(s);
};
function inspectorHTML(s) {
  const col = s.threat_level === 'high' ? 'hi' : s.threat_level === 'medium' ? 'warn' : 'ok';
  const ssigCol = s.ssig >= 0.7 ? 'hi' : s.ssig >= 0.45 ? 'warn' : 'ok';
  const canManage = ['Administrator', 'SecurityOperator'].includes(S.role);
  return `<div class="insp-head">
      ${s.thumb ? `<img class="insp-thumb" src="/api/thumb/${s.id}" onerror="this.style.opacity=.2">` : '<div class="insp-thumb"></div>'}
      <div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px">
          <b style="font-size:17px;font-family:var(--mono)">#${String(s.idx).padStart(3, '0')}</b>
          <span class="pill ${s.tier}">${s.tier || 'static'}</span>
          <span class="pill" style="color:var(--${col});background:transparent;border-color:var(--${col})">THREAT ${s.threat_level}</span>
          ${!s.motion ? '<span class="chip">static · skipped</span>' : ''}
        </div>
        <span style="font-size:12px;color:var(--dim)">${s.start_time.toFixed(0)}s–${s.end_time.toFixed(0)}s · ${new Date(s.ts).toLocaleString()}</span>
      </div>
    </div>
    ${s.motion ? `
    <div class="insp-grid">
      <div class="insp-stat"><div class="k">Significance</div><div class="v" style="color:var(--${ssigCol})">${s.ssig.toFixed(2)}</div></div>
      <div class="insp-stat"><div class="k">Hazard</div><div class="v" style="text-transform:capitalize">${esc(s.hazard_level || 'none')}</div></div>
      <div class="insp-stat"><div class="k">Objects</div><div class="v">${s.objects.length}</div></div>
      <div class="insp-stat"><div class="k">Actions</div><div class="v">${s.actions.length}</div></div>
    </div>
    <div style="margin-bottom:14px">
      ${s.objects.map(o => `<span class="chip">${esc(o.label)} · ${(o.confidence * 100).toFixed(0)}%</span>`).join('') || '<span class="sub" style="margin:0">No objects detected</span>'}
      ${s.actions.map(a => `<span class="chip act">▷ ${esc(a.action)} · ${(a.confidence * 100).toFixed(0)}%</span>`).join('')}
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <button class="btn" onclick="playSeg('${s.id}')">▶ Playback</button>
      <button class="btn sm" onclick="viewMeta('${s.id}')">Full metadata</button>
      ${canManage ? `
      <select class="btn sm" onchange="setTierInsp('${s.id}',this.value)"><option value="">tier…</option><option>HIGH</option><option>MEDIUM</option><option>LOW</option></select>
      <button class="btn sm danger" onclick="delSegmentInsp('${s.id}')">Delete</button>` : ''}
    </div>` : '<p class="sub" style="margin:0">No motion detected — auto-tiered LOW and skipped deep analysis.</p>'}`;
}
window.setTierInsp = async (id, tier) => {
  if (!tier) return;
  const fd = new FormData(); fd.append('tier', tier);
  await api('/segments/' + id + '/tier', { method: 'POST', body: fd });
  toast('Tier set to ' + tier);
  if (_curProjectId) openProject(_curProjectId);
};
window.delSegmentInsp = async id => {
  if (!confirm('Delete this segment and its files? This cannot be undone.')) return;
  await api('/segments/' + id, { method: 'DELETE' });
  toast('Segment deleted');
  if (_curProjectId) openProject(_curProjectId);
};

// ---------- live animated pipeline tracker (driven by process logs) ----------
function pipelineTrackerHTML() {
  return `<div class="pipeline-tracker">
      <div class="pt-step active" data-pt="ingest"><div class="pt-dot"></div><span>Ingest</span></div>
      <div class="pt-line" data-pt-line="1"></div>
      <div class="pt-step" data-pt="analyze"><div class="pt-dot"></div><span>Analyze &amp; Score</span></div>
      <div class="pt-line" data-pt-line="2"></div>
      <div class="pt-step" data-pt="done"><div class="pt-dot"></div><span>Complete</span></div>
    </div>
    <div class="bar" style="margin-bottom:6px"><i id="pt-bar" style="width:0%;background:var(--info)"></i></div>
    <div class="pt-detail" id="pt-detail"><span class="spin">◐</span> Waiting for pipeline…</div>`;
}
function _ptStep(name, state) {
  const el = $(`.pt-step[data-pt="${name}"]`);
  if (!el) return;
  el.classList.remove('active', 'done');
  if (state) el.classList.add(state);
}
function _ptLine(n, filled) {
  $(`.pt-line[data-pt-line="${n}"]`)?.classList.toggle('filled', filled);
}
function _ptApply(msg) {
  const detail = $('#pt-detail');
  if (msg.startsWith('Stage 1/5 Ingestion')) {
    if (detail) detail.innerHTML = `<span class="spin">◐</span> ${esc(msg.replace('Stage 1/5 Ingestion: ', ''))}`;
  } else if (msg.startsWith('Stage 1/5 Segmentation')) {
    _ptStep('ingest', 'done'); _ptLine(1, true); _ptStep('analyze', 'active');
    if (detail) detail.innerHTML = `<span class="spin">◐</span> ${esc(msg.replace('Stage 1/5 Segmentation: ', ''))}`;
  } else if (/^Stage 2(-5)? Segment/.test(msg)) {
    _ptStep('analyze', 'active');
    const m = msg.match(/Segment (\d+)\/(\d+)/);
    if (m) { const bar = $('#pt-bar'); if (bar) bar.style.width = (m[1] / m[2] * 100) + '%'; }
    if (detail) detail.innerHTML = `<span class="spin">◐</span> ${esc(msg.replace(/^Stage [\d-]+ /, ''))}`;
  } else if (msg.startsWith('Stage 5/5 Complete')) {
    _ptStep('analyze', 'done'); _ptLine(2, true); _ptStep('done', 'done');
    const bar = $('#pt-bar'); if (bar) bar.style.width = '100%';
    if (detail) detail.innerHTML = `✓ ${esc(msg.replace('Stage 5/5 Complete: ', ''))}`;
    return true; // signals completion
  }
  return false;
}
let _ptSource = null;
async function startPipelineTracker(pid) {
  if (_ptSource) { _ptSource.close(); _ptSource = null; }
  // catch up on any stage messages that already happened before we connected
  try {
    const rows = await api('/logs?type=processing&limit=200');
    rows.reverse().forEach(r => {
      let ctx = {}; try { ctx = JSON.parse(r.context || '{}'); } catch { ctx = r.context || {}; }
      if (ctx.project === pid) _ptApply(r.message);
    });
  } catch { /* non-admin role: no /logs access, live SSE still works */ }
  const es = _ptSource = new EventSource('/api/logs/stream?session=' + encodeURIComponent(S.token));
  es.onmessage = e => {
    let r; try { r = JSON.parse(e.data); } catch { return; }
    let ctx = {}; try { ctx = JSON.parse(r.context || '{}'); } catch { ctx = r.context || {}; }
    if (ctx.project !== pid) return;
    if (_ptApply(r.message)) {
      es.close(); if (_ptSource === es) _ptSource = null;
      setTimeout(() => openProject(pid), 900);
    }
  };
  es.onerror = () => { es.close(); if (_ptSource === es) _ptSource = null; };
}

function segCard(s) {
  const col = s.threat_level === 'high' ? 'hi' : s.threat_level === 'medium' ? 'warn' : 'ok';
  const ssigCol = s.ssig >= 0.7 ? 'hi' : s.ssig >= 0.45 ? 'warn' : 'ok';
  return `<div class="seg" id="seg-${s.id}" style="border-left:3px solid var(--${col})">
    ${s.thumb ? `<img class="thumb" src="/api/thumb/${s.id}" onerror="this.style.opacity=.2">` : '<div class="thumb"></div>'}
    <div class="body">
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <b style="font-family:var(--mono)">#${String(s.idx).padStart(3, '0')}</b>
        <span style="font-size:12px;color:var(--dim)">${s.start_time?.toFixed(0)}s–${s.end_time?.toFixed(0)}s · ${new Date(s.ts).toLocaleString()}</span>
        ${!s.motion ? '<span class="chip">static · skipped</span>' : ''}
        <span style="margin-left:auto" class="pill ${s.tier}">${s.tier || '—'}</span>
        <span class="pill" style="color:var(--${col});background:transparent;border:1px solid var(--${col})">THREAT ${s.threat_level}</span>
      </div>
      ${s.motion ? `<div style="display:flex;align-items:center;gap:10px;margin-top:10px">
        <span style="font-size:11px;color:var(--dim);width:40px">Ssig</span>
        <div class="bar" style="flex:1"><i style="width:${s.ssig * 100}%;background:var(--${ssigCol})"></i></div>
        <b style="font-family:var(--mono);width:42px;text-align:right">${s.ssig.toFixed(2)}</b></div>
        <div style="margin-top:8px">
          ${s.objects.slice(0, 6).map(o => `<span class="chip">${esc(o.label)} · ${(o.confidence * 100).toFixed(0)}%</span>`).join('')}
          ${s.actions.slice(0, 2).map(a => `<span class="chip act">▷ ${esc(a.action)} · ${(a.confidence * 100).toFixed(0)}%</span>`).join('')}
        </div>
        <div style="margin-top:8px">
          <button class="btn sm" onclick="playSeg('${s.id}')">Playback</button>
          <button class="btn sm" onclick="viewMeta('${s.id}')">Metadata</button>
          ${['Administrator', 'SecurityOperator'].includes(S.role) ? `
          <select class="btn sm" onchange="setTier('${s.id}',this.value)" style="margin-left:4px">
            <option value="">tier…</option><option>HIGH</option><option>MEDIUM</option><option>LOW</option></select>
          <button class="btn sm danger" onclick="delSegment('${s.id}',this)" style="margin-left:4px">Delete</button>` : ''}
        </div>` : ''}
    </div></div>`;
}

let _blobUrl = null;
function closeModal() {
  $('#modal-backdrop').classList.add('hidden');
  if (_blobUrl) { URL.revokeObjectURL(_blobUrl); _blobUrl = null; }
}
window.playSeg = async id => {
  try {
    const r = await fetch('/api/playback/' + id, { headers: { 'x-session': S.token } });
    if (!r.ok) { toast('No playable media (keyframe-only tier)'); return; }
    const blob = await r.blob();
    if (_blobUrl) URL.revokeObjectURL(_blobUrl);
    _blobUrl = URL.createObjectURL(blob);
    $('#modal-title').textContent = 'Segment Playback';
    $('#modal-body').innerHTML = blob.type.startsWith('image/')
      ? `<img src="${_blobUrl}" style="width:100%;border-radius:8px;display:block">`
      : `<video src="${_blobUrl}" controls autoplay style="width:100%;border-radius:8px;background:#000;display:block"></video>`;
    $('#modal-backdrop').classList.remove('hidden');
  } catch { toast('Playback unavailable'); }
};
window.viewMeta = async id => {
  const m = await api('/segments/' + id);
  $('#modal-title').textContent = `Segment #${String(m.idx).padStart(3, '0')} Metadata`;
  $('#modal-body').innerHTML = `<dl>
    <dt>Time range</dt><dd>${m.start_time?.toFixed(1)}s – ${m.end_time?.toFixed(1)}s</dd>
    <dt>Captured</dt><dd>${new Date(m.ts).toLocaleString()}</dd>
    <dt>Ssig</dt><dd>${m.ssig?.toFixed(3)}</dd>
    <dt>Threat level</dt><dd>${esc(m.threat_level)}</dd>
    <dt>Hazard level</dt><dd>${esc(m.hazard_level ?? '—')}</dd>
    <dt>Storage tier</dt><dd><span class="pill ${m.tier}">${m.tier || '—'}</span></dd>
    <dt>Objects</dt><dd>${(m.objects || []).map(o => `<span class="chip">${esc(o.label)} · ${(o.confidence * 100).toFixed(0)}%</span>`).join('') || '—'}</dd>
    <dt>Actions</dt><dd>${(m.actions || []).map(a => `<span class="chip act">▷ ${esc(a.action)} · ${(a.confidence * 100).toFixed(0)}%</span>`).join('') || '—'}</dd>
  </dl>`;
  $('#modal-backdrop').classList.remove('hidden');
};
$('#modal-close').onclick = closeModal;
$('#modal-backdrop').onclick = e => { if (e.target.id === 'modal-backdrop') closeModal(); };
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
window.delSegment = async (id, btn) => {
  if (!confirm('Delete this segment and its files? This cannot be undone.')) return;
  await api('/segments/' + id, { method: 'DELETE' });
  toast('Segment deleted');
  btn.closest('.seg')?.remove();
};
window.setTier = async (id, tier) => {
  if (!tier) return;
  const fd = new FormData(); fd.append('tier', tier);
  await api('/segments/' + id + '/tier', { method: 'POST', body: fd });
  toast('Tier set to ' + tier);
};

VIEWS.search = async () => {
  V.innerHTML = `<h2>Forensic Search</h2><p class="sub">Query analyzed segments by time, object, action, tier, and significance.</p>
    <div class="card row" style="align-items:end">
      <div class="field"><label>From</label><input type="datetime-local" id="s-start"></div>
      <div class="field"><label>To</label><input type="datetime-local" id="s-end"></div>
      <div class="field"><label>Object</label><input id="s-obj" placeholder="e.g. person"></div>
      <div class="field"><label>Action</label><input id="s-act" placeholder="e.g. fighting"></div>
      <div class="field"><label>Tier</label><select id="s-tier"><option value="">any</option><option>HIGH</option><option>MEDIUM</option><option>LOW</option></select></div>
      <div class="field"><label>Min Ssig <span id="s-minlbl">0</span></label><input type="range" id="s-min" min="0" max="1" step="0.05" value="0"></div>
      <button class="primary" id="s-go">Search</button>
    </div><div id="s-results" style="margin-top:16px"></div>`;
  $('#s-min').oninput = e => $('#s-minlbl').textContent = e.target.value;
  $('#s-go').onclick = runSearch;
};
async function runSearch() {
  const q = new URLSearchParams();
  const g = id => $(id).value;
  if (g('#s-start')) q.set('start', new Date(g('#s-start')).toISOString());
  if (g('#s-end')) q.set('end', new Date(g('#s-end')).toISOString());
  if (g('#s-obj')) q.set('objects', g('#s-obj'));
  if (g('#s-act')) q.set('actions', g('#s-act'));
  if (g('#s-tier')) q.set('tier', g('#s-tier'));
  q.set('min_ssig', g('#s-min'));
  const d = await api('/search?' + q);
  $('#s-results').innerHTML = `<p class="sub">${d.total} result(s)</p>` +
    (d.results.map(segCard).join('') || '<div class="empty">No matching segments.</div>');
}

VIEWS.alerts = async () => {
  V.innerHTML = `<h2>Security Alerts</h2><p class="sub">High-significance events requiring operator review.</p><div id="alist"></div>`;
  const rows = await api('/alerts');
  const isAdmin = S.role === 'Administrator';
  $('#alist').innerHTML = rows.length ? `<table><thead><tr><th>Time</th><th>Severity</th><th>Ssig</th><th>Detail</th><th>Status</th><th></th></tr></thead><tbody>${
    rows.map(a => `<tr>
      <td style="font-family:var(--mono)">${new Date(a.ts).toLocaleString()}</td>
      <td><span class="pill ${a.severity === 'critical' ? 'HIGH' : 'MEDIUM'}">${a.severity}</span></td>
      <td>${a.ssig.toFixed(2)}</td>
      <td style="font-size:12px">${esc((a.detail.objects || []).join(', '))} · ${esc(a.detail.hazard || '')}</td>
      <td><span class="pill ${a.status === 'new' ? 'processing' : 'done'}">${a.status}</span></td>
      <td>${a.status === 'new' ? `<button class="btn sm" onclick="ackAlert(${a.id})">Acknowledge</button>` : esc(a.ack_by)}
        ${isAdmin ? `<button class="btn sm danger" onclick="delAlert(${a.id},this)" style="margin-left:4px">Delete</button>` : ''}</td>
    </tr>`).join('')}</tbody></table>` : '<div class="empty">No alerts.</div>';
};
window.ackAlert = async id => { await api('/alerts/' + id + '/ack', { method: 'POST' }); toast('Acknowledged'); VIEWS.alerts(); pollAlerts(); };
window.delAlert = async (id, btn) => {
  if (!confirm('Delete this alert record?')) return;
  await api('/alerts/' + id, { method: 'DELETE' });
  toast('Alert deleted');
  btn.closest('tr')?.remove();
  pollAlerts();
};

VIEWS.config = async () => {
  const c = await api('/config');
  V.innerHTML = `<h2>System Configuration</h2><p class="sub">Processing, storage tier, and alert parameters (FR44–47).</p>
    <div class="card"><div class="grid2">
      ${cfgField('threshold_high', 'Storage threshold HIGH', c)}
      ${cfgField('threshold_low', 'Storage threshold LOW', c)}
      ${cfgField('alert_threshold', 'Alert threshold', c)}
      ${cfgField('motion_sensitivity', 'Motion sensitivity (Invaligator)', c)}
      ${cfgField('object_conf_threshold', 'Object detection confidence', c)}
      ${cfgField('action_min_frames', 'Action recognition min frames', c)}
      ${cfgField('segment_seconds', 'Segment duration (s)', c)}
    </div>
    <div style="margin-top:14px"><button class="primary" id="cfg-save">Save configuration</button></div></div>`;
  $('#cfg-save').onclick = async () => {
    const body = {};
    $$('[data-cfg]').forEach(i => body[i.dataset.cfg] = i.value);
    try { await api('/config', { method: 'POST', body: JSON.stringify(body), headers: { 'content-type': 'application/json' } });
      toast('Configuration saved'); } catch (e) { toast(e.message); }
  };
};
function cfgField(k, label, c) {
  return `<div class="field"><label>${label}</label><input data-cfg="${k}" value="${esc(c[k])}"></div>`;
}

VIEWS.logs = async () => {
  V.innerHTML = `<h2>System Logs</h2><p class="sub">Audit trail, API activity, and live processing stream (FR30, 48, 53, 54, 57).</p>
    <div class="card row" style="align-items:end;margin-bottom:14px">
      <div class="field"><label>Type</label><select id="l-type"><option value="">all</option>
        <option>system</option><option>api</option><option>processing</option><option>audit</option><option>error</option></select></div>
      <div class="field"><label>Severity</label><select id="l-sev"><option value="">all</option>
        <option>info</option><option>warning</option><option>error</option></select></div>
      <button class="btn" id="l-go">Filter</button>
      <button class="btn" id="l-live">▶ Live stream</button>
    </div><div class="card" id="loglist" style="max-height:520px;overflow:auto"></div>`;
  const load = async () => {
    const q = new URLSearchParams();
    if ($('#l-type').value) q.set('type', $('#l-type').value);
    if ($('#l-sev').value) q.set('severity', $('#l-sev').value);
    const rows = await api('/logs?' + q);
    $('#loglist').innerHTML = rows.map(logLine).join('') || '<div class="empty">No logs.</div>';
  };
  $('#l-go').onclick = load;
  let es = null;
  $('#l-live').onclick = () => {
    if (es) { es.close(); es = null; $('#l-live').textContent = '▶ Live stream'; return; }
    es = new EventSource('/api/logs/stream?session=' + encodeURIComponent(S.token));
    $('#l-live').textContent = '■ Stop stream';
    es.onmessage = e => { const r = JSON.parse(e.data);
      $('#loglist').insertAdjacentHTML('afterbegin', logLine(r)); };
  };
  load();
};
function logLine(r) {
  return `<div class="logline ${r.severity}"><span class="lv">[${new Date(r.ts).toLocaleTimeString()}] ${r.type.toUpperCase()} ${r.severity}</span> ${esc(r.message)}</div>`;
}

VIEWS.users = async () => {
  V.innerHTML = `<h2>User Management</h2><p class="sub">Approve registrations, edit roles, and remove accounts (FR03, 56).</p><div id="ulist"></div>`;
  loadUsers();
};
const ROLE_OPTS = ['User', 'SecurityOperator', 'Administrator'];
async function loadUsers() {
  const rows = await api('/admin/users');
  $('#ulist').innerHTML = `<table><thead><tr><th>User</th><th>Name</th><th>Role</th><th>Status</th><th>Actions</th></tr></thead><tbody>${
    rows.map(u => {
      const isSelf = u.username === S.user;
      return `<tr id="urow-${u.id}">
      <td style="font-family:var(--mono)">${esc(u.username)}${isSelf ? ' <span class="chip">you</span>' : ''}</td>
      <td>${esc(u.full_name || '')}</td>
      <td>${esc(u.role)}</td>
      <td><span class="pill ${u.status}">${u.status}</span></td>
      <td>${u.status === 'pending' ? `
        <select id="role-${u.id}" class="btn sm">${ROLE_OPTS.map(r => `<option>${r}</option>`).join('')}</select>
        <button class="btn sm" onclick="approve(${u.id},'approve')">Approve</button>
        <button class="btn sm danger" onclick="approve(${u.id},'reject')">Reject</button>` : `
        <button class="btn sm" onclick="editUser(${u.id})">Edit</button>
        ${!isSelf ? `<button class="btn sm danger" onclick="deleteUser(${u.id})">Delete</button>` : ''}`}</td>
    </tr>`;
    }).join('')}</tbody></table>`;
}
window.approve = async (id, action) => {
  const fd = new FormData(); fd.append('action', action);
  const sel = $('#role-' + id); if (sel) fd.append('role', sel.value);
  await api('/admin/users/' + id + '/approve', { method: 'POST', body: fd });
  toast('User ' + (action === 'approve' ? 'approved' : 'rejected')); loadUsers();
};
window.editUser = row => {
  const tr = $('#urow-' + row);
  const cells = tr.querySelectorAll('td');
  const name = cells[1].textContent.trim(), role = cells[2].textContent.trim();
  cells[1].innerHTML = `<input id="edit-name-${row}" value="${esc(name)}" style="width:140px;padding:4px 8px;
    background:var(--bg);border:1px solid var(--line2);border-radius:6px;color:var(--text);font-size:13px">`;
  cells[2].innerHTML = `<select id="edit-role-${row}" class="btn sm">${
    ROLE_OPTS.map(r => `<option ${r === role ? 'selected' : ''}>${r}</option>`).join('')}</select>`;
  cells[4].innerHTML = `<button class="btn sm" onclick="saveUser(${row})">Save</button>
    <button class="btn sm" onclick="loadUsers()">Cancel</button>`;
};
window.saveUser = async row => {
  const fd = new FormData();
  fd.append('full_name', $('#edit-name-' + row).value.trim());
  fd.append('role', $('#edit-role-' + row).value);
  try { await api('/admin/users/' + row, { method: 'PATCH', body: fd }); toast('User updated'); }
  catch (e) { toast(e.message); }
  loadUsers();
};
window.deleteUser = async id => {
  if (!confirm('Delete this user account? This cannot be undone.')) return;
  try { await api('/admin/users/' + id, { method: 'DELETE' }); toast('User deleted'); }
  catch (e) { toast(e.message); }
  loadUsers();
};

VIEWS.docs = async () => {
  V.innerHTML = `<h2>Analytics &amp; Scoring Documentation</h2>
    <p class="sub">How Quintrix turns raw footage into a significance score and a storage decision.</p>
    <div id="docs-body"></div>`;
  let cfg = {};
  try { cfg = await api('/config'); } catch { /* non-admin: show defaults below */ }
  const th_high = cfg.threshold_high ?? '0.7', th_low = cfg.threshold_low ?? '0.4',
        alert_th = cfg.alert_threshold ?? '0.8', sens = cfg.motion_sensitivity ?? '0.001';
  $('#docs-body').innerHTML = `
    <div class="card" style="margin-bottom:14px">
      <h3 style="margin:0 0 10px;font-size:14px">The 5-stage pipeline</h3>
      <p class="sub" style="margin:0 0 12px">Every upload runs through these stages automatically, in order.</p>
      <div class="row" style="gap:10px">
        ${[
          ['1', 'Ingestion', 'Read metadata, cut into fixed-length segments'],
          ['2', 'Invaligator', 'MOG2 motion filter — static segments skip straight to LOW tier'],
          ['3', 'Deep analysis', 'YOLOv8 objects + X3D-S actions + MobileNetV3 sentiment/threat'],
          ['4', 'Prioritization', 'Combine everything into one Ssig significance score'],
          ['5', 'Tiered storage', 'Write the segment out according to its tier'],
        ].map(([n, t, d]) => `<div class="card" style="flex:1;min-width:160px;background:var(--panel2)">
            <div class="pill LOW" style="margin-bottom:8px">STAGE ${n}</div>
            <b style="font-size:13px">${t}</b>
            <p style="margin:6px 0 0;font-size:12px;color:var(--dim)">${d}</p>
          </div>`).join('')}
      </div>
    </div>

    <div class="grid2" style="margin-bottom:14px">
      <div class="card">
        <h3 style="margin:0 0 10px;font-size:14px">Significance score (Ssig)</h3>
        <p class="sub" style="margin:0 0 10px">A single 0–1 score per segment, weighted from three model outputs plus context modifiers:</p>
        <dl style="display:grid;grid-template-columns:1fr auto;gap:6px 12px;margin:0 0 12px;font-size:12px">
          <dt style="color:var(--dim)">Objects detected (YOLOv8)</dt><dd style="margin:0;font-family:var(--mono)">35%</dd>
          <dt style="color:var(--dim)">Top action confidence (X3D-S)</dt><dd style="margin:0;font-family:var(--mono)">30%</dd>
          <dt style="color:var(--dim)">Sentiment/threat (MobileNetV3)</dt><dd style="margin:0;font-family:var(--mono)">35%</dd>
        </dl>
        <p class="sub" style="margin:0 0 6px">Boosted further by context rules:</p>
        <div>
          <span class="chip">weapon detected +0.25</span>
          <span class="chip">3+ people (crowd) +0.15</span>
          <span class="chip">night footage +0.10</span>
          <span class="chip">critical hazard +0.25</span>
        </div>
      </div>
      <div class="card">
        <h3 style="margin:0 0 10px;font-size:14px">Live thresholds</h3>
        <p class="sub" style="margin:0 0 10px">From Configuration — tune these to change tiering/alerting sensitivity.</p>
        <div style="margin-bottom:10px"><div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px">
          <span class="pill HIGH">HIGH</span><span>Ssig &gt; ${th_high}</span></div></div>
        <div style="margin-bottom:10px"><div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px">
          <span class="pill MEDIUM">MEDIUM</span><span>${th_low} &lt; Ssig ≤ ${th_high}</span></div></div>
        <div style="margin-bottom:10px"><div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px">
          <span class="pill LOW">LOW</span><span>Ssig ≤ ${th_low}, or no motion</span></div></div>
        <div style="display:flex;justify-content:space-between;font-size:12px;padding-top:8px;border-top:1px solid var(--line)">
          <span style="color:var(--dim)">Alert threshold</span><span>Ssig ≥ ${alert_th}</span></div>
        <div style="display:flex;justify-content:space-between;font-size:12px;margin-top:6px">
          <span style="color:var(--dim)">Motion sensitivity</span><span>${sens}</span></div>
      </div>
    </div>

    <div class="card">
      <h3 style="margin:0 0 10px;font-size:14px">What each storage tier actually keeps</h3>
      <div class="grid2" style="gap:10px">
        <div class="card" style="background:var(--panel2)"><span class="pill HIGH">HIGH</span>
          <p style="margin:8px 0 0;font-size:12px;color:var(--dim)">Original clip kept lossless — full forensic detail, largest storage cost.</p></div>
        <div class="card" style="background:var(--panel2)"><span class="pill MEDIUM">MEDIUM</span>
          <p style="margin:8px 0 0;font-size:12px;color:var(--dim)">Re-encoded at a lower bitrate — playable, meaningfully smaller.</p></div>
        <div class="card" style="background:var(--panel2)"><span class="pill LOW">LOW</span>
          <p style="margin:8px 0 0;font-size:12px;color:var(--dim)">Single JPEG keyframe only — no video, minimal footprint.</p></div>
      </div>
    </div>`;
};

// ---------- alert badge polling (FR42) ----------
async function pollAlerts() {
  if (!S.token || !['Administrator', 'SecurityOperator'].includes(S.role)) return;
  try {
    const rows = await api('/alerts');
    const open = rows.filter(a => a.status === 'new').length;
    const dot = $('#alert-dot');
    if (dot) dot.style.display = open ? 'inline-block' : 'none';
  } catch {}
  setTimeout(pollAlerts, 5000);
}

// ---------- init ----------
if (S.token) boot();
