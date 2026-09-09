/* Home Ops -- dashboard landing untuk role ops. Mirror pola sales-home.js.
   Endpoint: GET /api/ops/home. Palet konsisten dengan Brand UMAR (gold+charcoal). */

const OH_COLORS = {
  gold: '#F8BE20', darkGold: '#B8860B', charcoal: '#1D1D1B',
  cream: '#F4F1EA', gray500: '#6B7280', red: '#DC2626', amber: '#B45309',
  green: '#059669', blue: '#2563EB',
};

function ohUpdateTabBadge(n) {
  if (typeof window.setUmarTabBadge === 'function') { window.setUmarTabBadge('ops', n); return; }
}

function ohFetch(url, opts) { return (window.authFetch || fetch)(url, opts); }
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
    ohLoadReminders();  // Phase 9a: panel H-7 + H-30 + trigger notif on-load
    if (typeof lucide !== 'undefined') lucide.createIcons();
  } catch (e) { console.error('[ops-home] error', e); }
}

// Phase 9a: Reminder Keberangkatan
async function ohLoadReminders() {
  try {
    const res = await ohFetch('/api/reminders/departures');
    if (!res.ok) { console.error('[ops-home] reminders fetch gagal', res.status); return; }
    const data = await res.json();
    ohRenderRemindBucket('h7', data.h7 || []);
    ohRenderRemindBucket('h30', data.h30 || []);
    // Fire-and-forget notif check -- kirim notif ke role ops kalau ada paket bermasalah.
    // Dedupe per-hari di backend, aman dipanggil setiap kali user buka Home Ops.
    ohFetch('/api/reminders/departures/check', {method: 'POST'}).catch(() => {});
    if (typeof lucide !== 'undefined') lucide.createIcons();
  } catch (e) { console.error('[ops-home] reminders error', e); }
}

function ohRenderRemindBucket(kind, list) {
  const body = document.getElementById(`oh-remind-${kind}-body`);
  const count = document.getElementById(`oh-remind-${kind}-count`);
  if (!body || !count) return;
  count.textContent = String(list.length);
  if (!list.length) {
    body.innerHTML = `<div class="p-4 text-center text-xs text-gray-400 italic">Tidak ada paket dalam window ini.</div>`;
    return;
  }
  const pctBar = (label, pct, threshold) => {
    const p = pct || 0;
    const color = p >= threshold ? '#059669' : (p >= threshold - 20 ? '#B45309' : '#DC2626');
    return `
      <div class="flex items-center gap-2 text-[10px]">
        <span class="w-24 text-gray-600">${label}</span>
        <div class="flex-1 h-1.5 rounded-full bg-gray-200 overflow-hidden">
          <div class="h-1.5 rounded-full" style="width:${p}%;background:${color};"></div>
        </div>
        <span class="w-10 text-right font-bold" style="color:${color};">${p}%</span>
      </div>`;
  };
  const primaryLabel = kind === 'h7' ? 'Lunas' : 'Visa Siap';
  body.innerHTML = list.map(p => `
    <div class="px-4 py-3 border-b" style="border-color:#F4F1EA;">
      <div class="flex items-start justify-between gap-3 mb-2">
        <div class="flex-1 min-w-0">
          <p class="text-xs font-bold truncate" style="color:#1D1D1B;">${p.name}</p>
          <p class="text-[10px]" style="color:#6B7280;">
            Berangkat ${ohFmtDate(p.departure_date)} <span class="font-bold" style="color:#B8860B;">(H-${p.days_until})</span> · ${p.jamaah_count} jamaah
          </p>
        </div>
      </div>
      <div class="space-y-1">
        ${pctBar(primaryLabel, kind === 'h7' ? p.paid_full_pct : p.visa_ready_pct, kind === 'h7' ? 90 : 70)}
        ${pctBar('Checklist', p.checklist_pct, kind === 'h7' ? 100 : 60)}
        ${pctBar('Vendor', p.vendor_confirmed_pct, 80)}
      </div>
    </div>
  `).join('');
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
  // Phase 5b: shared welcome hero
  if (typeof umarRenderWelcomeHero === 'function') {
    umarRenderWelcomeHero('oh-hero-mount', data.me?.name || 'Tim Operasional');
  }

  const k = data.kpi || {};
  set('oh-kpi-packages', (k.active_packages || 0).toLocaleString('id-ID'));
  set('oh-kpi-jamaah', (k.active_jamaah || 0).toLocaleString('id-ID'));
  set('oh-kpi-stock', (k.low_stock_count || 0).toLocaleString('id-ID'));
  set('oh-kpi-asset', (k.asset_checkout_count || 0).toLocaleString('id-ID'));
  set('oh-kpi-passport', (k.passport_expiring_count || 0).toLocaleString('id-ID'));

  renderOhAttention(data.attention || {});
  renderOhUpcoming(data.upcoming_packages || []);
  renderOhReturned(data.returned_packages || []);
  renderOhIncidents(data.incidents_open || []);
  renderOhStock(data.low_stock_items || []);
  renderOhPassport(data.passport_expiring || []);
  renderOhHandover(data.handover_pending || []);
  ohWireSocketAutoRefresh();
}

