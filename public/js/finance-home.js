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
            const payload = JSON.stringify({id: v.id, vendor: v.vendor_name || v.vendor_type, pkg: v.package_name || '', sisa: v.sisa, currentDeposit: v.deposit_amount || 0, total: v.total_amount || 0}).replace(/"/g,'&quot;');
            return `<div class="px-4 py-3 hover:bg-indigo-50 flex items-center justify-between gap-3">
                <div class="min-w-0 flex-1">
                    <div class="text-sm font-medium text-gray-800 truncate">${fhEscape(v.vendor_name || v.vendor_type)}</div>
                    <div class="text-[11px] text-gray-500 truncate">${fhEscape(v.package_name || '')} - ${fhEscape(v.vendor_type)} - ${fhEscape(v.status)}</div>
                </div>
                <div class="text-right shrink-0">
                    <div class="text-sm font-bold text-indigo-700">${fhFmtShort(v.sisa)}</div>
                    <div class="text-[10px] font-medium px-1.5 py-0.5 rounded ${cls} inline-block mt-0.5">${label}</div>
                    <button class="mt-1 block ml-auto text-[10px] font-medium bg-indigo-600 hover:bg-indigo-700 text-white px-2 py-1 rounded" onclick='fhQuickPayVendor(${payload})'>Catat Bayar</button>
                </div>
            </div>`;
        }).join('');
    }

    window.fhQuickPayVendor = async function(ctx) {
        const suggested = ctx.sisa;
        const raw = prompt(`Catat pembayaran vendor "${ctx.vendor}" (${ctx.pkg}).\nSisa: Rp ${suggested.toLocaleString('id-ID')}\n\nMasukkan jumlah pembayaran:`, String(suggested));
        if (!raw) return;
        const amount = parseInt(String(raw).replace(/[^0-9]/g, ''), 10);
        if (!amount || amount <= 0) { alert('Jumlah tidak valid.'); return; }
        if (amount > ctx.sisa) {
            if (!confirm(`Jumlah Rp ${amount.toLocaleString('id-ID')} lebih besar dari sisa Rp ${ctx.sisa.toLocaleString('id-ID')}. Tetap lanjut?`)) return;
        }
        const newDeposit = (ctx.currentDeposit || 0) + amount;
        const isFullyPaid = newDeposit >= (ctx.total || 0);
        try {
            const exp = await fhFetch('/transactions/expense', {
                method: 'POST',
                body: JSON.stringify({
                    category: 'vendor',
                    amount,
                    description: `Bayar vendor: ${ctx.vendor} (${ctx.pkg})`,
                    package_name: ctx.pkg || null,
                }),
            });
            if (!exp.ok) throw new Error('Gagal catat expense');
            const vend = await fhFetch(`/vendors/${ctx.id}`, {
                method: 'PATCH',
                body: JSON.stringify({
                    deposit_amount: newDeposit,
                    status: isFullyPaid ? 'Paid' : 'Deposit',
                }),
            });
            if (!vend.ok) throw new Error('Expense tercatat tapi update vendor gagal');
            alert(`Pembayaran Rp ${amount.toLocaleString('id-ID')} tercatat. Status vendor: ${isFullyPaid ? 'Paid (Lunas)' : 'Deposit'}.`);
            window.initFinanceHome();
        } catch (err) {
            alert('Error: ' + err.message);
        }
    };

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

    let forecastChart = null;
    const CAT_ICON = { vendor: 'V', refund: 'R', komisi: 'K', payroll: 'P', piutang: 'J' };

    function renderForecastSummary(summary) {
        const el = document.getElementById('fh-forecast-summary');
        if (!el) return;
        const net = summary.net_30d || 0;
        const netCls = net >= 0 ? 'text-emerald-700' : 'text-red-700';
        const netSign = net >= 0 ? '+' : '';
        el.innerHTML = `
            <span>Masuk 30h: <b class="text-emerald-700">${fhFmtShort(summary.total_in_30d)}</b></span>
            <span>Keluar 30h: <b class="text-red-700">${fhFmtShort(summary.total_out_30d)}</b></span>
            <span>Net 30h: <b class="${netCls}">${netSign}${fhFmtShort(net)}</b></span>
            <span>Saldo terendah 60h: <b>${fhFmtShort(summary.min_balance)}</b> (${summary.min_balance_date || '-'})</span>
        `;
    }

    function renderForecastChart(cashCurrent, events) {
        const canvas = document.getElementById('fh-forecast-chart');
        if (!canvas || typeof Chart === 'undefined') return;
        const byDate = new Map();
        events.forEach(e => {
            const d = e.date;
            const bucket = byDate.get(d) || {in: 0, out: 0};
            if (e.kind === 'cash_in') bucket.in += e.amount || 0;
            else bucket.out += e.amount || 0;
            byDate.set(d, bucket);
        });
        const today = new Date().toISOString().slice(0, 10);
        const dates = Array.from(new Set([today, ...byDate.keys()])).sort();
        let running = cashCurrent;
        const balancePoints = [];
        const inBars = [];
        const outBars = [];
        dates.forEach(d => {
            const b = byDate.get(d) || {in: 0, out: 0};
            running += b.in - b.out;
            balancePoints.push(running);
            inBars.push(b.in);
            outBars.push(-b.out);
        });
        if (forecastChart) forecastChart.destroy();
        forecastChart = new Chart(canvas, {
            data: {
                labels: dates.map(d => d.slice(5)),
                datasets: [
                    {type: 'line', label: 'Saldo', data: balancePoints, borderColor: '#10b981', backgroundColor: 'rgba(16,185,129,0.1)', tension: 0.3, fill: true, yAxisID: 'y', order: 0, pointRadius: 3},
                    {type: 'bar', label: 'Masuk', data: inBars, backgroundColor: '#34d399', yAxisID: 'y1', order: 1},
                    {type: 'bar', label: 'Keluar', data: outBars, backgroundColor: '#f87171', yAxisID: 'y1', order: 1},
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {mode: 'index', intersect: false},
                plugins: {
                    legend: {position: 'bottom', labels: {font: {size: 10}, boxWidth: 10}},
                    tooltip: {callbacks: {label: (ctx) => `${ctx.dataset.label}: Rp ${Math.abs(ctx.parsed.y).toLocaleString('id-ID')}`}},
                },
                scales: {
                    x: {ticks: {font: {size: 9}, maxRotation: 45}},
                    y: {position: 'left', ticks: {font: {size: 9}, callback: v => fhFmtShort(v)}},
                    y1: {position: 'right', grid: {drawOnChartArea: false}, ticks: {font: {size: 9}, callback: v => fhFmtShort(Math.abs(v))}},
                },
            },
        });
    }

    function renderForecastEvents(events) {
        const el = document.getElementById('fh-forecast-events');
        if (!el) return;
        if (!events || !events.length) {
            el.innerHTML = `<div class="p-4 text-center text-xs text-gray-400">Tidak ada event proyeksi 60 hari ke depan.</div>`;
            return;
        }
        el.innerHTML = events.map(e => {
            const inCls = e.kind === 'cash_in' ? 'text-emerald-700' : 'text-red-700';
            const sign = e.kind === 'cash_in' ? '+' : '-';
            const badge = CAT_ICON[e.category] || 'X';
            return `<div class="px-3 py-2 flex items-center justify-between gap-3">
                <div class="min-w-0 flex-1">
                    <div class="text-[11px] text-gray-500">${e.date}</div>
                    <div class="text-xs text-gray-800 truncate"><span class="inline-block w-4 h-4 rounded bg-gray-100 text-center text-[9px] font-bold text-gray-600 mr-1">${badge}</span>${fhEscape(e.label)}</div>
                </div>
                <div class="text-right shrink-0">
                    <div class="text-xs font-bold ${inCls}">${sign}${fhFmtShort(e.amount)}</div>
                    <div class="text-[10px] text-gray-400">saldo ${fhFmtShort(e.running_balance)}</div>
                </div>
            </div>`;
        }).join('');
    }

    async function loadForecast() {
        try {
            const res = await fhFetch('/finance/forecast');
            if (!res.ok) return;
            const data = await res.json();
            renderForecastSummary(data.summary || {});
            renderForecastChart(data.cash_current || 0, data.events || []);
            renderForecastEvents(data.events || []);
        } catch (err) {
            console.error('[finance-home] forecast', err);
        }
    }

    let agedChart = null;
    let agedAllRows = [];

    function agedRender(rows) {
        const tbody = document.getElementById('ar-tbody');
        if (!tbody) return;
        if (!rows.length) {
            tbody.innerHTML = `<tr><td colspan="9" class="text-center py-8 text-gray-400 text-xs">Tidak ada piutang cocok filter.</td></tr>`;
            return;
        }
        tbody.innerHTML = rows.map(j => {
            const depCls = j.days_to_depart !== null && j.days_to_depart <= 30 ? 'text-red-700 font-bold' : 'text-gray-600';
            const ageCls = j.age_days > 60 ? 'text-red-700' : j.age_days > 30 ? 'text-orange-600' : j.age_days > 7 ? 'text-amber-600' : 'text-emerald-700';
            return `<tr class="hover:bg-amber-50">
                <td class="px-3 py-2">
                    <div class="font-medium text-gray-800">${fhEscape(j.name)}</div>
                    <div class="text-[10px] text-gray-400">${fhEscape(j.phone || '')}</div>
                </td>
                <td class="px-3 py-2 text-gray-600 truncate max-w-[180px]">${fhEscape(j.package_type)}</td>
                <td class="px-3 py-2 text-gray-600">${fhEscape(j.sales_name)}</td>
                <td class="px-3 py-2 text-right">${fhFmtShort(j.total_price)}</td>
                <td class="px-3 py-2 text-right text-emerald-700">${fhFmtShort(j.paid_amount)}</td>
                <td class="px-3 py-2 text-right font-bold text-amber-700">${fhFmtShort(j.sisa)}</td>
                <td class="px-3 py-2 text-center ${ageCls} font-semibold">${j.age_days}h</td>
                <td class="px-3 py-2 text-center ${depCls}">${j.days_to_depart !== null ? 'H-' + j.days_to_depart : '-'}</td>
                <td class="px-3 py-2 text-center"><span class="text-[10px] px-1.5 py-0.5 rounded ${j.payment_status === 'DP' ? 'bg-amber-100 text-amber-800' : 'bg-red-100 text-red-800'}">${j.payment_status}</span></td>
            </tr>`;
        }).join('');
    }

    function agedFilterAndRender() {
        const q = (document.getElementById('ar-filter')?.value || '').toLowerCase();
        const bucket = document.getElementById('ar-bucket-filter')?.value || '';
        const filtered = agedAllRows.filter(j => {
            if (bucket) {
                const age = j.age_days;
                const jBucket = age <= 7 ? '0-7 hari' : age <= 30 ? '8-30 hari' : age <= 60 ? '31-60 hari' : '60+ hari';
                if (jBucket !== bucket) return false;
            }
            if (q) {
                const hay = `${j.name} ${j.package_type} ${j.sales_name}`.toLowerCase();
                if (!hay.includes(q)) return false;
            }
            return true;
        });
        agedRender(filtered);
    }

    function agedChartRender(buckets) {
        const canvas = document.getElementById('ar-chart');
        if (!canvas || typeof Chart === 'undefined') return;
        if (agedChart) agedChart.destroy();
        const colorMap = {'0-7 hari': '#10b981', '8-30 hari': '#f59e0b', '31-60 hari': '#f97316', '60+ hari': '#ef4444'};
        agedChart = new Chart(canvas, {
            type: 'bar',
            data: {
                labels: buckets.map(b => b.bucket),
                datasets: [{
                    label: 'Piutang',
                    data: buckets.map(b => b.sisa),
                    backgroundColor: buckets.map(b => colorMap[b.bucket] || '#6b7280'),
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: 'y',
                plugins: {
                    legend: {display: false},
                    tooltip: {callbacks: {
                        label: (ctx) => {
                            const b = buckets[ctx.dataIndex];
                            return `${b.n} jamaah · ${fhFmtShort(b.sisa)}`;
                        },
                    }},
                },
                scales: {
                    x: {ticks: {font: {size: 9}, callback: v => fhFmtShort(v)}},
                    y: {ticks: {font: {size: 10}}},
                },
            },
        });
    }

    window.initAgedReceivable = async function() {
        try {
            const res = await fhFetch('/finance/aged-receivable');
            if (!res.ok) throw new Error('Gagal muat data');
            const d = await res.json();
            document.getElementById('ar-total').textContent = fhFmtShort(d.total);
            document.getElementById('ar-count').textContent = d.count;
            agedChartRender(d.buckets || []);
            agedAllRows = d.rows || [];
            agedRender(agedAllRows);
            document.getElementById('ar-filter').oninput = agedFilterAndRender;
            document.getElementById('ar-bucket-filter').onchange = agedFilterAndRender;
            if (typeof lucide !== 'undefined') lucide.createIcons();
        } catch (err) {
            document.getElementById('ar-tbody').innerHTML = `<tr><td colspan="9" class="text-center py-8 text-red-600 text-xs">Error: ${fhEscape(err.message)}</td></tr>`;
        }
    };

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
            loadForecast();
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
