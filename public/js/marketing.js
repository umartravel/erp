// Marketing Analytics dashboard -- heatmap Leaflet + 6 chart Chart.js.
// Palet: brand UMAR (Gold #F8BE20, Dark Charcoal #1D1D1B, Warm Gold #B8860B, Cream #F4F1EA).
// Sumber koordinat kota: dict statis (dari admin.js web-umar) supaya offline-safe.

const MKT_COLORS = {
  gold: '#F8BE20',
  darkGold: '#B8860B',
  goldSoft: '#FDE047',
  goldStroke: '#78350F',
  charcoal: '#1D1D1B',
  cream: '#F4F1EA',
  gray300: '#D1D5DB',
  gray600: '#4B5563',
  gray800: '#1F2937',
};

const INDONESIA_CITY_COORDINATES = {
  "JAKARTA": [-6.2088, 106.8456],
  "JAKARTA PUSAT": [-6.1818, 106.8223],
  "JAKARTA SELATAN": [-6.2615, 106.8106],
  "JAKARTA TIMUR": [-6.2250, 106.9004],
  "JAKARTA BARAT": [-6.1683, 106.7589],
  "JAKARTA UTARA": [-6.1384, 106.8640],
  "KEPULAUAN SERIBU": [-5.6122, 106.5622],
  "TANGERANG": [-6.1783, 106.6319],
  "TANGERANG SELATAN": [-6.2888, 106.7179],
  "KABUPATEN TANGERANG": [-6.1964, 106.5200],
  "SERANG": [-6.1104, 106.1640],
  "CILEGON": [-6.0028, 106.0125],
  "PANDEGLANG": [-6.5083, 105.8447],
  "LEBAK": [-6.6496, 106.2163],
  "BEKASI": [-6.2383, 106.9756],
  "KABUPATEN BEKASI": [-6.2649, 107.1352],
  "BOGOR": [-6.5971, 106.8060],
  "KABUPATEN BOGOR": [-6.5518, 106.6291],
  "DEPOK": [-6.4025, 106.7942],
  "BANDUNG": [-6.9175, 107.6191],
  "KABUPATEN BANDUNG": [-7.0253, 107.5198],
  "BANDUNG BARAT": [-6.8436, 107.4947],
  "CIMAHI": [-6.8723, 107.5420],
  "CIREBON": [-6.7320, 108.5523],
  "KABUPATEN CIREBON": [-6.7667, 108.4833],
  "INDRAMAYU": [-6.3264, 108.3200],
  "SUKABUMI": [-6.9277, 106.9299],
  "KABUPATEN SUKABUMI": [-7.0500, 106.7000],
  "KARAWANG": [-6.3042, 107.3075],
  "SUBANG": [-6.5716, 107.7587],
  "PURWAKARTA": [-6.5569, 107.4433],
  "GARUT": [-7.2278, 107.9086],
  "TASIKMALAYA": [-7.3274, 108.2207],
  "CIAMIS": [-7.3264, 108.3542],
  "KUNINGAN": [-6.9764, 108.4831],
  "MAJALENGKA": [-6.8361, 108.2278],
  "SUMEDANG": [-6.8586, 107.9267],
  "CIANJUR": [-6.8222, 107.1394],
  "BANJAR": [-7.3742, 108.5339],
  "PANGANDARAN": [-7.7025, 108.4950],
  "SEMARANG": [-6.9667, 110.4167],
  "KABUPATEN SEMARANG": [-7.2086, 110.4381],
  "SURAKARTA": [-7.5755, 110.8243],
  "SOLO": [-7.5755, 110.8243],
  "MAGELANG": [-7.4797, 110.2177],
  "KABUPATEN MAGELANG": [-7.4706, 110.2178],
  "YOGYAKARTA": [-7.7956, 110.3695],
  "SLEMAN": [-7.7156, 110.3556],
  "BANTUL": [-7.8920, 110.3340],
  "GUNUNGKIDUL": [-7.9620, 110.6033],
  "KULON PROGO": [-7.7960, 110.1584],
  "WONOGIRI": [-7.8186, 110.9254],
  "DEMAK": [-6.8906, 110.6394],
  "KUDUS": [-6.8048, 110.8405],
  "JEPARA": [-6.5925, 110.6775],
  "PATI": [-6.7558, 111.0378],
  "REMBANG": [-6.7078, 111.3411],
  "BLORA": [-6.9697, 111.4186],
  "GROBOGAN": [-7.0286, 110.9167],
  "PEKALONGAN": [-6.8886, 109.6753],
  "BATANG": [-6.9083, 109.7333],
  "PEMALANG": [-6.8911, 109.3808],
  "TEGAL": [-6.8694, 109.1402],
  "KABUPATEN TEGAL": [-6.9829, 109.1402],
  "BREBES": [-6.8706, 109.0417],
  "BANYUMAS": [-7.5167, 109.2944],
  "PURWOKERTO": [-7.4244, 109.2303],
  "CILACAP": [-7.7028, 109.0153],
  "PURBALINGGA": [-7.3894, 109.3639],
  "BANJARNEGARA": [-7.3975, 109.6978],
  "KEBUMEN": [-7.6686, 109.6517],
  "PURWOREJO": [-7.7144, 110.0089],
  "WONOSOBO": [-7.3628, 109.9078],
  "TEMANGGUNG": [-7.3167, 110.1778],
  "KENDAL": [-6.9247, 110.2036],
  "SALATIGA": [-7.3305, 110.5084],
  "BOYOLALI": [-7.5333, 110.5944],
  "KLATEN": [-7.7056, 110.6044],
  "SUKOHARJO": [-7.6833, 110.8333],
  "KARANGANYAR": [-7.5967, 110.9511],
  "SRAGEN": [-7.4267, 111.0222],
  "SURABAYA": [-7.2575, 112.7521],
  "SIDOARJO": [-7.4478, 112.7183],
  "GRESIK": [-7.1564, 112.6556],
  "MOJOKERTO": [-7.4722, 112.4339],
  "PASURUAN": [-7.6453, 112.9075],
  "PROBOLINGGO": [-7.7544, 113.2158],
  "MALANG": [-7.9666, 112.6326],
  "KABUPATEN MALANG": [-8.1667, 112.6667],
  "BATU": [-7.8711, 112.5269],
  "JEMBER": [-8.1845, 113.6681],
  "BANYUWANGI": [-8.2192, 114.3692],
  "BONDOWOSO": [-7.9136, 113.8211],
  "SITUBONDO": [-7.7064, 114.0047],
  "LUMAJANG": [-8.1333, 113.2167],
  "KEDIRI": [-7.8167, 112.0167],
  "BLITAR": [-8.0983, 112.1681],
  "TULUNGAGUNG": [-8.0667, 111.9000],
  "TRENGGALEK": [-8.0500, 111.7167],
  "NGANJUK": [-7.6047, 111.9042],
  "MADIUN": [-7.6297, 111.5239],
  "MAGETAN": [-7.6500, 111.3333],
  "NGAWI": [-7.4042, 111.4456],
  "PONOROGO": [-7.8667, 111.4667],
  "PACITAN": [-8.1969, 111.1072],
  "BOJONEGORO": [-7.1500, 111.8833],
  "TUBAN": [-6.8978, 112.0650],
  "LAMONGAN": [-7.1194, 112.4153],
  "BANGKALAN": [-7.0306, 112.7483],
  "SAMPANG": [-7.1878, 113.2431],
  "PAMEKASAN": [-7.1614, 113.4828],
  "SUMENEP": [-7.0167, 113.8667],
  "MEDAN": [3.5952, 98.6722],
  "DELI SERDANG": [3.5500, 98.6833],
  "PALEMBANG": [-2.9761, 104.7754],
  "BANYUASIN": [-2.8833, 104.3833],
  "OGAN ILIR": [-3.4333, 104.6000],
  "BANDAR LAMPUNG": [-5.4292, 105.2625],
  "LAMPUNG SELATAN": [-5.7000, 105.6000],
  "PADANG": [-0.9471, 100.4172],
  "BUKITTINGGI": [-0.3056, 100.3692],
  "PAYAKUMBUH": [-0.2247, 100.6308],
  "PEKANBARU": [0.5071, 101.4478],
  "DUMAI": [1.6667, 101.4500],
  "BATAM": [1.1301, 104.0529],
  "TANJUNGPINANG": [0.9167, 104.4500],
  "JAMBI": [-1.6101, 103.6131],
  "BENGKULU": [-3.8004, 102.2655],
  "BANDA ACEH": [5.5483, 95.3238],
  "PANGKALPINANG": [-2.1333, 106.1167],
  "PONTIANAK": [-0.0263, 109.3425],
  "BANJARMASIN": [-3.3194, 114.5908],
  "BANJARBARU": [-3.4400, 114.8300],
  "BALIKPAPAN": [-1.2379, 116.8529],
  "SAMARINDA": [-0.5022, 117.1536],
  "PALANGKARAYA": [-2.2167, 113.9167],
  "KOTAWARINGIN BARAT": [-2.6837, 111.6167],
  "TARAKAN": [3.3000, 117.6333],
  "MAKASSAR": [-5.1477, 119.4327],
  "GOWA": [-5.3302, 119.7431],
  "MAROS": [-5.0000, 119.5667],
  "PAREPARE": [-4.0133, 119.6253],
  "PALU": [-0.8917, 119.8707],
  "MANADO": [1.4748, 124.8421],
  "KENDARI": [-3.9985, 122.5126],
  "GORONTALO": [0.5435, 123.0568],
  "DENPASAR": [-8.6705, 115.2126],
  "BADUNG": [-8.5833, 115.1833],
  "MATARAM": [-8.5833, 116.1167],
  "SUMBAWA": [-8.4932, 117.4200],
  "LOMBOK TIMUR": [-8.5500, 116.5500],
  "BIMA": [-8.4608, 118.7275],
  "KUPANG": [-10.1772, 123.6070],
  "AMBON": [-3.6547, 128.1906],
  "TERNATE": [0.7833, 127.3833],
  "JAYAPURA": [-2.5916, 140.6690],
  "SORONG": [-0.8833, 131.2500],
};