function renderOhReturned(list) {
  const body = document.getElementById('oh-returned-body');
  if (!body) return;
  if (!list.length) { body.innerHTML = ohEmpty('Belum ada paket selesai dalam 60 hari terakhir.'); return; }
  body.innerHTML = list.map(p => {
    const debriefPct = Math.round(((p.debrief_filled || 0) / 6) * 100);
    const debriefColor = debriefPct >= 100 ? OH_COLORS.green : debriefPct >= 50 ? OH_COLORS.gold : OH_COLORS.red;
    const feedbackColor = (p.feedback_count || 0) >= (p.filled || 0) ? OH_COLORS.green : OH_COLORS.darkGold;
    return `<div class="p-4 border-b last:border-b-0" style="border-color:#F4F1EA;">
      <div class="flex justify-between items-start mb-2">
        <div class="flex-1 pr-3">
          <b class="text-sm" style="color:${OH_COLORS.charcoal};">${p.name}</b>
          <div class="text-[11px] mt-0.5" style="color:${OH_COLORS.gray500};">
            Kembali <b style="color:${OH_COLORS.charcoal};">${ohFmtDate(p.effective_return || p.return_date || p.departure_date)}</b>
            &middot; ${p.filled || 0} jamaah
            &middot; ${p.hotel_mekkah || 'Hotel Mekkah -'}
          </div>
        </div>
      </div>
      <div class="grid grid-cols-2 gap-2 text-[11px] mt-2">
        <div>
          <span style="color:${OH_COLORS.gray500};">Debrief:</span>
          <b style="color:${debriefColor};">${p.debrief_filled||0}/6 kategori</b>
        </div>
        <div>
          <span style="color:${OH_COLORS.gray500};">Feedback jamaah:</span>
          <b style="color:${feedbackColor};">${p.feedback_count||0}/${p.filled||0}</b>
        </div>
      </div>
      <div class="flex gap-1 mt-2 justify-end">
        <button onclick="openDebriefModal(${p.id}, ${JSON.stringify(p.name).replace(/"/g,'&quot;')})" class="text-xs px-2 py-1 rounded font-medium" style="background:${OH_COLORS.gold};color:${OH_COLORS.charcoal};">Debrief Operasional</button>
        <button onclick="openFeedbackModal(${p.id}, ${JSON.stringify(p.name).replace(/"/g,'&quot;')})" class="text-xs px-2 py-1 rounded font-medium border" style="border-color:#E8DFC8;color:${OH_COLORS.charcoal};background:#E0E7FF;">Feedback Jamaah</button>
      </div>
    </div>`;
  }).join('');
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
    chip('Paket H-7 belum siap (<70%)', a.paket_not_ready, '#FEE2E2', "document.getElementById('oh-upcoming-body')?.scrollIntoView({behavior:'smooth',block:'start'})"),
    chip('Vendor belum confirmed H-14', a.vendor_at_risk, '#FCE7F3', "document.getElementById('oh-upcoming-body')?.scrollIntoView({behavior:'smooth',block:'start'})"),
    chip('Insiden terbuka', a.incidents_open, '#FEF3C7', "showPage('incidents')"),
    chip('Stok kritis', a.low_stock, '#FFEDD5', "document.getElementById('oh-stock-body')?.scrollIntoView({behavior:'smooth',block:'start'})"),
    chip('Paspor expiring', a.passport_expiring, '#DBEAFE', "document.getElementById('oh-passport-body')?.scrollIntoView({behavior:'smooth',block:'start'})"),
    chip('Handover pending', a.handover_pending, '#F3E8FF', "document.getElementById('oh-handover-body')?.scrollIntoView({behavior:'smooth',block:'start'})"),
    chip('Paket selesai belum debrief', a.debrief_pending, '#E0E7FF', "document.getElementById('oh-returned-body')?.scrollIntoView({behavior:'smooth',block:'start'})"),
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
    const pct = p.checklist_pct || 0;
    const barColor = pct >= 100 ? OH_COLORS.green : pct >= 70 ? OH_COLORS.gold : pct >= 40 ? OH_COLORS.darkGold : OH_COLORS.red;
    const pctBadgeColor = pct >= 70 ? OH_COLORS.green : OH_COLORS.red;
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
      <div class="mt-2">
        <div class="flex items-baseline justify-between mb-1">
          <span class="text-[10px] font-bold uppercase tracking-wider" style="color:${OH_COLORS.darkGold};">Checklist Kesiapan</span>
          <span class="text-[10px] font-black" style="color:${pctBadgeColor};">${p.checklist_done || 0}/${p.checklist_total || 0} &middot; ${pct}%</span>
        </div>
        <div class="h-1.5 w-full rounded-full overflow-hidden" style="background:#E8DFC8;">
          <div style="width:${Math.min(100,pct)}%;height:100%;background:${barColor};transition:width 0.4s ease;"></div>
        </div>
      </div>
      <div class="text-[10px] mt-1.5" style="color:${OH_COLORS.gray500};">
        Vendor: <b style="color:${(p.vendor_pending||0)>0 && (days!==null&&days<=14) ? OH_COLORS.red : OH_COLORS.charcoal};">${p.vendor_confirmed||0}/${p.vendor_total||0} confirmed</b>
        ${(p.vendor_pending||0)>0 ? `&middot; <span style="color:${OH_COLORS.red};">${p.vendor_pending} pending</span>` : ''}
      </div>
      <div class="flex items-center justify-between text-[11px] mt-2">
        <span style="color:${OH_COLORS.gray500};">
          Terisi: <b style="color:${seatColor};">${p.filled || 0} / ${p.quota || 45}</b>
          ${p.airline_depart ? '&middot; ' + p.airline_depart : ''}
        </span>
        <div class="flex gap-1">
          <button onclick="openChecklistModal(${p.id}, ${JSON.stringify(p.name).replace(/"/g,'&quot;')})" class="text-xs px-2 py-1 rounded font-medium" style="background:${OH_COLORS.gold};color:${OH_COLORS.charcoal};">Checklist</button>
          <button onclick="openVendorModal(${p.id}, ${JSON.stringify(p.name).replace(/"/g,'&quot;')})" class="text-xs px-2 py-1 rounded font-medium border" style="border-color:#E8DFC8;color:${OH_COLORS.charcoal};background:#FCE7F3;">Vendor</button>
          <button onclick="showPage('operasional')" class="text-xs px-2 py-1 rounded font-medium border" style="border-color:#E8DFC8;color:${OH_COLORS.charcoal};">Manifest</button>
        </div>
      </div>
    </div>`;
  }).join('');
}

// ============================================================================
// CHECKLIST PRA-KEBERANGKATAN
// ============================================================================
let __chkPkgId = null;

async function openChecklistModal(pkgId, pkgName) {
  __chkPkgId = pkgId;
  document.getElementById('chk-title').textContent = 'Checklist: ' + pkgName;
  document.getElementById('chk-body').innerHTML = '<p class="text-xs text-gray-400 py-4 text-center">Memuat...</p>';
  openModal('modal-checklist');
  await renderChecklistDetail();
}

async function renderChecklistDetail() {
  try {
    const res = await ohFetch(`/api/packages/${__chkPkgId}/checklist`);
    if (!res.ok) throw new Error('Gagal muat checklist');
    const d = await res.json();
    const body = document.getElementById('chk-body');
    const s = d.summary || { done: 0, total: 0, pct: 0 };
    const barColor = s.pct >= 100 ? OH_COLORS.green : s.pct >= 70 ? OH_COLORS.gold : s.pct >= 40 ? OH_COLORS.darkGold : OH_COLORS.red;
    const daysToGoLabel = d.package?.days_to_go != null
      ? `Berangkat ${ohFmtDate(d.package.departure_date)} <b>(H-${d.package.days_to_go})</b>`
      : `Berangkat ${ohFmtDate(d.package?.departure_date)}`;
    const catColors = { Dokumen: '#2563EB', Logistik: '#B8860B', Vendor: '#7C3AED', Umum: '#6B7280' };
    // Group by category
    const grouped = {};
    (d.items || []).forEach(i => { (grouped[i.category] = grouped[i.category] || []).push(i); });
    const groupsHtml = Object.entries(grouped).map(([cat, items]) => `
      <div class="mb-3">
        <div class="text-[10px] font-bold uppercase tracking-wider mb-1.5" style="color:${catColors[cat]||catColors.Umum};">${cat}</div>
        ${items.map(i => renderChecklistItem(i)).join('')}
      </div>
    `).join('');
    body.innerHTML = `
      <div class="rounded-lg p-3 mb-4 border" style="background:#F4F1EA;border-color:#E8DFC8;">
        <div class="text-xs" style="color:${OH_COLORS.gray500};">${daysToGoLabel}</div>
        <div class="flex items-baseline justify-between mt-2 mb-1">
          <span class="text-xs font-bold" style="color:${OH_COLORS.charcoal};">Progress Kesiapan</span>
          <span class="text-sm font-black" style="color:${barColor};">${s.done}/${s.total} &middot; ${s.pct}%</span>
        </div>
        <div class="h-2 w-full rounded-full overflow-hidden" style="background:#E8DFC8;">
          <div style="width:${Math.min(100,s.pct)}%;height:100%;background:${barColor};transition:width 0.4s ease;"></div>
        </div>
      </div>
      ${groupsHtml}
    `;
    if (typeof lucide !== 'undefined') lucide.createIcons();
  } catch (e) { showToast(e.message, 'error'); }
}

function renderChecklistItem(i) {
  const statusColor = { done: OH_COLORS.green, na: OH_COLORS.gray500, pending: OH_COLORS.red };
  const statusLabel = { done: 'Selesai', na: 'Tidak Berlaku', pending: 'Belum' };
  const statusBg = { done: '#D1FAE5', na: '#F3F4F6', pending: '#FEE2E2' };
  const s = i.status || 'pending';
  const dueLabel = i.default_offset_days ? `<span class="text-[10px]" style="color:${OH_COLORS.gray500};">target H-${i.default_offset_days}</span>` : '';
  const completedInfo = i.completed_at
    ? `<div class="text-[10px] mt-0.5" style="color:${OH_COLORS.gray500};">Oleh <b>${i.completed_by}</b> &middot; ${ohFmtDate(i.completed_at)}</div>`
    : '';
  return `
    <div class="border rounded-lg p-3 mb-2" style="border-color:#F4F1EA;background:#FFFDF7;">
      <div class="flex items-start justify-between gap-2">
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="text-[10px] font-bold px-1.5 py-0.5 rounded" style="background:${statusBg[s]};color:${statusColor[s]};">${statusLabel[s]}</span>
            ${dueLabel}
          </div>
          <p class="text-sm mt-1" style="color:${OH_COLORS.charcoal};">${i.label}</p>
          ${completedInfo}
        </div>
        <div class="flex gap-1 shrink-0">
          <button onclick="toggleChecklistItem('${i.item_key}', 'done')" class="text-[10px] px-2 py-1 rounded font-bold ${s==='done'?'ring-2':''}" style="background:${OH_COLORS.green};color:#fff;">&#10003;</button>
          <button onclick="toggleChecklistItem('${i.item_key}', 'na')" class="text-[10px] px-2 py-1 rounded font-bold ${s==='na'?'ring-2':''}" style="background:${OH_COLORS.gray500};color:#fff;" title="Tidak berlaku">N/A</button>
          <button onclick="toggleChecklistItem('${i.item_key}', 'pending')" class="text-[10px] px-2 py-1 rounded font-bold ${s==='pending'?'ring-2':''}" style="background:${OH_COLORS.red};color:#fff;">&#8635;</button>
        </div>
      </div>
    </div>
  `;
}

async function toggleChecklistItem(itemKey, status) {
  if (!__chkPkgId) return;
  try {
    const res = await ohFetch(`/api/packages/${__chkPkgId}/checklist`, {
      method: 'POST',
      body: JSON.stringify({ item_key: itemKey, status }),
    });
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || 'Gagal update');
    await renderChecklistDetail();
    // Refresh Home Ops kalau lagi visible -- socket bisa saja belum wired,
    // jadi pull manual supaya progress bar + attention hero konsisten.
    const homePage = document.getElementById('page-ops-home');
    if (homePage && !homePage.classList.contains('hidden')) initOpsHome();
  } catch (e) { showToast(e.message, 'error'); }
}

// ============================================================================
// VENDOR BOOKINGS
// ============================================================================
const VENDOR_TYPE_LABEL = {
  hotel_mekkah: 'Hotel Mekkah', hotel_madinah: 'Hotel Madinah',
  airline_depart: 'Maskapai Berangkat', airline_return: 'Maskapai Pulang', airline_transit: 'Maskapai Transit',
  bus_local: 'Bus Lokal', muthawif: 'Muthawif / Tour Leader', catering: 'Catering', other: 'Lainnya',
};
const VENDOR_STATUS_COLOR = {
  Booked:    { bg: '#F3F4F6', fg: '#6B7280' },
  Deposit:   { bg: '#DBEAFE', fg: '#2563EB' },
  Paid:      { bg: '#FEF3C7', fg: '#B45309' },
  Confirmed: { bg: '#D1FAE5', fg: '#059669' },
  Cancelled: { bg: '#FEE2E2', fg: '#DC2626' },
};
let __vendPkgId = null;
let __vendPkgName = '';

function openVendorModal(pkgId, pkgName) {
  __vendPkgId = pkgId;
  __vendPkgName = pkgName;
  document.getElementById('vend-title').textContent = 'Vendor Timeline: ' + pkgName;
  document.getElementById('vend-list').innerHTML = '<p class="text-xs text-gray-400 py-4 text-center">Memuat...</p>';
  hideVendorForm();
  openModal('modal-vendor');
  renderVendorList();
}

async function renderVendorList() {
  try {
    const res = await ohFetch(`/api/packages/${__vendPkgId}/vendors`);
    if (!res.ok) throw new Error('Gagal muat vendor');
    const list = await res.json();
    const box = document.getElementById('vend-list');
    if (!list.length) {
      box.innerHTML = `<div class="text-center py-6">
        <p class="text-sm" style="color:${OH_COLORS.gray500};">Belum ada vendor booking untuk paket ini.</p>
        <button onclick="openAddVendorForm()" class="mt-3 px-3 py-1.5 rounded-lg text-xs font-bold" style="background:${OH_COLORS.gold};color:${OH_COLORS.charcoal};"><i data-lucide="plus" class="inline w-3.5 h-3.5"></i> Tambah Vendor Booking</button>
      </div>`;
      if (typeof lucide !== 'undefined') lucide.createIcons();
      return;
    }
    box.innerHTML = list.map(v => {
      const sc = VENDOR_STATUS_COLOR[v.status] || VENDOR_STATUS_COLOR.Booked;
      const dueLabel = v.due_date ? `Due: ${ohFmtDate(v.due_date)}` : '';
      const amountLabel = (v.deposit_amount || v.total_amount)
        ? `DP ${ohFmtRp(v.deposit_amount)} / ${ohFmtRp(v.total_amount)}` : '';
      return `<div class="border rounded-lg p-3 mb-2" style="border-color:#F4F1EA;background:#FFFDF7;">
        <div class="flex justify-between items-start gap-2">
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2 flex-wrap mb-1">
              <span class="text-[10px] font-bold px-1.5 py-0.5 rounded" style="background:${sc.bg};color:${sc.fg};">${v.status}</span>
              <span class="text-[10px] font-bold" style="color:${OH_COLORS.darkGold};">${VENDOR_TYPE_LABEL[v.vendor_type] || v.vendor_type}</span>
              ${dueLabel ? `<span class="text-[10px]" style="color:${OH_COLORS.gray500};">${dueLabel}</span>` : ''}
            </div>
            <b class="text-sm" style="color:${OH_COLORS.charcoal};">${v.vendor_name || '<span style="color:'+OH_COLORS.gray500+';">(nama vendor belum diisi)</span>'}</b>
            ${v.confirmation_code ? `<div class="text-[10px] mt-0.5" style="color:${OH_COLORS.gray500};">Ref: <b>${v.confirmation_code}</b></div>` : ''}
            ${amountLabel ? `<div class="text-[10px] mt-0.5" style="color:${OH_COLORS.gray500};">${amountLabel}</div>` : ''}
            ${v.notes ? `<div class="text-[10px] mt-1 italic" style="color:${OH_COLORS.gray500};">"${v.notes}"</div>` : ''}
          </div>
          <div class="flex flex-col gap-1 shrink-0">
            <button onclick="openEditVendorForm(${v.id})" class="text-[10px] px-2 py-1 rounded font-bold" style="background:${OH_COLORS.gold};color:${OH_COLORS.charcoal};">Edit</button>
            ${v.status !== 'Confirmed' && v.status !== 'Cancelled' ? `<button onclick="quickAdvanceVendor(${v.id}, '${nextStatus(v.status)}')" class="text-[10px] px-2 py-1 rounded font-bold" style="background:${OH_COLORS.green};color:#fff;" title="Advance ke ${nextStatus(v.status)}">&rarr; ${nextStatus(v.status)}</button>` : ''}
          </div>
        </div>
      </div>`;
    }).join('') + `<button onclick="openAddVendorForm()" class="w-full mt-2 px-3 py-2 rounded-lg text-xs font-bold border-2 border-dashed" style="border-color:${OH_COLORS.gold};color:${OH_COLORS.darkGold};"><i data-lucide="plus" class="inline w-3.5 h-3.5"></i> Tambah Vendor Booking</button>`;
    if (typeof lucide !== 'undefined') lucide.createIcons();
  } catch (e) { showToast(e.message, 'error'); }
}

function nextStatus(current) {
  const flow = { Booked: 'Deposit', Deposit: 'Paid', Paid: 'Confirmed' };
  return flow[current] || current;
}

async function quickAdvanceVendor(vid, newStatus) {
  try {
    const res = await ohFetch(`/api/vendors/${vid}`, { method: 'PATCH', body: JSON.stringify({ status: newStatus }) });
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || 'Gagal advance');
    showToast('Status: ' + newStatus);
    await renderVendorList();
    const homePage = document.getElementById('page-ops-home');
    if (homePage && !homePage.classList.contains('hidden')) initOpsHome();
  } catch (e) { showToast(e.message, 'error'); }
}

function openAddVendorForm() {
  showVendorForm();
  document.getElementById('vend-form-title').textContent = 'Tambah Vendor Booking';
  document.getElementById('vend-form-id').value = '';
  document.getElementById('vend-form-type').value = 'hotel_mekkah';
  document.getElementById('vend-form-name').value = '';
  document.getElementById('vend-form-status').value = 'Booked';
  document.getElementById('vend-form-deposit').value = '';
  document.getElementById('vend-form-total').value = '';
  document.getElementById('vend-form-due').value = '';
  document.getElementById('vend-form-code').value = '';
  document.getElementById('vend-form-notes').value = '';
  document.getElementById('vend-form-delete').classList.add('hidden');
}

async function openEditVendorForm(vid) {
  try {
    const res = await ohFetch(`/api/packages/${__vendPkgId}/vendors`);
    const list = await res.json();
    const v = list.find(x => x.id === vid);
    if (!v) return showToast('Vendor tidak ditemukan', 'error');
    showVendorForm();
    document.getElementById('vend-form-title').textContent = 'Edit Vendor Booking';
    document.getElementById('vend-form-id').value = v.id;
    document.getElementById('vend-form-type').value = v.vendor_type;
    document.getElementById('vend-form-name').value = v.vendor_name || '';
    document.getElementById('vend-form-status').value = v.status;
    document.getElementById('vend-form-deposit').value = v.deposit_amount || '';
    document.getElementById('vend-form-total').value = v.total_amount || '';
    document.getElementById('vend-form-due').value = v.due_date || '';
    document.getElementById('vend-form-code').value = v.confirmation_code || '';
    document.getElementById('vend-form-notes').value = v.notes || '';
    document.getElementById('vend-form-delete').classList.remove('hidden');
  } catch (e) { showToast(e.message, 'error'); }
}

function showVendorForm() { document.getElementById('vend-form').classList.remove('hidden'); }
function hideVendorForm() { document.getElementById('vend-form').classList.add('hidden'); }

async function saveVendor(e) {
  if (e && e.preventDefault) e.preventDefault();
  const id = document.getElementById('vend-form-id').value;
  const payload = {
    vendor_type: document.getElementById('vend-form-type').value,
    vendor_name: document.getElementById('vend-form-name').value,
    status: document.getElementById('vend-form-status').value,
    deposit_amount: parseInt(document.getElementById('vend-form-deposit').value) || 0,
    total_amount: parseInt(document.getElementById('vend-form-total').value) || 0,
    due_date: document.getElementById('vend-form-due').value,
    confirmation_code: document.getElementById('vend-form-code').value,
    notes: document.getElementById('vend-form-notes').value,
  };
  try {
    const url = id ? `/api/vendors/${id}` : `/api/packages/${__vendPkgId}/vendors`;
    const method = id ? 'PATCH' : 'POST';
    const res = await ohFetch(url, { method, body: JSON.stringify(payload) });
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || 'Gagal simpan');
    showToast(d.message || 'Tersimpan');
    hideVendorForm();
    await renderVendorList();
    const homePage = document.getElementById('page-ops-home');
    if (homePage && !homePage.classList.contains('hidden')) initOpsHome();
  } catch (e) { showToast(e.message, 'error'); }
}

async function deleteVendor() {
  const id = document.getElementById('vend-form-id').value;
  if (!id) return;
  if (!confirm('Hapus vendor booking ini?')) return;
  try {
    const res = await ohFetch(`/api/vendors/${id}`, { method: 'DELETE' });
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || 'Gagal hapus');
    showToast(d.message);
    hideVendorForm();
    await renderVendorList();
    const homePage = document.getElementById('page-ops-home');
    if (homePage && !homePage.classList.contains('hidden')) initOpsHome();
  } catch (e) { showToast(e.message, 'error'); }
}

// ============================================================================
// DEBRIEF POST-TRIP + FEEDBACK JAMAAH
// ============================================================================
const DEBRIEF_CAT_LABEL = {
  hotel_mekkah: 'Hotel Mekkah', hotel_madinah: 'Hotel Madinah',
  airline: 'Maskapai', bus: 'Bus Lokal', muthawif: 'Muthawif / Tour Leader',
  overall: 'Overall / Kesan Keseluruhan',
};
let __dbrPkgId = null;
let __fbPkgId = null;

function starRow(active, itemKey, prefix) {
  return [1,2,3,4,5].map(n => `<button type="button" onclick="setStar${prefix}('${itemKey}',${n})" class="text-lg" style="color:${n<=active?OH_COLORS.gold:'#D1D5DB'};line-height:1;">&#9733;</button>`).join('');
}

