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
        return `<tr>
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
    }).join('') || `<tr><td colspan="7" class="text-center text-xs text-gray-400 py-3">Belum ada item. Klik + Tambah Item.</td></tr>`;
    _renderBoqFormTotals();
    if (typeof lucide !== 'undefined') lucide.createIcons();
}
function _renderBoqFormTotals() {
    const pax = Math.max(parseInt(document.getElementById('boq-form-pax').value) || 1, 1);
    const margin = parseFloat(document.getElementById('boq-form-margin').value) || 0;
    const et = parseInt(document.getElementById('boq-form-extra-triple')?.value) || 0;
    const ed = parseInt(document.getElementById('boq-form-extra-double')?.value) || 0;
    let totalGroup = 0;
    (__boqFormItems || []).forEach(it => {
        const sub = (it.quantity || 0) * (it.unit_price || 0);
        if (it.unit === 'per_pax' || it.unit === 'per_pax_per_day') totalGroup += sub * pax;
        else totalGroup += sub;
    });
    const costPerPax = Math.floor(totalGroup / pax);
    const marginAmt = Math.floor(costPerPax * (margin / 100));
    const priceQuad = costPerPax + marginAmt;
    const priceTriple = priceQuad + et;
    const priceDouble = priceQuad + ed;
    const el = document.getElementById('boq-form-totals');
    if (el) el.innerHTML = `
        <div><span class="text-gray-500">Total group:</span> <b class="tabular-nums">${formatRp(totalGroup)}</b></div>
        <div><span class="text-gray-500">Cost/pax:</span> <b class="tabular-nums">${formatRp(costPerPax)}</b></div>
        <div><span class="text-gray-500">Margin:</span> <b class="tabular-nums">${formatRp(marginAmt)}</b></div>
        <div class="text-emerald-700 border-l pl-3">
            <div class="text-[10px] text-gray-500">Harga per pax</div>
            <div class="tabular-nums text-sm">QUAD <b>${formatRp(priceQuad)}</b></div>
            <div class="tabular-nums text-sm">TRIPLE <b>${formatRp(priceTriple)}</b>${et ? '' : '<span class="text-[10px] text-gray-400 ml-1">(= QUAD)</span>'}</div>
            <div class="tabular-nums text-sm">DOUBLE <b>${formatRp(priceDouble)}</b>${ed ? '' : '<span class="text-[10px] text-gray-400 ml-1">(= QUAD)</span>'}</div>
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

    document.getElementById('boq-detail-header').innerHTML = `
        <div class="flex flex-wrap items-start gap-3 justify-between">
            <div>
                <h3 class="text-lg font-bold text-gray-800">${_esc(b.name)}</h3>
                <div class="text-xs text-gray-500 mt-1">
                    ${b.package_name ? _esc(b.package_name) : '<i>Belum terikat ke paket (BOQ paket baru)</i>'}
                    &middot; Target: ${b.target_pax} pax &middot; Margin: ${b.target_margin_pct}%
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

    const tbody = document.getElementById('boq-detail-items');
    tbody.innerHTML = (b.items || []).map(it => `
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
        </tr>
    `).join('') || `<tr><td colspan="8" class="text-center py-4 text-xs text-gray-400">Belum ada item.</td></tr>`;

    const t = b.totals || {};
    const hasSplit = (t.extra_triple || 0) > 0 || (t.extra_double || 0) > 0;
    document.getElementById('boq-detail-totals').innerHTML = `
        <div class="grid grid-cols-3 md:grid-cols-6 gap-2 text-xs">
            <div><span class="text-gray-500">Total group:</span> <b class="tabular-nums block">${formatRp(t.total_group_cost || 0)}</b></div>
            <div><span class="text-gray-500">Cost/pax:</span> <b class="tabular-nums block">${formatRp(t.cost_per_pax || 0)}</b></div>
            <div><span class="text-gray-500">Margin:</span> <b class="tabular-nums block">${formatRp(t.margin_amount || 0)}</b></div>
            <div class="text-emerald-700 col-span-3 md:col-span-3 border-l pl-3">
                <div class="text-[10px] text-gray-500 mb-0.5">Harga per pax ${hasSplit ? '(split per room type)' : '<span class="text-gray-400">(flat, semua room sama)</span>'}</div>
                <div class="grid grid-cols-3 gap-1 tabular-nums">
                    <div>QUAD<br><b>${formatRp(t.price_quad || 0)}</b></div>
                    <div>TRIPLE<br><b>${formatRp(t.price_triple || 0)}</b></div>
                    <div>DOUBLE<br><b>${formatRp(t.price_double || 0)}</b></div>
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
            }));
        } catch (e) { return _toast('Gagal load template: ' + e.message, 'error'); }
    } else {
        document.getElementById('boq-tmpl-form-title').innerText = 'Template Baru';
        document.getElementById('boq-tmpl-form-id').value = '';
        document.getElementById('boq-tmpl-form-name').value = '';
        document.getElementById('boq-tmpl-form-desc').value = '';
        __boqTmplFormItems = [_blankItem()];
    }
    _renderTmplFormItems();
    openModal('modal-boq-tmpl-form');
}

