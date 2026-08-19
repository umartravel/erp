/* Home Sales -- dashboard MY-scoped untuk role sales (LINA/TITIN/FARAH).
   Endpoint: GET /api/sales/home. Palet konsisten dengan Brand UMAR (gold+charcoal). */

const SH_COLORS = {
  gold: '#F8BE20', darkGold: '#B8860B', charcoal: '#1D1D1B',
  cream: '#F4F1EA', gray500: '#6B7280', red: '#DC2626', amber: '#B45309',
};

function shFetch(url) {
  return (window.authFetch || fetch)(url);
}

function shFmtRp(n) {
  if (!n && n !== 0) return 'Rp 0';
  return 'Rp ' + (n | 0).toLocaleString('id-ID');
}

function shFmtDate(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('id-ID', { day: '2-digit', month: 'short', year: 'numeric' });
}

function shDaysDiff(iso) {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (isNaN(t)) return null;
  return Math.round((Date.now() - t) / (1000 * 60 * 60 * 24));
}

async function initSalesHome() {
  try {
    const res = await shFetch('/api/sales/home');
    if (!res.ok) {
      console.error('[sales-home] fetch gagal', res.status);
      return;
    }
    const data = await res.json();
    renderSalesHome(data);
    if (typeof lucide !== 'undefined') lucide.createIcons();
  } catch (e) {
    console.error('[sales-home] error', e);
  }
}

// Judul tab dinamis: (N) UMAR ketika ada aksi urgen -- disimpan supaya bisa restore
const SH_BASE_TITLE = document.title.replace(/^\(\d+\)\s*/, '');
function shUpdateTabBadge(n) {
  document.title = n > 0 ? `(${n}) ${SH_BASE_TITLE}` : SH_BASE_TITLE;
}

// Auto-refresh: dengar socket 'data_updated' untuk event yang mengubah tampilan Home Sales
function shWireSocketAutoRefresh() {
  if (window.__shSocketWired) return;
  window.__shSocketWired = true;
  const s = window.socket;
  if (!s || typeof s.on !== 'function') return;
  const refreshEvents = new Set(['jamaah', 'transaction', 'refund_request', 'sales_target', 'agent']);
  s.on('data_updated', (evt) => {
    if (!refreshEvents.has(evt)) return;
    const page = document.getElementById('page-sales-home');
    if (page && !page.classList.contains('hidden')) initSalesHome();
  });
}

function renderSalesHome(data) {
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };

  set('sh-me-name', data.me?.name || '-');
  set('sh-period', data.period || '-');

  const k = data.kpi || {};
  set('sh-kpi-pipeline', (k.pipeline || 0).toLocaleString('id-ID'));
  set('sh-kpi-closing', (k.closing_this_month || 0).toLocaleString('id-ID'));
  set('sh-kpi-omzet', 'Omzet: ' + shFmtRp(k.omzet_this_month));
  set('sh-kpi-piutang', shFmtRp(k.piutang));
  set('sh-kpi-leads', (k.new_leads_7d || 0).toLocaleString('id-ID'));

  renderAttention(data.attention || {}, data.me?.id);
  renderTarget(data.target_this_month, k);
  renderFollowupDue(data.followup_due || []);
  renderPaymentStale(data.payment_stale || []);
  renderLeaderboard(data.leaderboard || [], data.me?.id);
  renderStaleContact(data.stale_contact || []);
  renderUpcomingPackages(data.upcoming_packages || []);
  renderMyAgents(data.my_agents || { total: 0, top: [] });
  shWireSocketAutoRefresh();
}

