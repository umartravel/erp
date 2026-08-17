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

  renderFollowupDue(data.followup_due || []);
  renderPaymentStale(data.payment_stale || []);
  renderLeaderboard(data.leaderboard || [], data.me?.id);
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
