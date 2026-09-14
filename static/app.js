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
  { id: 'workflow', label: 'Workflow', roles: ['Administrator', 'SecurityOperator', 'User'] },
  { id: 'analytics', label: 'Analytics', roles: ['Administrator', 'SecurityOperator', 'User'] },
  { id: 'search', label: 'Search', roles: ['Administrator', 'SecurityOperator', 'User'] },
  { id: 'alerts', label: 'Alerts', roles: ['Administrator', 'SecurityOperator'], dot: true },
  { id: 'review', label: 'Review Queue', roles: ['Administrator', 'SecurityOperator'] },
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
  V.innerHTML = `<h2>Surveillance Overview</h2>
    <p class="sub">Live system state, storage reduction, and what the pipeline is finding.</p>
    <div id="dash"><div class="sub">Loading…</div></div>`;
  await renderDashboard();
};

async function renderDashboard() {
  const [a, recs] = await Promise.all([api('/analytics'), api('/recommendations')]);
  const st = a.storage || {};
  const q = a.job_queue || {};
  const saved = st.true_savings_percent ?? 0;
  const uploaded = st.uploaded_bytes || 0;
  const stored = st.total_stored || 0;
  const busy = (a.processing_footages ?? a.processing_projects ?? 0)
    + (a.queued_footages ?? a.queued_projects ?? 0);
  let modeName = '—';
  try { const m = await api('/modes'); modeName = m.is_custom ? 'Custom'
    : (m.modes.find(x => x.active)?.name || '—'); } catch {}

  const tiers = a.tiers || {};
  const tierTotal = Object.values(tiers).reduce((x, y) => x + y, 0) || 1;
  const held = (a.review_queue?.pending || {}).count || 0;

  $('#dash').innerHTML = `
    <div class="cmdstrip">
      <div class="cmdcell"><div class="k"><span class="pulse ${busy ? 'warn' : ''}"></span>Pipeline</div>
        <div class="v">${busy ? 'ACTIVE' : 'IDLE'}</div>
        <div class="s">${q.running || 0} running · ${a.queued_footages ?? a.queued_projects ?? 0} queued · ${q.workers || 0} workers</div></div>
      <div class="cmdcell"><div class="k"><span class="pulse"></span>Compute</div>
        <div class="v" style="color:var(--info)">${esc((a.device || '').toUpperCase())}</div>
        <div class="s">${esc(a.action_model || '')}</div></div>
      <div class="cmdcell"><div class="k">Operating mode</div>
        <div class="v" style="font-size:15px">${esc(modeName)}</div>
        <div class="s">${esc(a.pipeline_profile || '')} profile</div></div>
      <div class="cmdcell"><div class="k">Scoring model</div>
        <div class="v" style="font-size:15px;color:${a.sentiment_backend === 'ok' ? 'var(--ok)' : 'var(--warn)'}">
          ${a.sentiment_backend === 'ok' ? 'TRAINED' : esc(String(a.sentiment_backend || '—').toUpperCase())}</div>
        <div class="s">${a.sentiment_backend === 'ok' ? 'MLP checkpoint loaded' : 'rule fallback'}</div></div>
      <div class="cmdcell"><div class="k"><span class="pulse ${a.open_alerts ? 'hi' : ''}"></span>Open alerts</div>
        <div class="v" style="color:${a.open_alerts ? 'var(--hi)' : 'var(--ok)'}">${a.open_alerts}</div>
        <div class="s">${a.events} events extracted</div></div>
      <div class="cmdcell"><div class="k"><span class="pulse ${held ? 'warn' : ''}"></span>Awaiting review</div>
        <div class="v" style="color:${held ? 'var(--warn)' : 'var(--ok)'}">${held}</div>
        <div class="s">before irreversible degradation</div></div>
    </div>

    <div class="card hud" style="--acc:var(--info)">
      <div class="reduction">
        <div>
          <div class="k" style="font-size:9.5px;color:var(--dimmer);text-transform:uppercase;letter-spacing:.14em">Storage reduction</div>
          <div class="red-figure ${saved < 0 ? 'neg' : ''}">${saved.toFixed(1)}<span style="font-size:28px">%</span></div>
          <div class="red-sub">
            ${fmtBytes(uploaded)} ingested → <b style="color:var(--text)">${fmtBytes(stored)}</b> retained
            across ${a.segments_total} segments (${a.hours_analysed} h of footage).
            ${saved < 0
              ? '<b style="color:var(--hi)">Negative: legacy runs stored more than they ingested.</b> Scope to the deployed pipeline in Workflow → Savings.'
              : 'Measured against the files operators uploaded, not the pipeline\'s own intermediate segments.'}
          </div>
          <div class="red-bars">
            <div class="red-row"><span class="nm">Ingested</span>
              <span class="red-track"><i style="width:100%;background:linear-gradient(90deg,rgba(255,59,92,.85),rgba(255,120,73,.5))"></i></span>
              <span class="amt">${fmtBytes(uploaded)}</span></div>
            <div class="red-row"><span class="nm">Retained</span>
              <span class="red-track"><i style="width:${uploaded ? Math.max(1, Math.min(100, stored / uploaded * 100)) : 0}%;background:linear-gradient(90deg,rgba(38,208,124,.9),rgba(38,208,124,.45))"></i></span>
              <span class="amt">${fmtBytes(stored)}</span></div>
          </div>
        </div>
        <div>${donut(['HIGH', 'MEDIUM', 'LOW'].map((t, i) => ({
          label: t, value: tiers[t] || 0,
          color: ['#ff3b5c', '#ffb020', '#26d07c'][i],
        })).filter(x => x.value), { unit: 'SEGMENTS' })}</div>
      </div>
    </div>

    <div class="grid2" style="margin-top:14px">
      <div class="card hud" style="--acc:var(--hi)">
        <h3 style="margin:0 0 4px;font-size:14px">Threat distribution</h3>
        <p class="sub" style="margin:0 0 14px">Across ${a.segments_motion} segments with motion.</p>
        ${hbars(['high', 'medium', 'low'].map(l => ({
          label: l, value: (a.threats || {})[l] || 0,
          right: (a.threats || {})[l] || 0,
          color: l === 'high' ? 'linear-gradient(90deg,rgba(255,59,92,.9),rgba(255,59,92,.35))'
               : l === 'medium' ? 'linear-gradient(90deg,rgba(255,176,32,.9),rgba(255,176,32,.35))'
               : 'linear-gradient(90deg,rgba(38,208,124,.9),rgba(38,208,124,.35))',
        })))}
        <div class="mdl-metrics" style="margin-top:16px">
          <div class="metric"><div class="k">Segments</div><div class="v">${fmtNum(a.segments_total)}</div></div>
          <div class="metric"><div class="k">With motion</div><div class="v">${fmtNum(a.segments_motion)}</div></div>
          <div class="metric"><div class="k">Static skipped</div><div class="v">${fmtNum(a.segments_total - a.segments_motion)}</div></div>
        </div>
      </div>
      <div class="card hud" style="--acc:var(--warn)">
        <h3 style="margin:0 0 4px;font-size:14px">Recent alerts</h3>
        <p class="sub" style="margin:0 0 14px">Highest-significance events awaiting review.</p>
        <div class="feed" id="dash-feed"><div class="sub">Loading…</div></div>
      </div>
    </div>

    <div class="card hud" style="margin-top:14px;--acc:var(--ok)">
      <h3 style="margin:0 0 4px;font-size:14px">System recommendations</h3>
      <p class="sub" style="margin:0 0 14px">Generated after each run from the project's own numbers.</p>
      ${recs.length ? recs.slice(0, 8).map(r =>
        `<div style="display:flex;gap:10px;align-items:flex-start;padding:9px 0;border-bottom:1px solid var(--line);font-size:12.5px">
           <span class="pill LOW" style="flex-shrink:0">${esc(r.kind)}</span>
           <span style="color:var(--dim);line-height:1.6">${esc(r.message)}</span></div>`).join('')
        : '<div class="sub" style="margin:0">No recommendations yet.</div>'}
    </div>`;

  // alerts feed is role-gated, so it loads separately and degrades quietly
  try {
    const al = await api('/alerts');
    $('#dash-feed').innerHTML = al.length ? al.slice(0, 8).map(x => {
      const c = x.severity === 'critical' ? 'var(--hi)' : 'var(--warn)';
      const objs = (x.detail?.objects || []).slice(0, 3).join(', ');
      return `<div class="feed-row">
        <span class="sev" style="background:${c}"></span>
        <span class="txt"><b style="color:${c};font-family:var(--mono)">${esc(x.severity)}</b>
          · Ssig ${(x.ssig || 0).toFixed(2)}${objs ? ' · ' + esc(objs) : ''}
          ${x.status === 'acknowledged' ? '<span class="chip" style="margin-left:6px">ack</span>' : ''}</span>
        <span class="tm">${esc(String(x.ts).slice(5, 16).replace('T', ' '))}</span></div>`;
    }).join('') : '<div class="sub" style="margin:0">No alerts raised.</div>';
  } catch {
    $('#dash-feed').innerHTML = '<div class="sub" style="margin:0">Alerts require an operator role.</div>';
  }
}

/* Projects are containers; footages are the recordings inside them. The view
   walks three levels — projects → footages → one footage's segments — with a
   breadcrumb, so the list page never becomes a flat wall of recordings. */
let _pFilter = 'all';
let _pPoll = null;
let _pLevel = 'projects';     // projects | project | footage
let _pProject = null;
let _pProjectName = '';

VIEWS.projects = async () => {
  V.innerHTML = '<div id="pwrap"><div class="sub">Loading…</div></div>';
  if (_pLevel === 'project' && _pProject) return renderProject(_pProject);
  return renderProjectList();
};

function crumbs(parts) {
  return `<div class="crumbs">${parts.map((p, i) => i === parts.length - 1
    ? `<b>${esc(p.label)}</b>`
    : `<a data-crumb="${i}">${esc(p.label)}</a><span class="sep">›</span>`).join('')}</div>`;
}

// --------------------------------------------------------------- level 1 ---
async function renderProjectList() {
  _pLevel = 'projects';
  const d = await api('/projects');
  const t = d.totals;
  const canManage = ['Administrator', 'SecurityOperator'].includes(S.role);
  const isAdmin = S.role === 'Administrator';

  $('#pwrap').innerHTML = `
    <h2>Projects</h2>
    <p class="sub">Each project holds the recordings for one site, camera or
      investigation. Open one to see its footage.</p>

    <div class="cmdstrip">
      <div class="cmdcell"><div class="k">Projects</div><div class="v">${t.projects}</div>
        <div class="s">${t.footages} footage(s) · ${fmtNum(t.segments)} segments</div></div>
      <div class="cmdcell"><div class="k">Ingested</div><div class="v">${fmtBytes(t.uploaded_bytes)}</div>
        <div class="s">source footage</div></div>
      <div class="cmdcell"><div class="k">Retained</div>
        <div class="v" style="color:var(--ok)">${fmtBytes(t.stored_bytes)}</div>
        <div class="s">after tiering</div></div>
      <div class="cmdcell"><div class="k">Fleet saving</div>
        <div class="v" style="color:${t.savings_percent > 0 ? 'var(--ok)' : 'var(--hi)'}">${t.savings_percent}%</div>
        <div class="s">vs uploaded bytes</div></div>
      <div class="cmdcell"><div class="k"><span class="pulse ${t.busy ? 'warn' : ''}"></span>In pipeline</div>
        <div class="v">${t.busy}</div><div class="s">${t.held} held for review</div></div>
    </div>

    <div class="fleetbar">
      ${canManage ? '<button class="btn" id="p-new">+ New project</button>' : ''}
      <span class="cine-spacer"></span>
      ${isAdmin ? '<button class="btn sm danger" id="p-reproc-all">Reprocess everything</button>' : ''}
    </div>

    <div class="projgrid">${d.projects.map(projectCard).join('')
      || '<div class="empty">No projects yet. Create one, then upload footage into it.</div>'}</div>`;

  $$('[data-open-project]').forEach(el => el.onclick = e => {
    if (e.target.closest('[data-stop]')) return;
    openProjectView(el.dataset.openProject, el.dataset.projectName);
  });
  const nb = $('#p-new');
  if (nb) nb.onclick = createProject;
  const ra = $('#p-reproc-all');
  if (ra) ra.onclick = () => bulkReprocess(false, t.footages, null);

  clearTimeout(_pPoll);
  if (t.busy) _pPoll = setTimeout(() => { if (_pLevel === 'projects') renderProjectList(); }, 3000);
}

function projectCard(p) {
  const tierTotal = Object.values(p.tiers).reduce((a, b) => a + b, 0) || 1;
  const col = p.savings_percent > 0 ? 'var(--ok)' : 'var(--hi)';
  return `<div class="projcard ${p.busy ? 'busy' : ''}"
      data-open-project="${p.id}" data-project-name="${esc(p.name)}">
    <div class="pt">
      <span class="pn" title="${esc(p.name)}">${esc(p.name)}</span>
      ${p.busy ? '<span class="pill processing">' + p.busy + ' running</span>' : ''}
      ${p.held ? `<span class="chip" style="color:var(--warn)">${p.held} held</span>` : ''}
    </div>
    <div class="pd">${p.footages} footage(s) · ${fmtNum(p.segments)} segments ·
      ${fmtClock(p.duration)} of video</div>
    <div class="big" style="color:${col}">${p.savings_percent.toFixed(1)}%</div>
    <div class="bigs">${fmtBytes(p.uploaded_bytes)} → ${fmtBytes(p.stored_bytes)}</div>
    <div class="ptier">${['HIGH', 'MEDIUM', 'LOW'].map((k, i) => {
      const n = p.tiers[k] || 0;
      return n ? `<i style="width:${n / tierTotal * 100}%;background:${['var(--hi)','var(--warn)','var(--ok)'][i]}"
        title="${k}: ${n}"></i>` : '';
    }).join('') || '<i style="width:100%;background:var(--line2)"></i>'}</div>
    <div class="projstat">
      <div><div class="k">HIGH</div><div class="v" style="color:var(--hi)">${p.tiers.HIGH || 0}</div></div>
      <div><div class="k">MEDIUM</div><div class="v" style="color:var(--warn)">${p.tiers.MEDIUM || 0}</div></div>
      <div><div class="k">LOW</div><div class="v" style="color:var(--ok)">${p.tiers.LOW || 0}</div></div>
      <div><div class="k">Motion</div><div class="v">${p.motion_segments}</div></div>
    </div>
    <span class="arrow">›</span></div>`;
}

async function createProject() {
  const name = prompt('Project name — a site, camera or investigation:');
  if (!name || !name.trim()) return;
  const fd = new FormData(); fd.append('name', name.trim());
  try {
    await api('/projects', { method: 'POST', body: fd });
    toast('Project created');
    renderProjectList();
  } catch (e) { toast(e.message); }
}

window.openProjectView = (pid, name) => {
  _pProject = pid; _pProjectName = name || '';
  _pLevel = 'project';
  renderProject(pid);
};

// --------------------------------------------------------------- level 2 ---
async function renderProject(pid) {
  _pLevel = 'project';
  const d = await api('/projects/' + pid);
  const p = d.project;
  _pProjectName = p.name;
  const canManage = ['Administrator', 'SecurityOperator'].includes(S.role);
  const isAdmin = S.role === 'Administrator';
  const stale = d.footages.filter(f => f.profile !== 'fusion' || !f.has_telemetry).length;

  $('#pwrap').innerHTML = `
    ${crumbs([{ label: 'Projects' }, { label: p.name }])}
    <h2 style="margin-bottom:4px">${esc(p.name)}
      ${canManage ? `<button class="btn sm ghost-icon" title="Rename project"
        onclick="renameProjectPrompt('${p.id}')">✎</button>` : ''}</h2>
    <p class="sub">${esc(p.description || 'No description.')}</p>

    <div class="cmdstrip">
      <div class="cmdcell"><div class="k">Footages</div><div class="v">${p.footages}</div>
        <div class="s">${fmtNum(p.segments)} segments</div></div>
      <div class="cmdcell"><div class="k">Ingested</div><div class="v">${fmtBytes(p.uploaded_bytes)}</div>
        <div class="s">${fmtClock(p.duration)} of video</div></div>
      <div class="cmdcell"><div class="k">Retained</div>
        <div class="v" style="color:var(--ok)">${fmtBytes(p.stored_bytes)}</div>
        <div class="s">after tiering</div></div>
      <div class="cmdcell"><div class="k">Saved</div>
        <div class="v" style="color:${p.savings_percent > 0 ? 'var(--ok)' : 'var(--hi)'}">${p.savings_percent}%</div>
        <div class="s">vs uploaded bytes</div></div>
      <div class="cmdcell"><div class="k"><span class="pulse ${p.busy ? 'warn' : ''}"></span>In pipeline</div>
        <div class="v">${p.busy}</div><div class="s">${p.held} held for review</div></div>
    </div>

    <label class="dropzone" id="dz">
      <input type="file" id="file" accept=".mp4,.avi,.mov,.mkv" hidden>
      <div id="dztext"><b>Upload footage into “${esc(p.name)}”</b><br>
        <span style="color:var(--dim);font-size:12px">MP4 · AVI · MOV · MKV — max 500MB</span></div>
    </label>

    <div class="fleetbar" style="margin-top:16px">
      <span class="sub" style="margin:0">${d.footages.length} footage(s)</span>
      <span class="cine-spacer"></span>
      ${isAdmin && stale ? `<button class="btn sm" id="p-reproc-stale">Reprocess ${stale} stale</button>` : ''}
      ${isAdmin ? '<button class="btn sm" id="p-reproc-proj">Reprocess this project</button>' : ''}
      ${canManage ? '<button class="btn sm danger" id="p-del-proj">Delete project</button>' : ''}
    </div>

    <div id="plist">${d.footages.length ? d.footages.map(f => footageCard(f, canManage)).join('')
      : '<div class="empty">No footage in this project yet.</div>'}</div>
    <div id="pdetail"></div>`;

  $$('.crumbs [data-crumb]').forEach(a => a.onclick = () => { _pProject = null; renderProjectList(); });

  const dz = $('#dz'), fi = $('#file');
  dz.ondragover = e => { e.preventDefault(); dz.classList.add('drag'); };
  dz.ondragleave = () => dz.classList.remove('drag');
  dz.ondrop = e => { e.preventDefault(); dz.classList.remove('drag');
    if (e.dataTransfer.files[0]) doUpload(e.dataTransfer.files[0], pid); };
  fi.onchange = e => { if (e.target.files[0]) doUpload(e.target.files[0], pid); };

  const rs = $('#p-reproc-stale');
  if (rs) rs.onclick = () => bulkReprocess(true, stale, pid);
  const rp = $('#p-reproc-proj');
  if (rp) rp.onclick = () => bulkReprocess(false, d.footages.length, pid);
  const dp = $('#p-del-proj');
  if (dp) dp.onclick = async () => {
    if (!confirm(`Delete “${p.name}” and all ${p.footages} footage(s)?\n\n`
      + 'Every segment, tier file and uploaded source inside it is removed. This cannot be undone.')) return;
    await api('/projects/' + pid, { method: 'DELETE' });
    toast('Project deleted');
    _pProject = null; renderProjectList();
  };

  clearTimeout(_pPoll);
  if (p.busy) _pPoll = setTimeout(() => { if (_pLevel === 'project') renderProject(pid); }, 3000);
}

window.renameProjectPrompt = async pid => {
  const name = prompt('Rename project:', _pProjectName);
  if (!name || !name.trim()) return;
  const fd = new FormData(); fd.append('name', name.trim());
  try { await api('/projects/' + pid, { method: 'PATCH', body: fd }); toast('Renamed'); }
  catch (e) { toast(e.message); }
  renderProject(pid);
};