function renderAttention(a, myId) {
  const box = document.getElementById('sh-attention');
  const chips = document.getElementById('sh-attention-chips');
  if (!box || !chips) return;
  const total = a.total || 0;
  document.getElementById('sh-attention-total').textContent = total;
  shUpdateTabBadge(total);
  if (total <= 0) { box.classList.add('hidden'); chips.innerHTML = ''; return; }
  box.classList.remove('hidden');
  const chip = (label, count, color, onclick) => count > 0
    ? `<button onclick="${onclick}" class="px-3 py-1.5 rounded-lg text-xs font-bold flex items-center gap-1.5" style="background:${color};color:#1D1D1B;">
         <span class="px-1.5 py-0.5 rounded" style="background:#1D1D1B;color:#F8BE20;font-size:10px;">${count}</span>
         ${label}
       </button>`
    : '';
  chips.innerHTML = [
    chip('Follow-up jatuh tempo', a.followup_today, '#FEE2E2', "document.getElementById('sh-followup-body')?.scrollIntoView({behavior:'smooth',block:'start'})"),
    chip('Payment stale', a.payment_stale, '#FEF3C7', "document.getElementById('sh-payment-body')?.scrollIntoView({behavior:'smooth',block:'start'})"),
    chip('Belum dikontak >3 hari', a.stale_contact, '#FFEDD5', "document.getElementById('sh-stale-body')?.scrollIntoView({behavior:'smooth',block:'start'})"),
    chip('Lead baru menunggu', a.new_leads, '#DBEAFE', "showPage('inbox')"),
  ].filter(Boolean).join('');
}

function renderTarget(t, k) {
  const box = document.getElementById('sh-target');
  const body = document.getElementById('sh-target-body');
  if (!box || !body) return;
  if (!t) { box.classList.add('hidden'); return; }
  const tc = t.target_closing || 0;
  const to = t.target_omzet || 0;
  if (tc <= 0 && to <= 0) {
    box.classList.remove('hidden');
    document.getElementById('sh-target-note').textContent = 'Belum ada target -- minta admin/manager set target bulan ini.';
    body.innerHTML = `<div class="col-span-2 text-center py-4 text-xs" style="color:#6B7280;">Target closing dan omzet belum di-set untuk bulan ini.</div>`;
    return;
  }
  box.classList.remove('hidden');
  document.getElementById('sh-target-note').textContent = 'Target di-set oleh admin/management';
  const actualClose = k.closing_this_month || 0;
  const actualOmzet = k.omzet_this_month || 0;
  body.innerHTML =
    shProgressCard('Closing (jamaah)', actualClose, tc, v => v.toLocaleString('id-ID')) +
    shProgressCard('Omzet', actualOmzet, to, shFmtRp);
}

function shProgressCard(label, actual, target, fmt) {
  const pct = target > 0 ? Math.min(200, Math.round((actual / target) * 100)) : 0;
  const barPct = Math.min(100, pct);
  const barColor = pct >= 100 ? '#059669' : pct >= 70 ? SH_COLORS.gold : pct >= 40 ? SH_COLORS.darkGold : SH_COLORS.red;
  const pctColor = pct >= 100 ? '#059669' : SH_COLORS.charcoal;
  return `
    <div>
      <div class="flex items-baseline justify-between mb-1.5">
        <span class="text-xs font-bold uppercase tracking-wider" style="color:${SH_COLORS.darkGold};">${label}</span>
        <span class="text-xs font-black" style="color:${pctColor};">${pct}%</span>
      </div>
      <div class="text-sm font-bold" style="color:${SH_COLORS.charcoal};">${fmt(actual)} <span class="text-xs font-normal" style="color:${SH_COLORS.gray500};">dari target ${fmt(target)}</span></div>
      <div class="mt-2 h-2 w-full rounded-full overflow-hidden" style="background:#E8DFC8;">
        <div style="width:${barPct}%;height:100%;background:${barColor};transition:width 0.4s ease;"></div>
      </div>
    </div>
  `;
}