const INDONESIA_PROVINCE_COORDINATES = {
  "DKI JAKARTA": [-6.2088, 106.8456],
  "BANTEN": [-6.4058, 106.0640],
  "JAWA BARAT": [-6.9175, 107.6191],
  "JAWA TENGAH": [-7.1509, 110.1402],
  "DI YOGYAKARTA": [-7.8754, 110.4262],
  "D.I. YOGYAKARTA": [-7.8754, 110.4262],
  "JAWA TIMUR": [-7.5361, 112.2384],
  "SUMATERA SELATAN": [-3.3194, 104.1698],
  "LAMPUNG": [-4.5586, 105.4068],
  "SUMATERA UTARA": [2.1154, 99.5451],
  "SUMATERA BARAT": [-0.7399, 100.8000],
  "RIAU": [0.2933, 101.7068],
  "KEPULAUAN RIAU": [3.9456, 108.1429],
  "JAMBI": [-1.4852, 102.4380],
  "BENGKULU": [-3.5778, 102.3464],
  "ACEH": [4.6951, 96.7494],
  "KEPULAUAN BANGKA BELITUNG": [-2.7410, 106.4406],
  "KALIMANTAN BARAT": [-0.2787, 111.4753],
  "KALIMANTAN SELATAN": [-3.0926, 115.2838],
  "KALIMANTAN TIMUR": [0.5387, 116.4194],
  "KALIMANTAN TENGAH": [-1.6815, 113.3824],
  "KALIMANTAN UTARA": [3.0731, 116.0414],
  "SULAWESI SELATAN": [-3.6687, 119.9740],
  "SULAWESI TENGAH": [-1.4300, 121.4456],
  "SULAWESI UTARA": [0.6247, 123.9750],
  "SULAWESI TENGGARA": [-4.1449, 122.1746],
  "GORONTALO": [0.6999, 122.4467],
  "SULAWESI BARAT": [-2.8441, 119.2321],
  "BALI": [-8.4095, 115.1889],
  "NUSA TENGGARA BARAT": [-8.6529, 117.3616],
  "NUSA TENGGARA TIMUR": [-8.6574, 121.0794],
  "MALUKU": [-3.2385, 130.1453],
  "MALUKU UTARA": [1.5709, 127.8087],
  "PAPUA": [-4.2699, 138.0804],
};

