/* ============================================================================
 * Frontend Simulasi Paket (BOQ) -- Phase 1b.
 *
 * Modul terpisah dari Master Paket. Konsep: setiap paket bs punya beberapa BOQ
 * (Bill of Quantities) = breakdown biaya per line item + margin -> harga/pax.
 *
 * Bergantung ke fungsi global existing di index.html:
 *   - authFetch(path, opts)     -> fetch yang auto-attach Bearer + handle 401
 *   - currentUser               -> {id, role, name}
 *   - formatRp(n)               -> "Rp 1.234.567"
 *   - openModal(id) / closeModal(id) -> show/hide overlay modal
 *   - notyf / lucide.createIcons() -> toast + icon rerender
 * ==========================================================================*/
let __boqList = [];
let __boqFilterPackage = '';
let __boqFilterStatus = '';
let __boqCurrentDetail = null;   // {id, ...detail} yg lagi dibuka di modal
let __boqPackagesCache = null;   // list paket utk dropdown (lazy load)
const __boqSelectedIds = new Set();   // Phase 3a: pilihan checkbox utk compare

const BOQ_CATEGORIES = [
    'hotel_mekkah', 'hotel_madinah', 'tiket', 'visa', 'muthawwif',
    'transport', 'konsumsi', 'ziyarah', 'handling', 'perlengkapan',
    'vaksin', 'asuransi', 'margin', 'lain',
];
const BOQ_CATEGORY_LABEL = {
    hotel_mekkah: 'Hotel Mekkah', hotel_madinah: 'Hotel Madinah',
    tiket: 'Tiket Pesawat', visa: 'Visa', muthawwif: 'Muthawwif/TL',
    transport: 'Transport', konsumsi: 'Konsumsi', ziyarah: 'Ziyarah',
    handling: 'Handling', perlengkapan: 'Perlengkapan',
    vaksin: 'Vaksin', asuransi: 'Asuransi', margin: 'Margin/Overhead',
    lain: 'Lain-lain',
};
const BOQ_UNITS = [
    { value: 'per_pax', label: 'per Pax' },
    { value: 'per_pax_per_day', label: 'per Pax/Hari' },
    { value: 'per_group', label: 'per Group (flat)' },
    { value: 'per_room_per_night', label: 'per Room/Malam' },
];
const BOQ_STATUS_BADGE = {
    'Draft':            { cls: 'bg-gray-100 text-gray-700', ico: 'edit-3' },
    'Pending Approval': { cls: 'bg-amber-100 text-amber-800', ico: 'clock' },
    'Approved':         { cls: 'bg-emerald-100 text-emerald-800', ico: 'check-circle' },
    'Rejected':         { cls: 'bg-red-100 text-red-700', ico: 'x-circle' },
};
// Phase 6c: bucket = layer di formula harga jual. Urut sesuai section render.
const BOQ_BUCKETS = ['hpp', 'prorate_tl', 'fee_agen', 'fee_referal', 'margin'];
const BOQ_BUCKET_LABEL = {
    hpp: 'HPP (Biaya)',
    prorate_tl: 'Prorate TL',
    fee_agen: 'Fee Agen',
    fee_referal: 'Fee Referal',
    margin: 'Margin UMAR',
};
const BOQ_BUCKET_STYLE = {
    hpp:         { cls: 'bg-slate-100 text-slate-700',   ico: 'package' },
    prorate_tl:  { cls: 'bg-cyan-100 text-cyan-800',     ico: 'users' },
    fee_agen:    { cls: 'bg-indigo-100 text-indigo-800', ico: 'briefcase' },
    fee_referal: { cls: 'bg-purple-100 text-purple-800', ico: 'user-plus' },
    margin:      { cls: 'bg-emerald-100 text-emerald-800', ico: 'trending-up' },
};
// Phase 6c: tipe paket. Metadata + hint di UI.
const BOQ_TYPES = ['umar_reguler', 'umar_ramadhan', 'uts_partner', 'itikaf'];
const BOQ_TYPE_LABEL = {
    umar_reguler:  'UMAR Reguler',
    umar_ramadhan: 'UMAR Ramadhan',
    uts_partner:   'UTS Partner',
    itikaf:        'Itikaf',
};

const _isMgmt = () => ['admin', 'management'].includes(currentUser?.role);
const _isAuthor = () => ['admin', 'management', 'sales', 'ops'].includes(currentUser?.role);


/* ============================================================================
 * Init + list rendering
 * ==========================================================================*/