function addTmplFormItem() { __boqTmplFormItems.push(_blankItem()); _renderTmplFormItems(); }
function removeTmplFormItem(idx) { __boqTmplFormItems.splice(idx, 1); _renderTmplFormItems(); }
function _updateTmplFormItem(idx, field, value) {
    if (!__boqTmplFormItems[idx]) return;
    if (['quantity', 'unit_price'].includes(field)) value = parseFloat(value) || 0;
    __boqTmplFormItems[idx][field] = value;
}
function _renderTmplFormItems() {
    const tbody = document.getElementById('boq-tmpl-form-items');
    if (!tbody) return;
    tbody.innerHTML = __boqTmplFormItems.map((it, idx) => `<tr>
        <td class="px-1 py-1">
            <select class="w-full border rounded px-1 py-1 text-xs" onchange="_updateTmplFormItem(${idx},'category',this.value)">
                ${BOQ_CATEGORIES.map(c => `<option value="${c}" ${it.category===c?'selected':''}>${BOQ_CATEGORY_LABEL[c]}</option>`).join('')}
            </select>
        </td>
        <td class="px-1 py-1"><input type="text" value="${_esc(it.item_name)}" class="w-full border rounded px-2 py-1 text-xs" placeholder="Nama item" oninput="_updateTmplFormItem(${idx},'item_name',this.value)"/></td>
        <td class="px-1 py-1">
            <select class="w-full border rounded px-1 py-1 text-xs" onchange="_updateTmplFormItem(${idx},'unit',this.value)">
                ${BOQ_UNITS.map(u => `<option value="${u.value}" ${it.unit===u.value?'selected':''}>${u.label}</option>`).join('')}
            </select>
        </td>
        <td class="px-1 py-1"><input type="number" step="0.01" value="${it.quantity}" class="w-16 border rounded px-1 py-1 text-xs text-right" oninput="_updateTmplFormItem(${idx},'quantity',this.value)"/></td>
        <td class="px-1 py-1"><input type="number" value="${it.unit_price}" class="w-28 border rounded px-1 py-1 text-xs text-right" oninput="_updateTmplFormItem(${idx},'unit_price',this.value)"/></td>
        <td class="px-1 py-1"><input type="text" value="${_esc(it.vendor_name || '')}" class="w-full border rounded px-1 py-1 text-xs" placeholder="opsional" oninput="_updateTmplFormItem(${idx},'vendor_name',this.value)"/></td>
        <td class="px-1 py-1 text-center"><button type="button" onclick="removeTmplFormItem(${idx})" class="text-red-600 hover:text-red-800 text-xs"><i data-lucide="x" class="w-3.5 h-3.5"></i></button></td>
    </tr>`).join('') || `<tr><td colspan="7" class="text-center text-xs text-gray-400 py-3">Belum ada item.</td></tr>`;
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

async function saveBoqTemplate() {
    const tid = document.getElementById('boq-tmpl-form-id').value;
    const name = document.getElementById('boq-tmpl-form-name').value.trim();
    if (!name) return _toast('Nama template wajib diisi.', 'error');
    const items = (__boqTmplFormItems || []).filter(it => it.item_name && it.category);
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

async function loadTemplateIntoBoqForm(tid) {
    const has = (__boqFormItems || []).some(it => it.item_name);
    if (has && !confirm('Load template = replace semua items yang sudah diinput. Yakin?')) return;
    try {
        const r = await authFetch(`/boq/templates/${tid}`);
        const t = await r.json();
        __boqFormItems = (t.items || []).map(it => ({
            category: it.category, item_name: it.item_name, unit: it.unit,
            quantity: it.quantity, unit_price: it.unit_price,
            vendor_name: it.vendor_name || '', note: it.note || '',
        }));
        if (!__boqFormItems.length) __boqFormItems = [_blankItem()];
        _renderBoqFormItems();
        closeModal('modal-boq-tmpl-picker');
        _toast(`${t.items?.length || 0} item ter-load dari template "${t.name}".`, 'success');
    } catch (e) {
        _toast('Load gagal: ' + (e.message || e), 'error');
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
