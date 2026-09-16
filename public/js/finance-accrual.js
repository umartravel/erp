// Sprint AK-UI: Page Laporan Keuangan (Neraca + LR Multi-Step) + modal Kas & Prive.
// Mengonsumsi endpoint AK-4/AK-5/AK-6. Semua fetch pakai authFetch bila ada.
(function(){
    const fetch2 = (u, o) => (window.authFetch || fetch)(u, o);
    const escape = s => String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const fmt = n => 'Rp ' + Math.round(n || 0).toLocaleString('id-ID');
    const fmtNeg = n => {
        n = Math.round(n || 0);
        if (n < 0) return '(Rp ' + Math.abs(n).toLocaleString('id-ID') + ')';
        return 'Rp ' + n.toLocaleString('id-ID');
    };
    const todayISO = () => new Date().toISOString().slice(0, 10);

    async function loadBalanceSheet() {
        const date = document.getElementById('lk-bs-date').value || todayISO();
        const target = document.getElementById('lk-bs-body');
        target.innerHTML = '<div class="p-8 text-center text-xs text-gray-400">Memuat Neraca...</div>';
        try {
            const r = await fetch2('/api/finance/reports/balance-sheet?date=' + encodeURIComponent(date));
            if (!r.ok) throw new Error('HTTP ' + r.status);
            renderBalanceSheet(await r.json());
        } catch (e) {
            target.innerHTML = '<div class="p-6 text-center text-xs text-red-600">Gagal: ' + escape(e.message) + '</div>';
        }
    }

    function renderBalanceSheet(bs) {
        const target = document.getElementById('lk-bs-body');
        const rowsFor = items => items.map(i => `
            <tr class="border-b border-gray-100">
                <td class="py-1.5 px-3 text-xs text-gray-500 font-mono">${escape(i.account_code)}</td>
                <td class="py-1.5 px-3 text-xs text-gray-800">${escape(i.account_name)}</td>
                <td class="py-1.5 px-3 text-xs text-right font-semibold text-gray-900">${fmtNeg(i.balance)}</td>
            </tr>
        `).join('');
        target.innerHTML = `
            <div class="p-4 text-center border-b border-indigo-200 bg-indigo-50/40">
                <div class="text-xs uppercase tracking-wider text-indigo-600">Neraca (Balance Sheet)</div>
                <div class="text-lg font-bold text-indigo-900">per ${escape(bs.as_of)}</div>
                <div class="mt-1 text-[11px] ${bs.balanced ? 'text-emerald-700' : 'text-red-700'} font-semibold">
                    ${bs.balanced ? 'Aset = Liabilitas + Ekuitas (balanced)' : 'Off Rp ' + Math.abs(bs.delta).toLocaleString('id-ID')}
                </div>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4 p-4">
                <div class="border border-gray-200 rounded-lg overflow-hidden">
                    <div class="bg-gray-50 px-3 py-2 text-xs font-bold text-gray-700 border-b">ASET</div>
                    <table class="w-full"><tbody>
                        <tr class="bg-white"><td colspan="3" class="px-3 py-1 text-[10px] uppercase tracking-wider text-gray-500 font-semibold">Aset Lancar</td></tr>
                        ${rowsFor(bs.assets.current)}
                        <tr class="bg-gray-50 font-semibold"><td colspan="2" class="px-3 py-1.5 text-xs">Sub-total Aset Lancar</td><td class="px-3 py-1.5 text-xs text-right">${fmtNeg(bs.assets.total_current)}</td></tr>
                        <tr class="bg-white"><td colspan="3" class="px-3 py-1 text-[10px] uppercase tracking-wider text-gray-500 font-semibold">Aset Tetap</td></tr>
                        ${rowsFor(bs.assets.fixed)}
                        <tr class="bg-gray-50 font-semibold"><td colspan="2" class="px-3 py-1.5 text-xs">Sub-total Aset Tetap</td><td class="px-3 py-1.5 text-xs text-right">${fmtNeg(bs.assets.total_fixed)}</td></tr>
                        <tr class="bg-indigo-50 font-bold border-t-2 border-indigo-300"><td colspan="2" class="px-3 py-2 text-sm text-indigo-900">TOTAL ASET</td><td class="px-3 py-2 text-sm text-right text-indigo-900">${fmt(bs.assets.total)}</td></tr>
                    </tbody></table>
                </div>
                <div class="border border-gray-200 rounded-lg overflow-hidden">
                    <div class="bg-gray-50 px-3 py-2 text-xs font-bold text-gray-700 border-b">LIABILITAS + EKUITAS</div>
                    <table class="w-full"><tbody>
                        <tr class="bg-white"><td colspan="3" class="px-3 py-1 text-[10px] uppercase tracking-wider text-gray-500 font-semibold">Liabilitas</td></tr>
                        ${rowsFor(bs.liabilities.items)}
                        <tr class="bg-gray-50 font-semibold"><td colspan="2" class="px-3 py-1.5 text-xs">Sub-total Liabilitas</td><td class="px-3 py-1.5 text-xs text-right">${fmt(bs.liabilities.total)}</td></tr>
                        <tr class="bg-white"><td colspan="3" class="px-3 py-1 text-[10px] uppercase tracking-wider text-gray-500 font-semibold">Ekuitas</td></tr>
                        ${bs.equity.items.map(i => {
                            const contrib = i.normal_balance === 'CREDIT' ? i.balance : -i.balance;
                            return `<tr class="border-b border-gray-100">
                                <td class="py-1.5 px-3 text-xs text-gray-500 font-mono">${escape(i.account_code)}</td>
                                <td class="py-1.5 px-3 text-xs text-gray-800">${escape(i.account_name)}${i.normal_balance === 'DEBIT' ? ' <span class="text-[9px] text-red-600">(contra)</span>' : ''}</td>
                                <td class="py-1.5 px-3 text-xs text-right font-semibold text-gray-900">${fmtNeg(contrib)}</td>
                            </tr>`;
                        }).join('')}
                        <tr class="border-b border-gray-100">
                            <td class="py-1.5 px-3 text-xs text-gray-500 font-mono">RE</td>
                            <td class="py-1.5 px-3 text-xs text-gray-800">Laba Ditahan (Rev-Exp sd tanggal)</td>
                            <td class="py-1.5 px-3 text-xs text-right font-semibold text-gray-900">${fmtNeg(bs.equity.retained_earnings)}</td>
                        </tr>
                        <tr class="bg-gray-50 font-semibold"><td colspan="2" class="px-3 py-1.5 text-xs">Sub-total Ekuitas</td><td class="px-3 py-1.5 text-xs text-right">${fmtNeg(bs.equity.total)}</td></tr>
                        <tr class="bg-indigo-50 font-bold border-t-2 border-indigo-300"><td colspan="2" class="px-3 py-2 text-sm text-indigo-900">TOTAL LIABILITAS + EKUITAS</td><td class="px-3 py-2 text-sm text-right text-indigo-900">${fmt(bs.total_liab_equity)}</td></tr>
                    </tbody></table>
                </div>
            </div>
        `;
    }

    async function loadIncomeStatement() {
        const y = parseInt(document.getElementById('lk-is-year').value);
        const m = parseInt(document.getElementById('lk-is-month').value);
        const target = document.getElementById('lk-is-body');
        target.innerHTML = '<div class="p-8 text-center text-xs text-gray-400">Memuat Laba Rugi...</div>';
        try {
            const r = await fetch2('/api/finance/reports/income-statement?year=' + y + '&month=' + m);
            if (!r.ok) throw new Error('HTTP ' + r.status);
            renderIncomeStatement(await r.json());
        } catch (e) {
            target.innerHTML = '<div class="p-6 text-center text-xs text-red-600">Gagal: ' + escape(e.message) + '</div>';
        }
    }

    function renderIncomeStatement(is) {
        const target = document.getElementById('lk-is-body');
        const rowsFor = items => items.map(i => `
            <tr class="border-b border-gray-100">
                <td class="py-1.5 px-3 text-xs text-gray-500 font-mono">${escape(i.account_code)}</td>
                <td class="py-1.5 px-3 text-xs text-gray-800">${escape(i.account_name)}</td>
                <td class="py-1.5 px-3 text-xs text-right font-semibold text-gray-900">${fmt(i.amount)}</td>
            </tr>
        `).join('') || `<tr><td colspan="3" class="px-3 py-3 text-center text-[11px] text-gray-400 italic">-- kosong --</td></tr>`;
        target.innerHTML = `
            <div class="p-4 text-center border-b border-emerald-200 bg-emerald-50/40">
                <div class="text-xs uppercase tracking-wider text-emerald-600">Laba Rugi Multi-Step</div>
                <div class="text-lg font-bold text-emerald-900">Periode ${escape(is.period)}</div>
                <div class="text-[10px] text-gray-500 mt-0.5">${escape(is.period_start)} — ${escape(is.period_end)}</div>
            </div>
            <div class="p-4 space-y-4">
                <div class="border border-gray-200 rounded-lg overflow-hidden">
                    <div class="bg-emerald-50 px-3 py-1.5 text-xs font-bold text-emerald-800 border-b">PENDAPATAN</div>
                    <table class="w-full"><tbody>${rowsFor(is.revenue.items)}</tbody></table>
                    <div class="bg-emerald-50 px-3 py-1.5 flex justify-between text-xs font-bold text-emerald-800 border-t">
                        <span>Total Pendapatan</span><span>${fmt(is.revenue.total)}</span>
                    </div>
                </div>
                <div class="border border-gray-200 rounded-lg overflow-hidden">
                    <div class="bg-red-50 px-3 py-1.5 text-xs font-bold text-red-800 border-b">HARGA POKOK (COGS)</div>
                    <table class="w-full"><tbody>${rowsFor(is.cogs.items)}</tbody></table>
                    <div class="bg-red-50 px-3 py-1.5 flex justify-between text-xs font-bold text-red-800 border-t">
                        <span>Total COGS</span><span>(${fmt(is.cogs.total)})</span>
                    </div>
                </div>
                <div class="bg-indigo-50 border border-indigo-300 rounded-lg p-3 flex justify-between text-sm font-bold text-indigo-900">
                    <span>= LABA KOTOR (Gross Profit)</span><span>${fmtNeg(is.gross_profit)}</span>
                </div>
                <div class="border border-gray-200 rounded-lg overflow-hidden">
                    <div class="bg-amber-50 px-3 py-1.5 text-xs font-bold text-amber-800 border-b">BEBAN OPERASI (OPEX)</div>
                    <table class="w-full"><tbody>${rowsFor(is.opex.items)}</tbody></table>
                    <div class="bg-amber-50 px-3 py-1.5 flex justify-between text-xs font-bold text-amber-800 border-t">
                        <span>Total OPEX</span><span>(${fmt(is.opex.total)})</span>
                    </div>
                </div>
                <div class="bg-indigo-50 border border-indigo-300 rounded-lg p-3 flex justify-between text-sm font-bold text-indigo-900">
                    <span>= LABA OPERASIONAL</span><span>${fmtNeg(is.operating_profit)}</span>
                </div>
                <div class="border border-gray-200 rounded-lg overflow-hidden">
                    <div class="bg-gray-100 px-3 py-1.5 text-xs font-bold text-gray-700 border-b">POS LAIN-LAIN (6201-6202)</div>
                    <table class="w-full"><tbody>${rowsFor(is.other.items)}</tbody></table>
                    <div class="bg-gray-100 px-3 py-1.5 flex justify-between text-xs font-bold text-gray-700 border-t">
                        <span>Total Pos Lain</span><span>(${fmt(is.other.total)})</span>
                    </div>
                </div>
                <div class="bg-emerald-100 border-2 border-emerald-500 rounded-lg p-4 flex justify-between text-base font-bold text-emerald-900">
                    <span>= LABA BERSIH SEBELUM PAJAK</span><span>${fmtNeg(is.net_profit_before_tax)}</span>
                </div>
            </div>
        `;
    }

    async function loadRecentLedger() {
        const target = document.getElementById('lk-ledger-body');
        target.innerHTML = '<div class="p-4 text-center text-xs text-gray-400">Memuat...</div>';
        try {
            const r = await fetch2('/api/finance/ledger/recent?limit=20');
            if (!r.ok) throw new Error('HTTP ' + r.status);
            const d = await r.json();
            if (!d.entries.length) {
                target.innerHTML = '<div class="p-6 text-center text-xs text-gray-400">Belum ada entri.</div>';
                return;
            }
            target.innerHTML = d.entries.map(e => {
                const lines = (e.journal_lines || []).map(l => {
                    const side = l.debit > 0 ? 'Dr' : 'Cr';
                    const amt = l.debit || l.credit;
                    return `<div class="flex justify-between text-[11px] font-mono text-gray-600">
                        <span>${side} <span class="text-gray-500">${escape(l.account_code)}</span> ${escape(l.account_name)}</span>
                        <span>Rp ${amt.toLocaleString('id-ID')}</span>
                    </div>`;
                }).join('');
                return `<div class="border-b border-gray-100 px-4 py-2 hover:bg-indigo-50/40">
                    <div class="flex justify-between text-xs">
                        <span class="font-semibold text-gray-800">#${e.id} - ${escape(e.category || e.type)}</span>
                        <span class="text-gray-500">${escape((e.created_at || '').slice(0, 16))}</span>
                    </div>
                    <div class="text-[11px] text-gray-500 truncate mb-1">${escape(e.description || '')}</div>
                    ${lines}
                </div>`;
            }).join('');
        } catch (e) {
            target.innerHTML = '<div class="p-6 text-center text-xs text-red-600">Gagal: ' + escape(e.message) + '</div>';
        }
    }

    window.initLaporanKeuangan = function() {
        const now = new Date();
        document.getElementById('lk-bs-date').value = todayISO();
        const yearSel = document.getElementById('lk-is-year');
        if (yearSel && !yearSel.dataset.filled) {
            let opts = '';
            for (let y = now.getFullYear() + 1; y >= 2024; y--) opts += `<option value="${y}"${y === now.getFullYear() ? ' selected' : ''}>${y}</option>`;
            yearSel.innerHTML = opts;
            yearSel.dataset.filled = '1';
        }
        const monthSel = document.getElementById('lk-is-month');
        if (monthSel && !monthSel.dataset.filled) {
            const names = ['Januari','Februari','Maret','April','Mei','Juni','Juli','Agustus','September','Oktober','November','Desember'];
            let opts = '';
            for (let m = 1; m <= 12; m++) opts += `<option value="${m}"${m === now.getMonth() + 1 ? ' selected' : ''}>${names[m-1]}</option>`;
            monthSel.innerHTML = opts;
            monthSel.dataset.filled = '1';
        }
        loadBalanceSheet();
        loadIncomeStatement();
        loadRecentLedger();
        if (typeof lucide !== 'undefined') lucide.createIcons();
    };
    window.lkReloadBalanceSheet = loadBalanceSheet;
    window.lkReloadIncomeStatement = loadIncomeStatement;
    window.lkReloadLedger = loadRecentLedger;

    window.fhOpenCashOps = function() {
        const modal = document.getElementById('modal-cash-ops');
        if (!modal) return;
        modal.classList.remove('hidden');
        document.body.classList.add('overflow-hidden');
        loadSetorPending();
        loadPriveHistory();
        const priveForm = document.getElementById('form-prive');
        if (priveForm) {
            const role = window.currentUser?.role;
            if (role === 'admin' || role === 'management') priveForm.classList.remove('hidden');
            else priveForm.classList.add('hidden');
        }
        if (typeof lucide !== 'undefined') lucide.createIcons();
    };
    window.fhCloseCashOps = function() {
        const modal = document.getElementById('modal-cash-ops');
        if (!modal) return;
        modal.classList.add('hidden');
        document.body.classList.remove('overflow-hidden');
    };

    async function loadSetorPending() {
        const target = document.getElementById('setor-pending-body');
        target.innerHTML = '<div class="text-xs text-gray-400 italic">Memuat...</div>';
        try {
            const r = await fetch2('/api/finance/setor-tunai/pending');
            const d = await r.json();
            if (!d.pending?.length) {
                target.innerHTML = '<div class="text-xs text-gray-400 italic">Tidak ada setor menunggu konfirmasi.</div>';
                return;
            }
            target.innerHTML = d.pending.map(p => `
                <div class="flex justify-between items-center border border-amber-200 rounded p-2 mb-2 bg-amber-50">
                    <div class="flex-1 min-w-0">
                        <div class="text-xs font-semibold text-amber-900">Rp ${p.amount.toLocaleString('id-ID')}</div>
                        <div class="text-[10px] text-amber-700 truncate">${escape(p.description)}</div>
                        <div class="text-[9px] text-gray-500">${escape((p.created_at || '').slice(0, 16))}</div>
                    </div>
                    <button onclick="fhConfirmSetor(${p.id})" class="text-[10px] font-medium bg-emerald-600 hover:bg-emerald-700 text-white px-2 py-1 rounded ml-2 shrink-0">
                        Konfirmasi
                    </button>
                </div>
            `).join('');
        } catch (e) {
            target.innerHTML = '<div class="text-xs text-red-600">Gagal muat: ' + escape(e.message) + '</div>';
        }
    }

    window.fhSubmitSetor = async function(ev) {
        ev.preventDefault();
        const form = ev.target;
        const amount = parseInt(form.amount.value || 0);
        const to_bank = form.to_bank.value;
        const note = (form.note.value || '').trim();
        if (amount <= 0) { alert('Nominal harus > 0'); return; }
        try {
            const r = await fetch2('/api/finance/setor-tunai', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({amount, to_bank, note})
            });
            const d = await r.json();
            if (!r.ok) throw new Error(d.error || 'Gagal');
            alert('Setor tunai leg 1 tercatat. Tx #' + d.tx_id);
            form.reset();
            loadSetorPending();
        } catch (e) { alert('Gagal: ' + e.message); }
    };

    window.fhConfirmSetor = async function(leg1_id) {
        if (!confirm('Konfirmasi bank sudah terima setor #' + leg1_id + '?')) return;
        try {
            const r = await fetch2('/api/finance/setor-tunai/' + leg1_id + '/confirm', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({to_bank: '1102'})
            });
            const d = await r.json();
            if (!r.ok) throw new Error(d.error || 'Gagal');
            alert('Setor #' + leg1_id + ' berhasil dikonfirmasi. Leg 2 tx #' + d.leg2_tx_id);
            loadSetorPending();
        } catch (e) { alert('Gagal: ' + e.message); }
    };

    async function loadPriveHistory() {
        const target = document.getElementById('prive-history-body');
        if (!target) return;
        target.innerHTML = '<div class="text-xs text-gray-400 italic">Memuat...</div>';
        try {
            const r = await fetch2('/api/finance/prive/history?limit=10');
            const d = await r.json();
            document.getElementById('prive-total').textContent = 'Rp ' + (d.total || 0).toLocaleString('id-ID');
            if (!d.prive?.length) {
                target.innerHTML = '<div class="text-xs text-gray-400 italic">Belum ada prive.</div>';
                return;
            }
            target.innerHTML = d.prive.map(p => `
                <div class="border border-purple-100 rounded p-2 mb-2 bg-purple-50">
                    <div class="flex justify-between text-xs">
                        <span class="font-semibold text-purple-900">Rp ${p.amount.toLocaleString('id-ID')}</span>
                        <span class="text-[10px] text-gray-500">${escape((p.created_at || '').slice(0, 16))}</span>
                    </div>
                    <div class="text-[10px] text-purple-700 truncate">${escape(p.description)}</div>
                </div>
            `).join('');
        } catch (e) {
            target.innerHTML = '<div class="text-xs text-red-600">Gagal: ' + escape(e.message) + '</div>';
        }
    }

    window.fhSubmitPrive = async function(ev) {
        ev.preventDefault();
        const form = ev.target;
        const amount = parseInt(form.amount.value || 0);
        const from_bank = form.from_bank.value;
        const note = (form.note.value || '').trim();
        if (amount <= 0) { alert('Nominal harus > 0'); return; }
        if (!note) { alert('Catatan wajib diisi'); return; }
        if (!confirm(`Prive Rp ${amount.toLocaleString('id-ID')} dari ${from_bank}?\n"${note}"`)) return;
        try {
            const r = await fetch2('/api/finance/prive', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({amount, from_bank, note})
            });
            const d = await r.json();
            if (!r.ok) throw new Error(d.error || 'Gagal');
            alert('Prive tercatat. Tx #' + d.tx_id);
            form.reset();
            loadPriveHistory();
        } catch (e) { alert('Gagal: ' + e.message); }
    };
})();