async function initBoqPage() {
    if (!__boqPackagesCache) {
        try {
            const r = await authFetch('/packages');
            __boqPackagesCache = await r.json() || [];
        } catch (e) { __boqPackagesCache = []; }
    }
    _renderBoqPackageFilter();
    const addBtn = document.getElementById('boq-add-btn');
    if (addBtn) addBtn.classList.toggle('hidden', !_isAuthor());
    // Kelola Template = mgmt/admin only.
    const tmplBtn = document.getElementById('boq-tmpl-manage-btn');
    if (tmplBtn) tmplBtn.classList.toggle('hidden', !_isMgmt());
    // Phase 6j: kalau dibuka via link "Kelola BOQ" dari Master Paket, ambil
    // package_id preset dari localStorage lalu apply ke filter.
    try {
        const preset = localStorage.getItem('boq-filter-package-preset');
        if (preset) {
            localStorage.removeItem('boq-filter-package-preset');
            const filterEl = document.getElementById('boq-filter-package');
            if (filterEl) {
                filterEl.value = preset;
                if (typeof __boqFilterPackage !== 'undefined') __boqFilterPackage = preset;
            }
        }
    } catch (e) {}
    await fetchBoqList();
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

function _renderBoqPackageFilter() {
    const filter = document.getElementById('boq-filter-package');
    const formSel = document.getElementById('boq-form-package');
    const opts = '<option value="">-- Semua Paket --</option>'
        + '<option value="null">Tanpa Paket (draft baru)</option>'
        + (__boqPackagesCache || []).map(p =>
            `<option value="${p.id}">${_esc(p.name)}</option>`).join('');
    if (filter) filter.innerHTML = opts;
    // Form select: pilihan "kosong" (tanpa paket) + list paket
    if (formSel) formSel.innerHTML = '<option value="">-- (BOQ paket baru) --</option>'
        + (__boqPackagesCache || []).map(p =>
            `<option value="${p.id}">${_esc(p.name)}</option>`).join('');
}

async function fetchBoqList() {
    const params = new URLSearchParams();
    if (__boqFilterPackage === 'null') {
        // client-side filter (endpoint tdk support IS NULL)
    } else if (__boqFilterPackage) {
        params.set('package_id', __boqFilterPackage);
    }
    if (__boqFilterStatus) params.set('status', __boqFilterStatus);
    try {
        const r = await authFetch('/boq' + (params.toString() ? '?' + params : ''));
        let rows = await r.json() || [];
        if (__boqFilterPackage === 'null') rows = rows.filter(x => !x.package_id);
        __boqList = rows;
        _renderBoqTable();
    } catch (e) {
        _toast('Gagal memuat BOQ: ' + (e.message || e), 'error');
    }
}

function _renderBoqTable() {
    const tbody = document.getElementById('table-boq');
    if (!tbody) return;
    // Purge stale selections (BOQ deleted / filtered out) supaya count akurat.
    const visibleIds = new Set(__boqList.map(b => b.id));
    for (const id of Array.from(__boqSelectedIds)) if (!visibleIds.has(id)) __boqSelectedIds.delete(id);

    if (!__boqList.length) {
        tbody.innerHTML = `<tr><td colspan="8" class="px-4 py-8 text-center text-gray-400 text-sm">
            Belum ada BOQ. Klik <b>+ Tambah BOQ</b> untuk mulai bikin skenario harga.</td></tr>`;
        _updateCompareBtn();
        return;
    }
    tbody.innerHTML = __boqList.map(b => {
        const st = BOQ_STATUS_BADGE[b.status] || BOQ_STATUS_BADGE.Draft;
        const checked = __boqSelectedIds.has(b.id) ? 'checked' : '';
        return `<tr class="hover:bg-emerald-50/40">
            <td class="px-2 py-2 text-center"><input type="checkbox" ${checked} onchange="_toggleBoqSelect(${b.id}, this.checked)" title="Pilih utk bandingkan"/></td>
            <td class="px-3 py-2">
                <div class="font-semibold text-gray-800">${_esc(b.name)}</div>
                <div class="text-[11px] text-gray-500">by ${_esc(b.created_by_name || '-')}</div>
            </td>
            <td class="px-3 py-2 text-sm">${b.package_name ? _esc(b.package_name) : '<i class="text-gray-400">-- (paket baru)</i>'}</td>
            <td class="px-3 py-2">
                <span class="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] rounded-full font-bold ${st.cls}">
                    <i data-lucide="${st.ico}" class="w-3 h-3"></i>${_esc(b.status)}
                </span>
            </td>
            <td class="px-3 py-2 text-center text-sm">${b.item_count || 0}</td>
            <td class="px-3 py-2 text-right text-xs text-gray-500">
                ${b.target_pax || 0} pax &middot; ${b.target_margin_pct || 0}%
            </td>
            <td class="px-3 py-2 text-[11px] text-gray-500">${_esc((b.updated_at || '').slice(0, 16))}</td>
            <td class="px-3 py-2 text-right space-x-1 whitespace-nowrap">
                <button onclick="openBoqDetail(${b.id})" class="text-xs px-2 py-1 rounded bg-blue-50 text-blue-700 hover:bg-blue-100" title="Detail"><i data-lucide="eye" class="w-3.5 h-3.5"></i></button>
                ${_canEdit(b) ? `<button onclick="openEditBoq(${b.id})" class="text-xs px-2 py-1 rounded bg-gray-100 text-gray-700 hover:bg-gray-200" title="Edit header"><i data-lucide="edit-2" class="w-3.5 h-3.5"></i></button>` : ''}
                ${_isAuthor() ? `<button onclick="duplicateBoq(${b.id})" class="text-xs px-2 py-1 rounded bg-purple-50 text-purple-700 hover:bg-purple-100" title="Duplicate ke Draft baru"><i data-lucide="copy" class="w-3.5 h-3.5"></i></button>` : ''}
                ${_canDelete(b) ? `<button onclick="deleteBoq(${b.id})" class="text-xs px-2 py-1 rounded bg-red-50 text-red-700 hover:bg-red-100" title="Hapus"><i data-lucide="trash-2" class="w-3.5 h-3.5"></i></button>` : ''}
            </td>
        </tr>`;
    }).join('');
    _updateCompareBtn();
    const selectAll = document.getElementById('boq-select-all');
    if (selectAll) selectAll.checked = __boqList.length > 0 && __boqSelectedIds.size === __boqList.length;
    if (typeof lucide !== 'undefined') lucide.createIcons();
}


/* --- Phase 3a: multi-select + compare launcher --- */
function _toggleBoqSelect(bid, checked) {
    if (checked) __boqSelectedIds.add(bid); else __boqSelectedIds.delete(bid);
    _updateCompareBtn();
    const selectAll = document.getElementById('boq-select-all');
    if (selectAll) selectAll.checked = __boqList.length > 0 && __boqSelectedIds.size === __boqList.length;
}
function _toggleBoqSelectAll(checked) {
    if (checked) __boqList.forEach(b => __boqSelectedIds.add(b.id));
    else __boqSelectedIds.clear();
    _renderBoqTable();
}
function _updateCompareBtn() {
    const btn = document.getElementById('boq-compare-btn');
    const count = document.getElementById('boq-compare-count');
    if (!btn) return;
    const n = __boqSelectedIds.size;
    if (count) count.innerText = n;
    // Show hanya kalau 2-5 terpilih (endpoint validasi).
    btn.classList.toggle('hidden', n < 2);
    if (n > 5) {
        btn.disabled = true;
        btn.title = `Terpilih ${n}. Maksimal 5 BOQ per compare -- kurangi dulu.`;
        btn.classList.add('opacity-60', 'cursor-not-allowed');
    } else {
        btn.disabled = false;
        btn.title = '';
        btn.classList.remove('opacity-60', 'cursor-not-allowed');
    }
}

function _canEdit(b) {
    if (_isMgmt()) return true;
    return b.created_by === currentUser.id && b.status === 'Draft';
}
function _canDelete(b) {
    if (_isMgmt()) return true;
    return b.created_by === currentUser.id && b.status === 'Draft';
}


/* ============================================================================
 * Add / edit header modal
 * ==========================================================================*/
function openAddBoq() {
    document.getElementById('boq-modal-title').innerText = 'Tambah BOQ Baru';
    document.getElementById('boq-form-id').value = '';
    document.getElementById('boq-form-name').value = '';
    document.getElementById('boq-form-package').value = '';
    document.getElementById('boq-form-pax').value = 45;
    document.getElementById('boq-form-margin').value = 15;
    document.getElementById('boq-form-extra-triple').value = 0;
    document.getElementById('boq-form-extra-double').value = 0;
    document.getElementById('boq-form-notes').value = '';
    // Phase 6c: default boq_type = UMAR Reguler.
    const typeSel = document.getElementById('boq-form-type');
    if (typeSel) typeSel.value = 'umar_reguler';
    document.getElementById('boq-form-items-wrapper').classList.remove('hidden');
    __boqFormItems = [_blankItem()];
    _renderBoqFormItems();
    openModal('modal-boq-form');
}

function openEditBoq(bid) {
    const b = __boqList.find(x => x.id === bid);
    if (!b) return;
    document.getElementById('boq-modal-title').innerText = 'Edit Header BOQ (items diedit di modal Detail)';
    document.getElementById('boq-form-id').value = bid;
    document.getElementById('boq-form-name').value = b.name || '';
    document.getElementById('boq-form-package').value = b.package_id || '';
    document.getElementById('boq-form-pax').value = b.target_pax || 45;
    document.getElementById('boq-form-margin').value = b.target_margin_pct || 15;
    document.getElementById('boq-form-extra-triple').value = b.extra_triple || 0;
    document.getElementById('boq-form-extra-double').value = b.extra_double || 0;
    document.getElementById('boq-form-notes').value = b.notes || '';
    // Phase 6c: populate boq_type dari row existing.
    const typeSel = document.getElementById('boq-form-type');
    if (typeSel) typeSel.value = b.boq_type || 'umar_reguler';
    document.getElementById('boq-form-items-wrapper').classList.add('hidden');
    openModal('modal-boq-form');
}

async function saveBoq() {
    const id = document.getElementById('boq-form-id').value;
    const body = {
        name: document.getElementById('boq-form-name').value.trim(),
        package_id: document.getElementById('boq-form-package').value ? parseInt(document.getElementById('boq-form-package').value) : null,
        target_pax: parseInt(document.getElementById('boq-form-pax').value) || 45,
        target_margin_pct: parseFloat(document.getElementById('boq-form-margin').value) || 0,
        extra_triple: parseInt(document.getElementById('boq-form-extra-triple').value) || 0,
        extra_double: parseInt(document.getElementById('boq-form-extra-double').value) || 0,
        notes: document.getElementById('boq-form-notes').value,
        // Phase 6c: kirim boq_type.
        boq_type: document.getElementById('boq-form-type')?.value || 'umar_reguler',
    };
    if (!body.name) return _toast('Nama BOQ wajib diisi.', 'error');
    if (!id) {
        const items = (__boqFormItems || []).filter(it => it.item_name && it.category);
        body.items = items;
    }
    try {
        const path = id ? `/boq/${id}` : '/boq';
        const method = id ? 'PUT' : 'POST';
        const r = await authFetch(path, { method, body: JSON.stringify(body) });
        const j = await r.json();
        _toast(j.message || 'Tersimpan.', 'success');
        closeModal('modal-boq-form');
        await fetchBoqList();
    } catch (e) {
        _toast('Simpan gagal: ' + (e.message || e), 'error');
    }
}

/* --- items repeater di modal Add --- */
let __boqFormItems = [];
function _blankItem() {
    return {
        // Phase 6c: bucket default 'hpp' (biaya nyata). User pilih section
        // lain kalau mau taruh di prorate_tl / fee / margin.
        bucket: 'hpp',
        category: 'hotel_mekkah', item_name: '', unit: 'per_pax',
        quantity: 1, unit_price: 0, vendor_name: '', note: '',
    };
}
function addBoqFormItem() {
    __boqFormItems.push(_blankItem());
    _renderBoqFormItems();
}
function removeBoqFormItem(idx) {
    __boqFormItems.splice(idx, 1);
    _renderBoqFormItems();
}
function _updateFormItemField(idx, field, value) {
    if (!__boqFormItems[idx]) return;
    if (['quantity', 'unit_price'].includes(field)) value = parseFloat(value) || 0;
    __boqFormItems[idx][field] = value;
    _renderBoqFormTotals();
}
function _renderBoqFormItems() {
    const tbody = document.getElementById('boq-form-items');
    if (!tbody) return;
    tbody.innerHTML = __boqFormItems.map((it, idx) => {
        const subtotal = (it.quantity || 0) * (it.unit_price || 0);
        const bkt = it.bucket || 'hpp';
        const bStyle = BOQ_BUCKET_STYLE[bkt] || BOQ_BUCKET_STYLE.hpp;
        return `<tr>
            <td class="px-1 py-1">
                <select class="w-full border rounded px-1 py-1 text-xs ${bStyle.cls}" onchange="_updateFormItemField(${idx},'bucket',this.value)" title="Bucket = layer di formula harga jual">
                    ${BOQ_BUCKETS.map(b => `<option value="${b}" ${bkt===b?'selected':''}>${BOQ_BUCKET_LABEL[b]}</option>`).join('')}
                </select>
            </td>
            <td class="px-1 py-1">
                <select class="w-full border rounded px-1 py-1 text-xs" onchange="_updateFormItemField(${idx},'category',this.value)">
                    ${BOQ_CATEGORIES.map(c => `<option value="${c}" ${it.category===c?'selected':''}>${BOQ_CATEGORY_LABEL[c]}</option>`).join('')}
                </select>
            </td>
            <td class="px-1 py-1"><input type="text" value="${_esc(it.item_name)}" class="w-full border rounded px-2 py-1 text-xs" placeholder="Nama item" oninput="_updateFormItemField(${idx},'item_name',this.value)"/></td>
            <td class="px-1 py-1">
                <select class="w-full border rounded px-1 py-1 text-xs" onchange="_updateFormItemField(${idx},'unit',this.value)">
                    ${BOQ_UNITS.map(u => `<option value="${u.value}" ${it.unit===u.value?'selected':''}>${u.label}</option>`).join('')}
                </select>
            </td>
            <td class="px-1 py-1"><input type="number" step="0.01" value="${it.quantity}" class="w-16 border rounded px-1 py-1 text-xs text-right" oninput="_updateFormItemField(${idx},'quantity',this.value)"/></td>
            <td class="px-1 py-1"><input type="number" value="${it.unit_price}" class="w-28 border rounded px-1 py-1 text-xs text-right" oninput="_updateFormItemField(${idx},'unit_price',this.value)"/></td>
            <td class="px-1 py-1 text-right text-xs text-gray-600 tabular-nums">${formatRp(subtotal)}</td>
            <td class="px-1 py-1 text-center"><button type="button" onclick="removeBoqFormItem(${idx})" class="text-red-600 hover:text-red-800 text-xs" title="Hapus"><i data-lucide="x" class="w-3.5 h-3.5"></i></button></td>
        </tr>`;
    }).join('') || `<tr><td colspan="8" class="text-center text-xs text-gray-400 py-3">Belum ada item. Klik + Tambah Item.</td></tr>`;
    _renderBoqFormTotals();
    if (typeof lucide !== 'undefined') lucide.createIcons();
}
// Phase 6c: bucket-aware live calc. Mirror backend _compute_totals di
// routes/boq.py -- semua formula persis sama supaya preview == hasil simpan.
function _computeBucketsFromItems(items, pax, marginPct) {
    const by = { hpp: 0, prorate_tl: 0, fee_agen: 0, fee_referal: 0, margin: 0 };
    for (const it of (items || [])) {
        const bkt = BOQ_BUCKETS.includes(it.bucket) ? it.bucket : 'hpp';
        const sub = (it.quantity || 0) * (it.unit_price || 0);
        if (it.unit === 'per_pax' || it.unit === 'per_pax_per_day') {
            by[bkt] += sub * pax;
        } else {
            by[bkt] += sub;
        }
    }
    const perPax = {
        hpp: Math.floor(by.hpp / pax),
        prorate_tl: Math.floor(by.prorate_tl / pax),
        fee_agen: Math.floor(by.fee_agen / pax),
        fee_referal: Math.floor(by.fee_referal / pax),
        margin: Math.floor(by.margin / pax),
    };
    // Legacy backward-compat: kalau bucket=margin kosong, margin dari %.
    const marginFromItems = perPax.margin > 0;
    if (!marginFromItems && marginPct > 0) {
        perPax.margin = Math.floor(perPax.hpp * (marginPct / 100));
    }
    const costPerPax = perPax.hpp + perPax.prorate_tl;
    const pricePerPax = perPax.hpp + perPax.prorate_tl + perPax.fee_agen
        + perPax.fee_referal + perPax.margin;
    const totalGroup = by.hpp + by.prorate_tl + by.fee_agen + by.fee_referal + by.margin;
    return { by, perPax, costPerPax, pricePerPax, totalGroup, marginFromItems };
}

function _renderBoqFormTotals() {
    const pax = Math.max(parseInt(document.getElementById('boq-form-pax').value) || 1, 1);
    const margin = parseFloat(document.getElementById('boq-form-margin').value) || 0;
    const et = parseInt(document.getElementById('boq-form-extra-triple')?.value) || 0;
    const ed = parseInt(document.getElementById('boq-form-extra-double')?.value) || 0;
    const c = _computeBucketsFromItems(__boqFormItems, pax, margin);
    const priceQuad = c.pricePerPax;
    const priceTriple = priceQuad + et;
    const priceDouble = priceQuad + ed;
    const realMarginPct = c.pricePerPax > 0
        ? ((c.perPax.margin / c.pricePerPax) * 100).toFixed(1)
        : '0.0';
    const bucketRow = (bkt) => {
        const s = BOQ_BUCKET_STYLE[bkt];
        const suffix = bkt === 'margin' && !c.marginFromItems
            ? ` <span class="text-[9px] text-gray-400">(dari ${margin}%)</span>`
            : '';
        return `<div class="flex items-center gap-1.5 text-xs">
            <span class="px-1.5 py-0.5 rounded ${s.cls} text-[10px] font-bold">${BOQ_BUCKET_LABEL[bkt]}</span>
            <b class="tabular-nums">${formatRp(c.perPax[bkt])}</b>${suffix}
        </div>`;
    };
    const el = document.getElementById('boq-form-totals');
    if (el) el.innerHTML = `
        <div class="w-full grid grid-cols-1 md:grid-cols-2 gap-3 text-xs bg-gradient-to-br from-slate-50 to-emerald-50/30 p-3 rounded border">
            <div>
                <div class="text-[10px] text-gray-500 font-bold uppercase mb-1">Breakdown per pax</div>
                <div class="space-y-1">
                    ${BOQ_BUCKETS.map(bucketRow).join('')}
                </div>
                <div class="mt-2 pt-2 border-t text-[10px] text-gray-500 flex justify-between">
                    <span>Cost/pax (HPP+Prorate):</span>
                    <b class="tabular-nums">${formatRp(c.costPerPax)}</b>
                </div>
                <div class="text-[10px] text-gray-500 flex justify-between">
                    <span>Real margin:</span>
                    <b class="text-emerald-700">${realMarginPct}%</b>
                </div>
            </div>
            <div class="text-emerald-800 border-l pl-3">
                <div class="text-[10px] text-gray-500 font-bold uppercase mb-1">Harga per pax</div>
                <div class="tabular-nums text-sm">QUAD <b>${formatRp(priceQuad)}</b></div>
                <div class="tabular-nums text-sm">TRIPLE <b>${formatRp(priceTriple)}</b>${et ? '' : '<span class="text-[10px] text-gray-400 ml-1">(= QUAD)</span>'}</div>
                <div class="tabular-nums text-sm">DOUBLE <b>${formatRp(priceDouble)}</b>${ed ? '' : '<span class="text-[10px] text-gray-400 ml-1">(= QUAD)</span>'}</div>
                <div class="mt-2 pt-2 border-t text-[10px] text-gray-500 flex justify-between">
                    <span>Total group cost:</span>
                    <b class="tabular-nums">${formatRp(c.totalGroup)}</b>
                </div>
            </div>
        </div>`;
}


/* ============================================================================
 * Detail modal (items CRUD + workflow actions)
 * ==========================================================================*/
async function openBoqDetail(bid) {
    try {
        const r = await authFetch(`/boq/${bid}`);
        __boqCurrentDetail = await r.json();
        _renderBoqDetail();
        openModal('modal-boq-detail');
    } catch (e) {
        _toast('Gagal memuat detail: ' + (e.message || e), 'error');
    }
}

function _renderBoqDetail() {
    const b = __boqCurrentDetail;
    if (!b) return;
    const st = BOQ_STATUS_BADGE[b.status] || BOQ_STATUS_BADGE.Draft;
    const canEditItems = _canEdit(b);
    const isPending = b.status === 'Pending Approval';
    const isDraft = b.status === 'Draft';
    const isOwner = b.created_by === currentUser.id;
    const boqType = b.boq_type || 'umar_reguler';
    const typeLabel = BOQ_TYPE_LABEL[boqType] || boqType;

    document.getElementById('boq-detail-header').innerHTML = `
        <div class="flex flex-wrap items-start gap-3 justify-between">
            <div>
                <h3 class="text-lg font-bold text-gray-800">${_esc(b.name)}</h3>
                <div class="text-xs text-gray-500 mt-1 flex flex-wrap items-center gap-2">
                    ${b.package_name ? _esc(b.package_name) : '<i>Belum terikat ke paket (BOQ paket baru)</i>'}
                    <span>&middot; Target: ${b.target_pax} pax &middot; Margin: ${b.target_margin_pct}%</span>
                    <span class="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] rounded-full font-bold bg-amber-100 text-amber-800" title="Tipe paket BOQ (Phase 6c)">
                        <i data-lucide="tag" class="w-3 h-3"></i>${_esc(typeLabel)}
                    </span>
                </div>
            </div>
            <span class="inline-flex items-center gap-1 px-3 py-1 text-xs rounded-full font-bold ${st.cls}">
                <i data-lucide="${st.ico}" class="w-3.5 h-3.5"></i>${_esc(b.status)}
            </span>
        </div>
        ${b.notes ? `<div class="mt-2 text-xs text-gray-600 bg-yellow-50 border-l-2 border-yellow-400 px-2 py-1">${_esc(b.notes)}</div>` : ''}
        ${b.review_note ? `<div class="mt-2 text-xs text-gray-700 bg-blue-50 border-l-2 border-blue-400 px-2 py-1"><b>Review:</b> ${_esc(b.review_note)}</div>` : ''}
        <div class="text-[11px] text-gray-400 mt-2">
            Dibuat oleh <b>${_esc(b.created_by_name || '-')}</b> ${_esc((b.created_at||'').slice(0,16))}
            ${b.submitted_at ? ` &middot; Submit ${_esc(b.submitted_at.slice(0,16))}` : ''}
            ${b.reviewed_by_name ? ` &middot; Review oleh <b>${_esc(b.reviewed_by_name)}</b> ${_esc((b.reviewed_at||'').slice(0,16))}` : ''}
        </div>`;

    // Phase 6c: group items per bucket dgn section header.
    const itemsByBucket = { hpp: [], prorate_tl: [], fee_agen: [], fee_referal: [], margin: [] };
    for (const it of (b.items || [])) {
        const bkt = BOQ_BUCKETS.includes(it.bucket) ? it.bucket : 'hpp';
        itemsByBucket[bkt].push(it);
    }
    const tbody = document.getElementById('boq-detail-items');
    const itemRow = (it) => `
        <tr>
            <td class="px-2 py-1 text-xs">${_esc(BOQ_CATEGORY_LABEL[it.category] || it.category)}</td>
            <td class="px-2 py-1 text-xs">${_esc(it.item_name)}</td>
            <td class="px-2 py-1 text-xs text-gray-500">${_esc(it.unit)}</td>
            <td class="px-2 py-1 text-xs text-right tabular-nums">${it.quantity}</td>
            <td class="px-2 py-1 text-xs text-right tabular-nums">${formatRp(it.unit_price)}</td>
            <td class="px-2 py-1 text-xs text-right tabular-nums font-semibold">${formatRp(it.subtotal)}</td>
            <td class="px-2 py-1 text-xs text-gray-400">${_esc(it.vendor_name || '')}</td>
            <td class="px-2 py-1 text-right">
                ${canEditItems ? `
                    <button onclick="_editBoqItem(${it.id})" class="text-xs px-1.5 py-0.5 text-blue-600 hover:bg-blue-50 rounded" title="Edit"><i data-lucide="edit-2" class="w-3 h-3"></i></button>
                    <button onclick="_deleteBoqItem(${it.id})" class="text-xs px-1.5 py-0.5 text-red-600 hover:bg-red-50 rounded" title="Hapus"><i data-lucide="x" class="w-3 h-3"></i></button>
                ` : ''}
            </td>
        </tr>`;
    let html = '';
    let sectionCount = 0;
    for (const bkt of BOQ_BUCKETS) {
        const items = itemsByBucket[bkt];
        if (!items.length) continue;
        sectionCount++;
        const s = BOQ_BUCKET_STYLE[bkt];
        html += `<tr class="${s.cls}">
            <td colspan="8" class="px-2 py-1.5 text-[11px] font-bold uppercase tracking-wide">
                <i data-lucide="${s.ico}" class="w-3.5 h-3.5 inline"></i>
                ${BOQ_BUCKET_LABEL[bkt]}
                <span class="ml-2 text-[10px] font-normal opacity-70">${items.length} item${items.length > 1 ? 's' : ''}</span>
            </td>
        </tr>`;
        html += items.map(itemRow).join('');
    }
    tbody.innerHTML = html || `<tr><td colspan="8" class="text-center py-4 text-xs text-gray-400">Belum ada item.</td></tr>`;
    // Legacy marker: kalau semua items masih di bucket=hpp default, hint reviewer.
    if (sectionCount === 1 && itemsByBucket.hpp.length > 0 && b.items.every(i => (i.bucket || 'hpp') === 'hpp')) {
        tbody.insertAdjacentHTML('afterbegin', `<tr><td colspan="8" class="px-2 py-1 text-[10px] italic text-amber-700 bg-amber-50 border-l-2 border-amber-400">
            <i data-lucide="alert-circle" class="w-3 h-3 inline"></i>
            Legacy BOQ -- semua items masih di bucket <b>HPP default</b>. Klik Edit item untuk reklasifikasi (mis. Fee Agen, Margin) supaya breakdown akurat.
        </td></tr>`);
    }

    const t = b.totals || {};
    const hasSplit = (t.extra_triple || 0) > 0 || (t.extra_double || 0) > 0;
    const bkts = t.buckets || {};
    const marginFromItems = bkts.margin?.from_items;
    const realMarginPct = (t.price_per_pax > 0)
        ? ((t.margin_amount / t.price_per_pax) * 100).toFixed(1)
        : '0.0';
    const bucketBadge = (bkt) => {
        const s = BOQ_BUCKET_STYLE[bkt];
        const pp = bkts[bkt]?.per_pax || 0;
        const suffix = bkt === 'margin' && !marginFromItems
            ? ` <span class="text-[9px] opacity-60">(dari ${b.target_margin_pct}%)</span>`
            : '';
        return `<div class="flex items-center gap-1.5">
            <span class="px-1.5 py-0.5 rounded ${s.cls} text-[10px] font-bold">${BOQ_BUCKET_LABEL[bkt]}</span>
            <b class="tabular-nums">${formatRp(pp)}</b>${suffix}
        </div>`;
    };
    document.getElementById('boq-detail-totals').innerHTML = `
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 bg-gradient-to-br from-slate-50 to-emerald-50/30 p-3 rounded border">
            <div>
                <div class="text-[10px] text-gray-500 font-bold uppercase mb-1.5">Breakdown per pax (Phase 6c)</div>
                <div class="space-y-1 text-xs">${BOQ_BUCKETS.map(bucketBadge).join('')}</div>
                <div class="mt-2 pt-2 border-t text-[11px] flex justify-between">
                    <span class="text-gray-500">Cost/pax (HPP+Prorate):</span>
                    <b class="tabular-nums">${formatRp(t.cost_per_pax || 0)}</b>
                </div>
                <div class="text-[11px] flex justify-between">
                    <span class="text-gray-500">Real margin:</span>
                    <b class="text-emerald-700">${realMarginPct}%</b>
                </div>
                <div class="text-[11px] flex justify-between">
                    <span class="text-gray-500">Total group cost:</span>
                    <b class="tabular-nums">${formatRp(t.total_group_cost || 0)}</b>
                </div>
            </div>
            <div class="text-emerald-800 border-l pl-3">
                <div class="text-[10px] text-gray-500 font-bold uppercase mb-1.5">Harga jual per pax ${hasSplit ? '(split per room type)' : '<span class="text-gray-400 font-normal">(flat, semua room sama)</span>'}</div>
                <div class="grid grid-cols-3 gap-2 tabular-nums text-sm">
                    <div>QUAD<br><b class="text-base">${formatRp(t.price_quad || 0)}</b></div>
                    <div>TRIPLE<br><b class="text-base">${formatRp(t.price_triple || 0)}</b></div>
                    <div>DOUBLE<br><b class="text-base">${formatRp(t.price_double || 0)}</b></div>
                </div>
            </div>
        </div>`;

    const addItemBtn = document.getElementById('boq-detail-add-item-btn');
    if (addItemBtn) addItemBtn.classList.toggle('hidden', !canEditItems);

    const actions = document.getElementById('boq-detail-actions');
    let btns = '';
    if (isDraft && (isOwner || _isMgmt())) {
        btns += `<button onclick="submitBoq(${b.id})" class="px-3 py-1.5 rounded bg-blue-600 hover:bg-blue-700 text-white text-xs font-bold"><i data-lucide="send" class="w-3.5 h-3.5 inline"></i> Submit untuk Approval</button>`;
    }
    if (isPending && _isMgmt()) {
        btns += `<button onclick="_openReviewModal('approve')" class="px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-bold"><i data-lucide="check" class="w-3.5 h-3.5 inline"></i> Approve</button>`;
        btns += `<button onclick="_openReviewModal('reject')" class="px-3 py-1.5 rounded bg-red-600 hover:bg-red-700 text-white text-xs font-bold"><i data-lucide="x" class="w-3.5 h-3.5 inline"></i> Reject</button>`;
    }
    if (isDraft && _isMgmt()) {
        btns += `<button onclick="_openReviewModal('approve')" class="px-3 py-1.5 rounded bg-emerald-500 hover:bg-emerald-600 text-white text-xs"><i data-lucide="fast-forward" class="w-3.5 h-3.5 inline"></i> Approve Langsung</button>`;
    }
    // Phase 2: Convert to Package -- mgmt/admin only, hanya kalau Approved & belum linked.
    if (b.status === 'Approved' && !b.package_id && _isMgmt()) {
        btns += `<button onclick="openConvertBoqModal()" class="px-3 py-1.5 rounded bg-amber-600 hover:bg-amber-700 text-white text-xs font-bold"><i data-lucide="package-plus" class="w-3.5 h-3.5 inline"></i> Jadikan Paket</button>`;
    }
    if (_isAuthor()) {
        btns += `<button onclick="duplicateBoq(${b.id})" class="px-3 py-1.5 rounded bg-purple-100 text-purple-700 hover:bg-purple-200 text-xs"><i data-lucide="copy" class="w-3.5 h-3.5 inline"></i> Duplicate</button>`;
    }
    actions.innerHTML = btns;

    if (typeof lucide !== 'undefined') lucide.createIcons();
}


/* ============================================================================
 * Item CRUD (dari modal Detail)
 * ==========================================================================*/
function addBoqDetailItem() { _openItemModal(null); }
function _editBoqItem(iid) {
    const it = (__boqCurrentDetail?.items || []).find(x => x.id === iid);
    if (it) _openItemModal(it);
}
async function _deleteBoqItem(iid) {
    if (!confirm('Hapus item ini?')) return;
    try {
        await authFetch(`/boq/${__boqCurrentDetail.id}/items/${iid}`, { method: 'DELETE' });
        _toast('Item dihapus.', 'success');
        await openBoqDetail(__boqCurrentDetail.id);
        await fetchBoqList();
    } catch (e) {
        _toast('Gagal hapus item: ' + (e.message || e), 'error');
    }
}
function _openItemModal(it) {
    document.getElementById('boq-item-modal-title').innerText = it ? 'Edit Item' : 'Tambah Item';
    document.getElementById('boq-item-id').value = it?.id || '';
    // Phase 6c: bucket picker paling atas.
    const bucketSel = document.getElementById('boq-item-bucket');
    if (bucketSel) {
        bucketSel.innerHTML = BOQ_BUCKETS.map(b =>
            `<option value="${b}" ${(it?.bucket || 'hpp')===b?'selected':''}>${BOQ_BUCKET_LABEL[b]}</option>`).join('');
    }
    document.getElementById('boq-item-category').innerHTML = BOQ_CATEGORIES.map(c =>
        `<option value="${c}" ${it?.category===c?'selected':''}>${BOQ_CATEGORY_LABEL[c]}</option>`).join('');
    document.getElementById('boq-item-unit').innerHTML = BOQ_UNITS.map(u =>
        `<option value="${u.value}" ${it?.unit===u.value?'selected':''}>${u.label}</option>`).join('');
    document.getElementById('boq-item-name').value = it?.item_name || '';
    document.getElementById('boq-item-qty').value = it?.quantity ?? 1;
    document.getElementById('boq-item-price').value = it?.unit_price ?? 0;
    document.getElementById('boq-item-vendor').value = it?.vendor_name || '';
    document.getElementById('boq-item-note').value = it?.note || '';
    openModal('modal-boq-item');
}
async function saveBoqItem() {
    const iid = document.getElementById('boq-item-id').value;
    const body = {
        // Phase 6c: bucket.
        bucket: document.getElementById('boq-item-bucket')?.value || 'hpp',
        category: document.getElementById('boq-item-category').value,
        item_name: document.getElementById('boq-item-name').value.trim(),
        unit: document.getElementById('boq-item-unit').value,
        quantity: parseFloat(document.getElementById('boq-item-qty').value) || 0,
        unit_price: parseInt(document.getElementById('boq-item-price').value) || 0,
        vendor_name: document.getElementById('boq-item-vendor').value,
        note: document.getElementById('boq-item-note').value,
    };
    if (!body.item_name) return _toast('Nama item wajib.', 'error');
    try {
        const path = iid
            ? `/boq/${__boqCurrentDetail.id}/items/${iid}`
            : `/boq/${__boqCurrentDetail.id}/items`;
        await authFetch(path, { method: iid ? 'PUT' : 'POST', body: JSON.stringify(body) });
        _toast('Item tersimpan.', 'success');
        closeModal('modal-boq-item');
        await openBoqDetail(__boqCurrentDetail.id);
        await fetchBoqList();
    } catch (e) {
        _toast('Gagal simpan: ' + (e.message || e), 'error');
    }
}


/* ============================================================================
 * Workflow: submit / approve / reject / duplicate / delete
 * ==========================================================================*/
async function submitBoq(bid) {
    if (!confirm('Submit BOQ ini untuk review Management? Setelah submit tidak bisa edit lagi.')) return;
    try {
        await authFetch(`/boq/${bid}/submit`, { method: 'POST' });
        _toast('BOQ dikirim untuk approval.', 'success');
        closeModal('modal-boq-detail');
        await fetchBoqList();
    } catch (e) {
        _toast('Submit gagal: ' + (e.message || e), 'error');
    }
}

let __boqReviewAction = null;
function _openReviewModal(action) {
    __boqReviewAction = action;
    document.getElementById('boq-review-title').innerText =
        action === 'approve' ? 'Approve BOQ' : 'Reject BOQ';
    document.getElementById('boq-review-note').value = '';
    document.getElementById('boq-review-note-label').innerText =
        action === 'reject' ? 'Alasan (wajib):' : 'Catatan (opsional):';
    document.getElementById('boq-review-submit-btn').className =
        'px-3 py-1.5 rounded text-white text-sm font-bold '
        + (action === 'approve' ? 'bg-emerald-600 hover:bg-emerald-700' : 'bg-red-600 hover:bg-red-700');
    document.getElementById('boq-review-submit-btn').innerText =
        action === 'approve' ? 'Approve' : 'Reject';
    openModal('modal-boq-review');
}
async function submitBoqReview() {
    const note = document.getElementById('boq-review-note').value.trim();
    if (__boqReviewAction === 'reject' && !note) {
        return _toast('Alasan reject wajib diisi.', 'error');
    }
    try {
        await authFetch(`/boq/${__boqCurrentDetail.id}/${__boqReviewAction}`,
            { method: 'POST', body: JSON.stringify({ review_note: note }) });
        _toast(__boqReviewAction === 'approve' ? 'BOQ disetujui.' : 'BOQ ditolak.', 'success');
        closeModal('modal-boq-review');
        closeModal('modal-boq-detail');
        await fetchBoqList();
    } catch (e) {
        _toast('Gagal: ' + (e.message || e), 'error');
    }
}

/* ============================================================================
 * Phase 2: Convert BOQ Approved -> row Master Paket baru
 * ==========================================================================*/
function openConvertBoqModal() {
    const b = __boqCurrentDetail;
    if (!b) return;
    // Prefill dari BOQ + kalkulasi.
    document.getElementById('boq-conv-name').value = b.name || '';
    document.getElementById('boq-conv-departure').value = '';
    document.getElementById('boq-conv-return').value = '';
    document.getElementById('boq-conv-duration').value = 9;
    document.getElementById('boq-conv-quota').value = b.target_pax || 45;
    document.getElementById('boq-conv-hotel-mekkah').value = '';
    document.getElementById('boq-conv-hotel-madinah').value = '';
    document.getElementById('boq-conv-route').value = 'Direct';
    document.getElementById('boq-conv-commission').value = 0;
    const t = b.totals || {};
    const pq = t.price_quad || 0, pt = t.price_triple || 0, pd = t.price_double || 0;
    const previewEl = document.getElementById('boq-conv-preview');
    if (pq === pt && pq === pd) {
        previewEl.innerHTML = `Harga per pax dari BOQ (flat, semua room type sama): <b>${formatRp(pq)}</b>`;
    } else {
        previewEl.innerHTML = `Harga split akan disalin ke paket:
            QUAD <b>${formatRp(pq)}</b> &middot;
            TRIPLE <b>${formatRp(pt)}</b> &middot;
            DOUBLE <b>${formatRp(pd)}</b>`;
    }
    openModal('modal-boq-convert');
}

async function convertBoqToPackage() {
    const body = {
        name: document.getElementById('boq-conv-name').value.trim(),
        departure_date: document.getElementById('boq-conv-departure').value,
        return_date: document.getElementById('boq-conv-return').value || null,
        duration: parseInt(document.getElementById('boq-conv-duration').value) || 0,
        quota: parseInt(document.getElementById('boq-conv-quota').value) || null,
        hotel_mekkah: document.getElementById('boq-conv-hotel-mekkah').value.trim() || null,
        hotel_madinah: document.getElementById('boq-conv-hotel-madinah').value.trim() || null,
        route_type: document.getElementById('boq-conv-route').value,
        default_commission_fee: parseInt(document.getElementById('boq-conv-commission').value) || 0,
    };
    if (!body.name) return _toast('Nama paket wajib.', 'error');
    if (!body.departure_date) return _toast('Tanggal keberangkatan wajib.', 'error');
    if (body.duration <= 0) return _toast('Durasi (hari) wajib > 0.', 'error');
    try {
        const r = await authFetch(`/boq/${__boqCurrentDetail.id}/convert-to-package`,
            { method: 'POST', body: JSON.stringify(body) });
        const j = await r.json();
        _toast(`Paket #${j.package_id} berhasil dibuat dari BOQ ini.`, 'success');
        closeModal('modal-boq-convert');
        closeModal('modal-boq-detail');
        await fetchBoqList();
    } catch (e) {
        _toast('Convert gagal: ' + (e.message || e), 'error');
    }
}


async function duplicateBoq(bid) {
    const b = __boqList.find(x => x.id === bid) || __boqCurrentDetail;
    const name = prompt('Nama BOQ hasil duplikat:', (b?.name || 'BOQ') + ' (copy)');
    if (!name) return;
    try {
        const r = await authFetch(`/boq/${bid}/duplicate`,
            { method: 'POST', body: JSON.stringify({ name }) });
        const j = await r.json();
        _toast('Duplicated ke BOQ Draft baru #' + j.id, 'success');
        closeModal('modal-boq-detail');
        await fetchBoqList();
    } catch (e) {
        _toast('Duplicate gagal: ' + (e.message || e), 'error');
    }
}

async function deleteBoq(bid) {
    if (!confirm('Hapus BOQ ini? Semua line items ikut ke-hapus.')) return;
    try {
        await authFetch(`/boq/${bid}`, { method: 'DELETE' });
        _toast('BOQ dihapus.', 'success');
        await fetchBoqList();
    } catch (e) {
        _toast('Hapus gagal: ' + (e.message || e), 'error');
    }
}


/* ============================================================================
 * Phase 3a: Compare modal (side-by-side)
 * ==========================================================================*/
async function openCompareBoqModal() {
    const ids = Array.from(__boqSelectedIds);
    if (ids.length < 2) return _toast('Pilih minimal 2 BOQ dulu.', 'error');
    if (ids.length > 5) return _toast('Maksimal 5 BOQ per compare.', 'error');
    try {
        const r = await authFetch(`/boq/compare?ids=${ids.join(',')}`);
        const j = await r.json();
        if (!j || !j.boqs) throw new Error('Response invalid.');
        _renderCompareBody(j);
        openModal('modal-boq-compare');
    } catch (e) {
        _toast('Gagal load compare: ' + (e.message || e), 'error');
    }
}

function _renderCompareBody(data) {
    const boqs = data.boqs || [];
    const badge = document.getElementById('boq-cmp-count-badge');
    if (badge) badge.innerText = `${boqs.length} BOQ`;

    // Warn kalau cross-package (bukan apples-to-apples).
    const warn = document.getElementById('boq-cmp-warn');
    if (warn) {
        if (!data.same_package) {
            warn.classList.remove('hidden');
            warn.innerHTML = `<i data-lucide="alert-triangle" class="w-3.5 h-3.5 inline"></i>
                Cross-package compare: BOQ berasal dari paket berbeda (atau tanpa paket). Bandingkan angka dgn hati-hati -- durasi/kualitas hotel bisa beda.`;
        } else {
            warn.classList.add('hidden');
        }
    }

    // Kumpulkan semua (category, item_name) key across BOQs, urut per kategori.
    const keyMap = new Map();   // "category|item_name" -> {category, item_name, order}
    let order = 0;
    for (const cat of BOQ_CATEGORIES) {
        for (const b of boqs) {
            for (const it of (b.items || [])) {
                if (it.category !== cat) continue;
                const k = `${it.category}|${(it.item_name || '').trim().toLowerCase()}`;
                if (!keyMap.has(k)) {
                    keyMap.set(k, { category: it.category, item_name: it.item_name, order: order++ });
                }
            }
        }
    }
    const rows = Array.from(keyMap.values());

    // Lookup subtotal per BOQ per key.
    const subtotalFor = (b, cat, itemName) => {
        const key = (itemName || '').trim().toLowerCase();
        const hits = (b.items || []).filter(it => it.category === cat && (it.item_name || '').trim().toLowerCase() === key);
        if (!hits.length) return null;
        // Kalau ada > 1 item dengan nama sama (jarang), jumlahkan subtotal.
        return hits.reduce((s, it) => s + (it.subtotal || 0), 0);
    };

    // Cheapest price/pax utk highlight winner.
    const prices = boqs.map(b => (b.totals || {}).price_per_pax || 0).filter(p => p > 0);
    const bestPrice = prices.length ? Math.min(...prices) : null;

    // Group rows by category utk render header per section.
    const rowsByCat = new Map();
    for (const r of rows) {
        if (!rowsByCat.has(r.category)) rowsByCat.set(r.category, []);
        rowsByCat.get(r.category).push(r);
    }

    const colWidth = `${100 / (boqs.length + 1)}%`;
    let html = `<table class="w-full border text-xs">
        <thead class="bg-indigo-50 text-indigo-900 sticky top-0">
            <tr>
                <th class="border px-2 py-2 text-left" style="width:${colWidth}">Item / Kategori</th>
                ${boqs.map(b => {
                    const st = BOQ_STATUS_BADGE[b.status] || BOQ_STATUS_BADGE.Draft;
                    return `<th class="border px-2 py-2 text-left align-top" style="width:${colWidth}">
                        <div class="font-bold text-sm text-gray-800">${_esc(b.name)}</div>
                        <div class="text-[10px] text-gray-500 font-normal mt-0.5">
                            ${b.package_name ? _esc(b.package_name) : '<i>tanpa paket</i>'}
                        </div>
                        <div class="mt-1 flex flex-wrap items-center gap-1">
                            <span class="inline-flex items-center gap-1 px-1.5 py-0.5 text-[9px] rounded ${st.cls}">${_esc(b.status)}</span>
                            <span class="text-[10px] text-gray-500">${b.target_pax || 0} pax &middot; margin ${b.target_margin_pct || 0}%</span>
                        </div>
                    </th>`;
                }).join('')}
            </tr>
        </thead>
        <tbody>`;

    for (const [cat, items] of rowsByCat) {
        html += `<tr class="bg-gray-100">
            <td class="border px-2 py-1 font-bold text-[11px] uppercase text-gray-600" colspan="${boqs.length + 1}">
                ${_esc(BOQ_CATEGORY_LABEL[cat] || cat)}
            </td>
        </tr>`;
        for (const row of items) {
            html += `<tr>
                <td class="border px-2 py-1 text-gray-700">${_esc(row.item_name)}</td>`;
            const subs = boqs.map(b => subtotalFor(b, row.category, row.item_name));
            const present = subs.filter(s => s !== null);
            const minSub = present.length ? Math.min(...present) : null;
            const maxSub = present.length ? Math.max(...present) : null;
            for (const s of subs) {
                if (s === null) {
                    html += `<td class="border px-2 py-1 text-center text-gray-300">-</td>`;
                } else {
                    let cls = 'text-gray-800';
                    if (present.length > 1 && s === minSub && minSub !== maxSub) cls = 'text-emerald-700 font-bold';
                    else if (present.length > 1 && s === maxSub && minSub !== maxSub) cls = 'text-red-700';
                    html += `<td class="border px-2 py-1 text-right tabular-nums ${cls}">${formatRp(s)}</td>`;
                }
            }
            html += `</tr>`;
        }
    }

    // Footer totals.
    const totalRow = (label, keyGetter, isPrice) => {
        let row = `<tr class="${isPrice ? 'bg-emerald-50 font-bold text-sm' : 'bg-gray-50'}">
            <td class="border px-2 py-1.5">${label}</td>`;
        for (const b of boqs) {
            const val = keyGetter(b.totals || {}) || 0;
            const isBest = isPrice && bestPrice !== null && val === bestPrice && boqs.length > 1;
            const cls = isBest ? 'text-emerald-700' : (isPrice ? 'text-gray-800' : 'text-gray-600');
            row += `<td class="border px-2 py-1.5 text-right tabular-nums ${cls}">
                ${formatRp(val)} ${isBest ? '<span class="text-[10px] ml-1">🏆</span>' : ''}
            </td>`;
        }
        return row + `</tr>`;
    };
    html += `<tr><td colspan="${boqs.length + 1}" class="border-t-2 border-gray-400"></td></tr>`;

    // Phase 6c: bucket breakdown per pax sebelum totals klasik.
    html += `<tr class="bg-slate-100">
        <td colspan="${boqs.length + 1}" class="border px-2 py-1 font-bold text-[11px] uppercase text-slate-700">
            Breakdown per pax (Phase 6c buckets)
        </td>
    </tr>`;
    const bucketRow = (bkt) => {
        const s = BOQ_BUCKET_STYLE[bkt];
        const vals = boqs.map(b => b.totals?.buckets?.[bkt]?.per_pax || 0);
        const present = vals.filter(v => v > 0);
        const min = present.length ? Math.min(...present) : null;
        const max = present.length ? Math.max(...present) : null;
        let row = `<tr>
            <td class="border px-2 py-1">
                <span class="px-1.5 py-0.5 rounded ${s.cls} text-[10px] font-bold">${BOQ_BUCKET_LABEL[bkt]}</span>
            </td>`;
        for (const v of vals) {
            let cls = 'text-gray-800';
            if (present.length > 1 && v === min && min !== max && v > 0) cls = 'text-emerald-700 font-semibold';
            else if (present.length > 1 && v === max && min !== max) cls = 'text-red-700';
            else if (v === 0) cls = 'text-gray-300';
            row += `<td class="border px-2 py-1 text-right tabular-nums ${cls}">${formatRp(v)}</td>`;
        }
        return row + `</tr>`;
    };
    for (const bkt of BOQ_BUCKETS) html += bucketRow(bkt);

    html += `<tr><td colspan="${boqs.length + 1}" class="border-t-2 border-gray-300"></td></tr>`;
    html += totalRow('Total Group Cost', t => t.total_group_cost);
    html += totalRow('Cost per pax', t => t.cost_per_pax);
    html += totalRow('Margin', t => t.margin_amount);
    html += totalRow('Harga QUAD /pax', t => t.price_quad, true);
    html += totalRow('Harga TRIPLE /pax', t => t.price_triple, false);
    html += totalRow('Harga DOUBLE /pax', t => t.price_double, false);
    html += `</tbody></table>`;

    if (!rows.length) {
        html = `<div class="text-center text-gray-400 py-8">BOQ yang dipilih belum punya line item -- tambahkan item dulu untuk bisa dibandingkan.</div>`;
    }
    document.getElementById('boq-cmp-body').innerHTML = html;
    if (typeof lucide !== 'undefined') lucide.createIcons();
}


/* ============================================================================
 * Phase 3b: BOQ Templates (preset line items)
 * ==========================================================================*/
let __boqTmplList = [];
let __boqTmplFormItems = [];

async function openBoqTemplateManager() {
    try {
        const r = await authFetch('/boq/templates');
        __boqTmplList = await r.json() || [];
    } catch (e) { __boqTmplList = []; }
    _renderTmplList();
    openModal('modal-boq-tmpl-manager');
}

function _renderTmplList() {
    const tbody = document.getElementById('boq-tmpl-list');
    if (!tbody) return;
    if (!__boqTmplList.length) {
        tbody.innerHTML = `<tr><td colspan="4" class="px-4 py-8 text-center text-gray-400 text-sm">
            Belum ada template. Klik <b>+ Template Baru</b> untuk buat preset line items.</td></tr>`;
        return;
    }
    tbody.innerHTML = __boqTmplList.map(t => `
        <tr class="hover:bg-slate-50">
            <td class="px-3 py-2">
                <div class="font-semibold text-gray-800">${_esc(t.name)}</div>
                <div class="text-[11px] text-gray-500">${_esc(t.description || '')}</div>
            </td>
            <td class="px-3 py-2 text-center text-sm">${t.item_count || 0}</td>
            <td class="px-3 py-2 text-[11px] text-gray-500">
                ${_esc(t.created_by_name || '-')}<br>
                ${_esc((t.created_at || '').slice(0, 16))}
            </td>
            <td class="px-3 py-2 text-right whitespace-nowrap">
                <button onclick="openBoqTemplateForm(${t.id})" class="text-xs px-2 py-1 rounded bg-gray-100 text-gray-700 hover:bg-gray-200" title="Edit"><i data-lucide="edit-2" class="w-3.5 h-3.5"></i></button>
                <button onclick="deleteBoqTemplate(${t.id})" class="text-xs px-2 py-1 rounded bg-red-50 text-red-700 hover:bg-red-100" title="Hapus"><i data-lucide="trash-2" class="w-3.5 h-3.5"></i></button>
            </td>
        </tr>`).join('');
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

async function openBoqTemplateForm(tid) {
    if (tid) {
        // Edit mode: fetch detail.
        try {
            const r = await authFetch(`/boq/templates/${tid}`);
            const t = await r.json();
            document.getElementById('boq-tmpl-form-title').innerText = 'Edit Template';
            document.getElementById('boq-tmpl-form-id').value = tid;
            document.getElementById('boq-tmpl-form-name').value = t.name || '';
            document.getElementById('boq-tmpl-form-desc').value = t.description || '';
            __boqTmplFormItems = (t.items || []).map(it => ({
                category: it.category, item_name: it.item_name, unit: it.unit,
                quantity: it.quantity, unit_price: it.unit_price,
                vendor_name: it.vendor_name || '', note: it.note || '',
                // Phase 6d-c: bucket + sell_price + variant + optional.
                bucket: it.bucket || 'hpp',
                sell_price: it.sell_price,
                variant: it.variant || '',
                optional: !!it.optional,
            }));
        } catch (e) { return _toast('Gagal load template: ' + e.message, 'error'); }
    } else {
        document.getElementById('boq-tmpl-form-title').innerText = 'Template Baru';
        document.getElementById('boq-tmpl-form-id').value = '';
        document.getElementById('boq-tmpl-form-name').value = '';
        document.getElementById('boq-tmpl-form-desc').value = '';
        __boqTmplFormItems = [_blankTmplItem()];
    }
    _renderTmplFormItems();
    openModal('modal-boq-tmpl-form');
}

// Phase 6d-c: template blank item includes sell_price/variant/optional fields.
function _blankTmplItem() {
    return {
        bucket: 'hpp', category: 'hotel_mekkah', item_name: '', unit: 'per_pax',
        quantity: 1, unit_price: 0, vendor_name: '', note: '',
        sell_price: null, variant: '', optional: false,
    };
}
function addTmplFormItem() { __boqTmplFormItems.push(_blankTmplItem()); _renderTmplFormItems(); }
function removeTmplFormItem(idx) { __boqTmplFormItems.splice(idx, 1); _renderTmplFormItems(); }
function _updateTmplFormItem(idx, field, value) {
    if (!__boqTmplFormItems[idx]) return;
    if (['quantity', 'unit_price'].includes(field)) value = parseFloat(value) || 0;
    if (field === 'sell_price') value = value === '' ? null : parseFloat(value) || 0;
    if (field === 'optional') value = !!value;
    __boqTmplFormItems[idx][field] = value;
}
function _renderTmplFormItems() {
    const tbody = document.getElementById('boq-tmpl-form-items');
    if (!tbody) return;
    tbody.innerHTML = __boqTmplFormItems.map((it, idx) => {
        const bkt = it.bucket || 'hpp';
        const bStyle = (typeof BOQ_BUCKET_STYLE !== 'undefined')
            ? (BOQ_BUCKET_STYLE[bkt] || BOQ_BUCKET_STYLE.hpp)
            : { cls: '' };
        return `<tr>
        <td class="px-1 py-1">
            <select class="w-full border rounded px-1 py-1 text-xs ${bStyle.cls}" onchange="_updateTmplFormItem(${idx},'bucket',this.value)" title="Bucket target">
                ${BOQ_BUCKETS.map(b => `<option value="${b}" ${bkt===b?'selected':''}>${BOQ_BUCKET_LABEL[b]}</option>`).join('')}
            </select>
        </td>
        <td class="px-1 py-1">
            <select class="w-full border rounded px-1 py-1 text-xs" onchange="_updateTmplFormItem(${idx},'category',this.value)">
                ${BOQ_CATEGORIES.map(c => `<option value="${c}" ${it.category===c?'selected':''}>${BOQ_CATEGORY_LABEL[c]}</option>`).join('')}
            </select>
        </td>
        <td class="px-1 py-1"><input type="text" value="${_esc(it.item_name)}" class="w-full border rounded px-2 py-1 text-xs" placeholder="Nama item" oninput="_updateTmplFormItem(${idx},'item_name',this.value)"/></td>
        <td class="px-1 py-1"><input type="text" value="${_esc(it.variant || '')}" class="w-full border rounded px-1 py-1 text-xs" placeholder="Minimalis / Full Set / dst" oninput="_updateTmplFormItem(${idx},'variant',this.value)" title="Subcategory variant"/></td>
        <td class="px-1 py-1">
            <select class="w-full border rounded px-1 py-1 text-xs" onchange="_updateTmplFormItem(${idx},'unit',this.value)">
                ${BOQ_UNITS.map(u => `<option value="${u.value}" ${it.unit===u.value?'selected':''}>${u.label}</option>`).join('')}
            </select>
        </td>
        <td class="px-1 py-1"><input type="number" step="0.01" value="${it.quantity}" class="w-14 border rounded px-1 py-1 text-xs text-right" oninput="_updateTmplFormItem(${idx},'quantity',this.value)"/></td>
        <td class="px-1 py-1"><input type="number" value="${it.unit_price}" class="w-24 border rounded px-1 py-1 text-xs text-right" oninput="_updateTmplFormItem(${idx},'unit_price',this.value)" title="HPP / biaya"/></td>
        <td class="px-1 py-1"><input type="number" value="${it.sell_price ?? ''}" class="w-24 border rounded px-1 py-1 text-xs text-right bg-emerald-50" oninput="_updateTmplFormItem(${idx},'sell_price',this.value)" placeholder="opsional" title="Harga jual per item -- kalau > HPP, saat apply akan auto-buat margin item"/></td>
        <td class="px-1 py-1 text-center"><input type="checkbox" ${it.optional ? 'checked' : ''} onchange="_updateTmplFormItem(${idx},'optional',this.checked)" title="Kalau di-check, item ini opsional saat template di-apply"/></td>
        <td class="px-1 py-1"><input type="text" value="${_esc(it.vendor_name || '')}" class="w-full border rounded px-1 py-1 text-xs" placeholder="opsional" oninput="_updateTmplFormItem(${idx},'vendor_name',this.value)"/></td>
        <td class="px-1 py-1 text-center"><button type="button" onclick="removeTmplFormItem(${idx})" class="text-red-600 hover:text-red-800 text-xs"><i data-lucide="x" class="w-3.5 h-3.5"></i></button></td>
    </tr>`;
    }).join('') || `<tr><td colspan="11" class="text-center text-xs text-gray-400 py-3">Belum ada item.</td></tr>`;
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

async function saveBoqTemplate() {
    const tid = document.getElementById('boq-tmpl-form-id').value;
    const name = document.getElementById('boq-tmpl-form-name').value.trim();
    if (!name) return _toast('Nama template wajib diisi.', 'error');
    // Phase 6d-c: serialize new fields (bucket, sell_price, variant, optional).
    const items = (__boqTmplFormItems || [])
        .filter(it => it.item_name && it.category)
        .map(it => ({
            bucket: it.bucket || 'hpp',
            category: it.category,
            item_name: it.item_name,
            unit: it.unit,
            quantity: it.quantity,
            unit_price: it.unit_price,
            vendor_name: it.vendor_name || '',
            note: it.note || '',
            sell_price: it.sell_price != null && it.sell_price !== '' ? it.sell_price : null,
            variant: it.variant || null,
            optional: it.optional ? 1 : 0,
        }));
    const body = {
        name, description: document.getElementById('boq-tmpl-form-desc').value, items,
    };
    try {
        const path = tid ? `/boq/templates/${tid}` : '/boq/templates';
        const method = tid ? 'PUT' : 'POST';
        await authFetch(path, { method, body: JSON.stringify(body) });
        _toast('Template tersimpan.', 'success');
        closeModal('modal-boq-tmpl-form');
        await openBoqTemplateManager();  // refresh list
    } catch (e) {
        _toast('Simpan gagal: ' + (e.message || e), 'error');
    }
}

async function deleteBoqTemplate(tid) {
    if (!confirm('Hapus template ini? Aksi tidak bisa dibatalkan.')) return;
    try {
        await authFetch(`/boq/templates/${tid}`, { method: 'DELETE' });
        _toast('Template dihapus.', 'success');
        await openBoqTemplateManager();
    } catch (e) {
        _toast('Hapus gagal: ' + (e.message || e), 'error');
    }
}


/* --- Picker (dipakai dari Add BOQ modal utk load items) --- */
async function openTemplatePicker() {
    let list = [];
    try {
        const r = await authFetch('/boq/templates');
        list = await r.json() || [];
    } catch (e) { return _toast('Gagal load template: ' + e.message, 'error'); }
    const box = document.getElementById('boq-tmpl-picker-list');
    if (!list.length) {
        box.innerHTML = `<div class="text-center text-gray-400 py-8 text-sm">
            Belum ada template. ${_isMgmt() ? 'Klik "Kelola Template" di halaman utama untuk buat preset.' : 'Minta mgmt/admin untuk bikin preset dulu.'}
        </div>`;
    } else {
        box.innerHTML = list.map(t => `
            <div class="border rounded p-3 flex items-center justify-between hover:bg-slate-50">
                <div>
                    <div class="font-semibold text-sm">${_esc(t.name)}</div>
                    <div class="text-xs text-gray-500">${_esc(t.description || '')} &middot; ${t.item_count || 0} item</div>
                </div>
                <button onclick="loadTemplateIntoBoqForm(${t.id})" class="px-3 py-1.5 bg-slate-700 hover:bg-slate-800 text-white rounded text-xs font-bold">Load</button>
            </div>`).join('');
    }
    openModal('modal-boq-tmpl-picker');
}

// Phase 6d-c: state utk template picker + optional checklist.
let __boqTmplLoadingId = null;    // template id yg sedang dipilih items-nya
let __boqTmplLoadingItems = [];   // items dari template (utk checklist render)

async function loadTemplateIntoBoqForm(tid) {
    const has = (__boqFormItems || []).some(it => it.item_name);
    if (has && !confirm('Load template = replace semua items yang sudah diinput. Yakin?')) return;
    try {
        const r = await authFetch(`/boq/templates/${tid}`);
        const t = await r.json();
        __boqTmplLoadingId = tid;
        __boqTmplLoadingItems = t.items || [];
        // Phase 6d-c: kalau template punya items optional, tampilkan checklist
        // supaya user pilih mana yg mau di-load. Kalau semua required, langsung
        // apply.
        const hasOptional = __boqTmplLoadingItems.some(i => i.optional);
        if (hasOptional) {
            _renderTmplLoadChecklist(t);
            closeModal('modal-boq-tmpl-picker');
            openModal('modal-boq-tmpl-checklist');
        } else {
            _applyLoadedTemplateItems(__boqTmplLoadingItems);
            closeModal('modal-boq-tmpl-picker');
            _toast(`${__boqTmplLoadingItems.length} item ter-load dari template "${t.name}".`, 'success');
        }
    } catch (e) {
        _toast('Load gagal: ' + (e.message || e), 'error');
    }
}

// Render checklist utk items dari template (kalau ada optional).
function _renderTmplLoadChecklist(tmpl) {
    const title = document.getElementById('boq-tmpl-checklist-title');
    if (title) title.innerText = `Pilih Items: ${tmpl.name}`;
    const body = document.getElementById('boq-tmpl-checklist-body');
    if (!body) return;
    body.innerHTML = __boqTmplLoadingItems.map((it, idx) => {
        const bkt = it.bucket || 'hpp';
        const bStyle = (typeof BOQ_BUCKET_STYLE !== 'undefined')
            ? (BOQ_BUCKET_STYLE[bkt] || BOQ_BUCKET_STYLE.hpp)
            : { cls: 'bg-gray-100 text-gray-700' };
        const req = !it.optional;
        const marginNote = (it.sell_price && it.sell_price > it.unit_price)
            ? ` + <b class="text-emerald-700">margin ${formatRp(it.sell_price - it.unit_price)}</b>`
            : '';
        return `<label class="flex items-start gap-2 p-2 border rounded hover:bg-slate-50 ${req ? 'bg-slate-50' : ''}">
            <input type="checkbox" data-tmpl-idx="${idx}" ${req ? 'checked disabled' : 'checked'} class="mt-0.5"/>
            <div class="flex-1">
                <div class="flex items-center gap-2">
                    <b class="text-sm">${_esc(it.item_name)}</b>
                    ${it.variant ? `<span class="px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 text-[10px]">${_esc(it.variant)}</span>` : ''}
                    <span class="px-1.5 py-0.5 rounded ${bStyle.cls} text-[10px] font-bold">${BOQ_BUCKET_LABEL[bkt]}</span>
                    ${req ? '<span class="text-[10px] px-1 py-0.5 bg-gray-200 text-gray-700 rounded">WAJIB</span>'
                          : '<span class="text-[10px] px-1 py-0.5 bg-blue-100 text-blue-700 rounded">Opsional</span>'}
                </div>
                <div class="text-[11px] text-gray-500">
                    ${_esc(BOQ_CATEGORY_LABEL[it.category] || it.category)}
                    &middot; ${_esc(it.unit)} &times; ${it.quantity}
                    &middot; HPP <b class="tabular-nums">${formatRp(it.unit_price)}</b>${marginNote}
                </div>
            </div>
        </label>`;
    }).join('');
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

// Confirm handler untuk checklist modal.
function confirmLoadTemplateWithChecklist() {
    const checks = document.querySelectorAll('#boq-tmpl-checklist-body input[type="checkbox"][data-tmpl-idx]');
    const chosenIdx = new Set();
    checks.forEach(c => { if (c.checked) chosenIdx.add(parseInt(c.dataset.tmplIdx)); });
    const chosen = __boqTmplLoadingItems.filter((_, i) => chosenIdx.has(i));
    if (!chosen.length) return _toast('Pilih minimal 1 item.', 'error');
    _applyLoadedTemplateItems(chosen);
    closeModal('modal-boq-tmpl-checklist');
    _toast(`${chosen.length} item ter-load ke BOQ form.`, 'success');
}

// Convert template items ke __boqFormItems dgn auto-margin split
// (mirror backend boq_template_apply Phase 6d-b).
function _applyLoadedTemplateItems(items) {
    const out = [];
    for (const it of items) {
        const hpp = parseFloat(it.unit_price) || 0;
        const sell = it.sell_price != null ? parseFloat(it.sell_price) : null;
        const bkt = it.bucket || 'hpp';
        // Row 1: item HPP.
        out.push({
            bucket: bkt, category: it.category, item_name: it.item_name,
            unit: it.unit, quantity: it.quantity || 1, unit_price: hpp,
            vendor_name: it.vendor_name || '', note: it.note || '',
        });
        // Row 2: margin item kalau sell > hpp.
        if (sell !== null && sell > hpp) {
            out.push({
                bucket: 'margin', category: it.category,
                item_name: `${it.item_name} (Margin)`,
                unit: it.unit, quantity: it.quantity || 1,
                unit_price: sell - hpp,
                vendor_name: it.vendor_name || '',
                note: `Auto dari template: sell ${sell} - HPP ${hpp}`,
            });
        }
    }
    __boqFormItems = out.length ? out : [_blankItem()];
    _renderBoqFormItems();
}


// ---------------------------------------------------------------------------
// Phase 6d-c: Import Preset Template
// ---------------------------------------------------------------------------
async function openPresetImportModal() {
    try {
        const r = await authFetch('/boq/templates/presets/available');
        const j = await r.json();
        const box = document.getElementById('boq-preset-list');
        if (!box) return _toast('Modal preset tidak ditemukan.', 'error');
        const presets = j.presets || [];
        if (!presets.length) {
            box.innerHTML = `<div class="text-center text-gray-400 py-6 text-sm">Belum ada preset tersedia.</div>`;
        } else {
            box.innerHTML = presets.map(p => `
                <div class="border rounded-lg p-4 hover:bg-emerald-50/40">
                    <div class="flex items-start justify-between gap-3">
                        <div class="flex-1">
                            <div class="font-bold text-sm text-slate-800">${_esc(p.name)}</div>
                            <div class="text-[11px] text-gray-500 mt-1">${_esc(p.description || '')}</div>
                            <div class="text-[10px] text-gray-400 mt-1">${p.item_count} item &middot; key: <code>${_esc(p.key)}</code></div>
                        </div>
                        <button onclick="importPresetTemplate('${_esc(p.key)}', ${JSON.stringify(p.name)})"
                                class="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded text-xs font-bold whitespace-nowrap">
                            <i data-lucide="download" class="w-3 h-3 inline"></i> Import
                        </button>
                    </div>
                </div>`).join('');
        }
        openModal('modal-boq-preset');
        if (typeof lucide !== 'undefined') lucide.createIcons();
    } catch (e) {
        _toast('Gagal load preset: ' + (e.message || e), 'error');
    }
}

async function importPresetTemplate(presetKey, defaultName) {
    const name = prompt(
        `Nama template baru dari preset "${presetKey}":`,
        defaultName || presetKey,
    );
    if (!name || !name.trim()) return;
    try {
        const r = await authFetch('/boq/templates/preset', {
            method: 'POST',
            body: JSON.stringify({ preset_key: presetKey, name: name.trim() }),
        });
        const j = await r.json();
        _toast(`Template "${name}" dibuat dengan ${j.items_created} item.`, 'success');
        closeModal('modal-boq-preset');
        await openBoqTemplateManager();
    } catch (e) {
        _toast('Import preset gagal: ' + (e.message || e), 'error');
    }
}


/* ============================================================================
 * util: escape + toast fallback
 * ==========================================================================*/
function _esc(s) {
    if (s === null || s === undefined) return '';
    return String(s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function _toast(msg, type) {
    if (typeof notyf !== 'undefined') {
        try { type === 'error' ? notyf.error(msg) : notyf.success(msg); return; } catch(e) {}
    }
    alert(msg);
}