function getCityCoordinates(kabKota = '', provinsi = '') {
  let clean = (kabKota || '').toUpperCase()
    .replace(/KOTA ADMINISTRASI/g, '')
    .replace(/KOTA/g, '')
    .replace(/KABUPATEN/g, '')
    .replace(/KAB\./g, '')
    .replace(/ADM\./g, '')
    .replace(/[^A-Z\s]/g, '')
    .trim();

  if (INDONESIA_CITY_COORDINATES[clean]) return INDONESIA_CITY_COORDINATES[clean];

  for (const key of Object.keys(INDONESIA_CITY_COORDINATES)) {
    if (clean && (clean.includes(key) || key.includes(clean))) {
      return INDONESIA_CITY_COORDINATES[key];
    }
  }

  const provClean = (provinsi || '').toUpperCase().trim();
  if (INDONESIA_PROVINCE_COORDINATES[provClean]) return INDONESIA_PROVINCE_COORDINATES[provClean];

  return [-2.5489, 118.0149]; // center of Indonesia
}

function formatRupiah(amount) {
  if (isNaN(amount) || amount == null) return 'Rp 0';
  return new Intl.NumberFormat('id-ID', { style: 'currency', currency: 'IDR', maximumFractionDigits: 0 }).format(amount);
}

// -------- State --------
const MKT = {
  filters: { period: '', paket: '', admin: '', channel: '', statpay: '' },
  charts: {},
  mapJamaah: null,
  mapJamaahLayer: null,
  mapAgen: null,
  mapAgenLayer: null,
  initialized: false,
};

