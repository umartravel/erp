/*
 * 2026-09-23: Overview Perusahaan (page-mgmt-overview).
 * 3 card KPI cross-divisi (Sales/Finance/Ops) untuk admin/mgmt oversight.
 * Data dari Promise.all 3 endpoint existing (no backend baru).
 */

let _moLastLoad = 0;

async function renderMgmtOverviewPage() {
    if (Date.now() - _moLastLoad < 500) return;
    _moLastLoad = Date.now();
    try {
        const [mgmt, finance, ops] = await Promise.all([
            authFetch('/mgmt/home').then(r => r.json()).catch(() => ({})),
            authFetch('/finance/home').then(r => r.json()).catch(() => ({})),
            authFetch('/ops/home').then(r => r.json()).catch(() => ({})),
        ]);
        _renderSalesCard(mgmt);
        _renderFinanceCard(finance);
        _renderOpsCard(ops);
        if (typeof lucide !== 'undefined') lucide.createIcons();
    } catch (e) {
        document.getElementById('mo-card-sales').innerHTML =
            `<div class="text-red-600 text-sm">Gagal load: ${_moEsc(e.message)}</div>`;
    }
}

function _renderSalesCard(mgmt) {
    const kpi = mgmt.kpi || {};
    const closing = kpi.closing_this_month || 0;
    const omzet = kpi.omzet_this_month || 0;
    const piutangCount = kpi.piutang_count || 0;
    document.getElementById('mo-card-sales').innerHTML = `
        <div class="flex items-center gap-3">
            <div class="w-12 h-12 rounded-xl flex items-center justify-center" style="background:rgba(184,134,11,0.12);">
                <i data-lucide="users" class="w-6 h-6" style="color:#B8860B;"></i>
            </div>
            <div>
                <div class="text-xs font-bold uppercase tracking-wider" style="color:#B8860B;">Sales</div>
                <div class="text-sm font-semibold" style="color:var(--umar-charcoal);">Closing bulan berjalan</div>
            </div>
        </div>
        <div class="grid grid-cols-3 gap-2 pt-3 border-t">
            <div>
                <div class="text-xl font-bold" style="color:#B8860B;">${_moFmtNum(closing)}</div>
                <div class="text-[10px] uppercase font-bold text-gray-500 tracking-wider">Closing MTD</div>
            </div>
            <div>
                <div class="text-xl font-bold" style="color:#B8860B;">${_moFmtIdrShort(omzet)}</div>
                <div class="text-[10px] uppercase font-bold text-gray-500 tracking-wider">Omzet MTD</div>
            </div>
            <div>
                <div class="text-xl font-bold" style="color:#B8860B;">${_moFmtNum(piutangCount)}</div>
                <div class="text-[10px] uppercase font-bold text-gray-500 tracking-wider">Piutang Jml</div>
            </div>
        </div>
        <button onclick="showPage('sales-home')" class="w-full mt-3 py-2 rounded-lg text-xs font-bold text-white transition-colors" style="background:#B8860B;">
            Buka Detail Sales <i data-lucide="arrow-right" class="w-3 h-3 inline"></i>
        </button>
    `;
}

function _renderFinanceCard(finance) {
    const accrual = finance.accrual_kpi || {};
    const kas = accrual.saldo_kas_bank || 0;
    const piutang = finance.piutang_total || 0;
    const approval = (finance.expense_pending_count || 0)
        + (finance.refund_pending_count || 0)
        + (finance.komisi_pending_count || 0);
    document.getElementById('mo-card-finance').innerHTML = `
        <div class="flex items-center gap-3">
            <div class="w-12 h-12 rounded-xl flex items-center justify-center" style="background:rgba(16,185,129,0.12);">
                <i data-lucide="wallet" class="w-6 h-6 text-emerald-600"></i>
            </div>
            <div>
                <div class="text-xs font-bold uppercase tracking-wider text-emerald-700">Finance</div>
                <div class="text-sm font-semibold" style="color:var(--umar-charcoal);">Kas & liabilitas</div>
            </div>
        </div>
        <div class="grid grid-cols-3 gap-2 pt-3 border-t">
            <div>
                <div class="text-xl font-bold text-emerald-700">${_moFmtIdrShort(kas)}</div>
                <div class="text-[10px] uppercase font-bold text-gray-500 tracking-wider">Kas & Bank</div>
            </div>
            <div>
                <div class="text-xl font-bold text-emerald-700">${_moFmtIdrShort(piutang)}</div>
                <div class="text-[10px] uppercase font-bold text-gray-500 tracking-wider">Piutang</div>
            </div>
            <div>
                <div class="text-xl font-bold text-emerald-700">${_moFmtNum(approval)}</div>
                <div class="text-[10px] uppercase font-bold text-gray-500 tracking-wider">Approval</div>
            </div>
        </div>
        <button onclick="showPage('finance-home')" class="w-full mt-3 py-2 rounded-lg text-xs font-bold text-white bg-emerald-600 hover:bg-emerald-700 transition-colors">
            Buka Detail Finance <i data-lucide="arrow-right" class="w-3 h-3 inline"></i>
        </button>
    `;
}

function _renderOpsCard(ops) {
    const kpi = ops.kpi || {};
    const paket = kpi.active_packages || 0;
    const jamaah = kpi.active_jamaah || 0;
    const attention = (ops.attention || []).length;
    document.getElementById('mo-card-ops').innerHTML = `
        <div class="flex items-center gap-3">
            <div class="w-12 h-12 rounded-xl flex items-center justify-center bg-blue-50">
                <i data-lucide="box" class="w-6 h-6 text-blue-600"></i>
            </div>
            <div>
                <div class="text-xs font-bold uppercase tracking-wider text-blue-700">Operasional</div>
                <div class="text-sm font-semibold" style="color:var(--umar-charcoal);">Paket & jamaah aktif</div>
            </div>
        </div>
        <div class="grid grid-cols-3 gap-2 pt-3 border-t">
            <div>
                <div class="text-xl font-bold text-blue-700">${_moFmtNum(paket)}</div>
                <div class="text-[10px] uppercase font-bold text-gray-500 tracking-wider">Paket Aktif</div>
            </div>
            <div>
                <div class="text-xl font-bold text-blue-700">${_moFmtNum(jamaah)}</div>
                <div class="text-[10px] uppercase font-bold text-gray-500 tracking-wider">Jamaah</div>
            </div>
            <div>
                <div class="text-xl font-bold ${attention > 0 ? 'text-red-600' : 'text-blue-700'}">${_moFmtNum(attention)}</div>
                <div class="text-[10px] uppercase font-bold text-gray-500 tracking-wider">Attention</div>
            </div>
        </div>
        <button onclick="showPage('ops-home')" class="w-full mt-3 py-2 rounded-lg text-xs font-bold text-white bg-blue-600 hover:bg-blue-700 transition-colors">
            Buka Detail Operasional <i data-lucide="arrow-right" class="w-3 h-3 inline"></i>
        </button>
    `;
}

function _moFmtNum(n) {
    return new Intl.NumberFormat('id-ID').format(n || 0);
}

function _moFmtIdrShort(n) {
    n = Number(n) || 0;
    if (n >= 1e9) return `Rp ${(n / 1e9).toFixed(1)} M`;
    if (n >= 1e6) return `Rp ${(n / 1e6).toFixed(1)} jt`;
    if (n >= 1e3) return `Rp ${(n / 1e3).toFixed(0)} rb`;
    return `Rp ${_moFmtNum(n)}`;
}

function _moEsc(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[ch]);
}