async function openDebriefModal(pkgId, pkgName) {
  __dbrPkgId = pkgId;
  document.getElementById('dbr-title').textContent = 'Debrief Post-Trip: ' + pkgName;
  document.getElementById('dbr-body').innerHTML = '<p class="text-xs text-gray-400 py-4 text-center">Memuat...</p>';
  openModal('modal-debrief');
  await renderDebriefDetail();
}

async function renderDebriefDetail() {
  try {
    const res = await ohFetch(`/api/packages/${__dbrPkgId}/debriefs`);
    if (!res.ok) throw new Error('Gagal muat debrief');
    const d = await res.json();
    const s = d.summary;
    const body = document.getElementById('dbr-body');
    body.innerHTML = `
      <div class="rounded-lg p-3 mb-3 border" style="background:#F4F1EA;border-color:#E8DFC8;">
        <div class="flex justify-between items-center">
          <div class="text-xs" style="color:${OH_COLORS.gray500};">${d.package.name} &middot; ${ohFmtDate(d.package.departure_date)}</div>
          <div class="text-sm font-black" style="color:${OH_COLORS.darkGold};">${s.filled}/${s.total} kategori ${s.avg_rating ? '&middot; ⭐ ' + s.avg_rating : ''}</div>
        </div>
      </div>
      ${d.items.map(i => `
        <div class="border rounded-lg p-3 mb-2" style="border-color:#F4F1EA;background:#FFFDF7;">
          <div class="flex justify-between items-center mb-1.5">
            <b class="text-sm" style="color:${OH_COLORS.charcoal};">${DEBRIEF_CAT_LABEL[i.category] || i.category}</b>
            <div class="flex items-center gap-2">
              <div id="dbr-stars-${i.category}">${starRow(i.rating || 0, i.category, 'Debrief')}</div>
              <span class="text-[10px]" style="color:${OH_COLORS.gray500};">${i.rating || '-'}/5</span>
            </div>
          </div>
          <textarea id="dbr-notes-${i.category}" rows="2" placeholder="Catatan kualitas ${DEBRIEF_CAT_LABEL[i.category].toLowerCase()}..." class="w-full text-xs border rounded p-2 bg-white" style="border-color:#E8DFC8;">${i.notes || ''}</textarea>
          <div class="flex justify-between items-center mt-1">
            <div class="text-[10px]" style="color:${OH_COLORS.gray500};">${i.updated_at ? 'Oleh ' + (i.created_by||'-') + ' &middot; ' + ohFmtDate(i.updated_at) : 'Belum diisi'}</div>
            <button onclick="saveDebrief('${i.category}')" class="text-[10px] px-2 py-1 rounded font-bold" style="background:${OH_COLORS.gold};color:${OH_COLORS.charcoal};">Simpan</button>
          </div>
        </div>
      `).join('')}
    `;
  } catch (e) { showToast(e.message, 'error'); }
}