async function doUpload(file, projectId) {
  $('#dztext').innerHTML = `<b>Uploading ${esc(file.name)}…</b>`;
  const fd = new FormData();
  fd.append('file', file);
  if (projectId) fd.append('project_id', projectId);
  try {
    await api('/footages', { method: 'POST', body: fd });
    toast('Upload accepted — queued for processing');
  } catch (e) { toast('Upload failed: ' + e.message); }
  if (_pProject) renderProject(_pProject);
}

function footageCard(f, canManage) {
  const inFlight = f.status === 'processing' || f.status === 'queued';
  const legacy = f.profile !== 'fusion';
  const tierTotal = Object.values(f.tiers).reduce((a, b) => a + b, 0) || 1;
  const savedCol = f.savings_percent > 0 ? 'var(--ok)' : 'var(--hi)';

  return `<div class="pcard ${inFlight ? 'busy' : ''} ${legacy ? 'legacy' : ''}" id="pc-${f.id}">
    <div class="pcard-top">
      <div style="min-width:0">
        <div class="pname">
          <span id="pname-${f.id}">${esc(f.name)}</span>
          <span class="pill ${f.status}">${esc(f.status)}</span>
          ${legacy ? '<span class="chip" style="color:var(--warn);border-color:rgba(255,176,32,.35)">legacy data</span>' : ''}
          ${f.held ? `<span class="chip" style="color:var(--warn)">${f.held} held</span>` : ''}
          ${canManage ? `<button class="btn sm ghost-icon" title="Rename"
            onclick="renameFootage('${f.id}')">✎</button>` : ''}
        </div>
        <div class="pmeta">${esc(f.filename)} · ${(f.duration || 0).toFixed(0)}s ·
          ${f.width}×${f.height} · ${(f.fps || 0).toFixed(0)}fps ·
          ${f.segments} segs (${f.motion_segments} motion)
          ${f.processing_ms ? ' · ' + fmtDur(f.processing_ms) : ''}</div>
      </div>
      <div>
        <div class="psaved" style="color:${savedCol}">${f.savings_percent.toFixed(1)}%</div>
        <div class="psaved-sub">${fmtBytes(f.original_bytes)} → ${fmtBytes(f.stored_bytes)}</div>
      </div>
      <div>
        <div style="font-size:9px;color:var(--dimmer);text-transform:uppercase;letter-spacing:.11em">Tier mix</div>
        <div class="ptier">${['HIGH', 'MEDIUM', 'LOW'].map((k, i) => {
          const n = f.tiers[k] || 0;
          return n ? `<i style="width:${n / tierTotal * 100}%;background:${['var(--hi)','var(--warn)','var(--ok)'][i]}"
            title="${k}: ${n}"></i>` : '';
        }).join('') || '<i style="width:100%;background:var(--line2)"></i>'}</div>
        <div class="psaved-sub">H${f.tiers.HIGH || 0} · M${f.tiers.MEDIUM || 0} · L${f.tiers.LOW || 0}</div>
      </div>
      <div class="pacts">
        <button class="btn sm" onclick="openFootage('${f.id}')">Open</button>
        <button class="btn sm" onclick="simulateFootage('${f.id}')"
          ${f.has_telemetry ? '' : 'disabled title="No stage telemetry — reprocess first"'}>Simulate</button>
        ${canManage ? `<button class="btn sm" onclick="reprocessFootage('${f.id}')"
          ${f.reprocessable && !inFlight ? '' : 'disabled'}>Reprocess</button>
        <button class="btn sm danger" onclick="delFootage('${f.id}')">Delete</button>` : ''}
      </div>
    </div>
    ${f.stages.length ? `<div class="pstages">${f.stages.map(st => {
      const inV = st.unit === 'bytes' ? st.bytes_in : st.unit === 'frames' ? st.frames_in : st.items_in;
      const outV = st.unit === 'bytes' ? st.bytes_out : st.unit === 'frames' ? st.frames_out : st.items_out;
      const red = st.reduction_percent;
      const fmt = st.unit === 'bytes' ? fmtBytes : fmtNum;
      const good = red > 0.5, grew = red < -0.5;
      const col = good ? 'var(--ok)' : grew ? (st.stage === 1 ? 'var(--info)' : 'var(--hi)') : 'var(--dimmer)';
      return `<div class="pstage" title="${esc(st.name)}">
        <div class="n">${st.stage}. ${esc(st.name.replace(/ &.*| Motion Filter| Sampling| Storage/, ''))}</div>
        <div class="r" style="color:${col}">${Math.abs(red).toFixed(1)}%</div>
        <div class="io">${fmt(inV)} → ${fmt(outV)}</div>
        <div class="mini"><i style="width:${Math.max(0, Math.min(100, red))}%;background:${col}"></i></div>
      </div>`;
    }).join('')}</div>`
    : `<div class="pstages"><div class="pstage" style="flex:1">
        <div class="n">Per-stage telemetry</div>
        <div class="io" style="margin-top:6px">Not recorded — reprocess to capture it.</div></div></div>`}
  </div>`;
}

window.simulateFootage = id => { _wfFootage = id; _wfTab = 'pipeline'; go('workflow'); };

window.reprocessFootage = async id => {
  if (!confirm('Re-run this footage through the current pipeline?\n\n'
    + 'Its segments, tier files, alerts and held footage are deleted and rebuilt. '
    + 'The uploaded source file is kept.')) return;
  try {
    const r = await api('/footages/' + id + '/reprocess', { method: 'POST' });
    toast(`Queued — cleaned ${r.cleaned.segments_removed} segment(s), freed ${fmtBytes(r.cleaned.bytes_freed)}`);
    if (_pProject) renderProject(_pProject);
  } catch (e) { toast(e.message); }
};

async function bulkReprocess(onlyStale, n, projectId) {
  if (!confirm(`Re-run ${n} footage(s) through the current pipeline?\n\n`
    + 'All derived data — segments, tier files, alerts, held footage — is deleted '
    + 'and rebuilt. Uploaded sources are kept. This can take a while.')) return;
  const q = new URLSearchParams();
  if (onlyStale) q.set('only_legacy', 'true');
  if (projectId) q.set('project', projectId);
  const r = await api('/footages/reprocess-all' + (q.toString() ? '?' + q : ''),
    { method: 'POST' });
  toast(`${r.queued.length} queued on ${r.workers} workers`
    + (r.skipped.length ? ` · ${r.skipped.length} skipped` : ''));
  _pProject ? renderProject(_pProject) : renderProjectList();
}

window.renameFootage = async id => {
  const el = $('#pname-' + id);
  const current = el.textContent.trim();
  el.outerHTML = `<span id="pname-${id}">
    <input id="pname-input-${id}" value="${esc(current)}" style="width:210px;padding:4px 8px;
      background:var(--bg);border:1px solid var(--line2);border-radius:6px;color:var(--text);font-size:13px">
    <button class="btn sm" onclick="saveFootageName('${id}')">Save</button></span>`;
  const input = $('#pname-input-' + id);
  input.focus(); input.select();
  input.onkeydown = e => { if (e.key === 'Enter') saveFootageName(id); if (e.key === 'Escape') renderProject(_pProject); };
};
window.saveFootageName = async id => {
  const name = $('#pname-input-' + id).value.trim();
  if (!name) { toast('Name cannot be empty'); return; }
  const fd = new FormData(); fd.append('name', name);
  try { await api('/footages/' + id, { method: 'PATCH', body: fd }); toast('Renamed'); }
  catch (e) { toast(e.message); }
  renderProject(_pProject);
};
/* Level 3 — one footage: its analysis timeline, inspector, and (while it is
   still in the pipeline) the live stage tracker plus processing charts. */
let _curFootageId = null;

window.openFootage = async id => {
  _curFootageId = id;
  const d = await api('/footages/' + id);
  const f = d.footage;
  const isQueued = f.status === 'queued';
  const inFlight = f.status === 'processing' || isQueued;

  $('#pdetail').innerHTML = `<div class="card" style="margin-top:16px">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:6px;flex-wrap:wrap">
      <h3 style="margin:0">${esc(f.name || f.filename)}</h3>
      <span class="pill ${f.status}">${esc(f.status)}</span>
      <button class="btn sm ghost-icon" style="margin-left:auto"
        onclick="document.getElementById('pdetail').innerHTML=''" title="Close">✕</button>
    </div>
    <p class="sub">${esc(f.filename)} ·
      <span style="font-family:var(--mono)">${f.id.slice(0, 12)}</span> ·
      ${(f.duration || 0).toFixed(1)}s · ${f.width}×${f.height} ·
      ${(f.fps || 0).toFixed(0)}fps${f.project_name ? ' · ' + esc(f.project_name) : ''}</p>
    ${isQueued ? `<div class="banner info" style="margin-top:12px"><span>◷</span><div>
      Queued — waiting for a pipeline worker. Uploads run on a bounded pool so that
      simultaneous uploads finish reliably instead of all stalling at once.
    </div></div>` : ''}
    ${inFlight ? pipelineTrackerHTML() : ''}
    ${!inFlight && d.segments.length ? analysisShellHTML(d.segments, f.duration) : ''}
    ${!inFlight && !d.segments.length ? '<div class="empty">No segments.</div>' : ''}
  </div>`;
  $('#pdetail').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  if (inFlight) startPipelineTracker(id);
  else if (d.segments.length) selectSegment(0);
};

window.delFootage = async id => {
  if (!confirm('Delete this footage and all its segments/files?')) return;
  await api('/footages/' + id, { method: 'DELETE' });
  toast('Footage deleted');
  renderProject(_pProject);
};

