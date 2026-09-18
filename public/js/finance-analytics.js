/* Phase F3: Analisis Keuangan -- dashboard kategori + tren + drill-down.
   Endpoint: /api/finance/categories, /summary/year, /summary/month */
(function() {
  const FA_COLORS = {
    gold: '#B8860B', red: '#B91C1C', green: '#166534',
    charcoal: '#1D1D1B', gray: '#6B7280', cream: '#FFFDF7',
    palette: ['#B8860B', '#F8BE20', '#B45309', '#166534', '#0369A1', '#7C3AED',
              '#DB2777', '#DC2626', '#059669'],
  };
  const MONTH_LONG = ['Januari','Februari','Maret','April','Mei','Juni',
                       'Juli','Agustus','September','Oktober','November','Desember'];
  const MONTH_SHORT = ['Jan','Feb','Mar','Apr','Mei','Jun','Jul','Ags','Sep','Okt','Nov','Des'];

  let faYear = null;
  let faMonth = null;
  let faTree = null;
  let faTrendChart = null;
  let faCatChart = null;
  let faProjectChart = null;
  let faModalType = 'expense';
  // Phase EX-6: state drill-down. Ketika user klik bar project, donut Kategori
  // di-refilter ke category_breakdown project itu.
  let faSelectedProjectId = null;
  let faLastMonthData = null;

  function faFetch(url, opts) { return (window.authFetch || fetch)(url, opts); }

  function faFmtRp(n) { return 'Rp ' + Number(n || 0).toLocaleString('id-ID'); }
  function faFmtRpShort(n) {
    n = Number(n || 0);
    if (n >= 1e9) return 'Rp ' + (n / 1e9).toFixed(1).replace('.0', '') + ' mlr';
    if (n >= 1e6) return 'Rp ' + (n / 1e6).toFixed(1).replace('.0', '') + 'jt';
    if (n >= 1e3) return 'Rp ' + Math.round(n / 1e3) + 'rb';
    return 'Rp ' + n;
  }
  function faEsc(s) {
    if (s == null) return '';
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  }

  async function faLoadCategories() {
    try {
      const r = await faFetch('/api/finance/categories');
      if (!r.ok) return;
      faTree = await r.json();
    } catch (e) { console.error('[fa] load categories', e); }
  }

  function faPopulatePickers() {
    const now = new Date();
    faYear = faYear || now.getFullYear();
    faMonth = faMonth || (now.getMonth() + 1);
    const yp = document.getElementById('fa-year-picker');
    const mp = document.getElementById('fa-month-picker');
    if (yp && !yp.dataset.filled) {
      const years = [];
      for (let y = now.getFullYear(); y >= now.getFullYear() - 5; y--) years.push(y);
      yp.innerHTML = years.map(y => `<option value="${y}"${y === faYear ? ' selected' : ''}>${y}${y === now.getFullYear() ? ' (berjalan)' : ''}</option>`).join('');
      yp.onchange = () => { faYear = parseInt(yp.value, 10); faReload(); };
      yp.dataset.filled = '1';
    }
    if (mp && !mp.dataset.filled) {
      mp.innerHTML = MONTH_LONG.map((n, i) => `<option value="${i+1}"${(i+1) === faMonth ? ' selected' : ''}>${n}${(i+1) === now.getMonth() + 1 ? ' (berjalan)' : ''}</option>`).join('');
      mp.onchange = () => { faMonth = parseInt(mp.value, 10); faReload(); };
      mp.dataset.filled = '1';
    }
  }

  async function faLoadSummaryYear() {
    const r = await faFetch(`/api/finance/summary/year?year=${faYear}`);
    if (!r.ok) return null;
    return await r.json();
  }
  async function faLoadSummaryMonth() {
    const r = await faFetch(`/api/finance/summary/month?year=${faYear}&month=${faMonth}`);
    if (!r.ok) return null;
    return await r.json();
  }

  function faRenderKPI(yearData, monthData) {
    const set = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = v; };
    set('fa-kpi-income-year', faFmtRpShort(yearData.total_income));
    set('fa-kpi-expense-year', faFmtRpShort(yearData.total_expense));
    set('fa-kpi-net-year', faFmtRpShort(yearData.net_saldo));
    set('fa-kpi-income-month', 'Bulan ini: ' + faFmtRpShort(monthData.total_income));
    set('fa-kpi-expense-month', 'Bulan ini: ' + faFmtRpShort(monthData.total_expense));
    set('fa-kpi-net-month', 'Bulan ini: ' + faFmtRpShort(monthData.net));
    set('fa-kpi-tx-count', (monthData.transactions || []).length.toLocaleString('id-ID'));
  }

  function faRenderTrend(yearData) {
    const el = document.getElementById('fa-trend-chart');
    if (!el || typeof Chart === 'undefined') return;
    const income = yearData.monthly.map(m => m.income);
    const expense = yearData.monthly.map(m => m.expense);
    if (faTrendChart) faTrendChart.destroy();
    faTrendChart = new Chart(el.getContext('2d'), {
      type: 'line',
      data: {
        labels: MONTH_SHORT,
        datasets: [
          { label: 'Pemasukan', data: income, borderColor: FA_COLORS.green, backgroundColor: FA_COLORS.green + '20', tension: 0.35, fill: true },
          { label: 'Pengeluaran', data: expense, borderColor: FA_COLORS.red, backgroundColor: FA_COLORS.red + '20', tension: 0.35, fill: true },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom', labels: { font: { size: 10 } } } },
        scales: {
          y: { ticks: { callback: v => faFmtRpShort(v), font: { size: 9 } } },
          x: { ticks: { font: { size: 9 } } },
        },
      },
    });
  }

  function faRenderCategoryDonut(monthData) {
    const el = document.getElementById('fa-cat-chart');
    if (!el || typeof Chart === 'undefined') return;
    // Phase EX-6: kalau ada project selected, filter donut ke category_breakdown
    // project itu; else tampilkan semua kategori bulan.
    let expenseCats;
    const scopeEl = document.getElementById('fa-cat-scope');
    const resetBtn = document.getElementById('fa-cat-reset');
    if (faSelectedProjectId) {
      const proj = (monthData.by_project || []).find(p => p.project_id === faSelectedProjectId);
      if (proj) {
        expenseCats = (proj.category_breakdown || []).map(c => ({
          name: c.cat_name, total: c.total, group: 'expense',
        }));
        if (scopeEl) scopeEl.textContent = `(project: ${proj.project_name})`;
        if (resetBtn) resetBtn.classList.remove('hidden');
      } else {
        expenseCats = [];
      }
    } else {
      expenseCats = (monthData.by_category || []).filter(c => c.group === 'expense' && c.total > 0);
      if (scopeEl) scopeEl.textContent = '(bulan terpilih)';
      if (resetBtn) resetBtn.classList.add('hidden');
    }
    if (faCatChart) faCatChart.destroy();
    if (!expenseCats.length) {
      const ctx = el.getContext('2d');
      ctx.clearRect(0, 0, el.width, el.height);
      ctx.fillStyle = FA_COLORS.gray;
      ctx.font = '12px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('Belum ada pengeluaran bulan ini.', el.width / 2, el.height / 2);
      return;
    }
    faCatChart = new Chart(el.getContext('2d'), {
      type: 'doughnut',
      data: {
        labels: expenseCats.map(c => c.name),
        datasets: [{ data: expenseCats.map(c => c.total), backgroundColor: FA_COLORS.palette }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { position: 'bottom', labels: { font: { size: 10 }, boxWidth: 10 } },
          tooltip: { callbacks: { label: ctx => ctx.label + ': ' + faFmtRp(ctx.parsed) } },
        },
      },
    });
  }

  function faRenderProjectBar(monthData) {
    const el = document.getElementById('fa-project-chart');
    const empty = document.getElementById('fa-project-empty');
    if (!el || typeof Chart === 'undefined') return;
    const projects = (monthData.by_project || []).filter(p => p.total > 0);
    if (faProjectChart) faProjectChart.destroy();
    if (!projects.length) {
      el.style.display = 'none';
      if (empty) empty.classList.remove('hidden');
      return;
    }
    el.style.display = '';
    if (empty) empty.classList.add('hidden');
    faProjectChart = new Chart(el.getContext('2d'), {
      type: 'bar',
      data: {
        labels: projects.map(p => p.project_name),
        datasets: [{
          data: projects.map(p => p.total),
          backgroundColor: projects.map((p, i) =>
            p.project_id === faSelectedProjectId ? FA_COLORS.gold : FA_COLORS.palette[i % FA_COLORS.palette.length]),
          borderRadius: 4,
        }],
      },
      options: {
        indexAxis: 'y',
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: ctx => faFmtRp(ctx.parsed.x) + ' (' + (projects[ctx.dataIndex].count || 0) + ' expense)',
            },
          },
        },
        scales: {
          x: { ticks: { callback: v => faFmtRpShort(v), font: { size: 9 } } },
          y: { ticks: { font: { size: 10 } } },
        },
        onClick: (evt, elements) => {
          if (!elements.length) return;
          const idx = elements[0].index;
          const pid = projects[idx].project_id;
          // Toggle: klik lagi bar yg sama = reset
          faSelectedProjectId = (faSelectedProjectId === pid) ? null : pid;
          faRenderProjectBar(faLastMonthData);
          faRenderCategoryDonut(faLastMonthData);
        },
      },
    });
  }

  window.faResetProjectFilter = function() {
    faSelectedProjectId = null;
    if (faLastMonthData) {
      faRenderProjectBar(faLastMonthData);
      faRenderCategoryDonut(faLastMonthData);
    }
  };

  function faRenderTable(monthData) {
    const body = document.getElementById('fa-tx-body');
    if (!body) return;
    const tx = monthData.transactions || [];
    if (!tx.length) {
      body.innerHTML = '<tr><td colspan="5" class="px-3 py-6 text-center" style="color:#6B7280;">Belum ada transaksi bulan ini.</td></tr>';
      return;
    }
    body.innerHTML = tx.map(t => {
      const dt = new Date(t.created_at);
      const dstr = isNaN(dt) ? '-' : `${String(dt.getDate()).padStart(2,'0')}/${String(dt.getMonth()+1).padStart(2,'0')} ${String(dt.getHours()).padStart(2,'0')}:${String(dt.getMinutes()).padStart(2,'0')}`;
      const typColor = t.type === 'income' ? '#166534' : '#B91C1C';
      const typLabel = t.type === 'income' ? 'Masuk' : 'Keluar';
      const catFull = t.parent_name ? `${faEsc(t.parent_name)} &raquo; ${faEsc(t.category_name || '-')}` : faEsc(t.category_name || '-');
      return `<tr class="border-b hover:bg-amber-50/40" style="border-color:#F4F1EA;">
        <td class="px-3 py-2" style="color:#1D1D1B;">${dstr}</td>
        <td class="px-3 py-2"><span class="text-[10px] font-bold px-1.5 py-0.5 rounded" style="background:${typColor}20;color:${typColor};">${typLabel}</span></td>
        <td class="px-3 py-2 text-[11px]">${catFull}</td>
        <td class="px-3 py-2 text-[11px]" style="color:#6B7280;">${faEsc(t.description || '-')}</td>
        <td class="px-3 py-2 text-right font-bold" style="color:${typColor};">${faFmtRpShort(t.amount)}</td>
      </tr>`;
    }).join('');
  }

  async function faReload() {
    try {
      const [y, m] = await Promise.all([faLoadSummaryYear(), faLoadSummaryMonth()]);
      if (!y || !m) return;
      // Phase EX-6: reset drill-down state saat reload period (year/month baru).
      faSelectedProjectId = null;
      faLastMonthData = m;
      faRenderKPI(y, m);
      faRenderTrend(y);
      faRenderProjectBar(m);
      faRenderCategoryDonut(m);
      faRenderTable(m);
    } catch (e) { console.error('[fa] reload', e); }
  }

  window.initFinanceAnalytics = async function() {
    await faLoadCategories();
    faPopulatePickers();
    await faReload();
    if (typeof lucide !== 'undefined') lucide.createIcons();
  };

  window.faOpenAddModal = function(type) {
    faModalType = type;
    document.getElementById('fa-modal-title').textContent =
      type === 'income' ? 'Catat Pemasukan' : 'Catat Pengeluaran';
    document.getElementById('fa-modal-submit').style.background =
      type === 'income' ? FA_COLORS.green : FA_COLORS.red;
    const sel = document.getElementById('fa-modal-category');
    const tree = (faTree && faTree[type]) || [];
    const opts = ['<option value="">-- Pilih Kategori --</option>'];
    tree.forEach(parent => {
      opts.push(`<optgroup label="${faEsc(parent.name)}">`);
      (parent.subcategories || []).forEach(sub => {
        opts.push(`<option value="${sub.id}">${faEsc(sub.name)}</option>`);
      });
      opts.push('</optgroup>');
    });
    sel.innerHTML = opts.join('');
    document.getElementById('fa-modal-amount').value = '';
    document.getElementById('fa-modal-desc').value = '';
    document.getElementById('fa-modal').classList.remove('hidden');
  };

  window.faCloseModal = function() {
    document.getElementById('fa-modal').classList.add('hidden');
  };

  window.faSubmitTransaction = async function() {
    const cid = document.getElementById('fa-modal-category').value;
    const amt = parseInt(document.getElementById('fa-modal-amount').value, 10);
    const desc = document.getElementById('fa-modal-desc').value;
    if (!cid) { alert('Pilih kategori dulu.'); return; }
    if (!amt || amt <= 0) { alert('Jumlah harus > 0.'); return; }
    const payload = { category_id: parseInt(cid, 10), amount: amt, description: desc };
    if (faModalType === 'expense') {
      const sel = document.getElementById('fa-modal-category');
      const opt = sel.options[sel.selectedIndex];
      payload.category = opt ? opt.textContent : '';
    }
    try {
      const url = faModalType === 'income' ? '/api/transactions/income' : '/api/transactions/expense';
      const r = await faFetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        alert('Gagal: ' + (j.detail || r.status));
        return;
      }
      window.faCloseModal();
      await faReload();
    } catch (e) { alert('Error: ' + e.message); }
  };

  // Phase F4: buka endpoint export dgn ?token=... supaya route ber-authenticate_file_token
  // dpt verifikasi tanpa header Authorization (browser <a target="_blank"> tidak kirim header).
  function faBuildExportUrl(period, fmt) {
    const y = faYear || new Date().getFullYear();
    const m = faMonth || (new Date().getMonth() + 1);
    const token = encodeURIComponent(sessionStorage.getItem('token') || '');
    const params = period === 'month'
      ? `year=${y}&month=${m}&token=${token}`
      : `year=${y}&token=${token}`;
    return `/api/finance/export/${period}.${fmt}?${params}`;
  }

  window.faDownloadPdf = function(period) {
    period = period === 'month' ? 'month' : 'year';
    window.open(faBuildExportUrl(period, 'pdf'), '_blank');
  };
  window.faDownloadExcel = function(period) {
    period = period === 'month' ? 'month' : 'year';
    // Content-Disposition: attachment -- trigger download via anchor click.
    const a = document.createElement('a');
    a.href = faBuildExportUrl(period, 'xlsx');
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };
})();
