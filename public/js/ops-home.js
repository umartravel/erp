/* Home Ops -- dashboard landing untuk role ops. Mirror pola sales-home.js.
   Endpoint: GET /api/ops/home. Palet konsisten dengan Brand UMAR (gold+charcoal). */

const OH_COLORS = {
  gold: '#F8BE20', darkGold: '#B8860B', charcoal: '#1D1D1B',
  cream: '#F4F1EA', gray500: '#6B7280', red: '#DC2626', amber: '#B45309',
  green: '#059669', blue: '#2563EB',
};

const OH_BASE_TITLE = document.title.replace(/^\(\d+\)\s*/, '');
function ohUpdateTabBadge(n) {
  document.title = n > 0 ? `(${n}) ${OH_BASE_TITLE}` : OH_BASE_TITLE;
}

function ohFetch(url) { return (window.authFetch || fetch)(url); }
function ohFmtRp(n) { if (!n && n !== 0) return 'Rp 0'; return 'Rp ' + (n | 0).toLocaleString('id-ID'); }
function ohFmtDate(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('id-ID', { day: '2-digit', month: 'short', year: 'numeric' });
}

async function initOpsHome() {
  try {
    const res = await ohFetch('/api/ops/home');
    if (!res.ok) { console.error('[ops-home] fetch gagal', res.status); return; }
    const data = await res.json();
    renderOpsHome(data);
    if (typeof lucide !== 'undefined') lucide.createIcons();
  } catch (e) { console.error('[ops-home] error', e); }
}

function ohWireSocketAutoRefresh() {
  if (window.__ohSocketWired) return;
  window.__ohSocketWired = true;
  const s = window.socket;
  if (!s || typeof s.on !== 'function') return;
  const refreshEvents = new Set(['jamaah', 'incident', 'inventory', 'asset', 'package']);
  s.on('data_updated', (evt) => {
    if (!refreshEvents.has(evt)) return;
    const page = document.getElementById('page-ops-home');
    if (page && !page.classList.contains('hidden')) initOpsHome();
  });
}

function renderOpsHome(data) {
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
  set('oh-me-name', data.me?.name || '-');

  const k = data.kpi || {};
  set('oh-kpi-packages', (k.active_packages || 0).toLocaleString('id-ID'));
  set('oh-kpi-jamaah', (k.active_jamaah || 0).toLocaleString('id-ID'));
  set('oh-kpi-stock', (k.low_stock_count || 0).toLocaleString('id-ID'));
  set('oh-kpi-asset', (k.asset_checkout_count || 0).toLocaleString('id-ID'));
  set('oh-kpi-passport', (k.passport_expiring_count || 0).toLocaleString('id-ID'));

  renderOhAttention(data.attention || {});
  renderOhUpcoming(data.upcoming_packages || []);
  renderOhIncidents(data.incidents_open || []);
  renderOhStock(data.low_stock_items || []);
  renderOhPassport(data.passport_expiring || []);
  renderOhHandover(data.handover_pending || []);
  ohWireSocketAutoRefresh();
}

function renderOhAttention(a) {
  const box = document.getElementById('oh-attention');
  const chips = document.getElementById('oh-attention-chips');
  if (!box || !chips) return;
  const total = a.total || 0;
  document.getElementById('oh-attention-total').textContent = total;
  ohUpdateTabBadge(total);
  if (total <= 0) { box.classList.add('hidden'); chips.innerHTML = ''; return; }
  box.classList.remove('hidden');
  const chip = (label, count, color, onclick) => count > 0
    ? `<button onclick="${onclick}" class="px-3 py-1.5 rounded-lg text-xs font-bold flex items-center gap-1.5" style="background:${color};color:#1D1D1B;">
         <span class="px-1.5 py-0.5 rounded" style="background:#1D1D1B;color:#F8BE20;font-size:10px;">${count}</span>
         ${label}
       </button>`
    : '';
  chips.innerHTML = [
    chip('Paket berangkat H-7', a.upcoming_H7, '#FEE2E2', "document.getElementById('oh-upcoming-body')?.scrollIntoView({behavior:'smooth',block:'start'})"),
    chip('Insiden terbuka', a.incidents_open, '#FEF3C7', "showPage('incidents')"),
    chip('Stok kritis', a.low_stock, '#FFEDD5', "document.getElementById('oh-stock-body')?.scrollIntoView({behavior:'smooth',block:'start'})"),
    chip('Paspor expiring', a.passport_expiring, '#DBEAFE', "document.getElementById('oh-passport-body')?.scrollIntoView({behavior:'smooth',block:'start'})"),
    chip('Handover pending', a.handover_pending, '#F3E8FF', "document.getElementById('oh-handover-body')?.scrollIntoView({behavior:'smooth',block:'start'})"),
  ].filter(Boolean).join('');
}