// Segments currently on screen — the playback list the player steps through.
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
      <button class="btn" onclick="openPlayer('${s.id}', _curSegments)">▶ Playback</button>
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
  if (_curFootageId) openFootage(_curFootageId);
};
window.delSegmentInsp = async id => {
  if (!confirm('Delete this segment and its files? This cannot be undone.')) return;
  await api('/segments/' + id, { method: 'DELETE' });
  toast('Segment deleted');
  if (_curFootageId) openFootage(_curFootageId);
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
    <div class="pt-detail" id="pt-detail"><span class="spin">◐</span> Waiting for pipeline…</div>
    ${livePanelHTML()}`;
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
    liveIngest(msg);
    if (detail) detail.innerHTML = `<span class="spin">◐</span> ${esc(msg.replace('Stage 1/5 Segmentation: ', ''))}`;
  } else if (/^Stage 2(-5)? Segment/.test(msg)) {
    _ptStep('analyze', 'active');
    const m = msg.match(/Segment (\d+)\/(\d+)/);
    if (m) { const bar = $('#pt-bar'); if (bar) bar.style.width = (m[1] / m[2] * 100) + '%'; }
    if (detail) detail.innerHTML = `<span class="spin">◐</span> ${esc(msg.replace(/^Stage [\d-]+ /, ''))}`;
    liveIngest(msg);
  } else if (msg.startsWith('Stage 5/5 Complete')) {
    _ptStep('analyze', 'done'); _ptLine(2, true); _ptStep('done', 'done');
    const bar = $('#pt-bar'); if (bar) bar.style.width = '100%';
    if (detail) detail.innerHTML = `✓ ${esc(msg.replace('Stage 5/5 Complete: ', ''))}`;
    return true; // signals completion
  }
  return false;
}
let _ptSource = null;
async function startPipelineTracker(fid) {
  if (_ptSource) { _ptSource.close(); _ptSource = null; }
  liveReset(0);
  // catch up on any stage messages that already happened before we connected
  try {
    const rows = await api('/logs?type=processing&limit=200');
    rows.reverse().forEach(r => {
      let ctx = {}; try { ctx = JSON.parse(r.context || '{}'); } catch { ctx = r.context || {}; }
      if (ctx.footage === fid) _ptApply(r.message);
    });
  } catch { /* non-admin role: no /logs access, live SSE still works */ }
  const es = _ptSource = new EventSource('/api/logs/stream?session=' + encodeURIComponent(S.token));
  es.onmessage = e => {
    let r; try { r = JSON.parse(e.data); } catch { return; }
    let ctx = {}; try { ctx = JSON.parse(r.context || '{}'); } catch { ctx = r.context || {}; }
    if (ctx.footage !== fid) return;
    if (_ptApply(r.message)) {
      es.close(); if (_ptSource === es) _ptSource = null;
      setTimeout(() => openFootage(fid), 900);
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
          <button class="btn sm" onclick="openPlayer('${s.id}', _curSegments)">Playback</button>
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
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  if (_blobUrl) { URL.revokeObjectURL(_blobUrl); _blobUrl = null; }
  // The detection player runs a requestAnimationFrame loop and holds a blob
  // URL; leaving either alive after close leaks on every open.
  if (typeof stopPlayer === 'function') stopPlayer();
  $('.modal').classList.remove('cinema');
}
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
  // segCard's playback button steps through `_curSegments`; pointing it at the
  // result set means prev/next walks the search hits, not the last-opened project.
  _curSegments = d.results;
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

let _cfgTab = 'modes';

VIEWS.config = async () => {
  V.innerHTML = `<h2>System Configuration</h2>
    <p class="sub">Pick an operating mode, or tune every parameter by hand (FR44–47).</p>
    <div class="subtabs" id="cfg-tabs">
      <button class="subtab ${_cfgTab === 'modes' ? 'active' : ''}" data-cfg-tab="modes">Operating Modes</button>
      <button class="subtab ${_cfgTab === 'manual' ? 'active' : ''}" data-cfg-tab="manual">Manual Parameters</button>
    </div>
    <div id="cfg-body"><div class="sub">Loading…</div></div>`;
  $$('#cfg-tabs .subtab').forEach(b => b.onclick = () => {
    _cfgTab = b.dataset.cfgTab;
    $$('#cfg-tabs .subtab').forEach(x => x.classList.toggle('active', x === b));
    (_cfgTab === 'modes' ? cfgModes : cfgManual)();
  });
  (_cfgTab === 'modes' ? cfgModes : cfgManual)();
};

async function cfgModes() {
  const box = $('#cfg-body');
  box.innerHTML = '<div class="sub">Loading modes…</div>';
  const d = await api('/modes');
  const isAdmin = S.role === 'Administrator';

  box.innerHTML = `
    <div class="banner ${d.is_custom ? 'info' : 'good'}"><span>${d.is_custom ? 'ℹ' : '✓'}</span><div>
      ${d.is_custom
        ? `Configuration is <b>Custom</b> — it does not match any preset. Each card
           below shows how many owned keys differ from it, so you can see how far
           the current setup has drifted and adopt the nearest one in a click.`
        : `Running the <b>${esc(d.modes.find(m => m.active)?.name || '')}</b> mode.`}
      Modes affect <b>future uploads only</b> — already-processed segments keep their tier.
      Full write-up in <code>docs/MODES.md</code>.
    </div></div>

    <div class="mode-grid">${d.modes.map(m => `
      <div class="mode ${m.active ? 'on' : ''}" style="--acc:${m.accent}">
        <div class="mode-ico">${m.icon}</div>
        <div class="mode-name">${esc(m.name)}</div>
        <div class="mode-tag">${esc(m.tagline)}</div>
        <div class="mode-metrics">
          <div class="mode-metric"><div class="k">Critical miss</div>
            <div class="v" style="color:${/≈0|1\.27/.test(m.expected.critical_miss) ? 'var(--ok)' : 'var(--warn)'}">${esc(m.expected.critical_miss)}</div></div>
          <div class="mode-metric"><div class="k">Storage</div>
            <div class="v">${esc(m.expected.storage_index)}</div></div>
          <div class="mode-metric"><div class="k">Compute</div>
            <div class="v">${esc(m.expected.compute)}</div></div>
        </div>
        <div class="mode-purpose">${esc(m.purpose)}</div>
        <ul class="mode-list">${m.use_when.map(u => `<li>${esc(u)}</li>`).join('')}</ul>
        <div class="mode-cost"><b>Cost:</b> ${esc(m.cost)}</div>
        <div class="mode-diff">${m.active ? 'matches live configuration'
          : `${m.differences.length} of ${d.owned_keys.length} owned key(s) differ`}</div>
        <div class="mode-act">
          <button class="mode-apply" data-apply="${m.id}"
            ${m.active || !isAdmin ? 'disabled' : ''}>${m.active ? 'Active' : 'Apply mode'}</button>
          <button class="btn sm" data-why="${m.id}">Why</button>
          <button class="btn sm" data-diff="${m.id}" ${m.differences.length ? '' : 'disabled'}>Diff</button>
        </div>
      </div>`).join('')}</div>
    ${isAdmin ? '' : '<p class="sub" style="margin-top:14px">Applying a mode requires an administrator.</p>'}`;

  $$('[data-apply]').forEach(b => b.onclick = () => applyMode(b.dataset.apply));
  $$('[data-diff]').forEach(b => b.onclick = () => showModeDiff(b.dataset.diff));
  $$('[data-why]').forEach(b => b.onclick = () => showModeWhy(b.dataset.why));
}

/* Explain a mode against the system's own scored segments, so the trade-off is
   shown rather than claimed. */
async function showModeWhy(id) {
  const d = await api(`/modes/${id}/rationale`);
  const worse = d.storage_delta_percent > 0;
  const total = Object.values(d.tiers).reduce((a, b) => a + b, 0) || 1;
  const curTotal = Object.values(d.current_tiers).reduce((a, b) => a + b, 0) || 1;

  const bar = (counts, n) => `<div class="ptier" style="height:16px;margin-top:6px">
    ${['HIGH', 'MEDIUM', 'LOW'].map((k, i) => {
      const c = counts[k] || 0;
      return c ? `<i style="width:${c / n * 100}%;background:${['var(--hi)','var(--warn)','var(--ok)'][i]}"
        title="${k}: ${c}"></i>` : '';
    }).join('')}</div>
    <div class="psaved-sub">H${counts.HIGH || 0} · M${counts.MEDIUM || 0} · L${counts.LOW || 0}</div>`;

  $('.modal').classList.add('wide');
  $('#modal-title').textContent = `Why “${d.name}” behaves this way`;
  $('#modal-body').innerHTML = `
    <p class="sub" style="margin:0 0 16px">${esc(d.note)}
      Replayed over <b>${fmtNum(d.sample_size)}</b> scored segment(s).</p>

    ${d.sample_size ? `
    <div class="grid2" style="gap:14px">
      <div class="card" style="margin:0">
        <h4 style="margin:0 0 4px;font-size:11px;color:var(--dimmer);text-transform:uppercase;letter-spacing:.1em">Now — ${d.current_thresholds.low} / ${d.current_thresholds.high}</h4>
        ${bar(d.current_tiers, curTotal)}
        <div style="margin-top:10px;font-family:var(--mono);font-size:19px;font-weight:800">
          ${d.current_storage_index.toFixed(3)}</div>
        <div class="psaved-sub">storage index</div>
      </div>
      <div class="card" style="margin:0;border-color:rgba(125,211,252,.35)">
        <h4 style="margin:0 0 4px;font-size:11px;color:var(--dimmer);text-transform:uppercase;letter-spacing:.1em">With this mode — ${d.thresholds.low} / ${d.thresholds.high}</h4>
        ${bar(d.tiers, total)}
        <div style="margin-top:10px;font-family:var(--mono);font-size:19px;font-weight:800;color:${worse ? 'var(--warn)' : 'var(--ok)'}">
          ${d.storage_index.toFixed(3)}
          <span style="font-size:12px">(${d.storage_delta_percent >= 0 ? '+' : ''}${d.storage_delta_percent}%)</span></div>
        <div class="psaved-sub">storage index</div>
      </div>
    </div>

    <div class="banner ${d.newly_destroyed_segments ? '' : 'good'}" style="margin-top:14px">
      <span>${d.newly_destroyed_segments ? '⚠' : '✓'}</span><div>
      Against the current configuration, <b>${d.moved_up}</b> segment(s) would be
      promoted and <b>${d.moved_down}</b> demoted.
      ${d.newly_destroyed_segments
        ? `<b style="color:var(--hi)">${d.newly_destroyed_segments} segment(s)
           (${fmtBytes(d.newly_destroyed_bytes)}) that currently keep their video would be
           reduced to keyframes — irreversibly.</b>`
        : 'No segment that currently keeps its video would lose it.'}
    </div></div>` : `<div class="banner info"><span>ℹ</span><div>
      No scored segments yet, so the tier replay has nothing to run over. Process a
      project first.</div></div>`}

    <h4 style="margin:20px 0 10px;font-size:11px;color:var(--dimmer);text-transform:uppercase;letter-spacing:.1em">What each setting does</h4>
    <table class="tbl">
      <tr><th>Setting</th><th>This mode</th><th>Effect</th></tr>
      ${d.knobs.filter(k => k.why).map(k => `<tr class="${k.differs ? 'hl' : ''}">
        <td style="white-space:nowrap">${esc(k.label)}</td>
        <td style="white-space:nowrap;color:${k.differs ? 'var(--ok)' : 'var(--dim)'}">
          ${esc(String(k.value).length > 22 ? String(k.value).slice(0, 22) + '…' : k.value)}</td>
        <td style="font-family:inherit;color:var(--dim);line-height:1.55">${esc(k.why)}</td></tr>`).join('')}
    </table>`;
  $('#modal-backdrop').classList.remove('hidden');
}

async function showModeDiff(id) {
  const d = await api(`/modes/${id}/preview`);
  $('#modal-title').textContent = 'Changes this mode would make';
  $('#modal-body').innerHTML = d.changes.length
    ? `<table class="diff-tbl">${d.changes.map(c => `<tr>
        <td>${esc(c.key)}</td>
        <td class="from">${esc(String(c.from ?? '—')).slice(0, 46)}</td>
        <td style="color:var(--dimmer)">→</td>
        <td class="to">${esc(String(c.to)).slice(0, 46)}</td></tr>`).join('')}</table>`
    : '<div class="sub">No changes — this mode matches the live configuration.</div>';
  $('#modal-backdrop').classList.remove('hidden');
}

async function applyMode(id) {
  const d = await api(`/modes/${id}/preview`);
  const lines = d.changes.map(c => `  ${c.key}: ${c.from} → ${c.to}`).join('\n');
  if (!confirm(`Apply this mode?\n\n${d.changes.length} setting(s) will change:\n\n${lines}\n\n`
    + 'This changes how future uploads are tiered. Already-processed segments keep their tier.')) return;
  const r = await api(`/modes/${id}/apply`, { method: 'POST' });
  toast(`${r.name} applied — ${r.changed.length} setting(s) changed`);
  cfgModes();
}

async function cfgManual() {
  const box = $('#cfg-body');
  const c = await api('/config');
  box.innerHTML = `
    <div class="banner info"><span>ℹ</span><div>
      Editing any of these puts the system into <b>Custom</b>. The Operating Modes
      tab shows how far a hand-tuned setup has drifted from each preset.
    </div></div>
    <div class="card"><div class="grid2">
      ${cfgField('threshold_high', 'Tier threshold HIGH', c, 'Ssig above this is stored losslessly')}
      ${cfgField('threshold_low', 'Tier threshold LOW', c, 'below this, keyframes only — irreversible')}
      ${cfgField('alert_threshold', 'Alert threshold', c, 'Ssig at which an alert is raised')}
      ${cfgField('motion_sensitivity', 'Motion sensitivity (Stage 2)', c, 'foreground ratio gate; higher skips more')}
      ${cfgField('track1_conf_threshold', 'Track 1 confidence (threat detector)', c)}
      ${cfgField('track2_conf_threshold', 'Track 2 confidence (context)', c)}
      ${cfgField('detection_stride_seconds', 'Detector sampling stride (s)', c, 'seconds between sampled frames')}
      ${cfgField('action_min_frames', 'Action recognition min frames', c)}
      ${cfgField('segment_seconds', 'Segment duration (s)', c)}
      ${cfgField('tier3_grace_hours', 'Tier-3 grace period (h)', c, '0 disables the review queue')}
      ${cfgField('max_concurrent_jobs', 'Pipeline workers', c, 'blank = derive from core count')}
      <div class="field"><label>Pipeline profile</label>
        <select data-cfg="pipeline_profile">
          <option value="fusion" ${c.pipeline_profile !== 'legacy' ? 'selected' : ''}>fusion — deployed (R3D-18 + Track 1 + Track 2)</option>
          <option value="legacy" ${c.pipeline_profile === 'legacy' ? 'selected' : ''}>legacy — X3D-S + COCO + MobileNetV3</option>
        </select></div>
      <div class="field"><label>Legacy action backend</label>
        <select data-cfg="action_model_backend">
          <option value="x3d" ${c.action_model_backend !== 'r3d18' ? 'selected' : ''}>X3D-S (Kinetics-400)</option>
          <option value="r3d18" ${c.action_model_backend === 'r3d18' ? 'selected' : ''}>R3D-18 (UCF-Crime)</option>
        </select>
        <span class="fhint">Applies to the legacy profile only — fusion always uses R3D-18.</span></div>
    </div>
    <div style="margin-top:16px"><button class="primary" id="cfg-save">Save configuration</button></div></div>`;
  $('#cfg-save').onclick = async () => {
    const body = {};
    $$('[data-cfg]').forEach(i => body[i.dataset.cfg] = i.value);
    try {
      await api('/config', { method: 'POST', body: JSON.stringify(body),
        headers: { 'content-type': 'application/json' } });
      toast('Configuration saved — now in Custom mode');
    } catch (e) { toast(e.message); }
  };
}

function cfgField(k, label, c, hint) {
  return `<div class="field"><label>${label}</label>
    <input data-cfg="${k}" value="${esc(c[k] ?? '')}">
    ${hint ? `<span class="fhint">${esc(hint)}</span>` : ''}</div>`;
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

/* ==========================================================================
   Workflow Simulator — stage-by-stage walkthrough of the deployed pipeline
   ========================================================================== */

// ---------- formatting + tiny chart helpers (no chart library) ----------
function fmtBytes(n) {
  n = Number(n) || 0;
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n : n.toFixed(n < 10 ? 2 : 1)) + ' ' + u[i];
}
function fmtNum(n) { return (Number(n) || 0).toLocaleString(); }
function fmtDur(ms) {
  ms = Number(ms) || 0;
  return ms < 1000 ? Math.round(ms) + 'ms' : (ms / 1000).toFixed(1) + 's';
}
function fmtCountdown(iso) {
  const d = (new Date(iso + 'Z') - Date.now()) / 1000;
  if (d <= 0) return 'due now';
  const h = Math.floor(d / 3600), m = Math.floor((d % 3600) / 60);
  return h >= 1 ? `${h}h ${m}m left` : `${m}m left`;
}

/* Minimal inline-SVG multi-series line chart.
   A charting library would be ~60KB for four line charts; this is ~40 lines
   and keeps the frontend's zero-dependency promise intact. */
function lineChart(series, opts = {}) {
  const W = opts.width || 720, H = opts.height || 250;
  const P = { t: 14, r: 16, b: 28, l: 56 };
  const all = series.flatMap(s => s.points);
  if (!all.length) return '<div class="sub">No data.</div>';
  const xs = all.map(p => p[0]), ys = all.map(p => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs) || 1;
  const y1 = Math.max(...ys) || 1;
  const fx = x => P.l + (x - x0) / ((x1 - x0) || 1) * (W - P.l - P.r);
  const fy = y => H - P.b - (y / y1) * (H - P.t - P.b);
  const fmtY = opts.formatY || (v => v >= 1000 ? (v / 1000).toFixed(1) + 'k' : v.toFixed(0));

  let g = '';
  for (let i = 0; i <= 4; i++) {
    const v = y1 * i / 4, y = fy(v);
    g += `<line class="gridline" x1="${P.l}" y1="${y}" x2="${W - P.r}" y2="${y}"/>
          <text class="lbl-y" x="${P.l - 8}" y="${y + 3}" text-anchor="end">${esc(fmtY(v))}</text>`;
  }
  const ticks = Math.min(6, x1 - x0 + 1);
  for (let i = 0; i < ticks; i++) {
    const v = Math.round(x0 + (x1 - x0) * i / (ticks - 1 || 1));
    g += `<text x="${fx(v)}" y="${H - 9}" text-anchor="middle">${v}</text>`;
  }
  g += `<line class="axis" x1="${P.l}" y1="${H - P.b}" x2="${W - P.r}" y2="${H - P.b}"/>`;

  series.forEach(s => {
    const d = s.points.map((p, i) => (i ? 'L' : 'M') + fx(p[0]).toFixed(1) + ' ' + fy(p[1]).toFixed(1)).join(' ');
    if (s.fill) {
      g += `<path d="${d} L ${fx(s.points[s.points.length - 1][0]).toFixed(1)} ${H - P.b} L ${fx(s.points[0][0]).toFixed(1)} ${H - P.b} Z"
             fill="${s.color}" opacity=".13"/>`;
    }
    g += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2.2"
           stroke-linejoin="round" stroke-linecap="round"${s.dash ? ' stroke-dasharray="5 4"' : ''}/>`;
  });
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img"
            aria-label="${esc(opts.label || 'chart')}">${g}</svg>
    <div class="legend">${series.map(s =>
      `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join('')}</div>`;
}

function scatterChart(groups, opts = {}) {
  const W = opts.width || 700, H = opts.height || 260;
  const P = { t: 14, r: 16, b: 32, l: 56 };
  const all = groups.flatMap(g => g.points);
  if (!all.length) return '<div class="sub">No data.</div>';
  const x1 = Math.max(...all.map(p => p[0])) || 1;
  const y1 = Math.max(...all.map(p => p[1])) || 1;
  const fx = x => P.l + x / x1 * (W - P.l - P.r);
  const fy = y => H - P.b - y / y1 * (H - P.t - P.b);
  let g = '';
  for (let i = 0; i <= 4; i++) {
    const v = y1 * i / 4, y = fy(v);
    g += `<line class="gridline" x1="${P.l}" y1="${y}" x2="${W - P.r}" y2="${y}"/>
          <text class="lbl-y" x="${P.l - 8}" y="${y + 3}" text-anchor="end">${(v * 100).toFixed(1)}%</text>`;
  }
  groups.forEach(gr => {
    gr.points.forEach(p => {
      g += `<circle cx="${fx(p[0]).toFixed(1)}" cy="${fy(p[1]).toFixed(1)}" r="2.6"
             fill="${gr.color}" opacity=".55"/>`;
    });
  });
  g += `<line class="axis" x1="${P.l}" y1="${H - P.b}" x2="${W - P.r}" y2="${H - P.b}"/>
        <text x="${(W + P.l) / 2}" y="${H - 6}" text-anchor="middle">${esc(opts.xLabel || '')}</text>`;
  return `<svg class="chart" viewBox="0 0 ${W} ${H}">${g}</svg>
    <div class="legend">${groups.map(gr =>
      `<span><i style="background:${gr.color}"></i>${esc(gr.name)}</span>`).join('')}</div>`;
}

// ---------- state ----------
let _wfTab = 'pipeline';
let _wfFootage = null;
let _wfData = null;
let _wfTimer = null;

VIEWS.workflow = async () => {
  V.innerHTML = `<h2>Workflow Simulator</h2>
    <p class="sub">Walk the deployed pipeline stage by stage on real processed
      footage — what each stage consumes, what it discards, and which model does it.</p>
    <div class="subtabs" id="wf-tabs">
      ${[['pipeline', 'Pipeline'], ['models', 'Model Integration'], ['scoring', 'Scoring & Fusion'],
         ['savings', 'Savings Projection'], ['calibration', 'Threshold Evidence']]
        .map(([id, label]) => `<button class="subtab ${id === _wfTab ? 'active' : ''}"
          data-wf="${id}">${label}</button>`).join('')}
    </div>
    <div id="wf-body"><div class="sub">Loading…</div></div>`;
  $$('#wf-tabs .subtab').forEach(b => b.onclick = () => {
    _wfTab = b.dataset.wf;
    $$('#wf-tabs .subtab').forEach(x => x.classList.toggle('active', x === b));
    renderWfTab();
  });
  renderWfTab();
};

function renderWfTab() {
  if (_wfTimer) { clearInterval(_wfTimer); _wfTimer = null; }
  ({ pipeline: wfPipeline, models: wfModels, scoring: wfScoring,
     savings: wfSavings, calibration: wfCalibration }[_wfTab] || wfPipeline)();
}

// =========================== TAB 1 — PIPELINE ===============================
async function wfPipeline() {
  const box = $('#wf-body');
  box.innerHTML = '<div class="sub">Loading projects…</div>';
  const projects = (await api('/overview/footages')).footages.filter(p => p.status === 'done');
  if (!projects.length) {
    box.innerHTML = `<div class="banner info"><span>ℹ</span><div>No completed projects yet.
      Upload footage on the <b>Projects</b> page — the simulator replays the
      measurements taken during that real run, so it needs one to exist.</div></div>`;
    return;
  }
  if (!_wfFootage || !projects.some(p => p.id === _wfFootage)) _wfFootage = projects[0].id;

  box.innerHTML = `<div class="toolbar">
      <label>Project
        <select id="wf-proj">${projects.map(p =>
          `<option value="${p.id}" ${p.id === _wfFootage ? 'selected' : ''}>${esc(p.name || p.filename)}</option>`).join('')}</select>
      </label>
      <button class="btn" id="wf-play">▶ Run walkthrough</button>
      <button class="btn sm" id="wf-all">Show all stages</button>
      <span class="sub" style="margin:0 0 0 auto" id="wf-profile"></span>
    </div>
    <div id="wf-summary"></div>
    <div class="wf-rail" id="wf-rail"></div>
    <div class="card" style="margin-top:16px">
      <h3 style="margin:0 0 14px;font-size:14px">Cumulative reduction funnel</h3>
      <div id="wf-funnel"></div>
      <p class="wf-note" id="wf-funnel-note"></p>
    </div>`;
  $('#wf-proj').onchange = e => { _wfFootage = e.target.value; wfPipeline(); };
  $('#wf-play').onclick = () => wfPlay();
  $('#wf-all').onclick = () => $$('.wf-stage').forEach(s => s.classList.add('done'));
  await wfLoadProject();
}

async function wfLoadProject() {
  _wfData = await api('/footages/' + _wfFootage + '/simulate');
  const d = _wfData, e = d.end_to_end, p = d.footage;
  $('#wf-profile').textContent =
    `${p.pipeline_profile || 'legacy'} profile · ${fmtDur(p.processing_ms)} total · ${fmtNum(p.frames_total)} frames`;

  const savingsClass = e.savings_percent > 0 ? 'good' : 'bad';
  $('#wf-summary').innerHTML = `<div class="row" style="margin-bottom:16px">
    ${[['Uploaded', fmtBytes(e.uploaded_bytes)],
       ['Stored', fmtBytes(e.stored_bytes)],
       ['Saved', e.savings_percent.toFixed(1) + '%', savingsClass === 'good' ? 'var(--ok)' : 'var(--hi)'],
       ['Compression', e.compression_ratio.toFixed(2) + '×', 'var(--info)'],
       ['Segments', fmtNum(d.segments.length)],
       ['Frames analysed', fmtNum(p.frames_analyzed)]]
      .map(([k, v, c]) => `<div class="card stat"><div class="k">${k}</div>
        <div class="v" style="color:${c || 'var(--text)'}">${v}</div></div>`).join('')}
  </div>`;

  const models = d.models || { fusion: [], legacy: [] };
  // Group by the ITSO pipeline stage each model runs in, and include the
  // non-neural stage operations so stages 2 and 5 are described too.
  const stageModels = (n) => [
    ...(p.pipeline_profile === 'legacy' ? models.legacy : models.fusion),
    ...(models.stage_ops || []),
  ].filter(m => m.stage === n);

  $('#wf-rail').innerHTML = d.stages.map((s, i) => {
    const unit = s.unit;
    const inV = unit === 'bytes' ? s.bytes_in : unit === 'frames' ? s.frames_in : s.items_in;
    const outV = unit === 'bytes' ? s.bytes_out : unit === 'frames' ? s.frames_out : s.items_out;
    const fmt = unit === 'bytes' ? fmtBytes : fmtNum;
    const red = s.reduction_percent;
    // A negative reduction is growth. Stage 1 is decomposition, so that is
    // expected there and alarming anywhere else — colour accordingly.
    const cls = red > 0 ? 'good' : red < -0.5 ? (s.stage === 1 ? 'neutral' : 'bad') : 'neutral';
    const width = Math.max(0, Math.min(100, red));
    const ms = stageModels(s.stage);
    return `<div class="wf-stage" data-stage="${i}">
      <div class="wf-node"><div class="wf-num">${s.stage}</div></div>
      <div class="wf-body">
        <div class="wf-head">
          <span class="wf-title">${esc(s.name)}</span>
          <span class="pill ${red > 0 ? 'LOW' : 'MEDIUM'}">${unit}</span>
          <span class="wf-timing">${fmtDur(s.duration_ms)}</span>
        </div>
        <div class="wf-meter">
          <div class="wf-side in"><div class="k">In</div><div class="v">${fmt(inV)}</div></div>
          <div class="wf-arrow">
            <span class="amt ${cls}">${Math.abs(red).toFixed(2)}%</span>
            <span class="track"><i style="width:${width}%;background:var(--${cls === 'good' ? 'ok' : cls === 'bad' ? 'hi' : 'info'})"></i></span>
            <span class="lbl">${red > 0 ? 'reduced' : red < 0 ? 'expanded' : 'unchanged'}</span>
          </div>
          <div class="wf-side out"><div class="k">Out</div><div class="v">${fmt(outV)}</div></div>
        </div>
        ${ms.length ? `<div class="wf-models">${ms.map(m =>
          `<span class="wf-model ${m.available ? '' : 'off'}"><span class="dotm"></span>
            <b>${esc(m.name)}</b> · ${esc(m.role)}</span>`).join('')}</div>` : ''}
        <p class="wf-note">${esc(s.detail.note || '')}${
          s.detail.method ? ` <span style="color:var(--dimmer)">(${esc(s.detail.method)})</span>` : ''}</p>
        ${wfStageExtras(s)}
      </div></div>`;
  }).join('');

  // cumulative funnel: bytes surviving each stage, relative to the upload
  const base = e.uploaded_bytes || 1;
  const rows = [['Uploaded video', base]];
  const st5 = d.stages.find(s => s.stage === 5);
  const st1 = d.stages.find(s => s.stage === 1);
  if (st1) rows.push(['After segmentation', st1.bytes_out]);
  if (st5) rows.push(['After tiering (stored)', st5.bytes_out]);
  $('#wf-funnel').innerHTML = `<div class="funnel">${rows.map(([nm, v]) => {
    const pct = Math.max(0.6, v / base * 100);
    return `<div class="funnel-row">
      <span class="nm">${esc(nm)}</span>
      <span class="funnel-bar"><i class="${v > base * 1.01 ? 'warnfill' : ''}" style="width:${Math.min(100, pct)}%"></i></span>
      <span class="amt">${fmtBytes(v)}</span></div>`;
  }).join('')}</div>`;
  $('#wf-funnel-note').innerHTML = e.savings_percent > 0
    ? `Every figure is measured against the <b>file the operator uploaded</b>
       (${fmtBytes(base)}) — not against the intermediate segment files the
       pipeline produces, which would be a baseline of our own making.`
    : `This project <b>stored more than it ingested</b>. That happens when the
       splitter re-encodes into a less efficient codec than the source, so the
       tiering stage claws back less than segmentation added. Reprocessing with
       the current build (lossless stream-copy segmentation) resolves it.`;
}

function wfStageExtras(s) {
  const d = s.detail || {};
  if (s.stage === 2 && d.skipped !== undefined) {
    return `<div class="mods"><span class="mod">${d.skipped} segment(s) skipped deep analysis</span>
      <span class="mod neg">sensitivity ${d.sensitivity}</span></div>`;
  }
  if (s.stage === 3) {
    return `<div class="mods"><span class="mod neg">${fmtNum(d.action_frames || 0)} action frames</span>
      <span class="mod neg">${fmtNum(d.detector_frames || 0)} detector frames @ ${d.detector_stride_seconds}s stride</span></div>`;
  }
  if (s.stage === 5 && d.tiers) {
    return `<div class="mods">${Object.entries(d.tiers).map(([t, n]) =>
      `<span class="mod ${t === 'LOW' ? 'neg' : ''}">${t} ${n}</span>`).join('')}
      ${d.grace_hours ? `<span class="mod neg">Tier-3 grace ${d.grace_hours}h</span>` : ''}</div>`;
  }
  return '';
}

function wfPlay() {
  const stages = $$('.wf-stage');
  stages.forEach(s => s.classList.remove('on', 'done'));
  let i = 0;
  const step = () => {
    if (i > 0) stages[i - 1].classList.replace('on', 'done');
    if (i >= stages.length) return;
    stages[i].classList.add('on');
    stages[i].scrollIntoView({ block: 'center', behavior: 'smooth' });
    i++;
    setTimeout(step, 1150);
  };
  step();
}

// =========================== TAB 2 — MODELS =================================
async function wfModels() {
  const box = $('#wf-body');
  box.innerHTML = '<div class="sub">Loading model registry…</div>';
  const r = await api('/models');

  const card = m => `<div class="mdl ${m.active ? 'active' : 'inactive'}">
    <div class="mdl-stage">Stage ${m.stage} ${m.active ? '· active' : '· not in use'}</div>
    <div class="mdl-name">${esc(m.name)}</div>
    <div class="mdl-role">${esc(m.role)}</div>
    <dl class="kv">
      <dt>Classes</dt><dd>${esc(m.dataset)}</dd>
      <dt>Input</dt><dd>${esc(m.input)}</dd>
      <dt>Output</dt><dd>${esc(m.output)}</dd>
      <dt>Weights</dt><dd>${esc(String(m.weights).replace(/^.*\//, '')) || '—'}
        ${m.available ? '' : '<span style="color:var(--hi)"> (missing)</span>'}</dd>
      ${m.trained_at ? `<dt>Trained</dt><dd>${esc(m.trained_at)}</dd>` : ''}
    </dl>
    ${m.metrics ? `<div class="mdl-metrics">
      ${[['MAE', m.metrics.mae], ['R²', m.metrics.r2], ['ρ', m.metrics.spearman],
         ['Tier acc', m.metrics.tier_accuracy]].map(([k, v]) =>
        `<div class="metric"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('')}
    </div>` : ''}</div>`;

  box.innerHTML = `
    <div class="banner good"><span>✓</span><div>
      <b>Deployed action model: ${esc(r.deployed_action_model)}</b> — reported by the
      running service from its live config and checkpoint paths, not from documentation.
      The active pipeline profile is <b>${esc(r.active_profile)}</b>.
      X3D-S (Kinetics-400) remains available as a fallback backend but is
      <b>not</b> what scores footage in this deployment. See <code>docs/MODELS.md</code>.
    </div></div>

    <h3 style="font-size:14px;margin:22px 0 12px">Deployed — fusion pipeline</h3>
    <div class="mdl-grid">${r.fusion.map(card).join('')}</div>

    <h3 style="font-size:14px;margin:26px 0 12px">Available but inactive — legacy pipeline</h3>
    <div class="mdl-grid">${r.legacy.map(card).join('')}</div>

    <div class="card" style="margin-top:22px">
      <h3 style="margin:0 0 6px;font-size:14px">Fusion feature contract</h3>
      <p class="sub" style="margin:0 0 14px">The ${r.feature_contract.n}-dimensional vector
        the scoring model consumes. Defined once in <code>engine/fusion.py</code> and
        imported by both the trainer and the live pipeline, so a feature cannot mean
        one thing in training and another in production.</p>
      <div class="mods">${r.feature_contract.names.map((n, i) =>
        `<span class="mod neg">${String(i).padStart(2, '0')} ${esc(n)}</span>`).join('')}</div>
      <div class="mdl-metrics" style="margin-top:16px">
        ${Object.entries(r.feature_contract.weights).map(([k, v]) =>
          `<div class="metric"><div class="k">${esc(k)} weight</div><div class="v">${v}</div></div>`).join('')}
        <div class="metric"><div class="k">Device</div><div class="v">${esc(r.device.toUpperCase())}</div></div>
      </div>
    </div>`;
}

// =========================== TAB 3 — SCORING ================================
async function wfScoring() {
  const box = $('#wf-body');
  box.innerHTML = '<div class="sub">Loading…</div>';
  const projects = (await api('/overview/footages')).footages.filter(p => p.status === 'done');
  if (!projects.length) { box.innerHTML = '<div class="sub">No completed projects.</div>'; return; }
  if (!_wfFootage || !projects.some(p => p.id === _wfFootage)) _wfFootage = projects[0].id;
  const d = await api('/footages/' + _wfFootage + '/simulate');
  const scored = d.segments.filter(s => s.motion);

  box.innerHTML = `<div class="toolbar">
      <label>Project <select id="sc-proj">${projects.map(p =>
        `<option value="${p.id}" ${p.id === _wfFootage ? 'selected' : ''}>${esc(p.name || p.filename)}</option>`).join('')}</select></label>
      <label>Segment <select id="sc-seg">${scored.map(s =>
        `<option value="${s.id}">#${String(s.idx).padStart(3, '0')} · ${s.tier} · Ssig ${s.ssig.toFixed(2)}</option>`).join('')}</select></label>
    </div>
    <div class="grid2">
      <div class="card"><h3 style="margin:0 0 4px;font-size:14px">Why this score</h3>
        <p class="sub" style="margin:0 0 16px">Tiering is irreversible, so every decision
          records its reasons. This is that record.</p>
        <div id="sc-trace"></div></div>
      <div class="card"><h3 style="margin:0 0 4px;font-size:14px">What-if</h3>
        <p class="sub" style="margin:0 0 16px">Score a hypothetical segment through the
          real trained model and the real fusion arithmetic — no footage required.</p>
        <div id="sc-what"></div></div>
    </div>`;
  $('#sc-proj').onchange = e => { _wfFootage = e.target.value; wfScoring(); };
  if (!scored.length) {
    $('#sc-trace').innerHTML = '<div class="sub">No motion segments in this project.</div>';
  } else {
    $('#sc-seg').onchange = e => renderTrace(scored.find(s => s.id === e.target.value));
    renderTrace(scored[0]);
  }
  renderWhatIf(d.feature_names);
}

function renderTrace(seg) {
  const box = $('#sc-trace');
  if (!seg) { box.innerHTML = '<div class="sub">—</div>'; return; }
  const f = seg.fusion || {}, trace = f.trace || [], mods = f.modifiers || [];
  if (!trace.length) {
    box.innerHTML = `<div class="banner info"><span>ℹ</span><div>This segment was scored by
      the <b>legacy</b> pipeline, which did not record a fusion trace. Reprocess the
      project on the fusion profile to get an auditable breakdown.</div></div>`;
    return;
  }
  const colors = { action: '#7dd3fc', objects: '#ffb020', sentiment: '#26d07c' };
  box.innerHTML = `
    <div class="row" style="margin-bottom:16px">
      ${[['Ssig', seg.ssig.toFixed(3), seg.ssig >= 0.7 ? 'var(--hi)' : seg.ssig >= 0.45 ? 'var(--warn)' : 'var(--ok)'],
         ['Tier', seg.tier], ['Hazard', seg.hazard_level],
         ['Sentiment', (seg.sentiment_score || 0).toFixed(3), 'var(--ok)']]
        .map(([k, v, c]) => `<div class="card stat" style="min-width:104px"><div class="k">${k}</div>
          <div class="v" style="font-size:20px;color:${c || 'var(--text)'}">${esc(String(v))}</div></div>`).join('')}
    </div>
    <div class="trace">${trace.map(t => {
      const contrib = t.weight * t.value;
      return `<div class="trace-row">
          <span class="ch">${esc(t.channel)}</span>
          <span class="trace-bar"><i style="width:${Math.min(100, contrib / 0.6 * 100)}%;background:${colors[t.channel] || '#7dd3fc'}"></i></span>
          <span class="num">${t.value.toFixed(3)} × ${t.weight} = <b style="color:var(--text)">${contrib.toFixed(3)}</b></span>
        </div>
        <div class="trace-why">${esc(t.detail)} — ${esc(t.why)}</div>`;
    }).join('')}</div>
    ${mods.length ? `<div style="margin-top:14px">
      <div class="k" style="font-size:10px;color:var(--dimmer);text-transform:uppercase;letter-spacing:.1em;margin-bottom:8px">Context modifiers</div>
      <div class="mods">${mods.map(m =>
        `<span class="mod ${m.amount < 0 ? 'neg' : ''}" title="${esc(m.why)}">${esc(m.name)} ${m.amount >= 0 ? '+' : ''}${m.amount}</span>`).join('')}</div>
      <p class="wf-note">${mods.map(m => esc(m.name) + ': ' + esc(m.why)).join(' · ')}</p>
    </div>` : ''}
    <div style="margin-top:16px">
      <div class="k" style="font-size:10px;color:var(--dimmer);text-transform:uppercase;letter-spacing:.1em;margin-bottom:8px">Feature vector</div>
      <table class="tbl">${Object.entries(seg.features || {}).map(([k, v]) =>
        `<tr class="${v > 0 ? 'hl' : ''}"><td>${esc(k)}</td><td class="num">${Number(v).toFixed(4)}</td></tr>`).join('')}</table>
    </div>`;
}

const _whatDefaults = {
  action_severity: 0.85, action_top_conf: 0.75, t1_max_threat: 0.80,
  t1_weapon_conf: 0.80, t1_persistence: 0.70, ctx_people: 0.50,
  ctx_person_conf: 0.90, motion_ratio: 0.40, is_night: 0,
};
function renderWhatIf(names) {
  const sliders = ['action_severity', 'action_top_conf', 't1_max_threat', 't1_weapon_conf',
    't1_persistence', 'ctx_people', 'motion_ratio'];
  $('#sc-what').innerHTML = `
    ${sliders.map(n => `<label style="display:block;font-size:11px;color:var(--dim);margin-bottom:11px">
      <span style="display:flex;justify-content:space-between">
        <span>${esc(n)}</span><b id="v-${n}" style="font-family:var(--mono);color:var(--text)">${_whatDefaults[n]}</b></span>
      <input type="range" min="0" max="1" step="0.05" value="${_whatDefaults[n]}" data-f="${n}" style="width:100%">
    </label>`).join('')}
    <label style="font-size:11px;color:var(--dim);display:flex;gap:8px;align-items:center;margin-bottom:14px">
      <input type="checkbox" data-f="is_night"> night hours</label>
    <div id="sc-out"></div>`;
  const run = async () => {
    const body = { ...Object.fromEntries(names.map(n => [n, 0])) };
    $$('#sc-what input[type=range]').forEach(i => body[i.dataset.f] = parseFloat(i.value));
    body.ctx_person_conf = body.ctx_people > 0 ? 0.9 : 0;
    body.is_night = $('#sc-what input[type=checkbox]').checked ? 1 : 0;
    const r = await api('/simulate/score', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    const c = r.tier === 'HIGH' ? 'var(--hi)' : r.tier === 'MEDIUM' ? 'var(--warn)' : 'var(--ok)';
    $('#sc-out').innerHTML = `<div class="row">
        <div class="card stat"><div class="k">Ssig</div><div class="v" style="font-size:22px;color:${c}">${r.ssig.toFixed(3)}</div></div>
        <div class="card stat"><div class="k">Tier</div><div class="v" style="font-size:22px;color:${c}">${r.tier}</div></div>
        <div class="card stat"><div class="k">Trained model</div><div class="v" style="font-size:22px;color:var(--ok)">${r.sentiment_score.toFixed(3)}</div></div>
        <div class="card stat"><div class="k">Rule fallback</div><div class="v" style="font-size:22px;color:var(--dimmer)">${r.rule_score.toFixed(3)}</div></div>
      </div>
      <p class="wf-note">The trained model differs from the rule it replaced by
        <b style="color:${r.delta_vs_rule >= 0 ? 'var(--warn)' : 'var(--info)'}">${r.delta_vs_rule >= 0 ? '+' : ''}${r.delta_vs_rule.toFixed(3)}</b>
        here. Hazard: <b>${esc(r.hazard)}</b>.</p>`;
  };
  $$('#sc-what input').forEach(i => i.oninput = () => {
    const lbl = $('#v-' + i.dataset.f);
    if (lbl) lbl.textContent = i.value;
    run();
  });
  run();
}

// =========================== TAB 4 — SAVINGS ================================
async function wfSavings() {
  const box = $('#wf-body');
  box.innerHTML = `<div class="toolbar">
      <label>Horizon <select id="sv-months">${[12, 24, 36, 60].map(m =>
        `<option ${m === 36 ? 'selected' : ''}>${m}</option>`).join('')} </select> months</label>
      <label>Fleet growth <input type="number" id="sv-growth" value="6" min="0" max="30" step="0.5" style="width:74px"> %/mo</label>
      <label>Storage cost <input type="number" id="sv-cost" value="0.023" min="0" step="0.001" style="width:84px"> $/GB/mo</label>
      <label>Measured on <select id="sv-profile">
        <option value="fusion" selected>deployed pipeline only</option>
        <option value="">all footage (incl. legacy)</option></select></label>
      <button class="btn" id="sv-go">Recalculate</button>
    </div>
    <div id="sv-body"><div class="sub">Loading…</div></div>`;
  const run = async () => {
    const m = $('#sv-months').value, g = $('#sv-growth').value, c = $('#sv-cost').value;
    const pf = $('#sv-profile').value;
    const d = await api(`/savings/projection?months=${m}&growth=${g}&cost_per_gb=${c}`
      + (pf ? `&profile=${pf}` : ''));
    const a = d.assumptions;
    $('#sv-body').innerHTML = `
      <div class="row" style="margin-bottom:18px">
        ${[['Measured savings rate', d.savings_percent.toFixed(1) + '%', 'var(--ok)'],
           ['Corpus', d.corpus.footages + ' footage · ' + d.corpus.hours_analysed + ' h'],
           ['Saved by month ' + m + ' (flat fleet)', fmtNum(d.flat_fleet[d.flat_fleet.length - 1].saved_gb) + ' GB', 'var(--info)'],
           ['Saved by month ' + m + ' (growing)', fmtNum(d.growing_fleet[d.growing_fleet.length - 1].saved_gb) + ' GB', 'var(--warn)'],
           ['Saving doubles every', (d.doubling_months || '—') + ' mo', 'var(--warn)']]
          .map(([k, v, col]) => `<div class="card stat"><div class="k">${k}</div>
            <div class="v" style="font-size:22px;color:${col || 'var(--text)'}">${v}</div></div>`).join('')}
      </div>

      <div class="card">
        <h3 style="margin:0 0 4px;font-size:14px">Cumulative storage avoided</h3>
        <p class="sub" style="margin:0 0 14px">Both curves use the same measured
          per-hour savings rate. The difference between them is <b>fleet growth</b>, nothing else.</p>
        ${lineChart([
          { name: `Growing fleet (+${g}%/mo) — exponential`, color: '#ffb020', fill: true,
            points: d.growing_fleet.map(r => [r.month, r.saved_gb]) },
          { name: 'Flat fleet — linear', color: '#7dd3fc', dash: true,
            points: d.flat_fleet.map(r => [r.month, r.saved_gb]) },
        ], { formatY: v => v >= 1000 ? (v / 1000).toFixed(1) + ' TB' : v.toFixed(0) + ' GB',
             label: 'cumulative storage avoided by month' })}
      </div>

      <div class="grid2" style="margin-top:14px">
        <div class="card">
          <h3 style="margin:0 0 14px;font-size:14px">Baseline vs ITSO — growing fleet</h3>
          ${lineChart([
            { name: 'Store everything', color: '#ff3b5c', fill: true,
              points: d.growing_fleet.map(r => [r.month, r.baseline_gb]) },
            { name: 'With ITSO tiering', color: '#26d07c', fill: true,
              points: d.growing_fleet.map(r => [r.month, r.itso_gb]) },
          ], { width: 520, height: 220,
               formatY: v => v >= 1000 ? (v / 1000).toFixed(1) + ' TB' : v.toFixed(0) + ' GB' })}
        </div>
        <div class="card">
          <h3 style="margin:0 0 14px;font-size:14px">Cumulative cost avoided</h3>
          ${lineChart([
            { name: 'Growing fleet', color: '#ffb020', fill: true,
              points: d.growing_fleet.map(r => [r.month, r.saved_cost]) },
            { name: 'Flat fleet', color: '#7dd3fc', dash: true,
              points: d.flat_fleet.map(r => [r.month, r.saved_cost]) },
          ], { width: 520, height: 220, formatY: v => '$' + (v >= 1000 ? (v / 1000).toFixed(1) + 'k' : v.toFixed(0)) })}
        </div>
      </div>

      <div class="banner info" style="margin-top:16px"><span>ℹ</span><div>
        <b>Where the exponential comes from.</b> ${esc(a.why_exponential)}
        Savings rate is <b>${esc(a.savings_rate_source)}</b>; ingest is
        <b>${esc(a.ingest_rate_source)}</b> (${fmtBytes(a.bytes_per_hour)}/h, ${esc(a.recording)}).
        Storage priced at $${a.cost_per_gb_month_usd}/GB/month.
        ${d.corpus.hours_analysed < 1 ? '<b style="color:var(--warn)"> Corpus under one hour — treat the absolute numbers as indicative.</b>' : ''}
      </div></div>`;
  };
  $('#sv-go').onclick = run;
  $$('#wf-body .toolbar select, #wf-body .toolbar input').forEach(i => i.onchange = run);
  run();
}

// ========================= TAB 5 — CALIBRATION ==============================
async function wfCalibration() {
  const box = $('#wf-body');
  box.innerHTML = '<div class="sub">Loading calibration evidence…</div>';
  let d;
  try { d = await api('/calibration'); }
  catch {
    box.innerHTML = `<div class="banner"><span>⚠</span><div>No calibration run recorded yet.
      Run <code>python -m training.calibrate_thresholds</code> to generate the evidence.</div></div>`;
    return;
  }
  const cur = d.current, rec = d.recommended;
  const shifts = d.distributions;
  const row = (label, e, cls) => `<tr class="${cls || ''}">
    <td><b>${esc(label)}</b></td>
    <td class="num">${e.threshold_low} / ${e.threshold_high}</td>
    ${shifts.map(s => `<td class="num" style="color:${e.per_shift[s].critical_miss_rate > 0.02 ? 'var(--hi)' : 'var(--ok)'}">
      ${(e.per_shift[s].critical_miss_rate * 100).toFixed(2)}%</td>`).join('')}
    <td class="num">${(e.worst_critical_miss * 100).toFixed(2)}%</td>
    <td class="num">${e.mean_storage_index.toFixed(3)}</td></tr>`;

  const better = rec.worst_critical_miss < cur.worst_critical_miss;
  const cheaper = rec.mean_storage_index < cur.mean_storage_index;

  box.innerHTML = `
    <div class="banner ${better ? 'good' : 'info'}"><span>${better ? '✓' : 'ℹ'}</span><div>
      The tier boundaries were originally calibrated on a single dataset. This sweep
      re-tests them across <b>${shifts.length} distributions</b> on
      <b>${fmtNum(d.samples)}</b> segments each.
      ${better ? `Moving from <b>${cur.threshold_low}/${cur.threshold_high}</b> to
        <b>${rec.threshold_low}/${rec.threshold_high}</b> cuts worst-case irreversible loss from
        <b>${(cur.worst_critical_miss * 100).toFixed(2)}%</b> to
        <b>${(rec.worst_critical_miss * 100).toFixed(2)}%</b>${cheaper
        ? ' <b>while using less storage</b>' : ''}.`
        : 'The current boundaries hold up across all tested distributions.'}
    </div></div>

    <div class="card">
      <h3 style="margin:0 0 4px;font-size:14px">Irreversible loss under distribution shift</h3>
      <p class="sub" style="margin:0 0 16px"><b>Critical miss</b> = a segment with expert
        severity ≥ 0.80 tiered LOW. LOW keeps keyframes only, so a miss here destroys the
        moving footage of a serious incident. Lower is safer; this is the metric the
        boundaries should protect.</p>
      <table class="tbl">
        <tr><th>Boundary set</th><th class="num">low / high</th>
          ${shifts.map(s => `<th class="num">${esc(s)}</th>`).join('')}
          <th class="num">worst</th><th class="num">storage</th></tr>
        ${row('Current', cur)}
        ${row('Recommended', rec, 'hl')}
      </table>
      <div class="toolbar" style="margin:18px 0 0">
        <button class="btn" id="cal-apply" ${S.role === 'Administrator' ? '' : 'disabled'}>
          Apply recommended boundaries</button>
        <span class="sub" style="margin:0">Live config:
          <b>${esc(d.current_config.threshold_low)} / ${esc(d.current_config.threshold_high)}</b>
          ${S.role === 'Administrator' ? '' : '· administrator only'}</span>
      </div>
    </div>

    <div class="card" style="margin-top:14px">
      <h3 style="margin:0 0 4px;font-size:14px">Storage cost vs irreversible loss</h3>
      <p class="sub" style="margin:0 0 14px">Each point is one boundary pair. The usable
        region is the bottom-left: cheap storage <em>and</em> low loss.</p>
      ${scatterChart([{
        name: 'boundary pairs (worst-case across distributions)', color: '#7dd3fc',
        points: d.grid.map(g => [g.mean_storage_index, g.worst_critical_miss]),
      }], { xLabel: 'mean storage index (1.0 = keep everything lossless)' })}
    </div>`;

  const btn = $('#cal-apply');
  if (btn) btn.onclick = async () => {
    if (!confirm(`Apply ${rec.threshold_low} / ${rec.threshold_high} to live configuration?\n\n`
      + 'This changes how future uploads are tiered. Already-processed segments keep their tier.')) return;
    const r = await api('/calibration/apply', { method: 'POST' });
    toast(`Thresholds set to ${r.threshold_low} / ${r.threshold_high}`);
    wfCalibration();
  };
}

/* ==========================================================================
   Review Queue — the Tier-3 deletion safeguard
   ========================================================================== */
let _rqSort = 'deadline';

VIEWS.review = async () => {
  V.innerHTML = `<h2>Degradation Review Queue</h2>
    <p class="sub">Footage scheduled for irreversible degradation, held so a wrong
      significance score can be caught while the video still exists.</p>
    <div id="rq-body"><div class="sub">Loading…</div></div>`;
  await loadReviewQueue();
};

async function loadReviewQueue() {
  const d = await api('/review-queue');
  const s = d.summary || {};
  const pend = (s.pending || {}).count || 0;
  const items = [...d.items];
  items.sort((a, b) => _rqSort === 'ssig'
    ? (b.seg_ssig || 0) - (a.seg_ssig || 0)
    : String(a.purge_after).localeCompare(String(b.purge_after)));

  $('#rq-body').innerHTML = `
    <div class="banner info"><span>◷</span><div>
      Tier 3 keeps a <b>single keyframe</b> — the moving footage is destroyed and
      cannot be recovered. Rather than deleting at scoring time, LOW segments are
      held here for <b>${esc(d.grace_hours)} hours</b>. <b>Restore</b> promotes a
      segment and keeps its video; <b>Degrade now</b> confirms the deletion early.
      Anything still pending at its deadline is swept automatically.
      Set <code>tier3_grace_hours</code> to 0 to disable the hold entirely.
    </div></div>

    <div class="row" style="margin-bottom:16px">
      ${[['Awaiting review', pend, pend ? 'var(--warn)' : 'var(--ok)'],
         ['Video held on disk', fmtBytes((s.pending || {}).bytes || 0)],
         ['Rescued', (s.restored || {}).count || 0, 'var(--ok)'],
         ['Degraded', (s.purged || {}).count || 0, 'var(--dimmer)'],
         ['Next deadline', s.next_purge_at ? fmtCountdown(s.next_purge_at) : '—',
           'var(--info)']]
        .map(([k, v, c]) => `<div class="card stat"><div class="k">${k}</div>
          <div class="v" style="font-size:23px;color:${c || 'var(--text)'}">${v}</div></div>`).join('')}
    </div>

    ${items.length ? `<div class="bulkbar">
      <label style="font-size:11px;color:var(--dim);display:flex;gap:7px;align-items:center">Sort
        <select id="rq-sort" class="cine-sel" style="color:var(--text);background:rgba(5,7,12,.6);border-color:var(--line2)">
          <option value="deadline" ${_rqSort === 'deadline' ? 'selected' : ''}>soonest deadline</option>
          <option value="ssig" ${_rqSort === 'ssig' ? 'selected' : ''}>highest significance</option>
        </select></label>
      <button class="btn sm" id="rq-refresh">Refresh</button>
      <span class="cine-spacer"></span>
      <button class="btn sm" id="rq-rescue-all">Rescue all above Ssig 0.30</button>
      ${S.role === 'Administrator'
        ? '<button class="btn sm danger" id="rq-sweep">Run expiry sweep</button>' : ''}
    </div>` : ''}

    <div id="rq-list">${items.length ? items.map(rqCard).join('')
      : `<div class="banner good"><span>✓</span><div>Nothing is awaiting degradation.
         Every LOW-tier segment has been reviewed or its grace period has passed.</div></div>`}</div>`;

  const so = $('#rq-sort');
  if (so) so.onchange = e => { _rqSort = e.target.value; loadReviewQueue(); };
  $('#rq-refresh') && ($('#rq-refresh').onclick = loadReviewQueue);

  const all = $('#rq-rescue-all');
  if (all) all.onclick = async () => {
    const targets = items.filter(i => (i.seg_ssig || 0) >= 0.30 && i.exists);
    if (!targets.length) { toast('Nothing above Ssig 0.30 is held'); return; }
    if (!confirm(`Restore ${targets.length} segment(s) to MEDIUM and keep their video?`)) return;
    for (const t of targets) {
      const fd = new FormData(); fd.append('tier', 'MEDIUM');
      await api('/review-queue/' + t.id + '/restore', { method: 'POST', body: fd })
        .catch(() => {});
    }
    toast(`${targets.length} segment(s) rescued`);
    loadReviewQueue();
  };

  const sw = $('#rq-sweep');
  if (sw) sw.onclick = async () => {
    if (!confirm('Permanently degrade every held segment past its deadline?\n\n'
      + 'Their video is deleted; only keyframes remain.')) return;
    const r = await api('/review-queue/sweep', { method: 'POST' });
    toast(`${r.purged} degraded, ${fmtBytes(r.bytes_reclaimed)} reclaimed`);
    loadReviewQueue();
  };
}

function rqCard(i) {
  const msLeft = new Date(i.purge_after + 'Z') - Date.now();
  const soon = msLeft < 3600e3;
  const graceMs = new Date(i.purge_after + 'Z') - new Date(i.queued_at + 'Z');
  const elapsed = Math.max(0, Math.min(100, (1 - msLeft / (graceMs || 1)) * 100));
  const thumb = i.thumb ? `/api/thumb/${i.segment_id}` : '';
  return `<div class="rq-card ${soon ? 'soon' : ''}" id="rq-${i.id}">
    <div class="degrade">
      <div class="pane keep">
        ${thumb ? `<img src="${thumb}" onerror="this.style.opacity=.15">` : ''}
        <span class="tag">KEYFRAME · KEPT</span></div>
      <div class="arrow">→</div>
      <div class="pane gone">
        ${thumb ? `<img src="${thumb}" onerror="this.style.opacity=.15">` : ''}
        <span class="tag">${fmtBytes(i.bytes)} VIDEO · DESTROYED</span></div>
    </div>

    <div>
      <div class="rq-meta">
        <b style="font-family:var(--mono);font-size:15px">#${String(i.idx ?? 0).padStart(3, '0')}</b>
        <span class="pill ${i.tier}">${esc(i.tier)}</span>
        <span class="chip">Ssig ${(i.seg_ssig || 0).toFixed(2)}</span>
        <span style="font-size:11.5px;color:var(--dim)">${esc(i.project_name || '')}${
          i.footage_name ? ' / ' + esc(i.footage_name) : ''}</span>
      </div>
      <div class="rq-why">
        Scheduled because: <b>${esc(i.reason || 'tiered LOW')}</b>.
        ${i.exists ? `Once degraded, only the keyframe above remains — the
          ${fmtBytes(i.bytes)} of moving footage is unrecoverable.`
        : '<span style="color:var(--hi)">The held file is no longer on disk.</span>'}
      </div>
      <div class="rq-bar"><i style="width:${elapsed.toFixed(1)}%"></i></div>
      <div style="display:flex;justify-content:space-between;font-size:10.5px;color:var(--dimmer);font-family:var(--mono)">
        <span>held ${esc((i.queued_at || '').slice(0, 16).replace('T', ' '))}</span>
        <span class="rq-count ${soon ? 'soon' : ''}">${fmtCountdown(i.purge_after)}</span>
      </div>
    </div>

    <div class="rq-acts">
      <button class="btn sm" onclick="openPlayer('${i.segment_id}')">▶ Review footage</button>
      <button class="btn sm" onclick="restoreHeld(${i.id},'MEDIUM')" ${i.exists ? '' : 'disabled'}>Restore → MEDIUM</button>
      <button class="btn sm" onclick="restoreHeld(${i.id},'HIGH')" ${i.exists ? '' : 'disabled'}>Restore → HIGH</button>
      <button class="btn sm danger" onclick="purgeHeld(${i.id})">Degrade now</button>
    </div></div>`;
}

window.restoreHeld = async (id, tier) => {
  const fd = new FormData(); fd.append('tier', tier);
  await api('/review-queue/' + id + '/restore', { method: 'POST', body: fd });
  toast('Segment restored to ' + tier + ' — footage kept');
  loadReviewQueue();
};
window.purgeHeld = async id => {
  if (!confirm('Permanently delete this footage now? This cannot be undone.')) return;
  await api('/review-queue/' + id + '/purge', { method: 'POST' });
  toast('Footage deleted');
  loadReviewQueue();
};

/* ==========================================================================
   Cinematic detection player
   ==========================================================================
   A custom player rather than the browser's default controls, because the
   point of this view is the *evidence*: boxes drawn from the models' own
   output, detection ticks on the scrubber so you can jump straight to them,
   and a filmstrip for stepping between segments. Native controls can do none
   of that, and their chrome fights the footage.                              */
const BOX_COLORS = {
  t1critical: '#ff3b5c',   // Gun / Rifle / Knife / Fire — immediate hazard
  t1: '#ffb020',           // other Track 1 suspicious classes
  t2: '#7dd3fc',           // Track 2 context
};
const boxColor = b => b.track === 1
  ? (b.critical ? BOX_COLORS.t1critical : BOX_COLORS.t1) : BOX_COLORS.t2;

const ICON = {
  play: '<svg viewBox="0 0 24 24"><path d="M8 5.14v13.72L19 12z"/></svg>',
  pause: '<svg viewBox="0 0 24 24"><path d="M6 5h4v14H6zm8 0h4v14h-4z"/></svg>',
  back: '<svg viewBox="0 0 24 24"><path d="M11 7 4 12l7 5zm8 0-7 5 7 5z"/></svg>',
  fwd: '<svg viewBox="0 0 24 24"><path d="M13 7v10l7-5zM5 7v10l7-5z"/></svg>',
  prev: '<svg viewBox="0 0 24 24"><path d="M7 6h2v12H7zm3 6 8-6v12z"/></svg>',
  next: '<svg viewBox="0 0 24 24"><path d="M15 6h2v12h-2zM6 6l8 6-8 6z"/></svg>',
  full: '<svg viewBox="0 0 24 24"><path d="M4 9V4h5v2H6v3zm11-5h5v5h-2V6h-3zM4 15h2v3h3v2H4zm14 3v-3h2v5h-5v-2z"/></svg>',
  exit: '<svg viewBox="0 0 24 24"><path d="M9 4v5H4V7h3V4zm6 0h2v3h3v2h-5zm0 16v-5h5v2h-3v3zm-6 0H7v-3H4v-2h5z"/></svg>',
};

const P = {
  seg: null, segments: [], idx: 0, overlay: null,
  video: null, canvas: null, stage: null, raf: null,
  showBoxes: true, ambient: true, blob: null, continuous: false,
  idleTimer: null, ambTimer: null, keys: null, unbind: null,
};

/* ---------------------------------------------------------------- boxes --
   Detections are sampled at ~1 fps. Snapping boxes between samples reads as
   a stutter, so boxes are interpolated toward the next sample of the same
   class and faded at the edges of their sample window. The underlying data
   is still 1 fps — this smooths presentation, it does not invent detections,
   and the strip below the video shows the true sample points.               */
function boxesAt(t) {
  const frames = P.overlay?.frames || [];
  if (!frames.length) return [];
  let i = -1;
  for (let k = 0; k < frames.length; k++) { if (frames[k].t <= t + 0.25) i = k; else break; }
  if (i < 0) i = 0;
  const cur = frames[i], nxt = frames[i + 1];
  if (!nxt) return cur.boxes.map(b => ({ ...b, alpha: 1 }));

  const span = Math.max(0.001, nxt.t - cur.t);
  const f = Math.max(0, Math.min(1, (t - cur.t) / span));
  const used = new Set();
  const out = cur.boxes.map(b => {
    // match the nearest same-class box in the next sample by centroid
    let best = null, bestD = 1e9, bestJ = -1;
    nxt.boxes.forEach((n, j) => {
      if (n.label !== b.label || used.has(j)) return;
      const d = Math.hypot((n.box[0] + n.box[2]) / 2 - (b.box[0] + b.box[2]) / 2,
                           (n.box[1] + n.box[3]) / 2 - (b.box[1] + b.box[3]) / 2);
      if (d < bestD) { bestD = d; best = n; bestJ = j; }
    });
    // only track across a plausible jump; beyond that it is a different object
    if (best && bestD < 0.3) {
      used.add(bestJ);
      return { ...b, alpha: 1,
        box: b.box.map((v, k) => v + (best.box[k] - v) * f),
        conf: b.conf + (best.conf - b.conf) * f };
    }
    return { ...b, alpha: 1 - f * 0.85 };      // disappears before the next sample
  });
  // classes that only appear in the next sample fade in
  nxt.boxes.forEach((n, j) => {
    if (!used.has(j) && !cur.boxes.some(b => b.label === n.label)) {
      out.push({ ...n, alpha: f * 0.85 });
    }
  });
  return out;
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function drawBoxes() {
  const { video, canvas } = P;
  if (!video || !canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const w = video.clientWidth, h = video.clientHeight;
  if (!w || !h) return;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const t = video.currentTime || 0;
  const cur = $('#cine-cursor');
  if (cur && P.overlay?.duration) cur.style.left = (t / P.overlay.duration * 100) + '%';
  if (!P.showBoxes) return;

  const pulse = 0.75 + 0.25 * Math.sin(Date.now() / 320);
  for (const b of boxesAt(t)) {
    const [x1, y1, x2, y2] = b.box;
    const X = x1 * w, Y = y1 * h, W = (x2 - x1) * w, H = (y2 - y1) * h;
    if (W < 2 || H < 2) continue;
    const col = boxColor(b);
    ctx.globalAlpha = Math.max(0, Math.min(1, b.alpha ?? 1));

    // faint full outline, then bright corner brackets — reads as a targeting
    // HUD rather than a debug rectangle, and keeps the footage visible
    ctx.strokeStyle = col;
    ctx.lineWidth = 1;
    ctx.globalAlpha *= 0.28;
    roundRect(ctx, X, Y, W, H, 4); ctx.stroke();
    ctx.globalAlpha = Math.max(0, Math.min(1, b.alpha ?? 1));

    const arm = Math.max(9, Math.min(26, Math.min(W, H) * 0.26));
    ctx.lineWidth = b.critical ? 3 : 2.2;
    ctx.lineCap = 'round';
    if (b.critical) { ctx.shadowColor = col; ctx.shadowBlur = 14 * pulse; }
    ctx.beginPath();
    ctx.moveTo(X, Y + arm); ctx.lineTo(X, Y); ctx.lineTo(X + arm, Y);
    ctx.moveTo(X + W - arm, Y); ctx.lineTo(X + W, Y); ctx.lineTo(X + W, Y + arm);
    ctx.moveTo(X + W, Y + H - arm); ctx.lineTo(X + W, Y + H); ctx.lineTo(X + W - arm, Y + H);
    ctx.moveTo(X + arm, Y + H); ctx.lineTo(X, Y + H); ctx.lineTo(X, Y + H - arm);
    ctx.stroke();
    ctx.shadowBlur = 0;

    const label = `${b.label}  ${(b.conf * 100).toFixed(0)}%`;
    ctx.font = '600 11px ui-monospace, SFMono-Regular, Menlo, monospace';
    const tw = ctx.measureText(label).width + 16;
    const ly = Y - 24 < 0 ? Y + 6 : Y - 24;
    ctx.fillStyle = col;
    roundRect(ctx, X, ly, tw, 18, 5); ctx.fill();
    ctx.fillStyle = '#080b12';
    ctx.fillText(label, X + 8, ly + 13);
  }
  ctx.globalAlpha = 1;
}

/* ------------------------------------------------------------- ambient --
   Sample the frame at tiny resolution and paint it behind the stage; CSS
   blurs and scales it. Deliberately ~5fps: it is decoration, and running it
   per-frame would compete with the overlay for main-thread time.            */
function ambientTick() {
  const amb = $('#cine-amb');
  if (!amb || !P.video || !P.ambient) return;
  try {
    const c = amb.getContext('2d');
    c.drawImage(P.video, 0, 0, amb.width, amb.height);
  } catch { /* frame not ready */ }
}

function playerLoop() {
  drawBoxes();
  P.raf = requestAnimationFrame(playerLoop);
}

function cineHint(text) {
  const el = $('#cine-hint');
  if (!el) return;
  el.textContent = text;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 750);
}

function markIdle() {
  const st = P.stage;
  if (!st) return;
  st.classList.remove('idle');
  clearTimeout(P.idleTimer);
  // only hide chrome while actually playing — a paused frame should keep its
  // controls visible
  P.idleTimer = setTimeout(() => {
    if (P.video && !P.video.paused) st.classList.add('idle');
  }, 2400);
}

function fmtClock(s) {
  s = Math.max(0, s || 0);
  const m = Math.floor(s / 60), r = Math.floor(s % 60);
  return `${m}:${String(r).padStart(2, '0')}`;
}

function stopPlayer() {
  if (P.raf) { cancelAnimationFrame(P.raf); P.raf = null; }
  if (P.ambTimer) { clearInterval(P.ambTimer); P.ambTimer = null; }
  if (P.idleTimer) { clearTimeout(P.idleTimer); P.idleTimer = null; }
  if (P.blob) { URL.revokeObjectURL(P.blob); P.blob = null; }
  if (P.unbind) { P.unbind(); P.unbind = null; }
  if (P.keys) { document.removeEventListener('keydown', P.keys, true); P.keys = null; }
  // NB: `continuous`, `autoplay` and `seekOnOpen` deliberately survive this —
  // stopPlayer() runs on every open, and clearing them would break stepping
  // from one segment to the next.
  P.video = P.canvas = P.overlay = P.stage = null;
}

/* Sizing and drawing are event-driven as well as rAF-driven: rAF is paused
   whenever the tab is not painted, and the video resizes as metadata loads. */
function bindRedraw(video) {
  const redraw = () => drawBoxes();
  const evs = ['loadedmetadata', 'loadeddata', 'seeked', 'timeupdate', 'play', 'pause'];
  evs.forEach(e => video.addEventListener && video.addEventListener(e, redraw));
  let ro = null;
  if (window.ResizeObserver && video instanceof Element) {
    ro = new ResizeObserver(redraw); ro.observe(video);
  }
  const onWin = () => redraw();
  window.addEventListener('resize', onWin);
  document.addEventListener('visibilitychange', onWin);
  P.unbind = () => {
    evs.forEach(e => video.removeEventListener && video.removeEventListener(e, redraw));
    if (ro) ro.disconnect();
    window.removeEventListener('resize', onWin);
    document.removeEventListener('visibilitychange', onWin);
  };
}

/* ------------------------------------------------------------- markup --- */
function detectionStrip(overlay) {
  if (!overlay?.frames?.length) return '';
  const dur = overlay.duration || 15;
  const byLabel = {};
  overlay.frames.forEach(f => f.boxes.forEach(b => {
    (byLabel[b.label] = byLabel[b.label] ||
      { label: b.label, track: b.track, critical: b.critical, hits: [], best: 0 });
    byLabel[b.label].hits.push(f.t);
    byLabel[b.label].best = Math.max(byLabel[b.label].best, b.conf);
  }));
  const rows = Object.values(byLabel).sort((a, b) => (a.track - b.track) || (b.best - a.best));
  const slot = Math.max(1.6, 100 / Math.max(2, overlay.frames.length));
  return `<div class="side-block"><h4>Detections over time</h4>
    <div style="position:relative">
      ${rows.map(r => `<div class="dstrip-row">
        <span class="nm ${r.track === 1 ? (r.critical ? 't1c' : 't1w') : 't2c'}"
          title="${esc(r.label)}">${esc(r.label)}</span>
        <span class="dstrip-track" data-seek="1">${r.hits.map(t =>
          `<i style="left:${(t / dur * 100).toFixed(2)}%;width:${slot.toFixed(2)}%;background:${
            r.track === 1 ? (r.critical ? BOX_COLORS.t1critical : BOX_COLORS.t1) : BOX_COLORS.t2}"></i>`).join('')}
        </span></div>`).join('')}
      <div class="dstrip-cursor" id="cine-cursor" style="left:0"></div>
    </div>
    <div class="dlegend">
      <span><i style="background:${BOX_COLORS.t1critical}"></i>critical</span>
      <span><i style="background:${BOX_COLORS.t1}"></i>suspicious</span>
      <span><i style="background:${BOX_COLORS.t2}"></i>context</span>
      <span style="color:var(--dimmer)">~1 fps samples</span>
    </div></div>`;
}

function scrubTicks(overlay) {
  if (!overlay?.frames?.length) return '';
  const dur = overlay.duration || 15;
  const seen = new Map();
  overlay.frames.forEach(f => f.boxes.forEach(b => {
    if (b.track !== 1) return;                 // only threat classes earn a tick
    const key = f.t + '|' + b.label;
    if (!seen.has(key)) seen.set(key, { t: f.t, col: boxColor(b) });
  }));
  return [...seen.values()].map(d =>
    `<span class="cine-tick" style="left:${(d.t / dur * 100).toFixed(2)}%;background:${d.col};
      box-shadow:0 0 7px ${d.col}"></span>`).join('');
}

function playerSidebar(seg, overlay) {
  const fus = seg.fusion || {};
  const col = seg.tier === 'HIGH' ? 'var(--hi)' : seg.tier === 'MEDIUM' ? 'var(--warn)' : 'var(--ok)';
  const objs = seg.objects || [];
  const t1 = objs.filter(o => o.track === 1), t2 = objs.filter(o => o.track !== 1);
  return `
    ${detectionStrip(overlay)}
    <div class="side-block"><h4>Decision</h4>
      <div style="display:flex;gap:9px;align-items:baseline;margin-bottom:10px">
        <b style="font-family:var(--mono);font-size:26px;color:${col}">${(seg.ssig || 0).toFixed(3)}</b>
        <span class="pill ${seg.tier}">${esc(seg.tier || '—')}</span>
      </div>
      <dl class="kv">
        <dt>Action</dt><dd>${esc((seg.actions || [])[0]?.action || '—')}</dd>
        <dt>Hazard</dt><dd>${esc(seg.hazard_level || 'none')}</dd>
        <dt>Sentiment</dt><dd>${(seg.sentiment_score || 0).toFixed(3)}</dd>
      </dl></div>
    ${(fus.trace || []).length ? `<div class="side-block"><h4>Score contribution</h4>
      ${fus.trace.map(t => {
        const c = t.channel === 'action' ? '#7dd3fc' : t.channel === 'objects' ? '#ffb020' : '#26d07c';
        const v = t.weight * t.value;
        return `<div class="hbar" style="grid-template-columns:58px 1fr 44px">
          <span class="nm">${esc(t.channel)}</span>
          <span class="tk"><i style="width:${Math.min(100, v / 0.5 * 100)}%;background:${c}"></i></span>
          <span class="vv">${v.toFixed(3)}</span></div>`;
      }).join('')}</div>` : ''}
    ${(fus.modifiers || []).length ? `<div class="side-block"><h4>Modifiers</h4>
      <div class="mods">${fus.modifiers.map(m =>
        `<span class="mod ${m.amount < 0 ? 'neg' : ''}" title="${esc(m.why)}">${esc(m.name)} ${m.amount >= 0 ? '+' : ''}${m.amount}</span>`).join('')}</div></div>` : ''}
    ${t1.length ? `<div class="side-block"><h4>Track 1 — suspicious</h4>
      ${t1.map(o => `<span class="chip" style="border-color:${o.critical ? BOX_COLORS.t1critical : BOX_COLORS.t1};color:${o.critical ? BOX_COLORS.t1critical : BOX_COLORS.t1}">${esc(o.label)} · ${(o.confidence * 100).toFixed(0)}% · ${o.frame_hits || 0}f</span>`).join('')}</div>` : ''}
    ${t2.length ? `<div class="side-block"><h4>Track 2 — context</h4>
      ${t2.slice(0, 10).map(o => `<span class="chip">${esc(o.label)} · ${(o.confidence * 100).toFixed(0)}%${o.instances > 1 ? ' · ×' + o.instances : ''}</span>`).join('')}</div>` : ''}
    <div class="side-block"><h4>Shortcuts</h4>
      <div class="dlegend" style="gap:9px">
        <span><code>space</code> play</span><span><code>← →</code> 5s</span>
        <span><code>, .</code> frame</span><span><code>b</code> boxes</span>
        <span><code>a</code> ambient</span><span><code>f</code> fullscreen</span>
        <span><code>[ ]</code> segment</span>
      </div></div>`;
}

/* The whole recording as one bar: every segment sized by its real duration,
   coloured by tier, with its Ssig as a fill and Track 1 detections as ticks.
   Clicking anywhere jumps to that moment — this is the view an operator scans
   to find an incident, rather than opening segments one at a time. */
function masterTimeline(current) {
  const segs = P.segments;
  if (segs.length < 2) return '';
  const total = segs.reduce((a, s) => a + Math.max(0.1, (s.end_time - s.start_time) || 15), 0);
  let acc = 0;
  const bars = segs.map(s => {
    const dur = Math.max(0.1, (s.end_time - s.start_time) || 15);
    const w = dur / total * 100;
    const col = s.tier === 'HIGH' ? 'var(--hi)' : s.tier === 'MEDIUM' ? 'var(--warn)'
      : s.motion ? 'var(--ok)' : 'var(--dimmer)';
    const start = acc; acc += dur;
    const marks = (s.tier === 'HIGH' || (s.objects || []).some(o => o.critical))
      ? `<i class="dt" style="left:50%;background:#fff"></i>` : '';
    return `<div class="mtl-seg ${s.id === current ? 'on' : ''}"
      style="width:${w.toFixed(3)}%;background:${col}"
      data-seg="${s.id}" data-start="${start.toFixed(2)}" data-dur="${dur.toFixed(2)}"
      title="#${String(s.idx).padStart(3, '0')} · ${s.tier || 'static'} · Ssig ${(s.ssig || 0).toFixed(2)} · ${fmtClock(start)}">
      <span class="ss" style="height:${Math.round((s.ssig || 0) * 78)}%"></span>${marks}</div>`;
  }).join('');
  return `<div class="mtl">
    <div class="mtl-head"><h4>Recording timeline — ${segs.length} segments</h4>
      <span class="mtl-time" id="mtl-time">—</span></div>
    <div class="mtl-track" id="mtl-track">${bars}<div class="mtl-play" id="mtl-play" style="left:0"></div></div>
    <div class="mtl-ruler"><span>0:00</span><span>${fmtClock(total)}</span></div>
    <div class="mtl-legend">
      <span><i style="background:var(--hi)"></i>HIGH — lossless</span>
      <span><i style="background:var(--warn)"></i>MEDIUM — re-encoded</span>
      <span><i style="background:var(--ok)"></i>LOW — keyframe only</span>
      <span><i style="background:var(--dimmer)"></i>static — skipped</span>
      <span style="color:var(--dimmer)">bar height = Ssig</span>
    </div></div>`;
}

function filmstrip(current) {
  if (P.segments.length < 2) return '';
  return `<div class="filmstrip">${P.segments.map((s, i) => {
    const c = s.tier === 'HIGH' ? 'var(--hi)' : s.tier === 'MEDIUM' ? 'var(--warn)' : 'var(--ok)';
    return `<div class="film ${s.id === current ? 'on' : ''}" onclick="openPlayer('${s.id}')">
      ${s.thumb ? `<img src="/api/thumb/${s.id}" loading="lazy" onerror="this.style.opacity=.15">`
                : '<div style="height:58px"></div>'}
      <div class="cap"><span>#${String(s.idx).padStart(3, '0')}</span>
        <span style="display:flex;align-items:center;gap:5px">${(s.ssig || 0).toFixed(2)}
          <i class="tg" style="background:${c}"></i></span></div></div>`;
  }).join('')}</div>`;
}

/* ------------------------------------------------------------- open ----- */
window.openPlayer = async (sid, list) => {
  if (Array.isArray(list) && list.length) {
    P.segments = list;
  }
  P.idx = Math.max(0, P.segments.findIndex(s => s.id === sid));
  stopPlayer();

  let seg;
  try { seg = await api('/segments/' + sid + '/explain'); }
  catch { toast('Segment unavailable'); return; }
  const overlay = await api('/segments/' + sid + '/overlay').catch(() => null);
  P.overlay = overlay; P.seg = seg;

  const hasList = P.segments.length > 1;
  $('#modal-title').textContent = `Segment #${String(seg.idx).padStart(3, '0')}`;
  $('.modal').classList.add('cinema');
  $('#modal-body').innerHTML = `<div class="cine">
    <div class="cine-main">
      <div class="stage paused" id="cine-stage">
        <canvas class="ambient" id="cine-amb" width="32" height="18"></canvas>
        <div class="screen" id="cine-screen">
          <div class="sub" style="padding:70px;text-align:center;margin:0">Loading media…</div>
        </div>
        <canvas class="boxes" id="cine-boxes"></canvas>
        <div class="scrim top"></div><div class="scrim bot"></div>
        <div class="cine-badge">
          <span class="id">#${String(seg.idx).padStart(3, '0')}</span>
          <span class="ss" style="color:${seg.tier === 'HIGH' ? '#ff8097' : seg.tier === 'MEDIUM' ? '#ffca6b' : '#6fe3ac'}">
            ${esc(seg.tier || '—')} · Ssig ${(seg.ssig || 0).toFixed(2)}</span>
        </div>
        <div class="cine-big" id="cine-big"><i>${ICON.play}</i></div>
        <div class="cine-hint" id="cine-hint"></div>
        <div class="cine-ctl" id="cine-ctl">
          <div class="cine-scrub" id="cine-scrub">
            <div class="cine-track">
              <div class="cine-played" id="cine-played" style="width:0%"></div>
              ${scrubTicks(overlay)}
              <div class="cine-head" id="cine-headdot" style="left:0%"></div>
            </div>
          </div>
          <div class="cine-row">
            <button class="cine-btn big" id="c-play" title="Play / pause (space)">${ICON.play}</button>
            <button class="cine-btn" id="c-back" title="Back 5s (←)">${ICON.back}</button>
            <button class="cine-btn" id="c-fwd" title="Forward 5s (→)">${ICON.fwd}</button>
            <span class="cine-time" id="c-time">0:00 / 0:00</span>
            <span class="cine-spacer"></span>
            ${hasList ? `<button class="cine-btn" id="c-prev" title="Previous segment ([)" ${P.idx === 0 ? 'disabled' : ''}>${ICON.prev}</button>
            <button class="cine-btn" id="c-next" title="Next segment (])" ${P.idx >= P.segments.length - 1 ? 'disabled' : ''}>${ICON.next}</button>` : ''}
            ${hasList ? `<button class="cine-chip ${P.continuous ? 'live' : 'off'}" id="c-cont"
              title="Play the whole recording continuously (c)">CONTINUOUS</button>` : ''}
            <button class="cine-chip" id="c-boxes" title="Toggle boxes (b)">BOXES</button>
            <button class="cine-chip" id="c-amb" title="Toggle ambient light (a)">AMBIENT</button>
            <select class="cine-sel" id="c-rate" title="Speed">
              <option>0.25</option><option>0.5</option><option selected>1</option>
              <option>1.5</option><option>2</option></select>
            <button class="cine-btn" id="c-full" title="Fullscreen (f)">${ICON.full}</button>
          </div>
        </div>
      </div>
      ${overlay && !overlay.available ? `<div class="banner info" style="margin-top:12px"><span>ℹ</span><div>
        ${esc(overlay.note || 'No per-frame detections recorded for this segment.')}</div></div>` : ''}
      ${masterTimeline(sid)}
      ${filmstrip(sid)}
    </div>
    <div class="cine-side">${playerSidebar(seg, overlay)}</div>
  </div>`;
  $('#modal-backdrop').classList.remove('hidden');
  P.stage = $('#cine-stage');
  P.canvas = $('#cine-boxes');

  await mountMedia(sid);
  wirePlayerControls(sid);
};

async function mountMedia(sid) {
  const screen = $('#cine-screen');
  try {
    const r = await fetch('/api/playback/' + sid, { headers: { 'x-session': S.token } });
    if (!r.ok) throw new Error('no media');
    const blob = await r.blob();
    P.blob = URL.createObjectURL(blob);

    if (blob.type.startsWith('image/')) {
      // Tier 3 keeps a keyframe only — show it as a still with its boxes.
      screen.innerHTML = `<img id="cine-img" src="${P.blob}">`;
      const img = $('#cine-img');
      const size = () => {
        P.video = { currentTime: 0, clientWidth: img.clientWidth,
                    clientHeight: img.clientHeight, paused: true };
        drawBoxes(); ambientFromImage(img);
      };
      img.onload = size; if (img.complete) size();
      if (window.ResizeObserver) { const ro = new ResizeObserver(size); ro.observe(img);
        P.unbind = () => ro.disconnect(); }
      $('#cine-ctl').style.display = 'none';
      P.stage.classList.remove('paused');
      return;
    }

    screen.innerHTML = `<video id="cine-video" src="${P.blob}" playsinline autoplay></video>`;
    P.video = $('#cine-video');
    bindRedraw(P.video);
    P.video.onloadedmetadata = () => {
      if (P.seekOnOpen != null) { P.video.currentTime = P.seekOnOpen; P.seekOnOpen = null; }
      if (P.autoplay) { P.autoplay = false; P.video.play().catch(() => {}); }
      // Trust the real media duration over the stored segment length: with
      // keyframe-aligned splitting they differ slightly, and the scrubber,
      // ticks and strip all have to line up with what is actually playing.
      if (P.overlay && P.video.duration) P.overlay.duration = P.video.duration;
      updateTime();
    };
    P.video.ontimeupdate = updateTime;
    P.video.onplay = () => { P.stage.classList.remove('paused'); syncPlayIcon(); markIdle(); };
    P.video.onpause = () => { P.stage.classList.add('paused'); syncPlayIcon();
      P.stage.classList.remove('idle'); };
    P.video.onended = () => P.stage.classList.add('paused');
    P.ambTimer = setInterval(ambientTick, 200);
    playerLoop();
  } catch {
    screen.innerHTML = `<div class="sub" style="padding:70px;text-align:center;margin:0">
      No playable media — this segment's video was discarded by its tier.</div>`;
    $('#cine-ctl').style.display = 'none';
  }
}

function ambientFromImage(img) {
  const amb = $('#cine-amb');
  if (!amb) return;
  try { amb.getContext('2d').drawImage(img, 0, 0, amb.width, amb.height); } catch {}
}

function updateTime() {
  if (!P.video) return;
  const d = P.video.duration || 0, t = P.video.currentTime || 0;
  const el = $('#c-time'); if (el) el.textContent = `${fmtClock(t)} / ${fmtClock(d)}`;
  const pct = d ? (t / d * 100) : 0;
  const pl = $('#cine-played'); if (pl) pl.style.width = pct + '%';
  const hd = $('#cine-headdot'); if (hd) hd.style.left = pct + '%';
}

function syncPlayIcon() {
  const b = $('#c-play');
  if (b && P.video) b.innerHTML = P.video.paused ? ICON.play : ICON.pause;
}

function wirePlayerControls(sid) {
  const stage = P.stage, v = P.video;
  const seekTo = frac => { if (v && v.duration) { v.currentTime = frac * v.duration; drawBoxes(); } };
  const toggle = () => { if (!v?.play) return; v.paused ? v.play() : v.pause(); };

  $('#c-play')?.addEventListener('click', toggle);
  $('#cine-big')?.addEventListener('click', toggle);
  $('#cine-screen')?.addEventListener('click', toggle);
  $('#c-back')?.addEventListener('click', () => { if (v) v.currentTime = Math.max(0, v.currentTime - 5); });
  $('#c-fwd')?.addEventListener('click', () => { if (v) v.currentTime = Math.min(v.duration, v.currentTime + 5); });
  $('#c-prev')?.addEventListener('click', () => openPlayer(P.segments[P.idx - 1].id));
  $('#c-next')?.addEventListener('click', () => openPlayer(P.segments[P.idx + 1].id));
  $('#c-rate')?.addEventListener('change', e => { if (v) v.playbackRate = parseFloat(e.target.value); });

  const bx = $('#c-boxes');
  bx?.addEventListener('click', () => {
    P.showBoxes = !P.showBoxes;
    bx.classList.toggle('off', !P.showBoxes);
    drawBoxes(); cineHint(P.showBoxes ? 'Boxes on' : 'Boxes off');
  });
  const ab = $('#c-amb');
  ab?.addEventListener('click', () => {
    P.ambient = !P.ambient;
    ab.classList.toggle('off', !P.ambient);
    stage.classList.toggle('noamb', !P.ambient);
    cineHint(P.ambient ? 'Ambient on' : 'Ambient off');
  });

  const full = $('#c-full');
  full?.addEventListener('click', () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else stage.requestFullscreen?.().catch(() => toast('Fullscreen unavailable'));
  });
  document.addEventListener('fullscreenchange', () => {
    const on = !!document.fullscreenElement;
    stage.classList.toggle('fs', on);
    if (full) full.innerHTML = on ? ICON.exit : ICON.full;
    drawBoxes();
  });

  // scrubbing — click and drag
  const scrub = $('#cine-scrub');
  if (scrub) {
    const at = e => {
      const b = scrub.getBoundingClientRect();
      return Math.max(0, Math.min(1, (e.clientX - b.left) / b.width));
    };
    let dragging = false;
    scrub.addEventListener('pointerdown', e => {
      dragging = true; scrub.setPointerCapture(e.pointerId); seekTo(at(e));
    });
    scrub.addEventListener('pointermove', e => { if (dragging) seekTo(at(e)); });
    scrub.addEventListener('pointerup', e => {
      dragging = false; scrub.releasePointerCapture(e.pointerId);
    });
  }
  // the detection strip doubles as a seek bar — click a hit to jump to it
  $$('.dstrip-track[data-seek]').forEach(el => el.addEventListener('click', e => {
    const b = el.getBoundingClientRect();
    seekTo(Math.max(0, Math.min(1, (e.clientX - b.left) / b.width)));
  }));

  // ---- master timeline: playhead, click-to-seek, continuous playback ----
  const mtl = $('#mtl-track');
  if (mtl) {
    const here = mtl.querySelector(`.mtl-seg[data-seg="${sid}"]`);
    const segStart = here ? parseFloat(here.dataset.start) : 0;
    const total = P.segments.reduce(
      (a, x) => a + Math.max(0.1, (x.end_time - x.start_time) || 15), 0);

    const paintHead = () => {
      const el = $('#mtl-play');
      if (!el || !v) return;
      const at = segStart + (v.currentTime || 0);
      el.style.left = (at / total * 100) + '%';
      const t = $('#mtl-time');
      if (t) t.textContent = `${fmtClock(at)} / ${fmtClock(total)}`;
    };
    v?.addEventListener?.('timeupdate', paintHead);
    paintHead();

    mtl.addEventListener('click', e => {
      const seg = e.target.closest('.mtl-seg');
      if (!seg) return;
      const b = seg.getBoundingClientRect();
      const into = Math.max(0, Math.min(1, (e.clientX - b.left) / b.width))
        * parseFloat(seg.dataset.dur);
      if (seg.dataset.seg === sid) {
        if (v) { v.currentTime = into; drawBoxes(); }
      } else {
        // remember where in the target segment to resume, then open it
        P.seekOnOpen = into;
        openPlayer(seg.dataset.seg);
      }
    });
  }

  const cont = $('#c-cont');
  cont?.addEventListener('click', () => {
    P.continuous = !P.continuous;
    cont.classList.toggle('live', P.continuous);
    cont.classList.toggle('off', !P.continuous);
    cineHint(P.continuous ? 'Continuous playback on' : 'Continuous off');
  });
  if (v) {
    v.addEventListener('ended', () => {
      if (P.continuous && P.idx < P.segments.length - 1) {
        P.autoplay = true;
        openPlayer(P.segments[P.idx + 1].id);
      }
    });
  }

  stage.addEventListener('mousemove', markIdle);
  stage.addEventListener('mouseleave', () => {
    if (v && !v.paused) stage.classList.add('idle');
  });
  markIdle();
  syncPlayIcon();

  P.keys = e => {
    if ($('#modal-backdrop').classList.contains('hidden')) return;
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'select' || tag === 'textarea') return;
    const step = 1 / 25;                     // one frame at 25fps, near enough
    const map = {
      ' ': () => { toggle(); cineHint(v?.paused ? 'Paused' : 'Playing'); },
      k: toggle,
      ArrowLeft: () => { if (v) { v.currentTime = Math.max(0, v.currentTime - 5); cineHint('− 5s'); } },
      ArrowRight: () => { if (v) { v.currentTime = Math.min(v.duration, v.currentTime + 5); cineHint('+ 5s'); } },
      ',': () => { if (v) { v.pause(); v.currentTime = Math.max(0, v.currentTime - step); cineHint('◀ frame'); } },
      '.': () => { if (v) { v.pause(); v.currentTime = Math.min(v.duration, v.currentTime + step); cineHint('frame ▶'); } },
      b: () => bx?.click(),
      a: () => ab?.click(),
      f: () => full?.click(),
      '[': () => { if (P.idx > 0) openPlayer(P.segments[P.idx - 1].id); },
      ']': () => { if (P.idx < P.segments.length - 1) openPlayer(P.segments[P.idx + 1].id); },
      c: () => cont?.click(),
    };
    const fn = map[e.key];
    if (fn) { e.preventDefault(); e.stopPropagation(); fn(); markIdle(); }
  };
  document.addEventListener('keydown', P.keys, true);
}

/* ==========================================================================
   Analytics — what the models found and what the engine decided
   ========================================================================== */
function hbars(rows, opts = {}) {
  if (!rows.length) return '<div class="sub" style="margin:0">No data.</div>';
  const max = Math.max(...rows.map(r => r.value)) || 1;
  return `<div class="hbars">${rows.map(r => `
    <div class="hbar ${r.critical ? 'crit' : ''}">
      <span class="nm" title="${esc(r.label)}">${esc(r.label)}</span>
      <span class="tk"><i style="width:${(r.value / max * 100).toFixed(1)}%;background:${r.color || 'linear-gradient(90deg,rgba(125,211,252,.9),rgba(76,141,255,.5))'}"></i></span>
      <span class="vv">${esc(r.right ?? r.value)}</span>
    </div>`).join('')}</div>`;
}

function histogram(buckets, opts = {}) {
  if (!buckets.length) return '<div class="sub" style="margin:0">No data.</div>';
  const max = Math.max(...buckets.map(b => b.count)) || 1;
  const marks = opts.marks || [];
  const band = b => {
    if (!opts.bands) return '';
    const mid = (b.lo + b.hi) / 2;
    return mid > (opts.bands.high ?? 0.7) ? 'hi'
         : mid > (opts.bands.low ?? 0.4) ? 'mid' : 'lo';
  };
  return `<div style="position:relative">
    <div class="histo">${buckets.map(b =>
      `<div class="hb ${band(b)}" style="height:${(b.count / max * 100).toFixed(1)}%"
        title="${b.lo}–${b.hi}: ${b.count}"></div>`).join('')}</div>
    <div class="histo-marks">${marks.map(m =>
      `<div class="histo-mark" style="left:${(m.at * 100).toFixed(1)}%"><span>${esc(m.label)}</span></div>`).join('')}</div>
    <div class="histo-axis"><span>${buckets[0].lo}</span><span>${buckets[buckets.length - 1].hi}</span></div>
  </div>`;
}

function donut(slices, opts = {}) {
  const total = slices.reduce((a, s) => a + s.value, 0);
  if (!total) return '<div class="sub" style="margin:0">No data.</div>';
  const R = 54, C = 2 * Math.PI * R;
  let off = 0;
  const rings = slices.map(s => {
    const frac = s.value / total;
    const seg = `<circle cx="70" cy="70" r="${R}" fill="none" stroke="${s.color}"
      stroke-width="20" stroke-dasharray="${(frac * C).toFixed(2)} ${C.toFixed(2)}"
      stroke-dashoffset="${(-off * C).toFixed(2)}" transform="rotate(-90 70 70)"/>`;
    off += frac;
    return seg;
  }).join('');
  return `<div class="donut-wrap">
    <svg class="donut" width="140" height="140" viewBox="0 0 140 140">
      <circle cx="70" cy="70" r="${R}" fill="none" stroke="rgba(255,255,255,.05)" stroke-width="20"/>
      ${rings}
      <text x="70" y="66" text-anchor="middle" fill="var(--text)"
        style="font:700 20px ui-monospace,Menlo,monospace">${fmtNum(total)}</text>
      <text x="70" y="83" text-anchor="middle" fill="var(--dimmer)"
        style="font:9px ui-monospace,Menlo,monospace;letter-spacing:.1em">${esc(opts.unit || 'TOTAL')}</text>
    </svg>
    <div class="donut-legend">${slices.map(s =>
      `<span><i style="background:${s.color}"></i>${esc(s.label)}<b>${fmtNum(s.value)}</b></span>`).join('')}</div>
  </div>`;
}

let _anTab = 'detections';
let _anFootage = '';
let _anProfile = 'fusion';

VIEWS.analytics = async () => {
  V.innerHTML = `<h2>Analytics</h2>
    <p class="sub">What the models found, what the engine decided with it, and what it raised.</p>
    <div class="subtabs" id="an-tabs">
      ${[['detections', 'Detections'], ['actions', 'Actions'], ['decisions', 'Decision Logic'],
         ['alerts', 'Alerts'], ['timeline', 'Timeline']]
        .map(([id, l]) => `<button class="subtab ${id === _anTab ? 'active' : ''}" data-an="${id}">${l}</button>`).join('')}
    </div>
    <div class="toolbar">
      <label>Footage <select id="an-proj"><option value="">all footage</option></select></label>
      <label>Corpus <select id="an-profile">
        <option value="fusion" ${_anProfile === 'fusion' ? 'selected' : ''}>deployed pipeline only</option>
        <option value="" ${_anProfile === '' ? 'selected' : ''}>all (incl. legacy)</option></select></label>
      <span class="sub" style="margin:0 0 0 auto" id="an-scope"></span>
    </div>
    <div id="an-body"><div class="sub">Loading…</div></div>`;

  const projects = (await api('/overview/footages')).footages.filter(p => p.status === 'done');
  $('#an-proj').innerHTML = '<option value="">all projects</option>' + projects.map(p =>
    `<option value="${p.id}" ${p.id === _anFootage ? 'selected' : ''}>${esc(p.name || p.filename)}</option>`).join('');
  $('#an-proj').onchange = e => { _anFootage = e.target.value; renderAnTab(); };
  $('#an-profile').onchange = e => { _anProfile = e.target.value; renderAnTab(); };
  $$('#an-tabs .subtab').forEach(b => b.onclick = () => {
    _anTab = b.dataset.an;
    $$('#an-tabs .subtab').forEach(x => x.classList.toggle('active', x === b));
    renderAnTab();
  });
  renderAnTab();
};

function anQuery() {
  const q = new URLSearchParams();
  if (_anFootage) q.set('project', _anFootage);
  if (_anProfile) q.set('profile', _anProfile);
  return q.toString() ? '?' + q : '';
}

function renderAnTab() {
  ({ detections: anDetections, actions: anActions, decisions: anDecisions,
     alerts: anAlerts, timeline: anTimeline }[_anTab] || anDetections)();
}

// ------------------------------- detections --------------------------------
async function anDetections() {
  const box = $('#an-body');
  box.innerHTML = '<div class="sub">Loading…</div>';
  const d = await api('/analytics/detections' + anQuery());
  $('#an-scope').textContent = `${fmtNum(d.segments_analysed)} analysed segments · ${d.profile} corpus`;
  if (!d.segments_analysed) {
    box.innerHTML = '<div class="banner info"><span>ℹ</span><div>No analysed segments in this scope.</div></div>';
    return;
  }
  const t1rows = d.track1.map(o => ({
    label: o.label, value: o.segments, critical: o.critical,
    right: `${o.segments} · ${(o.avg_conf * 100).toFixed(0)}%`,
    color: o.critical ? 'linear-gradient(90deg,rgba(255,59,92,.9),rgba(255,59,92,.4))'
                      : 'linear-gradient(90deg,rgba(255,176,32,.9),rgba(255,176,32,.4))',
  }));
  const t2rows = d.track2.slice(0, 12).map(o => ({
    label: o.label, value: o.segments,
    right: `${o.segments} · ${(o.avg_conf * 100).toFixed(0)}%`,
  }));

  box.innerHTML = `
    <div class="row" style="margin-bottom:16px">
      ${[['Segments analysed', fmtNum(d.segments_analysed)],
         ['Threat classes seen', d.track1.length, d.track1.length ? 'var(--hi)' : 'var(--ok)'],
         ['Context classes seen', d.track2.length, 'var(--info)'],
         ['Segments with a critical object', `${d.critical_rate}%`,
           d.critical_rate > 20 ? 'var(--warn)' : 'var(--ok)']]
        .map(([k, v, c]) => `<div class="card stat"><div class="k">${k}</div>
          <div class="v" style="font-size:24px;color:${c || 'var(--text)'}">${v}</div></div>`).join('')}
    </div>
    <div class="grid2">
      <div class="card">
        <h3 style="margin:0 0 4px;font-size:14px">Track 1 — purpose-trained threat detector</h3>
        <p class="sub" style="margin:0 0 14px">Segments each class appeared in, with mean confidence.
          Red = immediate life-safety hazard.</p>
        ${hbars(t1rows)}
      </div>
      <div class="card">
        <h3 style="margin:0 0 4px;font-size:14px">Track 2 — COCO context</h3>
        <p class="sub" style="margin:0 0 14px">Scene context, not threat. A high
          <code>person</code> count is normal; it is what the threat classes are weighed against.</p>
        ${hbars(t2rows)}
      </div>
    </div>
    <div class="grid2" style="margin-top:14px">
      <div class="card">
        <h3 style="margin:0 0 4px;font-size:14px">Detection confidence distribution</h3>
        <p class="sub" style="margin:0 0 10px">Track 1 (threat)</p>
        ${histogram(d.confidence_histogram.track1)}
        <p class="sub" style="margin:14px 0 10px">Track 2 (context)</p>
        ${histogram(d.confidence_histogram.track2)}
      </div>
      <div class="card">
        <h3 style="margin:0 0 4px;font-size:14px">What appears together</h3>
        <p class="sub" style="margin:0 0 14px">Co-occurrence within a segment — the
          combinations that drive context modifiers.</p>
        <div class="pairs">${d.co_occurrence.map(p =>
          `<span class="pair">${esc(p.a)} + ${esc(p.b)} <b>×${p.count}</b></span>`).join('')
          || '<span class="sub">No co-occurring classes.</span>'}</div>
      </div>
    </div>`;
}

// --------------------------------- actions ---------------------------------
async function anActions() {
  const box = $('#an-body');
  box.innerHTML = '<div class="sub">Loading…</div>';
  const d = await api('/analytics/detections' + anQuery());
  $('#an-scope').textContent = `${fmtNum(d.segments_analysed)} analysed segments · ${d.profile} corpus`;
  const acts = d.actions.filter(a => a.segments > 0);
  const SEV = { Shooting: 1, Explosion: 1, Assault: .9, Arson: .85, Fighting: .85,
    Abuse: .85, Robbery: .8, RoadAccidents: .7, Burglary: .6, Stealing: .55,
    Vandalism: .5, Shoplifting: .45, Arrest: .4, Normal: 0 };
  const rows = acts.map(a => {
    const sev = SEV[a.action];
    return {
      label: a.action, value: a.segments,
      right: `${a.segments} · ${(a.avg_conf * 100).toFixed(0)}%`,
      color: sev === undefined
        ? 'linear-gradient(90deg,rgba(120,130,150,.7),rgba(120,130,150,.3))'
        : sev >= .8 ? 'linear-gradient(90deg,rgba(255,59,92,.9),rgba(255,59,92,.4))'
        : sev >= .5 ? 'linear-gradient(90deg,rgba(255,176,32,.9),rgba(255,176,32,.4))'
        : 'linear-gradient(90deg,rgba(38,208,124,.9),rgba(38,208,124,.4))',
    };
  });
  const unknown = acts.filter(a => SEV[a.action] === undefined).length;

  box.innerHTML = `
    ${unknown ? `<div class="banner"><span>⚠</span><div>
      <b>${unknown} label(s) are not UCF-Crime classes.</b> Those segments were scored by
      the legacy X3D-S backend, whose Kinetics-400 labels carry no severity weighting.
      Switch the corpus selector to <b>deployed pipeline only</b> for a clean picture.
    </div></div>` : ''}
    <div class="grid2">
      <div class="card">
        <h3 style="margin:0 0 4px;font-size:14px">Action distribution</h3>
        <p class="sub" style="margin:0 0 14px">Top-1 class per segment, coloured by
          the severity weight the fusion stage assigns it.</p>
        ${hbars(rows)}
      </div>
      <div class="card">
        <h3 style="margin:0 0 4px;font-size:14px">Action confidence</h3>
        <p class="sub" style="margin:0 0 14px">How decisive the action model was.
          Low confidence damps its contribution to the score.</p>
        ${histogram(d.confidence_histogram.action)}
        <h3 style="margin:20px 0 4px;font-size:14px">Severity mix</h3>
        <p class="sub" style="margin:0 0 14px">Segments by action-severity band.</p>
        ${donut([
          { label: 'High (≥0.8)', color: '#ff3b5c',
            value: acts.filter(a => (SEV[a.action] ?? -1) >= .8).reduce((n, a) => n + a.segments, 0) },
          { label: 'Medium (0.5–0.8)', color: '#ffb020',
            value: acts.filter(a => (SEV[a.action] ?? -1) >= .5 && SEV[a.action] < .8).reduce((n, a) => n + a.segments, 0) },
          { label: 'Low (<0.5)', color: '#26d07c',
            value: acts.filter(a => (SEV[a.action] ?? -1) >= 0 && SEV[a.action] < .5).reduce((n, a) => n + a.segments, 0) },
          { label: 'Unweighted (legacy)', color: '#6b7a90',
            value: acts.filter(a => SEV[a.action] === undefined).reduce((n, a) => n + a.segments, 0) },
        ].filter(s => s.value), { unit: 'SEGMENTS' })}
      </div>
    </div>`;
}

// ------------------------------ decision logic -----------------------------
async function anDecisions() {
  const box = $('#an-body');
  box.innerHTML = '<div class="sub">Loading…</div>';
  const d = await api('/analytics/decisions' + anQuery());
  $('#an-scope').textContent = `${fmtNum(d.segments_analysed)} analysed · ${fmtNum(d.segments_with_trace)} with a score trace`;
  const th = d.thresholds;

  box.innerHTML = `
    ${d.segments_with_trace < d.segments_analysed ? `<div class="banner info"><span>ℹ</span><div>
      ${fmtNum(d.segments_analysed - d.segments_with_trace)} segment(s) in this scope predate the
      fusion stage and carry no score trace, so they contribute to the distributions below
      but not to the modifier or channel breakdowns.</div></div>` : ''}

    <div class="row" style="margin-bottom:16px">
      ${[['Mean Ssig', d.ssig_stats.mean.toFixed(3)],
         ['Range', `${d.ssig_stats.min.toFixed(2)}–${d.ssig_stats.max.toFixed(2)}`],
         ['Near a tier boundary', `${d.boundary_sensitivity.percent}%`,
           d.boundary_sensitivity.percent > 20 ? 'var(--warn)' : 'var(--ok)'],
         ['Thresholds', `${th.low} / ${th.high}`, 'var(--info)']]
        .map(([k, v, c]) => `<div class="card stat"><div class="k">${k}</div>
          <div class="v" style="font-size:22px;color:${c || 'var(--text)'}">${v}</div></div>`).join('')}
    </div>

    <div class="card">
      <h3 style="margin:0 0 4px;font-size:14px">Significance distribution vs the live tier boundaries</h3>
      <p class="sub" style="margin:0 0 22px">Where segments actually land. Bars are coloured
        by the tier they receive; the dashed lines are the configured thresholds.</p>
      ${histogram(d.ssig_histogram, { bands: { low: th.low, high: th.high },
        marks: [{ at: th.low, label: `LOW ${th.low}` }, { at: th.high, label: `HIGH ${th.high}` }] })}
      <p class="wf-note"><b>${d.boundary_sensitivity.percent}%</b> of segments sit within
        ±${d.boundary_sensitivity.band} of a boundary — ${esc(d.boundary_sensitivity.note)}.</p>
    </div>

    <div class="grid2" style="margin-top:14px">
      <div class="card">
        <h3 style="margin:0 0 4px;font-size:14px">Context modifiers — how often each fires</h3>
        <p class="sub" style="margin:0 0 14px">The rules that adjust a score after the
          three weighted channels have been combined.</p>
        ${hbars(d.modifiers.map(m => ({
          label: m.name, value: m.fired,
          right: `${m.fire_rate}% · ${m.avg_amount >= 0 ? '+' : ''}${m.avg_amount}`,
          color: m.avg_amount < 0
            ? 'linear-gradient(90deg,rgba(125,211,252,.9),rgba(125,211,252,.35))'
            : 'linear-gradient(90deg,rgba(255,176,32,.9),rgba(255,176,32,.35))',
        })))}
        ${d.modifiers.length ? `<p class="wf-note">${d.modifiers.map(m =>
          `<b>${esc(m.name)}</b>: ${esc(m.why)}`).join(' · ')}</p>` : ''}
      </div>
      <div class="card">
        <h3 style="margin:0 0 4px;font-size:14px">Which channel drives the score</h3>
        <p class="sub" style="margin:0 0 14px">Share of total contribution across
          action, suspicious objects, and the trained sentiment model.</p>
        ${donut(d.channels.map((c, i) => ({
          label: `${c.channel} (w ${c.weight})`, value: Math.round(c.sum_contribution * 1000),
          color: ['#7dd3fc', '#ffb020', '#26d07c'][i % 3],
        })), { unit: 'CONTRIB ×1000' })}
        ${hbars(d.channels.map(c => ({
          label: c.channel, value: c.share_percent, right: `${c.share_percent}%`,
        })))}
      </div>
    </div>

    <div class="grid2" style="margin-top:14px">
      <div class="card"><h3 style="margin:0 0 14px;font-size:14px">Tier outcome</h3>
        ${donut([['HIGH', '#ff3b5c'], ['MEDIUM', '#ffb020'], ['LOW', '#26d07c']]
          .map(([k, c]) => ({ label: k, value: d.tiers[k] || 0, color: c }))
          .filter(s => s.value), { unit: 'SEGMENTS' })}</div>
      <div class="card"><h3 style="margin:0 0 14px;font-size:14px">Hazard classification</h3>
        ${donut([['critical', '#ff3b5c'], ['elevated', '#ff7849'], ['moderate', '#ffb020'], ['none', '#26d07c']]
          .map(([k, c]) => ({ label: k, value: d.hazards[k] || 0, color: c }))
          .filter(s => s.value), { unit: 'SEGMENTS' })}</div>
    </div>`;
}

// --------------------------------- alerts ----------------------------------
async function anAlerts() {
  const box = $('#an-body');
  box.innerHTML = '<div class="sub">Loading…</div>';
  let d;
  try { d = await api('/analytics/alerts' + (_anFootage ? '?project=' + _anFootage : '')); }
  catch { box.innerHTML = '<div class="banner"><span>⚠</span><div>Alert analytics require an operator or administrator role.</div></div>'; return; }
  $('#an-scope').textContent = `${fmtNum(d.total)} alerts`;
  if (!d.total) { box.innerHTML = '<div class="banner good"><span>✓</span><div>No alerts raised in this scope.</div></div>'; return; }

  const lat = d.ack_latency_seconds;
  const days = d.by_day;
  box.innerHTML = `
    <div class="row" style="margin-bottom:16px">
      ${[['Total alerts', fmtNum(d.total)],
         ['Open', d.open, d.open ? 'var(--hi)' : 'var(--ok)'],
         ['Acknowledged', `${d.ack_rate}%`, d.ack_rate > 70 ? 'var(--ok)' : 'var(--warn)'],
         ['Median time to ack', lat.median != null ? fmtDur(lat.median * 1000) : '—', 'var(--info)']]
        .map(([k, v, c]) => `<div class="card stat"><div class="k">${k}</div>
          <div class="v" style="font-size:24px;color:${c || 'var(--text)'}">${v}</div></div>`).join('')}
    </div>
    <div class="grid2">
      <div class="card"><h3 style="margin:0 0 14px;font-size:14px">Severity mix</h3>
        ${donut([['critical', '#ff3b5c'], ['high', '#ffb020']]
          .map(([k, c]) => ({ label: k, value: d.severity[k] || 0, color: c }))
          .filter(s => s.value), { unit: 'ALERTS' })}</div>
      <div class="card">
        <h3 style="margin:0 0 4px;font-size:14px">What triggered them</h3>
        <p class="sub" style="margin:0 0 14px">Objects and actions recorded on the alert.</p>
        ${hbars(d.top_triggers.map(t => ({
          label: t.label.replace(/^action:/, '▷ '), value: t.count, right: t.count,
          color: t.label.startsWith('action:')
            ? 'linear-gradient(90deg,rgba(125,211,252,.9),rgba(125,211,252,.35))'
            : 'linear-gradient(90deg,rgba(255,59,92,.9),rgba(255,59,92,.35))',
        })))}</div>
    </div>
    <div class="card" style="margin-top:14px">
      <h3 style="margin:0 0 4px;font-size:14px">Alert volume over time</h3>
      <p class="sub" style="margin:0 0 14px">Raised vs acknowledged, by day.</p>
      ${days.length > 1 ? lineChart([
        { name: 'raised', color: '#ff3b5c', fill: true, points: days.map((x, i) => [i + 1, x.total]) },
        { name: 'acknowledged', color: '#26d07c', points: days.map((x, i) => [i + 1, x.acknowledged]) },
      ], { formatY: v => v.toFixed(0) })
      : `<div class="hbars">${days.map(x => `<div class="hbar">
          <span class="nm">${esc(x.day)}</span>
          <span class="tk"><i style="width:100%;background:linear-gradient(90deg,rgba(255,59,92,.9),rgba(255,59,92,.35))"></i></span>
          <span class="vv">${x.total} raised · ${x.acknowledged} ack</span></div>`).join('')}</div>`}
    </div>
    <div class="card" style="margin-top:14px">
      <h3 style="margin:0 0 4px;font-size:14px">Alert significance</h3>
      <p class="sub" style="margin:0 0 14px">Ssig of alerting segments — clustering just
        above the alert threshold suggests the threshold, not the footage, is setting volume.</p>
      ${histogram(d.ssig_histogram, { bands: { low: 0.4, high: 0.7 } })}
    </div>`;
}

// -------------------------------- timeline ---------------------------------
async function anTimeline() {
  const box = $('#an-body');
  box.innerHTML = '<div class="sub">Loading…</div>';
  const d = await api('/analytics/timeline' + anQuery());
  $('#an-scope').textContent = `${fmtNum(d.n)} segments in capture order`;
  if (!d.n) { box.innerHTML = '<div class="banner info"><span>ℹ</span><div>No segments in this scope.</div></div>'; return; }

  const pts = (arr) => arr.map((v, i) => [i + 1, v]);
  const savedPts = d.original_bytes.map((o, i) => {
    const s = d.stored_bytes[i] || 0;
    return [i + 1, o ? Math.max(0, (1 - s / o) * 100) : 0];
  });
  box.innerHTML = `
    <div class="card">
      <h3 style="margin:0 0 4px;font-size:14px">Significance and sentiment across the recording</h3>
      <p class="sub" style="margin:0 0 14px">One point per segment, in capture order.
        Where the two diverge, the object and action channels are doing the work.</p>
      ${lineChart([
        { name: 'Ssig', color: '#ff9f43', fill: true, points: pts(d.ssig) },
        { name: 'sentiment (trained model)', color: '#26d07c', dash: true, points: pts(d.sentiment) },
      ], { formatY: v => v.toFixed(2) })}
    </div>
    <div class="grid2" style="margin-top:14px">
      <div class="card">
        <h3 style="margin:0 0 4px;font-size:14px">Storage saved per segment</h3>
        <p class="sub" style="margin:0 0 14px">Percentage of each segment's bytes discarded by its tier.</p>
        ${lineChart([{ name: '% saved', color: '#7dd3fc', fill: true, points: savedPts }],
          { width: 520, height: 220, formatY: v => v.toFixed(0) + '%' })}
      </div>
      <div class="card">
        <h3 style="margin:0 0 4px;font-size:14px">Tier sequence</h3>
        <p class="sub" style="margin:0 0 14px">How the recording was tiered, segment by segment.</p>
        <div class="scrub-track" style="height:46px">${d.tier.map((t, i) => {
          const col = t === 'HIGH' ? 'hi' : t === 'MEDIUM' ? 'warn' : d.motion[i] ? 'ok' : 'dimmer';
          return `<div class="scrub-seg" style="width:${(100 / d.n).toFixed(3)}%;background:var(--${col})"
            title="#${d.idx[i]} · ${t || 'static'} · Ssig ${d.ssig[i].toFixed(2)}"></div>`;
        }).join('')}</div>
        <div class="dlegend">
          <span><i style="background:var(--hi)"></i>HIGH</span>
          <span><i style="background:var(--warn)"></i>MEDIUM</span>
          <span><i style="background:var(--ok)"></i>LOW (motion)</span>
          <span><i style="background:var(--dimmer)"></i>static — skipped</span>
        </div>
      </div>
    </div>`;
}

/* ==========================================================================
   Live processing panel — charts that build as the pipeline emits stage logs
   ==========================================================================
   The pipeline already streams a per-segment line over the SSE log endpoint.
   Rather than adding a second telemetry channel, these charts are driven by
   parsing that same stream, so there is one source of truth for progress and
   no risk of the numbers disagreeing with the log an operator can read.       */
const LIVE = { ssig: [], tiers: { HIGH: 0, MEDIUM: 0, LOW: 0 }, skipped: 0,
               total: 0, done: 0, t0: 0, rate: [], actions: {},
               feed: [], objects: {}, segInfo: '' };

function liveReset(total) {
  LIVE.ssig = []; LIVE.tiers = { HIGH: 0, MEDIUM: 0, LOW: 0 };
  LIVE.skipped = 0; LIVE.total = total || 0; LIVE.done = 0;
  LIVE.t0 = Date.now(); LIVE.rate = []; LIVE.actions = {};
  LIVE.feed = []; LIVE.objects = {}; LIVE.segInfo = '';
}

// Track 1 classes carry security semantics, so the live feed highlights them
// differently from context classes — an operator watching a run should be able
// to see a weapon appear without reading the numbers.
const T1_CLASSES = ['Gun', 'Knife', 'Rifle', 'Fire', 'Smoke',
                    'Unattended_Bag', 'Mask', 'Broken_Glass'];
const T1_CRITICAL = ['Gun', 'Rifle', 'Knife', 'Fire'];

function livePanelHTML() {
  return `<div class="live-grid" id="live-grid">
    <div class="live-card">
      <h4><span class="live-dot"></span>Segments scored</h4>
      <div class="live-num" id="lv-done">0</div>
      <div class="live-sub" id="lv-done-sub">waiting…</div>
      <div class="throughput" id="lv-rate"></div>
    </div>
    <div class="live-card">
      <h4><span class="live-dot"></span>Significance stream</h4>
      <div class="live-num" id="lv-mean">—</div>
      <div class="live-sub">mean Ssig so far</div>
      <div class="spark" id="lv-spark"></div>
    </div>
    <div class="live-card">
      <h4><span class="live-dot"></span>Tier allocation</h4>
      <div class="live-num" id="lv-saved">—</div>
      <div class="live-sub" id="lv-tier-sub">no segments tiered yet</div>
      <div class="tierflow" id="lv-tierflow"></div>
    </div>
    <div class="live-card">
      <h4><span class="live-dot"></span>Top actions</h4>
      <div id="lv-actions" class="live-sub" style="margin-top:2px">—</div>
      <div class="live-sub" id="lv-seginfo" style="margin-top:9px;color:var(--dimmer)"></div>
    </div>
  </div>
  <div class="live-card" style="margin-top:12px">
    <h4><span class="live-dot"></span>Detections as they are scored</h4>
    <div class="ticker" id="lv-ticker">
      <div class="live-sub" style="color:var(--dimmer)">waiting for the first segment…</div>
    </div>
  </div>`;
}

/* Parse one process-log line into the live series. Returns true if it moved. */
function liveIngest(msg) {
  const seg = msg.match(/Segment (\d+)\/(\d+)/);
  if (!seg) {
    const cut = msg.match(/cut into (\d+) segments/);
    if (cut) {
      liveReset(+cut[1]);
      LIVE.segInfo = msg.replace('Stage 1/5 Segmentation: ', '');
      liveRender();
    }
    return false;
  }
  if (!LIVE.total) LIVE.total = +seg[2];
  LIVE.done = +seg[1];
  LIVE.rate.push(Date.now());

  if (/no motion/.test(msg)) {
    LIVE.skipped++;
    LIVE.tiers.LOW++;
    LIVE.ssig.push({ v: 0, tier: 'none' });
    LIVE.feed.unshift({ idx: LIVE.done, static: true });
  } else {
    const s = msg.match(/ssig=([\d.]+)/);
    const t = msg.match(/tier=(HIGH|MEDIUM|LOW)/);
    const a = msg.match(/action=([A-Za-z_]+)/);
    const o = msg.match(/suspicious=\[([^\]]*)\]/);
    const tier = t ? t[1] : 'LOW';
    const objs = o && o[1] && o[1] !== 'none'
      ? o[1].split(',').map(x => x.trim()).filter(Boolean) : [];
    objs.forEach(l => { LIVE.objects[l] = (LIVE.objects[l] || 0) + 1; });
    LIVE.tiers[tier] = (LIVE.tiers[tier] || 0) + 1;
    LIVE.ssig.push({ v: s ? parseFloat(s[1]) : 0, tier });
    if (a && a[1] !== 'n' && a[1] !== 'na') {
      LIVE.actions[a[1]] = (LIVE.actions[a[1]] || 0) + 1;
    }
    LIVE.feed.unshift({ idx: LIVE.done, tier,
      ssig: s ? parseFloat(s[1]) : 0,
      action: a ? a[1] : '', objects: objs });
  }
  LIVE.feed = LIVE.feed.slice(0, 40);
  liveRender();
  return true;
}