// -------- Fetch helpers (pakai authFetch dari index.html) --------
async function mktFetch(path, opts = {}) {
  const fn = window.authFetch || fetch;
  return fn(path, opts);
}

// -------- Init (dipanggil dari showPage('marketing')) --------
async function initMarketingPage() {
  if (!MKT.initialized) {
    await loadFilterOptions();
    wireFilterHandlers();
    MKT.initialized = true;
  }
  await refreshMarketing();
}

async function loadFilterOptions() {
  try {
    const res = await mktFetch('/api/marketing/filters');
    const data = await res.json();
    const fill = (id, arr, labelPrefix = '') => {
      const el = document.getElementById(id);
      if (!el) return;
      el.innerHTML = '<option value="">-- Semua --</option>' +
        arr.map(v => `<option value="${v}">${labelPrefix}${v}</option>`).join('');
    };
    fill('mkt-filter-period', data.period || []);
    fill('mkt-filter-paket', data.paket || []);
    fill('mkt-filter-admin', data.admin || []);
    fill('mkt-filter-channel', data.channel || []);
    fill('mkt-filter-statpay', data.statpay || []);
  } catch (e) {
    console.error('[marketing] gagal load filter options', e);
  }
}

function wireFilterHandlers() {
  ['mkt-filter-period', 'mkt-filter-paket', 'mkt-filter-admin', 'mkt-filter-channel', 'mkt-filter-statpay']
    .forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('change', refreshMarketing);
    });
  const reset = document.getElementById('mkt-filter-reset');
  if (reset) reset.addEventListener('click', () => {
    ['mkt-filter-period', 'mkt-filter-paket', 'mkt-filter-admin', 'mkt-filter-channel', 'mkt-filter-statpay']
      .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    refreshMarketing();
  });
}

function readFilters() {
  return {
    period: (document.getElementById('mkt-filter-period') || {}).value || '',
    paket: (document.getElementById('mkt-filter-paket') || {}).value || '',
    admin: (document.getElementById('mkt-filter-admin') || {}).value || '',
    channel: (document.getElementById('mkt-filter-channel') || {}).value || '',
    statpay: (document.getElementById('mkt-filter-statpay') || {}).value || '',
  };
}