function setStarDebrief(cat, n) {
  document.getElementById(`dbr-stars-${cat}`).innerHTML = starRow(n, cat, 'Debrief');
  document.getElementById(`dbr-stars-${cat}`).dataset.rating = n;
  document.getElementById(`dbr-stars-${cat}`).nextElementSibling.textContent = `${n}/5`;
}

async function saveDebrief(cat) {
  const rating = parseInt(document.getElementById(`dbr-stars-${cat}`).dataset.rating || '0') || null;
  const notes = document.getElementById(`dbr-notes-${cat}`).value;
  if (!rating) { showToast('Klik bintang dulu untuk kasih rating', 'error'); return; }
  try {
    const res = await ohFetch(`/api/packages/${__dbrPkgId}/debriefs`, {
      method: 'POST',
      body: JSON.stringify({ category: cat, rating, notes }),
    });
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || 'Gagal simpan');
    showToast(d.message);
    await renderDebriefDetail();
    const homePage = document.getElementById('page-ops-home');
    if (homePage && !homePage.classList.contains('hidden')) initOpsHome();
  } catch (e) { showToast(e.message, 'error'); }
}

async function openFeedbackModal(pkgId, pkgName) {
  __fbPkgId = pkgId;
  document.getElementById('fb-title').textContent = 'Feedback Jamaah: ' + pkgName;
  document.getElementById('fb-body').innerHTML = '<p class="text-xs text-gray-400 py-4 text-center">Memuat...</p>';
  openModal('modal-feedback');
  await renderFeedbackList();
}