function renderMyAgents(m) {
  const totalEl = document.getElementById('sh-agents-total');
  const body = document.getElementById('sh-agents-body');
  if (totalEl) totalEl.textContent = `${m.total || 0} agen`;
  if (!body) return;

  const top = m.top || [];
  if (!top.length) {
    body.innerHTML = `
      <div class="py-6 text-center">
        <p class="text-sm" style="color:${SH_COLORS.gray500};">Belum ada agen yang di-assign ke kamu. Hubungi admin untuk assignment.</p>
      </div>`;
    return;
  }

  body.innerHTML = `<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">` + top.map((a, i) => {
    const rank = `#${i + 1}`;
    const rankBg = i === 0 ? SH_COLORS.gold : i === 1 ? '#E5E7EB' : i === 2 ? '#FDE68A' : SH_COLORS.cream;
    const loc = [a.city, a.province].filter(Boolean).join(', ') || '-';
    const jamaah = a.total_jamaah || 0;
    const jamaahLabel = jamaah > 0
      ? `<b style="color:${SH_COLORS.darkGold};">${jamaah}</b> jamaah`
      : `<span style="color:${SH_COLORS.gray500};">Belum ada closing</span>`;
    const lastLabel = a.last_order
      ? `terakhir order ${shFmtDate(a.last_order)}`
      : '<span style="color:#9CA3AF;">belum ada order</span>';
    return `
      <div class="rounded-lg border p-3" style="background:${SH_COLORS.cream};border-color:#E8DFC8;">
        <div class="flex justify-between items-start mb-1">
          <b class="text-xs leading-tight flex-1 pr-2" style="color:${SH_COLORS.charcoal};">${a.name || '(tanpa nama)'}</b>
          <span class="text-[10px] font-bold px-1.5 py-0.5 rounded shrink-0" style="background:${rankBg};color:${SH_COLORS.charcoal};">${rank}</span>
        </div>
        <div class="text-[10px] mb-1" style="color:${SH_COLORS.gray500};">${loc}</div>
        <div class="text-xs">${jamaahLabel}</div>
        <div class="text-[10px] mt-1" style="color:${SH_COLORS.gray500};">${lastLabel}</div>
      </div>`;
  }).join('') + `</div>`;
}

function renderStaleContact(list) {
  const body = document.getElementById('sh-stale-body');
  const cnt = document.getElementById('sh-stale-count');
  if (!body) return;
  if (cnt) cnt.textContent = list.length;

  if (!list.length) {
    body.innerHTML = `
      <div class="py-8 px-6 text-center">
        <i data-lucide="check-circle-2" class="w-10 h-10 mx-auto mb-2" style="color:#10B981;"></i>
        <p class="text-sm font-medium" style="color:#374151;">Semua jamaah aktif kamu masih dalam masa 3 hari kontak.</p>
      </div>`;
    return;
  }

  body.innerHTML = list.map(r => {
    const daysNoContact = r.last_contact
      ? shDaysDiff(r.last_contact)
      : (r.order_date ? shDaysDiff(r.order_date) : null);
    const label = r.last_contact
      ? `${daysNoContact} hari sejak kontak terakhir`
      : (r.order_date ? `Belum pernah dikontak (order ${daysNoContact} hari lalu)` : 'Belum pernah dikontak');
    const sisa = (r.total_price || 0) - (r.paid_amount || 0);
    const waHref = r.phone
      ? `https://wa.me/${String(r.phone).replace(/\D/g, '').replace(/^0/, '62')}`
      : null;
    const waBtn = waHref
      ? `<a href="${waHref}" target="_blank" class="text-xs px-2 py-1 rounded font-medium ml-2" style="background:#DCFCE7;color:#166534;"><i data-lucide="message-circle" class="w-3 h-3 inline"></i> WA</a>`
      : '';
    return `
      <div class="px-5 py-3 border-b hover:bg-gray-50" style="border-color:#F4F1EA;">
        <div class="flex justify-between items-start gap-3">
          <div class="flex-1 min-w-0">
            <b class="text-sm block truncate" style="color:${SH_COLORS.charcoal};">${r.name || '(tanpa nama)'}</b>
            <div class="text-[11px] mt-0.5" style="color:${SH_COLORS.gray500};">
              ${r.package_type || '-'} &middot; status <b style="color:${SH_COLORS.charcoal};">${r.status || '-'}</b>
            </div>
            <div class="text-[11px] mt-0.5" style="color:${SH_COLORS.red};">${label}</div>
          </div>
          <div class="text-right shrink-0">
            ${sisa > 0 ? `<div class="text-[11px] font-bold" style="color:${SH_COLORS.darkGold};">${shFmtRp(sisa)}</div><div class="text-[10px]" style="color:${SH_COLORS.gray500};">sisa</div>` : ''}
            <div class="flex justify-end mt-1">
              <button onclick="openActivityModal(${r.id}, ${JSON.stringify(r.name || '').replace(/"/g, '&quot;')})" class="text-xs px-2 py-1 rounded font-medium" style="background:${SH_COLORS.gold};color:${SH_COLORS.charcoal};">Catat</button>
              ${waBtn}
            </div>
          </div>
        </div>
      </div>`;
  }).join('');
}

