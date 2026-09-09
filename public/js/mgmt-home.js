// Home Management dashboard — executive landing untuk role management (dan admin).
(function(){
    let mhLoading = false;

    async function mhFetch(url) {
        return (window.authFetch || fetch)(url);
    }
    function mhFmtShort(n) {
        n = Math.round(n || 0);
        if (n >= 1_000_000_000) return 'Rp ' + (n/1_000_000_000).toFixed(1).replace('.0','') + 'M';
        if (n >= 1_000_000) return 'Rp ' + (n/1_000_000).toFixed(1).replace('.0','') + 'jt';
        if (n >= 1_000) return 'Rp ' + (n/1_000).toFixed(0) + 'rb';
        return 'Rp ' + n.toLocaleString('id-ID');
    }
    function mhEscape(s) {
        return String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }
    function mhUpdateTabBadge(urgentCount) {
        if (typeof window.setUmarTabBadge === 'function') {
            window.setUmarTabBadge('mgmt', urgentCount);
        }
    }
    const SEV_CLASS = {
        critical: 'bg-red-100 text-red-800 border-red-300',
        high: 'bg-orange-100 text-orange-800 border-orange-300',
        medium: 'bg-amber-100 text-amber-800 border-amber-300',
        low: 'bg-gray-100 text-gray-700 border-gray-300',
    };

    function renderAttention(list) {
        const wrap = document.getElementById('mh-attention');
        const inner = document.getElementById('mh-attention-list');
        if (!wrap || !inner) return;
        if (!list || !list.length) {
            wrap.classList.add('hidden');
            mhUpdateTabBadge(0);
            return;
        }
        wrap.classList.remove('hidden');
        inner.innerHTML = list.map(a => {
            const cls = SEV_CLASS[a.severity] || SEV_CLASS.low;
            const goto = a.goto && a.goto !== 'mgmt-home' ? `onclick="showPage('${a.goto}')"` : '';
            return `<button ${goto} class="text-xs font-medium px-3 py-1.5 rounded-full border ${cls} hover:opacity-80 transition">${mhEscape(a.label)}</button>`;
        }).join('');
        const urgent = list.filter(a => a.severity === 'critical' || a.severity === 'high').length;
        mhUpdateTabBadge(urgent);
    }

    function renderKPI(kpi) {
        document.getElementById('mh-kpi-omzet').textContent = mhFmtShort(kpi.omzet_this_month);
        document.getElementById('mh-kpi-closing').textContent = kpi.closing_this_month || 0;
        document.getElementById('mh-kpi-piutang').textContent = mhFmtShort(kpi.piutang_total);
        document.getElementById('mh-kpi-piutang-count').textContent = kpi.piutang_count || 0;
        document.getElementById('mh-kpi-cash').textContent = mhFmtShort(kpi.cash_saldo);
        const mom = kpi.mom_omzet_pct;
        const momEl = document.getElementById('mh-kpi-omzet-mom');
        if (mom === null || mom === undefined) {
            momEl.textContent = 'vs bulan lalu: -';
            momEl.className = 'text-[10px] text-gray-400 mt-1';
        } else {
            const up = mom >= 0;
            momEl.innerHTML = `<span class="${up ? 'text-emerald-600' : 'text-red-600'} font-semibold">${up ? 'naik' : 'turun'} ${Math.abs(mom)}%</span> vs bulan lalu`;
        }
        // Company target progress
        const trg = document.getElementById('mh-kpi-target');
        if (trg) {
            const rev = kpi.revenue_pct;
            const cls = kpi.closing_pct;
            if (rev === null && cls === null) {
                trg.innerHTML = `<span class="italic">Target belum ditetapkan (Admin/Mgmt → Settings)</span>`;
            } else {
                const revLabel = rev !== null ? `<span class="${rev >= 100 ? 'text-emerald-700' : rev >= 50 ? 'text-amber-700' : 'text-red-700'} font-semibold">${rev}%</span> revenue (${mhFmtShort(kpi.revenue_target)})` : '';
                const clsLabel = cls !== null ? `<span class="${cls >= 100 ? 'text-emerald-700' : cls >= 50 ? 'text-amber-700' : 'text-red-700'} font-semibold">${cls}%</span> closing (${kpi.closing_target} tgt)` : '';
                trg.innerHTML = [revLabel, clsLabel].filter(Boolean).join(' · ');
            }
        }
    }

    function renderSalesPerf(list) {
        const el = document.getElementById('mh-sales-list');
        if (!el) return;
        if (!list || !list.length) {
            el.innerHTML = `<div class="p-6 text-center text-xs text-gray-400">Tidak ada sales terdaftar.</div>`;
            return;
        }
        el.innerHTML = list.map(s => {
            const cPct = s.closing_pct;
            const oPct = s.omzet_pct;
            const noTarget = cPct === null && oPct === null;
            const bar = (pct, color) => {
                const w = Math.min(pct || 0, 100);
                return `<div class="h-1 bg-gray-100 rounded overflow-hidden"><div class="${color} h-full" style="width:${w}%"></div></div>`;
            };
            return `<div class="px-4 py-3 hover:bg-blue-50">
                <div class="flex justify-between items-center mb-1.5">
                    <span class="text-sm font-medium text-gray-800">${mhEscape(s.name)}</span>
                    <span class="text-xs text-gray-500">${s.actual_closing} closing - ${mhFmtShort(s.actual_omzet)}</span>
                </div>
                ${noTarget ? '<div class="text-[10px] text-gray-400 italic">Target belum ditetapkan</div>' : `
                    <div class="space-y-1.5 text-[10px]">
                        <div>
                            <div class="flex justify-between mb-0.5"><span class="text-gray-500">Closing</span><span class="font-semibold ${cPct >= 100 ? 'text-emerald-600' : cPct >= 50 ? 'text-amber-600' : 'text-red-600'}">${cPct !== null ? cPct + '%' : '-'}</span></div>
                            ${bar(cPct, cPct >= 100 ? 'bg-emerald-500' : cPct >= 50 ? 'bg-amber-500' : 'bg-red-500')}
                        </div>
                        <div>
                            <div class="flex justify-between mb-0.5"><span class="text-gray-500">Omzet</span><span class="font-semibold ${oPct >= 100 ? 'text-emerald-600' : oPct >= 50 ? 'text-amber-600' : 'text-red-600'}">${oPct !== null ? oPct + '%' : '-'}</span></div>
                            ${bar(oPct, oPct >= 100 ? 'bg-emerald-500' : oPct >= 50 ? 'bg-amber-500' : 'bg-red-500')}
                        </div>
                    </div>
                `}
            </div>`;
        }).join('');
    }

    function renderTopAgents(list) {
        const el = document.getElementById('mh-agents-list');
        if (!el) return;
        if (!list || !list.length) {
            el.innerHTML = `<div class="p-6 text-center text-xs text-gray-400">Belum ada closing agen bulan ini.</div>`;
            return;
        }
        el.innerHTML = list.map((a, i) => `
            <div class="px-4 py-3 hover:bg-emerald-50 flex items-center justify-between gap-3">
                <div class="min-w-0 flex-1 flex items-center gap-3">
                    <div class="w-6 h-6 rounded-full ${i === 0 ? 'bg-yellow-100 text-yellow-700' : i === 1 ? 'bg-gray-100 text-gray-700' : i === 2 ? 'bg-orange-100 text-orange-700' : 'bg-emerald-50 text-emerald-600'} flex items-center justify-center text-xs font-bold shrink-0">${i+1}</div>
                    <div class="min-w-0">
                        <div class="text-sm font-medium text-gray-800 truncate">${mhEscape(a.name)}</div>
                        <div class="text-[10px] text-gray-500">${a.closings} closing</div>
                    </div>
                </div>
                <div class="text-sm font-bold text-emerald-700 shrink-0">${mhFmtShort(a.omzet)}</div>
            </div>`).join('');
    }

    function renderPackages(list) {
        const el = document.getElementById('mh-packages-list');
        if (!el) return;
        if (!list || !list.length) {
            el.innerHTML = `<div class="p-4 text-center text-xs text-gray-400">Tidak ada paket berangkat 30 hari ke depan.</div>`;
            return;
        }
        el.innerHTML = list.map(p => {
            const fillPct = p.quota ? Math.round((p.filled / p.quota) * 100) : 0;
            const color = fillPct >= 90 ? 'bg-emerald-500' : fillPct >= 60 ? 'bg-amber-500' : 'bg-red-500';
            return `<div class="px-4 py-3 hover:bg-orange-50">
                <div class="flex justify-between items-center mb-1">
                    <span class="text-sm font-medium text-gray-800 truncate">${mhEscape(p.name)}</span>
                    <span class="text-[10px] font-bold text-orange-700">H-${p.days_to_go}</span>
                </div>
                <div class="flex justify-between text-[10px] text-gray-500 mb-1">
                    <span>${p.filled}/${p.quota || '?'} jamaah</span>
                    <span>${fillPct}%</span>
                </div>
                <div class="h-1 bg-gray-100 rounded overflow-hidden"><div class="${color} h-full" style="width:${Math.min(fillPct,100)}%"></div></div>
            </div>`;
        }).join('');
    }

    function renderApprovals(list) {
        const el = document.getElementById('mh-approvals-list');
        if (!el) return;
        if (!list || !list.length) {
            el.innerHTML = `<div class="p-4 text-center text-xs text-gray-400">Tidak ada antrian approval.</div>`;
            return;
        }
        el.innerHTML = list.map(a => {
            const badge = a.kind === 'incident' ? 'bg-red-100 text-red-700' : a.kind === 'refund' ? 'bg-rose-100 text-rose-700' : 'bg-indigo-100 text-indigo-700';
            const amt = a.amount ? `<span class="text-xs font-bold text-gray-800">${mhFmtShort(a.amount)}</span>` : '';
            const canAct = a.kind === 'refund' || a.kind === 'komisi';
            const actions = canAct
                ? `<div class="flex gap-1 mt-1.5">
                        <button class="text-[10px] font-medium bg-emerald-600 hover:bg-emerald-700 text-white px-2 py-0.5 rounded" onclick="event.stopPropagation();mhApproveItem('${a.kind}',${a.id})">Setujui</button>
                        <button class="text-[10px] font-medium bg-red-100 hover:bg-red-200 text-red-700 px-2 py-0.5 rounded" onclick="event.stopPropagation();mhRejectItem('${a.kind}',${a.id})">Tolak</button>
                   </div>`
                : `<div class="text-[10px] text-gray-400 mt-1">Buka halaman untuk detail</div>`;
            const goto = a.kind === 'incident' ? 'incidents' : 'finance';
            return `<div class="px-3 py-2 hover:bg-purple-50">
                <div class="flex justify-between items-start gap-2 cursor-pointer" onclick="showPage('${goto}')">
                    <div class="min-w-0 flex-1">
                        <div class="text-xs font-medium text-gray-800 truncate">${mhEscape(a.label)}</div>
                        <div class="text-[9px] font-medium px-1 rounded ${badge} inline-block mt-0.5">${a.kind}</div>
                    </div>
                    ${amt}
                </div>
                ${actions}
            </div>`;
        }).join('');
    }

    async function mhReview(kind, id, action, note) {
        const url = kind === 'refund' ? `/refund-requests/${id}/review` : `/commission-claims/${id}/review`;
        const res = await mhFetch(url, {
            method: 'PUT',
            body: JSON.stringify({action, note: note || ''}),
        });
        if (!res.ok) {
            let msg = 'Gagal memproses';
            try { msg = (await res.json()).detail || msg; } catch(e){}
            throw new Error(msg);
        }
        return res.json();
    }

    window.mhApproveItem = async function(kind, id) {
        if (!confirm(`Setujui ${kind} #${id}?`)) return;
        try {
            const r = await mhReview(kind, id, 'approve');
            alert(r.message || 'Berhasil disetujui.');
            window.initMgmtHome();
        } catch (err) { alert('Error: ' + err.message); }
    };

    window.mhDownloadPdf = async function() {
        const mEl = document.getElementById('mh-report-month');
        const month = mEl?.value || new Date().toISOString().slice(0, 7);
        try {
            const res = await mhFetch(`/mgmt/monthly-pdf?month=${month}`);
            if (!res.ok) {
                let m = 'Gagal generate PDF';
                try { m = (await res.json()).detail || m; } catch(e){}
                throw new Error(m);
            }
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `Laporan-Eksekutif-${month}.pdf`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            setTimeout(() => URL.revokeObjectURL(url), 5000);
        } catch (err) { alert('Error: ' + err.message); }
    };

    // Phase 8a-4: Excel export bulanan (3 laporan) -- reuse #mh-report-month picker.
    window.mhExportExcel = async function(kind) {
        const mEl = document.getElementById('mh-report-month');
        const month = mEl?.value || new Date().toISOString().slice(0, 7);
        try {
            const res = await mhFetch(`/exports/${kind}.xlsx?month=${month}`);
            if (!res.ok) {
                let m = 'Gagal generate Excel';
                try { m = (await res.json()).error || m; } catch(e){}
                throw new Error(m);
            }
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            const fnMap = {
                'jamaah-monthly': `closingan-jamaah-${month}.xlsx`,
                'packages-monthly': `paket-${month}.xlsx`,
                'finance-monthly': `keuangan-${month}.xlsx`,
            };
            a.download = fnMap[kind] || `laporan-${month}.xlsx`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            setTimeout(() => URL.revokeObjectURL(url), 5000);
        } catch (err) { alert('Error: ' + err.message); }
    };

    window.mhRejectItem = async function(kind, id) {
        const note = prompt(`Tolak ${kind} #${id}. Alasan penolakan (wajib):`);
        if (!note || !note.trim()) return;
        try {
            const r = await mhReview(kind, id, 'reject', note.trim());
            alert(r.message || 'Berhasil ditolak.');
            window.initMgmtHome();
        } catch (err) { alert('Error: ' + err.message); }
    };

    function ensureMonthPicker() {
        const p = document.getElementById('mh-report-month');
        if (p && !p.value) p.value = new Date().toISOString().slice(0, 7);
    }

    // Phase 11a: Revenue per Skenario BOQ
    async function loadBoqRevenue() {
        const body = document.getElementById('mh-boq-body');
        const totalEl = document.getElementById('mh-boq-total');
        if (!body) return;
        try {
            const res = await mhFetch('/mgmt/revenue-by-boq');
            if (!res.ok) throw new Error('gagal');
            const d = await res.json();
            if (totalEl) totalEl.textContent = mhFmtShort(d.total_revenue || 0);
            if (!d.scenarios || d.scenarios.length === 0) {
                body.innerHTML = `<div class="p-4 text-center text-xs text-gray-400 italic">
                    Belum ada BOQ Approved atau jamaah yg pilih skenario BOQ.
                </div>`;
                return;
            }
            // Render tabel scenario + total
            const rows = d.scenarios.map(s => `
                <tr class="border-b hover:bg-amber-50/40">
                    <td class="px-3 py-2 text-xs">
                        <div class="font-bold text-gray-800">${mhEscape(s.boq_name || '(tanpa nama)')}</div>
                        <div class="text-[10px] text-gray-500">${mhEscape(s.package_name || '-')}</div>
                    </td>
                    <td class="px-3 py-2 text-xs text-right font-bold">${s.jamaah_count}</td>
                    <td class="px-3 py-2 text-xs text-right font-bold text-amber-900">${mhFmtShort(s.total_revenue)}</td>
                    <td class="px-3 py-2 text-xs text-right text-gray-600">${mhFmtShort(s.avg_price)}</td>
                    <td class="px-3 py-2 text-xs w-28">
                        <div class="flex items-center gap-2">
                            <div class="flex-1 h-1.5 bg-gray-200 rounded-full overflow-hidden">
                                <div class="h-1.5 bg-amber-500 rounded-full" style="width:${s.share_pct}%"></div>
                            </div>
                            <span class="text-[10px] font-bold text-gray-700 w-8 text-right">${s.share_pct}%</span>
                        </div>
                    </td>
                </tr>
            `).join('');
            body.innerHTML = `
                <div class="text-[10px] text-gray-500 mb-2">
                    ${d.total_jamaah_with_boq} jamaah pilih skenario BOQ (periode: ${d.period === 'all' ? 'semua waktu' : d.period}).
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-xs">
                        <thead>
                            <tr class="bg-gray-50 border-b text-[10px] uppercase text-gray-500 font-bold">
                                <th class="px-3 py-2 text-left">Skenario BOQ / Paket</th>
                                <th class="px-3 py-2 text-right">Jamaah</th>
                                <th class="px-3 py-2 text-right">Revenue</th>
                                <th class="px-3 py-2 text-right">Rata2</th>
                                <th class="px-3 py-2 text-right">Share</th>
                            </tr>
                        </thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>`;
        } catch (e) {
            body.innerHTML = '<div class="p-4 text-center text-xs text-red-500">Gagal memuat revenue by BOQ.</div>';
        }
    }

    window.initMgmtHome = async function() {
        if (mhLoading) return;
        mhLoading = true;
        ensureMonthPicker();
        try {
            const res = await mhFetch('/mgmt/home');
            if (!res.ok) throw new Error('Gagal memuat Home Management');
            const data = await res.json();
            // Phase 5b: shared welcome hero
            if (typeof umarRenderWelcomeHero === 'function') {
                umarRenderWelcomeHero('mh-hero-mount', (typeof currentUser !== 'undefined' && currentUser?.name) || 'Manajemen');
            }
            renderKPI(data.kpi || {});
            renderAttention(data.attention || []);
            renderSalesPerf(data.sales_performance || []);
            renderTopAgents(data.top_agents || []);
            renderPackages(data.upcoming_packages || []);
            renderApprovals(data.approvals || []);
            loadBoqRevenue();  // Phase 11a: revenue per skenario BOQ
            if (typeof lucide !== 'undefined') lucide.createIcons();
        } catch (err) {
            console.error('[mgmt-home]', err);
            const inner = document.getElementById('mh-attention-list');
            if (inner) inner.innerHTML = `<span class="text-xs text-red-600">Gagal memuat data: ${mhEscape(err.message)}</span>`;
            document.getElementById('mh-attention')?.classList.remove('hidden');
        } finally {
            mhLoading = false;
        }
    };
})();