async function refreshMarketing() {
  const f = readFilters();
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v) params.set(k, v);
  const url = '/api/marketing/summary' + (params.toString() ? `?${params}` : '');
  try {
    const res = await mktFetch(url);
    const data = await res.json();
    renderKPI(data.kpi || {});
    renderMapJamaah(data.map_jamaah || []);
    renderMapAgen(data.map_agen || []);
    renderTopKota(data.map_jamaah || []);
    renderTopAgen(data.leaderboard_agen || []);
    renderChartTren(data.tren || []);
    renderChartAdmin(data.leaderboard_admin || []);
    renderChartPaket(data.dist_paket || []);
    renderChartChannel(data.channel || [], data.subchannel || []);
    renderChartAgenLeaderboard(data.leaderboard_agen || []);
    renderChartAgenPaket(data.paket_agen || []);
    renderChartGender(data.demo_gender || []);
    renderChartAge(data.demo_age || []);
    renderChartEducation(data.demo_education || []);
    renderChartJob(data.demo_job || []);
  } catch (e) {
    console.error('[marketing] gagal fetch summary', e);
  }
}

// -------- KPI --------
function renderKPI(k) {
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
  set('mkt-kpi-jamaah', (k.total_jamaah || 0).toLocaleString('id-ID'));
  set('mkt-kpi-omzet', formatRupiah(k.total_omzet || 0));
  set('mkt-kpi-dp', formatRupiah(k.total_dp || 0));
  set('mkt-kpi-kurang', formatRupiah(k.total_kurang || 0));
  set('mkt-kpi-agen', `${k.via_agen || 0} (${k.agen_ratio || 0}%)`);
  set('mkt-kpi-direct', `${k.via_direct || 0}`);
}

// -------- Heatmap Jamaah --------
function initMapJamaah() {
  if (typeof L === 'undefined') return;
  const el = document.getElementById('mkt-map-jamaah');
  if (!el || MKT.mapJamaah) return;
  MKT.mapJamaah = L.map('mkt-map-jamaah', {
    center: [-2.5489, 118.0149], zoom: 5, minZoom: 4, maxZoom: 15, scrollWheelZoom: true,
  });
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap | UMAR CRM Marketing Analytics',
  }).addTo(MKT.mapJamaah);
  MKT.mapJamaahLayer = L.layerGroup().addTo(MKT.mapJamaah);
}

function renderMapJamaah(list) {
  initMapJamaah();
  if (!MKT.mapJamaah || !MKT.mapJamaahLayer) return;
  MKT.mapJamaahLayer.clearLayers();

  list.forEach(item => {
    const coords = getCityCoordinates(item.kab_kota, item.provinsi);
    const radius = Math.max(8, Math.min(30, Math.sqrt(item.count) * 6 + 6));

    let color = MKT.mapJamaah && item.count >= 10 ? MKT_COLORS.goldStroke
      : item.count >= 3 ? '#D97706' : MKT_COLORS.goldSoft;
    let fillColor = item.count >= 10 ? MKT_COLORS.darkGold
      : item.count >= 3 ? MKT_COLORS.gold : MKT_COLORS.goldSoft;

    const marker = L.circleMarker(coords, {
      radius, color, fillColor, fillOpacity: 0.8, weight: 2,
    });
    const popupHtml = `
      <div style="font-family:'Inter',sans-serif;min-width:190px;">
        <div style="display:flex;align-items:center;gap:0.35rem;margin-bottom:4px;">
          <span style="color:${MKT_COLORS.darkGold};font-size:1rem;">◉</span>
          <b style="color:${MKT_COLORS.charcoal};">${item.kab_kota || '(kosong)'}</b>
        </div>
        <div style="font-size:0.7rem;color:${MKT_COLORS.gray600};border-bottom:1px solid #E5E7EB;padding-bottom:4px;margin-bottom:6px;">${item.provinsi || ''}</div>
        <div style="display:flex;justify-content:space-between;font-size:0.8rem;margin-bottom:3px;">
          <span style="color:${MKT_COLORS.gray600};">Jumlah Jamaah:</span>
          <b style="color:${MKT_COLORS.charcoal};">${item.count}</b>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:0.8rem;">
          <span style="color:${MKT_COLORS.gray600};">Total Omzet:</span>
          <b style="color:${MKT_COLORS.darkGold};">${formatRupiah(item.omzet)}</b>
        </div>
      </div>`;
    marker.bindTooltip(`<b>${item.kab_kota}</b>: ${item.count}`, { direction: 'top', offset: [0, -radius] });
    marker.bindPopup(popupHtml);
    marker.addTo(MKT.mapJamaahLayer);
  });

  const badge = document.getElementById('mkt-map-jamaah-badge');
  if (badge) badge.textContent = `${list.length} Kab/Kota`;
  setTimeout(() => MKT.mapJamaah && MKT.mapJamaah.invalidateSize(), 100);
}