function renderUpcomingPackages(list) {
  const body = document.getElementById('sh-upcoming-body');
  if (!body) return;
  if (!list.length) {
    body.innerHTML = `
      <div class="py-6 text-center">
        <p class="text-sm" style="color:${SH_COLORS.gray500};">Tidak ada paket dengan tanggal keberangkatan mendatang.</p>
      </div>`;
    return;
  }
  const today = new Date();
  body.innerHTML = `<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">` + list.map(p => {
    const sisa = (p.quota || 0) - (p.filled || 0);
    const kritis = sisa <= 5;
    const dep = new Date(p.departure_date);
    const daysToGo = Math.round((dep - today) / (1000 * 60 * 60 * 24));
    const seatColor = kritis ? SH_COLORS.red : (sisa <= 15 ? SH_COLORS.darkGold : '#10B981');
    return `
      <div class="rounded-lg border p-3" style="background:${SH_COLORS.cream};border-color:#E8DFC8;">
        <div class="flex justify-between items-start mb-2">
          <b class="text-xs leading-tight flex-1 pr-2" style="color:${SH_COLORS.charcoal};">${p.name}</b>
          <span class="text-[10px] font-bold px-1.5 py-0.5 rounded shrink-0" style="background:${seatColor};color:white;">${sisa} kursi</span>
        </div>
        <div class="text-[10px]" style="color:${SH_COLORS.gray500};">
          ${shFmtDate(p.departure_date)} &middot; <b>${daysToGo}</b> hari lagi &middot; ${p.duration || 9}D
        </div>
        <div class="text-sm font-bold mt-1" style="color:${SH_COLORS.darkGold};">${shFmtRp(p.price)}</div>
        <div class="text-[10px] mt-1 space-y-0.5" style="color:${SH_COLORS.gray500};">
          ${p.hotel_mekkah ? `<div>Mekkah: <b style="color:${SH_COLORS.charcoal};">${p.hotel_mekkah}</b></div>` : ''}
          ${p.airline_depart ? `<div>Maskapai: <b style="color:${SH_COLORS.charcoal};">${p.airline_depart}</b></div>` : ''}
        </div>
      </div>`;
  }).join('') + `</div>`;
}

function renderFollowupDue(list) {
  const body = document.getElementById('sh-followup-body');
  const cnt = document.getElementById('sh-followup-count');
  if (!body) return;
  if (cnt) cnt.textContent = list.length;

  if (!list.length) {
    body.innerHTML = `
      <div class="py-8 px-6 text-center">
        <i data-lucide="check-circle-2" class="w-10 h-10 mx-auto mb-2" style="color:#10B981;"></i>
        <p class="text-sm font-medium" style="color:#374151;">Tidak ada follow-up jatuh tempo hari ini.</p>
        <p class="text-xs mt-1" style="color:${SH_COLORS.gray500};">Buka Database Jamaah &rarr; Riwayat Aktivitas untuk jadwalkan follow-up berikutnya.</p>
      </div>`;
    return;
  }

  const today = new Date().toISOString().slice(0, 10);
  body.innerHTML = list.map(r => {
    const dueDate = (r.next_follow_up || '').slice(0, 10);
    const overdue = dueDate && dueDate < today;
    const overdueBadge = overdue
      ? `<span class="text-[10px] font-bold px-1.5 py-0.5 rounded" style="background:#FEE2E2;color:${SH_COLORS.red};">OVERDUE</span>`
      : `<span class="text-[10px] font-bold px-1.5 py-0.5 rounded" style="background:#FEF3C7;color:${SH_COLORS.amber};">HARI INI</span>`;
    const phone = r.phone ? `<a href="https://wa.me/${r.phone}" target="_blank" class="text-xs" style="color:${SH_COLORS.darkGold};text-decoration:underline;">${r.phone}</a>` : '<span class="text-xs text-gray-400">-</span>';
    return `
      <div class="px-5 py-3 border-b flex justify-between items-center hover:bg-gray-50" style="border-color:#F4F1EA;">
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2">
            <b class="text-sm truncate" style="color:${SH_COLORS.charcoal};">${r.name || '(tanpa nama)'}</b>
            ${overdueBadge}
          </div>
          <div class="text-[11px] mt-0.5" style="color:${SH_COLORS.gray500};">
            ${r.package_type || '-'} &middot; ${phone} &middot; jatuh tempo ${shFmtDate(r.next_follow_up)}
          </div>
        </div>
        <button onclick="openActivityModal(${r.id}, ${JSON.stringify(r.name || '').replace(/"/g, '&quot;')})" class="text-xs px-3 py-1.5 rounded-lg font-medium ml-3 shrink-0" style="background:${SH_COLORS.gold};color:${SH_COLORS.charcoal};">Catat Aktivitas</button>
      </div>`;
  }).join('');
}

