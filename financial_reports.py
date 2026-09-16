"""
Sprint AK-4: Neraca + Laba Rugi Multi-Step reports (PSAK-format).

Semua angka dihitung dari `journal_lines` -- bukan `transactions.type/category`
legacy -- supaya konsisten dgn double-entry Sprint AK-2.

`build_balance_sheet(as_of_date)`:
    Aset (1xxx) - Liabilitas (2xxx) - Ekuitas (3xxx) ledger balance sd tanggal.
    Aset = Debit - Credit (normal_balance DEBIT).
    Liab/Equity = Credit - Debit (normal_balance CREDIT).
    Ekuitas menyerap retained earnings (SUM Revenue - SUM Expense sd tanggal).

    Return dict siap render Neraca:
      {as_of, assets:{current,fixed,total}, liabilities:{items,total},
       equity:{items,retained_earnings,total}, total_liab_equity, balanced, delta}

`build_income_statement(year, month)`:
    Laba Rugi Multi-Step:
      Revenue (4xxx)
        - COGS (5xxx)             = Gross Profit
        - OPEX (6101-6108)        = Operating Profit
        - Pos Lain (6201-6202)   = Net Profit Before Tax
"""
import calendar

import db


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    last_day = calendar.monthrange(year, month)[1]
    return (
        f"{year:04d}-{month:02d}-01",
        f"{year:04d}-{month:02d}-{last_day:02d}",
    )


def _account_balance(as_of_date: str) -> list[dict]:
    """Return semua akun aktif + saldo (net Dr/Cr) sd tanggal.

    Setiap dict: {account_code, account_name, account_group, normal_balance,
                  total_debit, total_credit, balance}
    balance = (Dr - Cr) untuk akun normal_balance=DEBIT
              (Cr - Dr) untuk akun normal_balance=CREDIT
    Filter status != 'REVERSED' supaya reversing entry pair (orig + reverse)
    keluar dari ledger.
    """
    rows = db.query_all(
        "SELECT ca.id, ca.account_code, ca.account_name, ca.account_group, "
        "  ca.normal_balance, "
        "  COALESCE(SUM(jl.debit), 0) AS total_debit, "
        "  COALESCE(SUM(jl.credit), 0) AS total_credit "
        "FROM chart_of_accounts ca "
        "LEFT JOIN journal_lines jl ON jl.account_id = ca.id "
        "LEFT JOIN transactions t ON t.id = jl.transaction_id "
        "WHERE ca.is_active = 1 "
        "  AND (jl.id IS NULL OR (date(t.created_at) <= ? AND t.status != 'REVERSED')) "
        "GROUP BY ca.id "
        "ORDER BY ca.account_code",
        (as_of_date,),
    )
    out = []
    for r in rows:
        dr = int(r["total_debit"] or 0)
        cr = int(r["total_credit"] or 0)
        if r["normal_balance"] == "DEBIT":
            balance = dr - cr
        else:
            balance = cr - dr
        out.append({
            "account_code": r["account_code"],
            "account_name": r["account_name"],
            "account_group": r["account_group"],
            "normal_balance": r["normal_balance"],
            "total_debit": dr,
            "total_credit": cr,
            "balance": balance,
        })
    return out


def build_balance_sheet(as_of_date: str) -> dict:
    balances = _account_balance(as_of_date)

    assets_current: list[dict] = []
    assets_fixed: list[dict] = []
    liabilities: list[dict] = []
    equity_direct: list[dict] = []

    total_revenue = 0
    total_expense = 0

    for b in balances:
        code = b["account_code"]
        grp = b["account_group"]
        bal = b["balance"]

        if grp == "ASSET":
            if code.startswith("12"):
                assets_fixed.append(b)
            else:
                assets_current.append(b)
        elif grp == "LIABILITY":
            liabilities.append(b)
        elif grp == "EQUITY":
            equity_direct.append(b)
        elif grp == "REVENUE":
            total_revenue += bal
        elif grp == "EXPENSE":
            total_expense += bal

    retained_earnings = total_revenue - total_expense

    total_current = sum(b["balance"] for b in assets_current)
    total_fixed = sum(b["balance"] for b in assets_fixed)
    total_assets = total_current + total_fixed
    total_liabilities = sum(b["balance"] for b in liabilities)
    total_equity_direct = sum(b["balance"] for b in equity_direct)
    total_equity = total_equity_direct + retained_earnings
    total_liab_equity = total_liabilities + total_equity

    return {
        "as_of": as_of_date,
        "assets": {
            "current": assets_current,
            "fixed": assets_fixed,
            "total_current": total_current,
            "total_fixed": total_fixed,
            "total": total_assets,
        },
        "liabilities": {
            "items": liabilities,
            "total": total_liabilities,
        },
        "equity": {
            "items": equity_direct,
            "retained_earnings": retained_earnings,
            "total_direct": total_equity_direct,
            "total": total_equity,
        },
        "total_liab_equity": total_liab_equity,
        "balanced": total_assets == total_liab_equity,
        "delta": total_assets - total_liab_equity,
    }


def build_income_statement(year: int, month: int) -> dict:
    if not (1 <= month <= 12):
        raise ValueError(f"month harus 1-12, got {month}")

    start, end = _month_bounds(year, month)
    period_label = f"{year:04d}-{month:02d}"

    rows = db.query_all(
        "SELECT ca.account_code, ca.account_name, ca.account_group, "
        "  ca.normal_balance, "
        "  SUM(jl.debit) AS total_debit, "
        "  SUM(jl.credit) AS total_credit "
        "FROM journal_lines jl "
        "JOIN chart_of_accounts ca ON ca.id = jl.account_id "
        "JOIN transactions t ON t.id = jl.transaction_id "
        "WHERE date(t.created_at) BETWEEN ? AND ? "
        "  AND t.status != 'REVERSED' "
        "  AND ca.account_group IN ('REVENUE','EXPENSE') "
        "GROUP BY ca.id "
        "ORDER BY ca.account_code",
        (start, end),
    )

    revenue_items = []
    cogs_items = []
    opex_items = []
    other_items = []

    total_revenue = 0
    total_cogs = 0
    total_opex = 0
    total_other = 0

    for r in rows:
        code = r["account_code"]
        dr = int(r["total_debit"] or 0)
        cr = int(r["total_credit"] or 0)
        item = {
            "account_code": code,
            "account_name": r["account_name"],
            "account_group": r["account_group"],
            "total_debit": dr,
            "total_credit": cr,
        }
        if r["account_group"] == "REVENUE":
            amount = cr - dr
            item["amount"] = amount
            revenue_items.append(item)
            total_revenue += amount
        else:  # EXPENSE
            amount = dr - cr
            item["amount"] = amount
            if code.startswith("5"):
                cogs_items.append(item)
                total_cogs += amount
            elif code.startswith("62"):
                other_items.append(item)
                total_other += amount
            else:  # 61xx
                opex_items.append(item)
                total_opex += amount

    gross_profit = total_revenue - total_cogs
    operating_profit = gross_profit - total_opex
    net_profit = operating_profit - total_other

    return {
        "period": period_label,
        "period_start": start,
        "period_end": end,
        "revenue": {"items": revenue_items, "total": total_revenue},
        "cogs": {"items": cogs_items, "total": total_cogs},
        "gross_profit": gross_profit,
        "opex": {"items": opex_items, "total": total_opex},
        "operating_profit": operating_profit,
        "other": {"items": other_items, "total": total_other},
        "net_profit_before_tax": net_profit,
    }