// -------- Heatmap Agen --------
function initMapAgen() {
  if (typeof L === 'undefined') return;
  const el = document.getElementById('mkt-map-agen');
  if (!el || MKT.mapAgen) return;
  MKT.mapAgen = L.map('mkt-map-agen', {
    center: [-2.5489, 118.0149], zoom: 5, minZoom: 4, maxZoom: 15, scrollWheelZoom: true,
  });
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap | UMAR CRM Agen Map',
  }).addTo(MKT.mapAgen);
  MKT.mapAgenLayer = L.layerGroup().addTo(MKT.mapAgen);
}

function renderMapAgen(list) {
  initMapAgen();
  if (!MKT.mapAgen || !MKT.mapAgenLayer) return;
  MKT.mapAgenLayer.clearLayers();
  list.forEach(item => {
    const coords = getCityCoordinates(item.kab_kota, item.provinsi);
    const radius = Math.max(7, Math.min(24, Math.sqrt(item.count) * 5 + 5));
    const marker = L.circleMarker(coords, {
      radius, color: MKT_COLORS.charcoal, fillColor: MKT_COLORS.gold, fillOpacity: 0.85, weight: 2,
    });
    const popupHtml = `
      <div style="font-family:'Inter',sans-serif;min-width:180px;">
        <b style="color:${MKT_COLORS.charcoal};">${item.kab_kota || '(kosong)'}</b>
        <div style="font-size:0.7rem;color:${MKT_COLORS.gray600};margin-bottom:4px;">${item.provinsi || ''}</div>
        <div style="font-size:0.8rem;">Transaksi via agen: <b>${item.count}</b></div>
        <div style="font-size:0.8rem;">Omzet: <b style="color:${MKT_COLORS.darkGold};">${formatRupiah(item.omzet)}</b></div>
      </div>`;
    marker.bindTooltip(`<b>${item.kab_kota}</b>: ${item.count}`, { direction: 'top', offset: [0, -radius] });
    marker.bindPopup(popupHtml);
    marker.addTo(MKT.mapAgenLayer);
  });
  const badge = document.getElementById('mkt-map-agen-badge');
  if (badge) badge.textContent = `${list.length} Kab/Kota`;
  setTimeout(() => MKT.mapAgen && MKT.mapAgen.invalidateSize(), 100);
}

// -------- Top-kota + Top-agen (tabel di samping map) --------
function renderTopKota(list) {
  const el = document.getElementById('mkt-top-kota');
  if (!el) return;
  const top10 = list.slice(0, 10);
  el.innerHTML = top10.length ? top10.map((r, i) => `
    <li style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #F3F4F6;font-size:0.8rem;">
      <span><b style="color:${MKT_COLORS.darkGold};">${i+1}.</b> ${r.kab_kota}</span>
      <b style="color:${MKT_COLORS.charcoal};">${r.count}</b>
    </li>`).join('') : '<li style="color:#9CA3AF;font-size:0.8rem;text-align:center;padding:12px;">Belum ada data.</li>';
}

function renderTopAgen(list) {
  const el = document.getElementById('mkt-top-agen');
  if (!el) return;
  const top10 = list.slice(0, 10);
  el.innerHTML = top10.length ? top10.map((r, i) => `
    <li style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #F3F4F6;font-size:0.8rem;">
      <span><b style="color:${MKT_COLORS.darkGold};">${i+1}.</b> ${r.name}</span>
      <b style="color:${MKT_COLORS.charcoal};">${r.count}</b>
    </li>`).join('') : '<li style="color:#9CA3AF;font-size:0.8rem;text-align:center;padding:12px;">Belum ada data.</li>';
}

// -------- Charts --------
function destroyChart(key) {
  if (MKT.charts[key]) { MKT.charts[key].destroy(); MKT.charts[key] = null; }
}