function ohEmpty(msg) {
  return `<div class="p-6 text-center">
    <div class="inline-flex w-12 h-12 rounded-full items-center justify-center mb-2" style="background:#D1FAE5;">
      <i data-lucide="check-circle-2" class="w-6 h-6" style="color:${OH_COLORS.green};"></i>
    </div>
    <p class="text-sm" style="color:${OH_COLORS.gray500};">${msg}</p>
  </div>`;
}

function renderOhUpcoming(list) {
  const body = document.getElementById('oh-upcoming-body');
  if (!body) return;
  if (!list.length) { body.innerHTML = ohEmpty('Tidak ada paket berangkat dalam 30 hari ke depan.'); return; }
  body.innerHTML = list.map(p => {
    const days = p.days_to_go ?? null;
    const isUrgent = days !== null && days <= 7;
    const badge = days !== null
      ? `<span class="text-[10px] font-bold px-2 py-0.5 rounded" style="background:${isUrgent?'#FEE2E2':'#FEF3C7'};color:${isUrgent?OH_COLORS.red:OH_COLORS.amber};">H-${days}</span>`
      : '';
    const seatColor = (p.filled || 0) >= (p.quota || 45) ? OH_COLORS.red : OH_COLORS.darkGold;
    return `<div class="p-4 border-b last:border-b-0" style="border-color:#F4F1EA;">
      <div class="flex justify-between items-start mb-2">
        <div class="flex-1 pr-3">
          <b class="text-sm" style="color:${OH_COLORS.charcoal};">${p.name}</b>
          <div class="text-[11px] mt-0.5" style="color:${OH_COLORS.gray500};">
            Berangkat <b style="color:${OH_COLORS.charcoal};">${ohFmtDate(p.departure_date)}</b>
            &middot; ${p.duration || '?'} hari
            &middot; ${p.hotel_mekkah || 'Hotel Mekkah -'}
          </div>
        </div>
        ${badge}
      </div>
      <div class="flex items-center justify-between text-[11px]">
        <span style="color:${OH_COLORS.gray500};">
          Terisi: <b style="color:${seatColor};">${p.filled || 0} / ${p.quota || 45}</b>
          ${p.airline_depart ? '&middot; ' + p.airline_depart : ''}
        </span>
        <button onclick="showPage('operasional')" class="text-xs px-2 py-1 rounded font-medium" style="background:${OH_COLORS.gold};color:${OH_COLORS.charcoal};">Kelola Manifest</button>
      </div>
    </div>`;
  }).join('');
}

function renderOhIncidents(list) {
  const body = document.getElementById('oh-incidents-body');
  if (!body) return;
  if (!list.length) { body.innerHTML = ohEmpty('Tidak ada insiden terbuka. Sistem aman.'); return; }
  const sevColor = { Critical: OH_COLORS.red, High: OH_COLORS.amber, Medium: OH_COLORS.darkGold, Low: OH_COLORS.gray500 };
  body.innerHTML = list.map(i => `
    <div class="p-3 border-b last:border-b-0" style="border-color:#F4F1EA;">
      <div class="flex items-start justify-between gap-2">
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 mb-1">
            <span class="text-[10px] font-bold px-1.5 py-0.5 rounded" style="background:${(sevColor[i.severity]||OH_COLORS.gray500)}20;color:${sevColor[i.severity]||OH_COLORS.gray500};">${i.severity || 'Medium'}</span>
            <span class="text-[10px] font-bold px-1.5 py-0.5 rounded" style="background:#F4F1EA;color:${OH_COLORS.charcoal};">${i.status || 'Open'}</span>
            <span class="text-[10px]" style="color:${OH_COLORS.gray500};">${i.package_name || 'Umum'}</span>
          </div>
          <p class="text-xs truncate" style="color:${OH_COLORS.charcoal};">${(i.incident_text || '').slice(0, 120)}</p>
          <p class="text-[10px] mt-0.5" style="color:${OH_COLORS.gray500};">
            Reporter: <b>${i.reported_by || '-'}</b>
            ${i.assigned_to ? '&middot; PIC: <b>' + i.assigned_to + '</b>' : '&middot; <span style="color:'+OH_COLORS.red+';">Belum di-assign</span>'}
          </p>
        </div>
        <button onclick="openIncidentDetail(${i.id})" class="text-xs px-2 py-1 rounded font-medium shrink-0" style="background:${OH_COLORS.gold};color:${OH_COLORS.charcoal};">Kelola</button>
      </div>
    </div>
  `).join('');
}