function renderPaymentStale(list) {
  const body = document.getElementById('sh-payment-body');
  const cnt = document.getElementById('sh-payment-count');
  if (!body) return;
  if (cnt) cnt.textContent = list.length;

  if (!list.length) {
    body.innerHTML = `
      <div class="py-8 px-6 text-center">
        <i data-lucide="wallet" class="w-10 h-10 mx-auto mb-2" style="color:#10B981;"></i>
        <p class="text-sm font-medium" style="color:#374151;">Semua jamaah DP terbaru masih dalam masa 14 hari.</p>
      </div>`;
    return;
  }

  body.innerHTML = list.map(r => {
    const days = shDaysDiff(r.order_date);
    return `
      <div class="px-5 py-3 border-b flex justify-between items-center hover:bg-gray-50" style="border-color:#F4F1EA;">
        <div class="flex-1 min-w-0">
          <b class="text-sm block truncate" style="color:${SH_COLORS.charcoal};">${r.name || '(tanpa nama)'}</b>
          <div class="text-[11px] mt-0.5" style="color:${SH_COLORS.gray500};">
            ${r.package_type || '-'} &middot; sudah <b style="color:${SH_COLORS.red};">${days} hari</b> sejak order
          </div>
        </div>
        <div class="text-right ml-3">
          <div class="text-xs font-bold" style="color:${SH_COLORS.darkGold};">${shFmtRp(r.sisa)}</div>
          <div class="text-[10px]" style="color:${SH_COLORS.gray500};">Sisa tagihan</div>
        </div>
      </div>`;
  }).join('');
}

function renderLeaderboard(list, myId) {
  const el = document.getElementById('sh-leaderboard');
  if (!el) return;
  if (!list.length) {
    el.innerHTML = '<li class="text-xs text-gray-400 italic text-center">Belum ada closing bulan ini.</li>';
    return;
  }
  el.innerHTML = list.map((r, i) => {
    const isMe = myId && r.id === myId;
    const bg = isMe ? SH_COLORS.gold : 'transparent';
    const rank = i === 0 ? '#1' : i === 1 ? '#2' : i === 2 ? '#3' : `#${i + 1}`;
    return `
      <li class="flex justify-between items-center px-2 py-1.5 rounded" style="background:${bg};">
        <span class="text-sm font-medium" style="color:${SH_COLORS.charcoal};">
          <span class="mr-1 font-bold">${rank}</span>${r.name}${isMe ? ' <span class="text-[9px] font-bold" style="color:'+SH_COLORS.darkGold+';">(YOU)</span>' : ''}
        </span>
        <span class="text-xs font-bold" style="color:${isMe ? SH_COLORS.charcoal : SH_COLORS.darkGold};">${r.closing} closing</span>
      </li>`;
  }).join('');
}

window.initSalesHome = initSalesHome;
