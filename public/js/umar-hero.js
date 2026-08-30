/* ============================================================================
 * UMAR Shared Hero -- Phase 5a
 *
 * Reusable helpers untuk hero band di setiap page:
 *   - Realtime greeting (Assalamu'alaikum + Selamat pagi/siang/sore/malam)
 *   - Clock realtime (tanggal + jam WIB update tiap detik)
 *   - Islamic quote (rotate per hari, deterministic same-day-same-quote)
 *
 * Dependency: escape helper local (tidak assume globals).
 * ==========================================================================*/

let __umarQuotesCache = null;

async function umarLoadQuotes() {
    if (__umarQuotesCache) return __umarQuotesCache;
    try {
        const r = await fetch('/data/islamic-quotes.json', { cache: 'default' });
        __umarQuotesCache = await r.json();
    } catch (e) {
        // Fallback minimal: kalau JSON gagal load, tetap tampilkan 1 quote default.
        __umarQuotesCache = [{
            translation: "Semoga hari ini Allah lapangkan dada kita dan mudahkan urusan kita.",
            source: "—",
            category: "doa",
        }];
    }
    return __umarQuotesCache;
}

/**
 * Deterministic per-day rotation: hari yg sama -> quote sama untuk semua user.
 * Formula: dayOfYear % list.length
 * Cycle: 48 quotes -> ganti tiap hari, ulang tiap ~48 hari.
 */
function umarGetDailyQuote(quotes) {
    if (!quotes || !quotes.length) return null;
    const now = new Date();
    // dayOfYear (0-based)
    const start = new Date(now.getFullYear(), 0, 0);
    const diff = now - start;
    const dayOfYear = Math.floor(diff / 86400000);
    return quotes[dayOfYear % quotes.length];
}

/**
 * Bahasa Indonesia time-of-day greeting.
 *   04:00-09:59 = pagi
 *   10:00-14:59 = siang
 *   15:00-17:59 = sore
 *   18:00-03:59 = malam
 */
function umarGreetingText() {
    const h = new Date().getHours();
    if (h >= 4 && h < 10) return 'Selamat pagi';
    if (h >= 10 && h < 15) return 'Selamat siang';
    if (h >= 15 && h < 18) return 'Selamat sore';
    return 'Selamat malam';
}

/**
 * Format "Hari, DD Bulan YYYY • HH:MM:SS WIB".
 * Pakai local time browser + label WIB (asumsi user di Indonesia).
 */
function umarFormatDateTime() {
    const now = new Date();
    const days = ['Minggu', 'Senin', 'Selasa', 'Rabu', 'Kamis', "Jum'at", 'Sabtu'];
    const months = ['Januari','Februari','Maret','April','Mei','Juni','Juli','Agustus','September','Oktober','November','Desember'];
    const day = days[now.getDay()];
    const date = now.getDate();
    const month = months[now.getMonth()];
    const year = now.getFullYear();
    const hh = String(now.getHours()).padStart(2, '0');
    const mm = String(now.getMinutes()).padStart(2, '0');
    const ss = String(now.getSeconds()).padStart(2, '0');
    return `${day}, ${date} ${month} ${year} • ${hh}:${mm}:${ss} WIB`;
}

function _umarEsc(s) {
    if (s === null || s === undefined) return '';
    return String(s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/**
 * Render welcome hero (untuk Home pages) ke element tujuan.
 *
 * @param {string} elId       - id div/section tempat hero di-inject.
 * @param {string} userName   - "Tim Sales", "Administrator", dll.
 * @returns {Promise<void>}   - hero rendered + clock+greeting mulai tick per detik.
 */
async function umarRenderWelcomeHero(elId, userName) {
    const el = document.getElementById(elId);
    if (!el) return;

    const quotes = await umarLoadQuotes();
    const quote = umarGetDailyQuote(quotes) || { translation: '', source: '' };

    el.innerHTML = `
        <div class="umar-hero">
            <div class="umar-welcome-hero-grid">
                <div>
                    <div class="flex items-center gap-2 flex-wrap">
                        <span class="umar-hero-icon">\u{1F319}</span>
                        <div>
                            <div class="umar-hero-title">Assalamu'alaikum, <span class="umar-hero-greet-time" id="${elId}-greet">${umarGreetingText()}</span></div>
                            <div class="umar-hero-name text-lg">${_umarEsc(userName || 'Tim UMAR')}</div>
                        </div>
                    </div>
                    <div class="umar-hero-clock" id="${elId}-clock">${umarFormatDateTime()}</div>
                </div>
                <div class="umar-hero-quote">
                    <p class="umar-quote-translation">"${_umarEsc(quote.translation)}"</p>
                    <p class="umar-quote-source">— ${_umarEsc(quote.source)}</p>
                </div>
            </div>
        </div>
    `;

    // Tick jam + greeting per detik (greeting hanya berubah pada boundary jam).
    // Store handle di element supaya kalau re-render bisa clearInterval yang lama.
    const oldHandle = el.dataset.umarClockHandle ? parseInt(el.dataset.umarClockHandle) : null;
    if (oldHandle) { try { clearInterval(oldHandle); } catch (e) {} }
    const handle = setInterval(() => {
        const clockEl = document.getElementById(elId + '-clock');
        const greetEl = document.getElementById(elId + '-greet');
        if (clockEl) clockEl.textContent = umarFormatDateTime();
        if (greetEl) greetEl.textContent = umarGreetingText();
        // Kalau element sudah hilang dari DOM, stop interval.
        if (!clockEl) { clearInterval(handle); }
    }, 1000);
    el.dataset.umarClockHandle = String(handle);
}

/**
 * Render simple module hero (untuk Module pages, tanpa greeting/quote).
 *
 * @param {string} elId       - id div tempat hero di-inject.
 * @param {string} icon       - lucide icon name (mis. "calculator", "package", "users").
 * @param {string} title      - judul modul (mis. "Simulasi Paket BOQ").
 * @param {string} subtitle   - deskripsi singkat.
 */
function umarRenderModuleHero(elId, icon, title, subtitle) {
    const el = document.getElementById(elId);
    if (!el) return;
    el.innerHTML = `
        <div class="umar-module-hero">
            <div class="flex items-center gap-3">
                <i data-lucide="${_umarEsc(icon || 'star')}" class="w-6 h-6" style="color: var(--umar-gold);"></i>
                <div>
                    <div class="umar-module-hero-title">${_umarEsc(title)}</div>
                    ${subtitle ? `<div class="umar-module-hero-subtitle">${_umarEsc(subtitle)}</div>` : ''}
                </div>
            </div>
        </div>
    `;
    if (typeof lucide !== 'undefined') lucide.createIcons();
}
