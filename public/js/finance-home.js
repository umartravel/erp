// Home Finance dashboard — landing untuk role finance (dan management sebagai read-only)
// Mirrors pattern sales-home / ops-home.
(function(){
    let fhLoading = false;

    async function fhFetch(url) {
        return (window.authFetch || fetch)(url);
    }

    function fhFmt(n) {
        return 'Rp ' + (Math.round(n || 0)).toLocaleString('id-ID');
    }
    function fhFmtShort(n) {
        n = Math.round(n || 0);
        if (n >= 1_000_000_000) return 'Rp ' + (n/1_000_000_000).toFixed(1).replace('.0','') + 'M';
        if (n >= 1_000_000) return 'Rp ' + (n/1_000_000).toFixed(1).replace('.0','') + 'jt';
        if (n >= 1_000) return 'Rp ' + (n/1_000).toFixed(0) + 'rb';
        return 'Rp ' + n.toLocaleString('id-ID');
    }
    function fhEscape(s) {
        return String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    function fhUpdateTabBadge(urgentCount) {
        const t = document.title;
        const base = t.replace(/^\(\d+\)\s+/, '');
        document.title = urgentCount > 0 ? `(${urgentCount}) ${base}` : base;
    }

    const SEV_CLASS = {
        critical: 'bg-red-100 text-red-800 border-red-300',
        high: 'bg-orange-100 text-orange-800 border-orange-300',
        medium: 'bg-amber-100 text-amber-800 border-amber-300',
        low: 'bg-gray-100 text-gray-700 border-gray-300',
    };

    function renderAttention(list) {
        const wrap = document.getElementById('fh-attention');
        const inner = document.getElementById('fh-attention-list');
        if (!wrap || !inner) return;
        if (!list || !list.length) {
            wrap.classList.add('hidden');
            fhUpdateTabBadge(0);
            return;
        }
        wrap.classList.remove('hidden');
        inner.innerHTML = list.map(a => {
            const cls = SEV_CLASS[a.severity] || SEV_CLASS.low;
            const goto = a.goto ? `onclick="showPage('${a.goto}')"` : '';
            return `<button ${goto} class="text-xs font-medium px-3 py-1.5 rounded-full border ${cls} hover:opacity-80 transition">${fhEscape(a.label)}</button>`;
        }).join('');
        const urgent = list.filter(a => a.severity === 'critical' || a.severity === 'high').length;
        fhUpdateTabBadge(urgent);
    }

    function renderKPI(kpi) {
        document.getElementById('fh-kpi-cash').textContent = fhFmtShort(kpi.cash_saldo);
        document.getElementById('fh-kpi-piutang').textContent = fhFmtShort(kpi.piutang_total);
        document.getElementById('fh-kpi-piutang-count').textContent = kpi.piutang_count;
        document.getElementById('fh-kpi-utang').textContent = fhFmtShort(kpi.utang_vendor_total);
        document.getElementById('fh-kpi-utang-count').textContent = kpi.utang_vendor_count;
        const queue = (kpi.expense_pending_count || 0) + (kpi.refund_pending_count || 0) + (kpi.komisi_pending_count || 0);
        document.getElementById('fh-kpi-queue').textContent = queue;
    }

    function renderPiutang(list) {
        const el = document.getElementById('fh-piutang-list');
        if (!el) return;
        if (!list || !list.length) {
            el.innerHTML = `<div class="p-6 text-center text-xs text-gray-400">Tidak ada piutang aktif.</div>`;
            return;
        }
        el.innerHTML = list.map(j => {
            const dToDepart = j.days_to_depart;
            const urgent = dToDepart !== null && dToDepart <= 30;
            const badge = urgent
                ? `<span class="text-[10px] font-bold text-red-700 bg-red-100 px-1.5 py-0.5 rounded">H-${dToDepart}</span>`
                : dToDepart !== null && dToDepart > 0
                    ? `<span class="text-[10px] text-gray-500">H-${dToDepart}</span>`
                    : '';
            return `<div class="px-4 py-3 hover:bg-amber-50 flex items-center justify-between gap-3">
                <div class="min-w-0 flex-1">
                    <div class="text-sm font-medium text-gray-800 truncate">${fhEscape(j.name)}</div>
                    <div class="text-[11px] text-gray-500 truncate">${fhEscape(j.package_type)} · ${fhEscape(j.sales_name)}</div>
                </div>
                <div class="text-right shrink-0">
                    <div class="text-sm font-bold text-amber-700">${fhFmtShort(j.sisa)}</div>
                    <div class="text-[10px] text-gray-500 flex items-center gap-1 justify-end">${badge} <span>${j.payment_status}</span></div>
                </div>
            </div>`;
        }).join('');
    }

    function renderVendorDue(list) {
        const el = document.getElementById('fh-vendor-list');
        if (!el) return;
        if (!list || !list.length) {
            el.innerHTML = `<div class="p-6 text-center text-xs text-gray-400">Tidak ada tagihan vendor dalam +/- 14 hari.</div>`;
            return;
        }
        el.innerHTML = list.map(v => {
            const d = v.days_to_due;
            const overdue = d !== null && d < 0;
            const soon = d !== null && d >= 0 && d <= 7;
            const cls = overdue ? 'text-red-700 bg-red-100' : soon ? 'text-orange-700 bg-orange-100' : 'text-gray-600 bg-gray-100';
            const label = overdue ? `Telat ${Math.abs(d)}h` : `H-${d}`;
            return `<div class="px-4 py-3 hover:bg-indigo-50 flex items-center justify-between gap-3">
                <div class="min-w-0 flex-1">
                    <div class="text-sm font-medium text-gray-800 truncate">${fhEscape(v.vendor_name || v.vendor_type)}</div>
                    <div class="text-[11px] text-gray-500 truncate">${fhEscape(v.package_name || '')} - ${fhEscape(v.vendor_type)} - ${fhEscape(v.status)}</div>
                </div>
                <div class="text-right shrink-0">
                    <div class="text-sm font-bold text-indigo-700">${fhFmtShort(v.sisa)}</div>
                    <div class="text-[10px] font-medium px-1.5 py-0.5 rounded ${cls} inline-block mt-0.5">${label}</div>
                </div>
            </div>`;
        }).join('');
    }

    function renderAged(list) {
        const el = document.getElementById('fh-aged-list');
        if (!el) return;
        if (!list || !list.length) {
            el.innerHTML = `<div class="text-center text-xs text-gray-400 py-4">Tidak ada piutang.</div>`;
            return;
        }
        const total = list.reduce((s, b) => s + (b.sisa || 0), 0);
        el.innerHTML = list.map(b => {
            const pct = total ? Math.round((b.sisa / total) * 100) : 0;
            const color = b.bucket.startsWith('60') ? 'bg-red-500' :
                          b.bucket.startsWith('31') ? 'bg-orange-500' :
                          b.bucket.startsWith('8')  ? 'bg-amber-500' : 'bg-emerald-500';
            return `<div>
                <div class="flex justify-between text-xs mb-1">
                    <span class="font-medium text-gray-700">${b.bucket}</span>
                    <span class="text-gray-500">${b.n} jamaah - <b class="text-gray-800">${fhFmtShort(b.sisa)}</b></span>
                </div>
                <div class="h-1.5 bg-gray-100 rounded overflow-hidden">
                    <div class="${color} h-full" style="width:${pct}%"></div>
                </div>
            </div>`;
        }).join('');
    }

    function renderExpensePending(list) {
        const el = document.getElementById('fh-expense-list');
        if (!el) return;
        if (!list || !list.length) {
            el.innerHTML = `<div class="p-4 text-center text-xs text-gray-400">Tidak ada.</div>`;
            return;
        }
        el.innerHTML = list.map(e => `
            <div class="px-3 py-2 hover:bg-emerald-50 cursor-pointer" onclick="showPage('expense')">
                <div class="text-xs font-medium text-gray-800 truncate">${fhEscape(e.ref || 'Report #' + e.id)}</div>
                <div class="text-[10px] text-gray-500 flex justify-between">
                    <span>${fhEscape(e.user_name)} - ${e.status}</span>
                    <span class="font-bold text-emerald-700">${fhFmtShort(e.total_amount)}</span>
                </div>
            </div>`).join('');
    }

    function renderCair(refund, komisi) {
        const el = document.getElementById('fh-cair-list');
        if (!el) return;
        const items = [
            ...(refund || []).map(r => ({type: 'refund', label: `Refund ${r.jamaah_name || '-'}`, sub: r.reason || '', amount: r.amount})),
            ...(komisi || []).map(c => ({type: 'komisi', label: `Komisi ${c.agent_name || '-'}`, sub: c.jamaah_name || '', amount: c.amount})),
        ];
        if (!items.length) {
            el.innerHTML = `<div class="p-4 text-center text-xs text-gray-400">Tidak ada antrian cair.</div>`;
            return;
        }
        el.innerHTML = items.map(i => {
            const badge = i.type === 'refund' ? 'bg-rose-100 text-rose-700' : 'bg-indigo-100 text-indigo-700';
            return `<div class="px-3 py-2 hover:bg-rose-50 cursor-pointer" onclick="showPage('finance')">
                <div class="flex justify-between items-start gap-2">
                    <div class="min-w-0 flex-1">
                        <div class="text-xs font-medium text-gray-800 truncate">${fhEscape(i.label)}</div>
                        <div class="text-[10px] text-gray-500 truncate">${fhEscape(i.sub)}</div>
                    </div>
                    <div class="text-right shrink-0">
                        <div class="text-xs font-bold text-gray-800">${fhFmtShort(i.amount)}</div>
                        <div class="text-[9px] font-medium px-1 rounded ${badge} inline-block">${i.type}</div>
                    </div>
                </div>
            </div>`;
        }).join('');
    }

    window.initFinanceHome = async function() {
        if (fhLoading) return;
        fhLoading = true;
        try {
            const res = await fhFetch('/finance/home');
            if (!res.ok) throw new Error('Gagal memuat Home Finance');
            const data = await res.json();
            renderKPI(data.kpi || {});
            renderAttention(data.attention || []);
            renderPiutang(data.piutang_jamaah || []);
            renderVendorDue(data.vendor_due_soon || []);
            renderAged(data.aged_buckets || []);
            renderExpensePending(data.expense_pending || []);
            renderCair(data.refund_pending || [], data.komisi_pending || []);
            if (typeof lucide !== 'undefined') lucide.createIcons();
        } catch (err) {
            console.error('[finance-home]', err);
            const attention = document.getElementById('fh-attention-list');
            if (attention) attention.innerHTML = `<span class="text-xs text-red-600">Gagal memuat data: ${fhEscape(err.message)}</span>`;
            document.getElementById('fh-attention')?.classList.remove('hidden');
        } finally {
            fhLoading = false;
        }
    };
})();