function renderOhStock(list) {
  const body = document.getElementById('oh-stock-body');
  if (!body) return;
  if (!list.length) { body.innerHTML = ohEmpty('Semua stok aman.'); return; }
  body.innerHTML = list.map(s => {
    const diff = (s.stock || 0) - (s.min_stock_threshold || 20);
    return `<div class="flex justify-between items-center py-2 border-b last:border-b-0 px-4" style="border-color:#F4F1EA;">
      <div>
        <b class="text-sm" style="color:${OH_COLORS.charcoal};">${s.name}</b>
        <div class="text-[10px]" style="color:${OH_COLORS.gray500};">Ambang minimum: ${s.min_stock_threshold || 20}</div>
      </div>
      <span class="text-sm font-black" style="color:${diff < 0 ? OH_COLORS.red : OH_COLORS.amber};">${s.stock || 0}</span>
    </div>`;
  }).join('');
}

function renderOhPassport(list) {
  const body = document.getElementById('oh-passport-body');
  if (!body) return;
  if (!list.length) { body.innerHTML = ohEmpty('Tidak ada paspor akan expire dalam 6 bulan.'); return; }
  body.innerHTML = list.map(j => {
    const days = j.passport_expiry ? Math.round((new Date(j.passport_expiry) - Date.now()) / 86400000) : null;
    const isDanger = days !== null && days <= 90;
    return `<div class="p-3 border-b last:border-b-0" style="border-color:#F4F1EA;">
      <div class="flex justify-between items-center">
        <div class="flex-1 min-w-0">
          <b class="text-sm truncate block" style="color:${OH_COLORS.charcoal};">${j.name || '-'}</b>
          <div class="text-[10px]" style="color:${OH_COLORS.gray500};">
            ${j.package_type || '-'} &middot; No: <b>${j.passport_number || '-'}</b>
          </div>
        </div>
        <div class="text-right shrink-0 ml-3">
          <div class="text-xs font-bold" style="color:${isDanger?OH_COLORS.red:OH_COLORS.amber};">${ohFmtDate(j.passport_expiry)}</div>
          ${days !== null ? `<div class="text-[10px]" style="color:${isDanger?OH_COLORS.red:OH_COLORS.gray500};">${days} hari lagi</div>` : ''}
        </div>
      </div>
    </div>`;
  }).join('');
}

function renderOhHandover(list) {
  const body = document.getElementById('oh-handover-body');
  if (!body) return;
  if (!list.length) { body.innerHTML = ohEmpty('Semua jamaah lunas sudah menerima perlengkapan.'); return; }
  body.innerHTML = list.map(j => `
    <div class="flex justify-between items-center py-2 border-b last:border-b-0 px-4" style="border-color:#F4F1EA;">
      <div class="flex-1 min-w-0">
        <b class="text-sm truncate block" style="color:${OH_COLORS.charcoal};">${j.name || '-'}</b>
        <div class="text-[10px]" style="color:${OH_COLORS.gray500};">
          ${j.package_type || '-'} &middot; DP ${ohFmtRp(j.paid_amount)} / ${ohFmtRp(j.total_price)}
        </div>
      </div>
      <button onclick="showPage('operasional')" class="text-xs px-2 py-1 rounded font-medium shrink-0 ml-3" style="background:${OH_COLORS.gold};color:${OH_COLORS.charcoal};">Serahkan</button>
    </div>
  `).join('');
}

// ============================================================================
// INCIDENTS MANAGEMENT (page-incidents)
// ============================================================================
let __incFilterStatus = 'Open';
let __incFilterSev = '';