function liveRender() {
  if (!$('#live-grid')) return;
  const scored = LIVE.ssig.filter(s => s.tier !== 'none');
  const mean = scored.length
    ? scored.reduce((a, s) => a + s.v, 0) / scored.length : null;

  $('#lv-done').textContent = LIVE.done + (LIVE.total ? ` / ${LIVE.total}` : '');
  const elapsed = (Date.now() - LIVE.t0) / 1000;
  $('#lv-done-sub').textContent = LIVE.done
    ? `${(LIVE.done / Math.max(1, elapsed) * 60).toFixed(1)}/min · ${LIVE.skipped} static skipped`
    : 'waiting…';

  // throughput: seconds per segment over the last 24, inverted so taller = faster
  const gaps = [];
  for (let i = Math.max(1, LIVE.rate.length - 24); i < LIVE.rate.length; i++) {
    gaps.push((LIVE.rate[i] - LIVE.rate[i - 1]) / 1000);
  }
  const slowest = Math.max(...gaps, 0.001);
  $('#lv-rate').innerHTML = gaps.map(g =>
    `<i style="height:${Math.max(6, (1 - g / slowest) * 90 + 10).toFixed(0)}%"></i>`).join('');

  $('#lv-mean').textContent = mean == null ? '—' : mean.toFixed(3);
  const recent = LIVE.ssig.slice(-42);
  $('#lv-spark').innerHTML = recent.map(s =>
    `<i class="${s.tier}" style="height:${Math.max(4, s.v * 100).toFixed(0)}%"
      title="Ssig ${s.v.toFixed(2)} · ${s.tier}"></i>`).join('');

  const n = LIVE.tiers.HIGH + LIVE.tiers.MEDIUM + LIVE.tiers.LOW;
  if (n) {
    // Live estimate from measured per-tier compression — labelled as an
    // estimate because the real figure is only known once tiering has run.
    const est = 1 - (LIVE.tiers.HIGH * 1 + LIVE.tiers.MEDIUM * 0.18 + LIVE.tiers.LOW * 0.01) / n;
    $('#lv-saved').textContent = (est * 100).toFixed(0) + '%';
    $('#lv-tier-sub').textContent =
      `projected saving · H${LIVE.tiers.HIGH} M${LIVE.tiers.MEDIUM} L${LIVE.tiers.LOW}`;
    $('#lv-tierflow').innerHTML = [['HIGH', 'hi'], ['MEDIUM', 'warn'], ['LOW', 'ok']]
      .map(([t, c]) => {
        const pct = LIVE.tiers[t] / n * 100;
        return pct < 0.5 ? '' : `<i style="width:${pct}%;background:var(--${c})">${
          pct > 11 ? `<span>${LIVE.tiers[t]}</span>` : ''}</i>`;
      }).join('');
  }

  const acts = Object.entries(LIVE.actions).sort((a, b) => b[1] - a[1]).slice(0, 5);
  $('#lv-actions').innerHTML = acts.length
    ? acts.map(([a, c]) => `<span class="chip act">${esc(a)} ×${c}</span>`).join('')
    : '<span style="color:var(--dimmer)">none yet</span>';
  const si = $('#lv-seginfo');
  if (si) si.textContent = LIVE.segInfo;

  const tk = $('#lv-ticker');
  if (tk) {
    tk.innerHTML = LIVE.feed.length ? LIVE.feed.map(f => {
      if (f.static) {
        return `<div class="tick-row"><span class="ix">#${String(f.idx).padStart(3, '0')}</span>
          <span class="lb" style="color:var(--dimmer)">no motion — skipped deep analysis</span>
          <span class="pill LOW">LOW</span></div>`;
      }
      const chips = f.objects.map(l => {
        const t1 = T1_CLASSES.includes(l);
        const crit = T1_CRITICAL.includes(l);
        const col = crit ? 'var(--hi)' : t1 ? 'var(--warn)' : 'var(--info)';
        return `<span style="color:${col}">${esc(l)}</span>`;
      }).join(' · ') || '<span style="color:var(--dimmer)">no suspicious objects</span>';
      return `<div class="tick-row">
        <span class="ix">#${String(f.idx).padStart(3, '0')}</span>
        <span class="lb">${esc(f.action || '—')} &nbsp;${chips}</span>
        <span style="color:var(--dim)">${f.ssig.toFixed(2)}</span>
        <span class="pill ${f.tier}">${f.tier}</span></div>`;
    }).join('') : '<div class="live-sub" style="color:var(--dimmer)">waiting…</div>';
  }
}