function renderChartTren(rows) {
  const el = document.getElementById('mkt-chart-tren');
  if (!el || typeof Chart === 'undefined') return;
  destroyChart('tren');
  MKT.charts.tren = new Chart(el, {
    type: 'line',
    data: {
      labels: rows.map(r => r.period),
      datasets: [{
        label: 'Jumlah Closing',
        data: rows.map(r => r.count),
        borderColor: MKT_COLORS.darkGold,
        backgroundColor: 'rgba(248,190,32,0.25)',
        fill: true,
        tension: 0.3,
        pointBackgroundColor: MKT_COLORS.gold,
        pointBorderColor: MKT_COLORS.charcoal,
        pointRadius: 4,
      }],
    },
    options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } },
  });
}

function renderChartAdmin(rows) {
  const el = document.getElementById('mkt-chart-admin');
  if (!el || typeof Chart === 'undefined') return;
  destroyChart('admin');
  MKT.charts.admin = new Chart(el, {
    type: 'bar',
    data: {
      labels: rows.map(r => r.name),
      datasets: [{
        label: 'Closing',
        data: rows.map(r => r.count),
        backgroundColor: rows.map((_, i) => i === 0 ? MKT_COLORS.darkGold : i === 1 ? MKT_COLORS.gold : MKT_COLORS.goldSoft),
        borderColor: MKT_COLORS.charcoal,
        borderWidth: 1,
      }],
    },
    options: { indexAxis: 'y', plugins: { legend: { display: false } }, scales: { x: { beginAtZero: true } } },
  });
}

function renderChartPaket(rows) {
  const el = document.getElementById('mkt-chart-paket');
  if (!el || typeof Chart === 'undefined') return;
  destroyChart('paket');
  const palette = ['#B8860B','#F8BE20','#FDE047','#D97706','#78350F','#FBBF24','#FCD34D','#92400E','#EAB308','#CA8A04'];
  MKT.charts.paket = new Chart(el, {
    type: 'doughnut',
    data: {
      labels: rows.map(r => r.name),
      datasets: [{ data: rows.map(r => r.count), backgroundColor: rows.map((_, i) => palette[i % palette.length]) }],
    },
    options: { plugins: { legend: { position: 'bottom', labels: { font: { size: 10 } } } } },
  });
}

function renderChartChannel(channel, subchannel) {
  const el = document.getElementById('mkt-chart-channel');
  if (!el || typeof Chart === 'undefined') return;
  destroyChart('channel');
  MKT.charts.channel = new Chart(el, {
    type: 'bar',
    data: {
      labels: channel.map(r => r.name),
      datasets: [{
        label: 'Closing',
        data: channel.map(r => r.count),
        backgroundColor: [MKT_COLORS.darkGold, MKT_COLORS.gold, MKT_COLORS.goldSoft],
        borderColor: MKT_COLORS.charcoal, borderWidth: 1,
      }],
    },
    options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } },
  });

  const subEl = document.getElementById('mkt-sub-channel');
  if (subEl) {
    subEl.innerHTML = subchannel.length ? subchannel.map(r => `
      <li style="display:flex;justify-content:space-between;padding:4px 0;font-size:0.75rem;color:${MKT_COLORS.gray600};">
        <span>${r.name || '(kosong)'}</span><b>${r.count}</b>
      </li>`).join('') : '';
  }
}

function renderChartAgenLeaderboard(rows) {
  const el = document.getElementById('mkt-chart-agen-leaderboard');
  if (!el || typeof Chart === 'undefined') return;
  destroyChart('agenLead');
  MKT.charts.agenLead = new Chart(el, {
    type: 'bar',
    data: {
      labels: rows.map(r => r.name),
      datasets: [{
        label: 'Jamaah dikirim',
        data: rows.map(r => r.count),
        backgroundColor: MKT_COLORS.gold,
        borderColor: MKT_COLORS.charcoal, borderWidth: 1,
      }],
    },
    options: { indexAxis: 'y', plugins: { legend: { display: false } }, scales: { x: { beginAtZero: true } } },
  });
}