async function renderFeedbackList() {
  try {
    const res = await ohFetch(`/api/packages/${__fbPkgId}/feedback`);
    if (!res.ok) throw new Error('Gagal muat feedback');
    const d = await res.json();
    const s = d.summary;
    const body = document.getElementById('fb-body');
    body.innerHTML = `
      <div class="rounded-lg p-3 mb-3 border grid grid-cols-3 gap-2 text-center" style="background:#F4F1EA;border-color:#E8DFC8;">
        <div>
          <div class="text-[10px] font-bold uppercase" style="color:${OH_COLORS.darkGold};">Terisi</div>
          <div class="text-xl font-black" style="color:${OH_COLORS.charcoal};">${s.filled}/${s.total}</div>
        </div>
        <div>
          <div class="text-[10px] font-bold uppercase" style="color:${OH_COLORS.darkGold};">Avg Rating</div>
          <div class="text-xl font-black" style="color:${OH_COLORS.gold};">${s.avg_rating ? '⭐ ' + s.avg_rating : '-'}</div>
        </div>
        <div>
          <div class="text-[10px] font-bold uppercase" style="color:${OH_COLORS.darkGold};">Recommend</div>
          <div class="text-xl font-black" style="color:${OH_COLORS.green};">${s.recommend_pct}%</div>
        </div>
      </div>
      <div class="max-h-96 overflow-y-auto">
        ${(d.jamaah || []).map(j => `
          <div class="border rounded-lg p-3 mb-2" style="border-color:#F4F1EA;background:#FFFDF7;">
            <div class="flex justify-between items-start mb-1">
              <div>
                <b class="text-sm" style="color:${OH_COLORS.charcoal};">${j.name || '-'}</b>
                ${j.phone ? '<div class="text-[10px]" style="color:'+OH_COLORS.gray500+';">'+j.phone+'</div>' : ''}
              </div>
              <div class="flex items-center gap-2">
                <div id="fb-stars-${j.id}">${starRow(j.rating || 0, j.id, 'Feedback')}</div>
                <span class="text-[10px]" style="color:${OH_COLORS.gray500};">${j.rating || '-'}/5</span>
              </div>
            </div>
            <textarea id="fb-testi-${j.id}" rows="2" placeholder="Testimoni jamaah..." class="w-full text-xs border rounded p-2 bg-white mt-2" style="border-color:#E8DFC8;">${j.testimonial || ''}</textarea>
            <textarea id="fb-cmp-${j.id}" rows="1" placeholder="Komplain (opsional)..." class="w-full text-xs border rounded p-2 bg-white mt-2" style="border-color:#E8DFC8;">${j.complaint || ''}</textarea>
            <div class="flex justify-between items-center mt-2">
              <label class="flex items-center gap-1.5 text-[11px]" style="color:${OH_COLORS.gray500};">
                <input type="checkbox" id="fb-rec-${j.id}" ${j.would_recommend?'checked':''}>
                Bersedia rekomendasikan
              </label>
              <div class="flex items-center gap-2">
                ${j.created_at ? '<span class="text-[10px]" style="color:'+OH_COLORS.gray500+';">'+(j.source||'manual')+' &middot; '+ohFmtDate(j.created_at)+'</span>' : ''}
                <button onclick="saveFeedback(${j.id})" class="text-[10px] px-2 py-1 rounded font-bold" style="background:${OH_COLORS.gold};color:${OH_COLORS.charcoal};">Simpan</button>
              </div>
            </div>
          </div>
        `).join('')}
      </div>
    `;
  } catch (e) { showToast(e.message, 'error'); }
}

function setStarFeedback(jid, n) {
  document.getElementById(`fb-stars-${jid}`).innerHTML = starRow(n, jid, 'Feedback');
  document.getElementById(`fb-stars-${jid}`).dataset.rating = n;
  document.getElementById(`fb-stars-${jid}`).nextElementSibling.textContent = `${n}/5`;
}

async function saveFeedback(jid) {
  const rating = parseInt(document.getElementById(`fb-stars-${jid}`).dataset.rating || '0') || null;
  const testimonial = document.getElementById(`fb-testi-${jid}`).value;
  const complaint = document.getElementById(`fb-cmp-${jid}`).value;
  const would_recommend = document.getElementById(`fb-rec-${jid}`).checked;
  try {
    const res = await ohFetch('/api/feedback', {
      method: 'POST',
      body: JSON.stringify({ jamaah_id: jid, package_id: __fbPkgId, rating, testimonial, complaint, would_recommend, source: 'manual' }),
    });
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || 'Gagal simpan');
    showToast(d.message);
    await renderFeedbackList();
    const homePage = document.getElementById('page-ops-home');
    if (homePage && !homePage.classList.contains('hidden')) initOpsHome();
  } catch (e) { showToast(e.message, 'error'); }
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
