"""
Utility generik: parsing input + formatting + fire-and-forget task.
"""
import asyncio

from fastapi import HTTPException


def parse_int(value, field="nilai"):
    """Parse angka dari input. Balikan HTTP 400 yang rapi bila tidak valid."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Input {field} harus berupa angka yang valid.")


def fmt_id(n) -> str:
    """Format angka ala toLocaleString('id-ID'): 1000000 -> '1.000.000'."""
    try:
        return f"{int(n):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(n)


def fire_and_forget(coro):
    """Kirim WA tanpa menunggu (mirip pemanggilan wa.sendMessage tanpa await)."""
    asyncio.create_task(coro)


__all__ = ["parse_int", "fmt_id", "fire_and_forget"]