async function initIncidentsPage() {
  await fetchIncidents();
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

async function fetchIncidents() {
  const qs = new URLSearchParams();
  if (__incFilterStatus) qs.set('status', __incFilterStatus);
  if (__incFilterSev) qs.set('severity', __incFilterSev);
  try {
    const res = await ohFetch('/api/incidents?' + qs.toString());
    if (!res.ok) throw new Error('Gagal muat insiden');
    const list = await res.json();
    renderIncidentsTable(list);
    if (typeof lucide !== 'undefined') lucide.createIcons();
  } catch (e) { if (typeof showToast === 'function') showToast(e.message, 'error'); }
}

function renderIncidentsTable(list) {
  const tbody = document.getElementById('table-incidents');
  if (!tbody) return;
  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="text-center py-8 text-gray-400 text-sm">Tidak ada insiden dengan filter ini.</td></tr>';
    return;
  }
  const sevBadge = (s) => {
    const map = { Critical: ['bg-red-100', 'text-red-700'], High: ['bg-amber-100', 'text-amber-700'],
                  Medium: ['bg-yellow-100', 'text-yellow-700'], Low: ['bg-gray-100', 'text-gray-600'] };
    const c = map[s] || map.Medium;
    return `<span class="text-[10px] font-bold px-2 py-0.5 rounded ${c[0]} ${c[1]}">${s || 'Medium'}</span>`;
  };
  const statBadge = (s) => {
    const map = { Open: ['bg-red-100', 'text-red-700'], InProgress: ['bg-blue-100', 'text-blue-700'], Resolved: ['bg-emerald-100', 'text-emerald-700'] };
    const c = map[s] || map.Open;
    return `<span class="text-[10px] font-bold px-2 py-0.5 rounded ${c[0]} ${c[1]}">${s || 'Open'}</span>`;
  };
  tbody.innerHTML = list.map(i => `
    <tr class="hover:bg-gray-50">
      <td class="px-3 py-2 text-xs text-gray-500">${ohFmtDate(i.created_at)}</td>
      <td class="px-3 py-2">${sevBadge(i.severity)}</td>
      <td class="px-3 py-2">${statBadge(i.status)}</td>
      <td class="px-3 py-2 text-xs text-gray-700">${i.package_name || '-'}</td>
      <td class="px-3 py-2 text-xs text-gray-800 max-w-md truncate" title="${(i.incident_text || '').replace(/"/g,'&quot;')}">${(i.incident_text || '').slice(0,80)}</td>
      <td class="px-3 py-2 text-xs">
        <div>Reporter: <b>${i.reported_by || '-'}</b></div>
        <div class="text-[10px] text-gray-500">PIC: ${i.assigned_to || '<span class="text-red-600">-</span>'}</div>
      </td>
      <td class="px-3 py-2 text-center">
        <button onclick="openIncidentDetail(${i.id})" class="text-xs px-2 py-1 rounded font-bold" style="background:#F8BE20;color:#1D1D1B;">Kelola</button>
      </td>
    </tr>
  `).join('');
}

function openCreateIncident() {
  document.getElementById('inc-modal-title').textContent = 'Laporkan Insiden Baru';
  document.getElementById('inc-form-id').value = '';
  document.getElementById('inc-form-package').value = '';
  document.getElementById('inc-form-text').value = '';
  document.getElementById('inc-form-severity').value = 'Medium';
  document.getElementById('inc-form-status').value = 'Open';
  document.getElementById('inc-form-assigned').value = '';
  document.getElementById('inc-form-resolution').value = '';
  document.getElementById('inc-resolve-block').classList.add('hidden');
  document.getElementById('inc-status-block').classList.add('hidden');
  document.getElementById('inc-assigned-block').classList.add('hidden');
  openModal('modal-incident');
}

async function openIncidentDetail(iid) {
  try {
    const res = await ohFetch('/api/incidents');
    const list = await res.json();
    const i = list.find(x => x.id === iid);
    if (!i) return showToast('Insiden tidak ditemukan', 'error');
    document.getElementById('inc-modal-title').textContent = `Insiden #${i.id}`;
    document.getElementById('inc-form-id').value = i.id;
    document.getElementById('inc-form-package').value = i.package_name || '';
    document.getElementById('inc-form-text').value = i.incident_text || '';
    document.getElementById('inc-form-severity').value = i.severity || 'Medium';
    document.getElementById('inc-form-status').value = i.status || 'Open';
    document.getElementById('inc-form-assigned').value = i.assigned_to || '';
    document.getElementById('inc-form-resolution').value = i.resolution_note || '';
    document.getElementById('inc-status-block').classList.remove('hidden');
    document.getElementById('inc-assigned-block').classList.remove('hidden');
    document.getElementById('inc-resolve-block').classList.remove('hidden');
    openModal('modal-incident');
  } catch (e) { showToast(e.message, 'error'); }
}

async function saveIncident(e) {
  if (e && e.preventDefault) e.preventDefault();
  const id = document.getElementById('inc-form-id').value;
  const payload = {
    package_name: document.getElementById('inc-form-package').value || 'Umum',
    incident_text: document.getElementById('inc-form-text').value,
    severity: document.getElementById('inc-form-severity').value,
    status: document.getElementById('inc-form-status').value,
    assigned_to: document.getElementById('inc-form-assigned').value,
    resolution_note: document.getElementById('inc-form-resolution').value,
  };
  try {
    const url = id ? `/api/incidents/${id}` : '/api/incidents';
    const method = id ? 'PATCH' : 'POST';
    const res = await ohFetch(url, { method, body: JSON.stringify(payload) });
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || 'Gagal simpan');
    showToast(d.message || 'Tersimpan');
    closeModal('modal-incident');
    if (document.getElementById('page-incidents') && !document.getElementById('page-incidents').classList.contains('hidden')) fetchIncidents();
    if (document.getElementById('page-ops-home') && !document.getElementById('page-ops-home').classList.contains('hidden')) initOpsHome();
  } catch (e) { showToast(e.message, 'error'); }
}