function renderChartAgenPaket(rows) {
  const el = document.getElementById('mkt-chart-agen-paket');
  if (!el || typeof Chart === 'undefined') return;
  destroyChart('agenPaket');
  const palette = ['#B8860B','#F8BE20','#FDE047','#D97706','#78350F'];
  MKT.charts.agenPaket = new Chart(el, {
    type: 'doughnut',
    data: {
      labels: rows.map(r => r.name),
      datasets: [{ data: rows.map(r => r.count), backgroundColor: rows.map((_, i) => palette[i % palette.length]) }],
    },
    options: { plugins: { legend: { position: 'bottom', labels: { font: { size: 10 } } } } },
  });
}

// -------- Demografi Charts --------
function renderChartGender(rows) {
  const el = document.getElementById('mkt-chart-gender');
  if (!el || typeof Chart === 'undefined') return;
  destroyChart('gender');
  // Warna: perempuan gold soft (mayoritas historis), laki-laki dark gold, kosong abu.
  const colorFor = (name) => {
    const n = (name || '').toUpperCase();
    if (n.includes('PEREMPUAN') || n === 'P' || n === 'F') return MKT_COLORS.gold;
    if (n.includes('LAKI'))                                return MKT_COLORS.darkGold;
    return '#9CA3AF';
  };
  MKT.charts.gender = new Chart(el, {
    type: 'doughnut',
    data: {
      labels: rows.map(r => r.name),
      datasets: [{
        data: rows.map(r => r.count),
        backgroundColor: rows.map(r => colorFor(r.name)),
        borderColor: MKT_COLORS.charcoal, borderWidth: 1,
      }],
    },
    options: {
      plugins: {
        legend: { position: 'bottom', labels: { font: { size: 11 } } },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const total = ctx.dataset.data.reduce((a, b) => a + b, 0);
              const pct = total ? Math.round(ctx.parsed / total * 100) : 0;
              return `${ctx.label}: ${ctx.parsed} (${pct}%)`;
            },
          },
        },
      },
    },
  });
}

function renderChartAge(rows) {
  const el = document.getElementById('mkt-chart-age');
  if (!el || typeof Chart === 'undefined') return;
  destroyChart('age');
  MKT.charts.age = new Chart(el, {
    type: 'bar',
    data: {
      labels: rows.map(r => r.name),
      datasets: [{
        label: 'Jumlah Jamaah',
        data: rows.map(r => r.count),
        backgroundColor: rows.map(r => r.name === '(kosong)' ? '#9CA3AF' : MKT_COLORS.gold),
        borderColor: MKT_COLORS.charcoal, borderWidth: 1,
      }],
    },
    options: {
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
    },
  });
}

function renderChartEducation(rows) {
  const el = document.getElementById('mkt-chart-education');
  if (!el || typeof Chart === 'undefined') return;
  destroyChart('education');
  MKT.charts.education = new Chart(el, {
    type: 'bar',
    data: {
      labels: rows.map(r => r.name),
      datasets: [{
        label: 'Jamaah',
        data: rows.map(r => r.count),
        backgroundColor: rows.map((_, i) => i === 0 ? MKT_COLORS.darkGold : i === 1 ? MKT_COLORS.gold : MKT_COLORS.goldSoft),
        borderColor: MKT_COLORS.charcoal, borderWidth: 1,
      }],
    },
    options: {
      indexAxis: 'y',
      plugins: { legend: { display: false } },
      scales: { x: { beginAtZero: true, ticks: { precision: 0 } } },
    },
  });
}

function renderChartJob(rows) {
  const el = document.getElementById('mkt-chart-job');
  if (!el || typeof Chart === 'undefined') return;
  destroyChart('job');
  MKT.charts.job = new Chart(el, {
    type: 'bar',
    data: {
      labels: rows.map(r => r.name),
      datasets: [{
        label: 'Jamaah',
        data: rows.map(r => r.count),
        backgroundColor: rows.map((_, i) => i === 0 ? MKT_COLORS.darkGold : i === 1 ? MKT_COLORS.gold : MKT_COLORS.goldSoft),
        borderColor: MKT_COLORS.charcoal, borderWidth: 1,
      }],
    },
    options: {
      indexAxis: 'y',
      plugins: { legend: { display: false } },
      scales: { x: { beginAtZero: true, ticks: { precision: 0 } } },
    },
  });
}

// Expose to window (dipanggil dari inline onclick di index.html)
window.initMarketingPage = initMarketingPage;
window.refreshMarketing = refreshMarketing;
