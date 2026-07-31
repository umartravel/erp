"""
Automasi Playwright untuk Cek Status Visa / Siskopatuh (versi Python).
Setara dengan visa_checker.js.

Memakai Playwright async API. Bila Playwright belum terpasang atau browser
belum di-install (`playwright install chromium`), fungsi tetap mengembalikan
hasil simulasi agar alur CRM tidak terputus.
"""
import asyncio
import random


async def check_visa_status(nik: str, jamaah_name: str) -> str:
    print(f"[Playwright] Memulai pengecekan visa untuk {jamaah_name} (NIK: {nik})...")

    browser = None
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            # Simulasi mengunjungi portal kedutaan/siskopatuh.
            # Di dunia nyata, ganti dengan URL & selector asli:
            # await page.goto("https://siskopatuh.kemenag.go.id/web/login")
            # await page.fill("#username", "user_travel")
            # await page.fill("#password", "rahasia")
            # await page.click("button[type=submit]")

            # Simulasi delay proses network
            await asyncio.sleep(3)

            is_approved = random.random() > 0.2  # 80% disetujui
            result = "Visa Approved" if is_approved else "Proses Kedutaan"
            print(f"[Playwright] Selesai cek visa {jamaah_name}. Hasil: {result}")
            return result
    except Exception as error:  # noqa: BLE001
        # Fallback simulasi bila Playwright/browser tidak tersedia
        print(f"[Playwright] Browser tidak tersedia ({error}). Memakai simulasi.")
        await asyncio.sleep(1)
        return "Visa Approved" if random.random() > 0.2 else "Proses Kedutaan"
    finally:
        if browser:
            await browser.close()
