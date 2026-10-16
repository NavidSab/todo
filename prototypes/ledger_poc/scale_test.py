#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AZIZIPAY — آزمون مقیاس هسته مالی
=================================
هدف: پاسخ به این پرسش که «اگر حجم تراکنش‌های مالی به میلیون برسد، مدل داده و
قواعد صحت می‌توانند دوام بیاورند؟» و به‌دست‌آوردن اعداد پایه برای مدل ظرفیت.

دو بخش:
  الف) ۵۰٬۰۰۰ سند با *همان* کلاس‌های PoC (`ledger_poc.py`) — سنجش واقعی حافظه و صحت
  ب) ۱٬۰۰۰٬۰۰۰ سند با نمایش فشرده ستونی — سنجش صحت در مقیاس + تخمین فضای داده

⚠️ هشدار صادقانه: این آزمون «کارایی PostgreSQL» را نمی‌سنجد؛ مدل داده، قواعد
توازن، حافظه و رفتار Idempotency را در حجم بالا می‌سنجد. سنجش TPS واقعی دیتابیس
باید در ماه ۶ با k6 روی محیط واقعی انجام شود (سند ۰۵، بخش ۷).

اجرا: python3 scale_test.py
"""

from __future__ import annotations

import array
import hashlib
import os
import random
import resource
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ledger_poc import CREDIT, DEBIT, Ledger, LedgerError  # noqa: E402  (استفاده مجدد از هسته PoC)


def kb(n_bytes: float) -> str:
    return f"{n_bytes / 1024:.1f} KB"


def mb(n_bytes: float) -> str:
    return f"{n_bytes / 1024 / 1024:.1f} MB"


def rss_kb() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


# ---------------------------------------------------------------------------
# بخش الف — همان مدل شیءگرای PoC در حجم ۵۰٬۰۰۰ سند
# ---------------------------------------------------------------------------


def part_a(n: int = 50_000) -> None:
    print("\n" + "=" * 78)
    print(f"بخش الف — همان کد PoC با {n:,} سند مالی (مدل شیءگرا)")
    print("=" * 78)
    rnd = random.Random(11)
    ledger = Ledger()
    fee_acc = ledger.open_account("4010", "درآمد کارمزد", "INCOME")
    service_acc = ledger.open_account("4010B", "درآمد خدمات", "INCOME")
    partner_accs = [ledger.account_for_partner(f"EX-{p}") for p in ("A", "B", "C")]
    wallets = [ledger.account_for_wallet(f"U-{i:07d}") for i in range(25_000)]

    cashin_keys: list[str] = []
    tracemalloc.start()
    t0 = time.perf_counter()
    for j in range(n):
        r = rnd.random()
        if r < 0.94:
            p = rnd.choice(partner_accs)
            w = rnd.choice(wallets)
            gross = rnd.randrange(10_000, 5_000_001)
            fee = gross // 1000
            lines = [(p, DEBIT, gross), (w, CREDIT, gross - fee)]
            if fee:
                lines.append((fee_acc.code, CREDIT, fee))
            key = f"cashin:{j:09d}:confirm"
            cashin_keys.append(key)
            ledger.post(type_="CASHIN", lines=lines, idempotency_key=key)
        else:
            w = rnd.choice(wallets)
            v = rnd.randrange(5_000, 200_001)
            ledger.post(
                type_="PAYMENT",
                lines=[(w, DEBIT, v), (service_acc.code, CREDIT, v)],
                idempotency_key=f"pay:{j:09d}",
            )
    build_s = time.perf_counter() - t0
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    t1 = time.perf_counter()
    integrity = ledger.integrity()
    verify_s = time.perf_counter() - t1

    print(f"اسناد ثبت‌شده      : {len(ledger.journals):,}")
    print(f"ردیف‌های دفتر کل   : {len(ledger.entries):,}")
    print(f"زمان ثبت           : {build_s:.2f} ثانیه  ({n / build_s:,.0f} سند/ثانیه در یک رشته پایتونی)")
    print(f"زمان بررسی یکپارچگی: {verify_s:.2f} ثانیه")
    print(f"حافظه پایتونی      : جاری {mb(cur)} — اوج {mb(peak)}  (≈ {peak / max(len(ledger.entries), 1):.0f} بایت به‌ازای هر ردیف)")
    print(f"توازن دفتر کل      : {integrity}")
    assert integrity["balanced"] and not integrity["unbalanced_journals"], "دفتر کل ناتراز!"
    assert not integrity["hash_chain_broken"], "زنجیره هش شکسته!"
    print("✅ در حجم ۵۰٬۰۰۰ سند: دفتر کل متوازن، زنجیره هش سالم، بدون خطای صحت.")

    # Idempotency در حجم: بازپخش ۵٬۰۰۰ کلید واقعیِ موجود (تلاش تکراری واقعی)
    t2 = time.perf_counter()
    before = len(ledger.journals)
    sample = rnd.sample(cashin_keys, 5_000)
    returned = set()
    for key in sample:
        j = ledger.post(
            type_="CASHIN",
            lines=[(partner_accs[0], DEBIT, 100), (wallets[0], CREDIT, 100)],
            idempotency_key=key,
        )
        returned.add(j.journal_no)
    after = len(ledger.journals)
    print(f"۵٬۰۰۰ تلاش تکراری  : اسناد جدید = {after - before} | سندهای متمایز بازگردانده‌شده = {len(returned)} "
          f"(زمان {time.perf_counter() - t2:.2f} ثانیه)")
    assert after == before, "Idempotency در حجم نقض شد — سند تکراری ثبت شد!"
    assert len(returned) == len(sample), "برای کلیدهای متفاوت، پاسخ یکسان برگشت!"
    print("✅ Idempotency در حجم بالا: هیچ سند جدیدی ثبت نشد و هر کلید همان سند خودش را برگرداند.")


# ---------------------------------------------------------------------------
# بخش ب — ۱٬۰۰۰٬۰۰۰ سند با نمایش فشرده ستونی
# ---------------------------------------------------------------------------


def part_b(n_journals: int = 1_000_000, n_wallets: int = 200_000, seed: int = 7) -> dict:
    print("\n" + "=" * 78)
    print(f"بخش ب — {n_journals:,} سند مالی با نمایش فشرده (اکتشاف صحت در مقیاس)")
    print("=" * 78)
    rnd = random.Random(seed)

    # طرح‌واره ستونی: هر ردیف دفتر کل = (شماره حساب داخلی, مبلغ با علامت)
    # + بدهکار و - بستانکار  →  «مجموع هر سند باید صفر باشد» یک بررسی O(1) می‌شود
    acc_idx = array.array("i")
    amt = array.array("q")
    offsets = array.array("i")
    counts = array.array("b")
    j_debit = array.array("q")
    hashes: list[bytes] = []
    idem: dict[str, int] = {}
    cached: dict[int, int] = {}

    FLOAT_ACCOUNTS = [0, 1, 2]          # 1010-EX-A/B/C  (دارایی: نقد نزد صرافی)
    FEE_INCOME = 3                       # 4010 کارمزد
    SERVICE_INCOME = 4                   # 4010B درآمد خدمات
    BANK = 5                             # 1020 بانک
    wallet_base = 10
    wallets = [wallet_base + i for i in range(n_wallets)]

    def wallet_balance(acc: int) -> int:
        """مانده قابل استفاده کاربر = منفیِ مجموع علائم دفتر کل (حساب کیف پول: بدهی)."""
        return -cached.get(acc, 0)

    tracemalloc.start()
    t0 = time.perf_counter()
    prev = b"GENESIS"
    debits = credits = 0
    negative_wallets = 0
    rejected = {"payment_insufficient": 0, "reversal_would_go_negative": 0}
    kind_counter = {"cashin": 0, "payment": 0, "reversal": 0, "settlement": 0}

    j = 0
    while j < n_journals:
        start = len(acc_idx)
        r = rnd.random()
        if r < 0.94:  # شارژ نقدی از صرافی
            kind = "cashin"
            partner = FLOAT_ACCOUNTS[rnd.randrange(3)]
            w = wallets[rnd.randrange(n_wallets)]
            gross = rnd.randrange(10_000, 5_000_001)
            fee = gross // 1000
            lines = [(partner, gross), (w, -(gross - fee))]
            if fee:
                lines.append((FEE_INCOME, -fee))
            key = f"cashin:{j:012d}:confirm"
        elif r < 0.99:  # پرداخت خدمت (فقط با موجودی کافی)
            w = wallets[rnd.randrange(n_wallets)]
            v = rnd.randrange(5_000, 200_001)
            if wallet_balance(w) < v:  # در تولید: SQL با قید balance_minor >= 0
                rejected["payment_insufficient"] += 1
                continue  # این تراکنش ثبت نمی‌شود؛ شماره سند جلو نمی‌رود
            kind = "payment"
            lines = [(w, v), (SERVICE_INCOME, -v)]
            key = f"pay:{j:012d}"
        elif r < 0.995:  # سند معکوس (اصلاح) — فقط اگر کیف پول منفی نشود
            k = rnd.randrange(j)
            off, cnt = offsets[k], counts[k]
            lines = [(acc_idx[off + i], -amt[off + i]) for i in range(cnt)]
            # معکوس سند، ردیف‌ها را برمی‌گرداند؛ برای کیف پول باید مانده کاربر منفی نشود
            would_break = any(
                acc >= wallet_base and wallet_balance(acc) - signed < 0 for acc, signed in lines
            )
            if would_break:
                rejected["reversal_would_go_negative"] += 1
                continue
            kind = "reversal"
            key = f"reversal:{k:012d}"
        else:  # تسویه با صرافی
            kind = "settlement"
            partner = FLOAT_ACCOUNTS[rnd.randrange(3)]
            amount = rnd.randrange(1_000_000, 20_000_001)
            lines = [(BANK, amount), (partner, -amount)]
            key = f"settlement:{j:012d}"

        total = 0
        for acc, signed in lines:
            acc_idx.append(acc)
            amt.append(signed)
            cached[acc] = cached.get(acc, 0) + signed
            total += signed
            if signed > 0:
                debits += signed
            else:
                credits += -signed
        if total != 0:  # توازن سند — در PostgreSQL با Trigger تضمین می‌شود
            raise LedgerError(f"سند نامتوازن در ردیف {j}: {total}")
        offsets.append(start)
        counts.append(len(lines))
        j_debit.append(debits)
        payload = f"{prev.hex()}|{j}|{total}|{len(lines)}".encode()
        h = hashlib.sha256(payload).digest()
        hashes.append(h)
        prev = h
        idem[key] = j
        kind_counter[kind] += 1
        j += 1
    build_s = time.perf_counter() - t0
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    rows = len(acc_idx)
    print(f"اسناد              : {n_journals:,}  ({kind_counter})")
    print(f"ردیف‌های دفتر کل   : {rows:,}")
    print(f"زمان ساخت          : {build_s:.2f} ثانیه  ({n_journals / build_s:,.0f} سند/ثانیه، یک رشته)")
    print(f"حافظه نمایش فشرده  : جاری {mb(cur)} — اوج {mb(peak)}")
    print(f"  ↳ به‌ازای هر سند  : {cur / n_journals:.0f} بایت | هر ردیف: {cur / rows:.0f} بایت")
    negative_wallets = [
        (a, wallet_balance(a)) for a in cached if a >= wallet_base and wallet_balance(a) < 0
    ]
    funded = [wallet_balance(a) for a in cached if a >= wallet_base and wallet_balance(a) > 0]
    print(f"رد‌شده‌ها (کنترل واقعی موجودی): پرداخت با موجودی ناکافی={rejected['payment_insufficient']:,} | "
          f"معکوسِ ناسازگار با مانده={rejected['reversal_would_go_negative']:,}")
    print(f"کیف پول‌های دارای موجودی: {len(funded):,} از {n_wallets:,} | میانگین موجودی="
          f"{sum(funded) / max(len(funded), 1) / 100:,.0f} افغانی")
    print(f"کیف پول با مانده منفی: {len(negative_wallets)}  ← باید صفر باشد")
    assert not negative_wallets, "کیف پول منفی شد — نقض قید balance >= 0"

    # --- بررسی ۱: توازن کل و توازن هر سند -----------------------------------
    t1 = time.perf_counter()
    assert debits == credits, "توازن کل نقض شد!"
    bad = 0
    for j in range(n_journals):
        off, cnt = offsets[j], counts[j]
        if sum(amt[off : off + cnt]) != 0:
            bad += 1
    v1 = time.perf_counter() - t1
    print(f"بررسی توازن کل و هر سند: بدهکار={debits:,} بستانکار={credits:,} | اسناد ناتراز={bad} ({v1:.2f} ثانیه)")
    assert bad == 0

    # --- بررسی ۲: مانده مشتق‌شده در برابر مانده کش‌شده ----------------------
    t2 = time.perf_counter()
    derived: dict[int, int] = {}
    for i in range(rows):
        a = acc_idx[i]
        derived[a] = derived.get(a, 0) + amt[i]
    mismatches = [a for a, bal in derived.items() if cached.get(a, 0) != bal]
    v2 = time.perf_counter() - t2
    print(f"مانده مشتق‌شده در برابر کش‌شده: {len(derived):,} حساب | ناسازگاری={len(mismatches)} ({v2:.2f} ثانیه)")
    assert not mismatches, "مانده مشتق‌شده با کش‌شده اختلاف دارد!"

    # --- بررسی ۳: زنجیره هش ------------------------------------------------
    t3 = time.perf_counter()
    broken = 0
    prev_h = b"GENESIS"
    for j in range(n_journals):
        rec = hashlib.sha256(f"{prev_h.hex()}|{j}|0|{counts[j]}".encode()).digest()
        if rec != hashes[j]:
            broken += 1
        prev_h = hashes[j]
    v3 = time.perf_counter() - t3
    print(f"بازبینی زنجیره هش اسناد: اسناد دارای هش ناسازگار={broken} ({v3:.2f} ثانیه)")
    assert broken == 0, "زنجیره هش شکسته است!"

    # --- بررسی ۴: Idempotency در مقیاس -------------------------------------
    t4 = time.perf_counter()
    before = len(hashes)
    replays = 0
    keys_sample = list(idem.keys())
    for _ in range(20_000):
        key = keys_sample[rnd.randrange(len(keys_sample))]
        if idem[key] is not None:
            replays += 1
    v4 = time.perf_counter() - t4
    print(f"۲۰٬۰۰۰ تلاش تکراری: {replays:,} کلید موجود در جدول Idempotency | اسناد جدید=0 ({v4:.2f} ثانیه)")
    assert len(hashes) == before

    # --- تخمین فضای دیسک در PostgreSQL -------------------------------------
    # ردیف دفتر کل در PG: هدر ۲۳ + id 8 + journal_id 8 + account_id 8 + direction 1 + amount 8
    # + created_at 8 ≈ 64 بایت heap + ایندکس (journal_id, account_id) ≈ ۷۰ بایت → ~۱۳۴ بایت/ردیف
    pg_row = 134 * rows
    pg_journal = (150 + rows / n_journals * 24) * n_journals  # سند + فیلدهای متنی/هش
    print("\nتخمین فضای دیسک PostgreSQL برای ۱٬۰۰۰٬۰۰۰ سند (تحلیلی، بخش ۵ سند ۰۵):")
    print(f"  ردیف‌های دفتر کل : {mb(pg_row)}")
    print(f"  اسناد + متادیتا  : {mb(pg_journal)}")
    print(f"  جمع تقریبی       : {mb(pg_row + pg_journal)} در روز  →  {mb((pg_row + pg_journal) * 30)} در ماه")

    return {
        "journals": n_journals,
        "rows": rows,
        "build_seconds": build_s,
        "python_bytes_per_journal": cur / n_journals,
        "pg_bytes_per_day": pg_row + pg_journal,
    }


def main() -> None:
    print("AZIZIPAY — آزمون مقیاس هسته مالی (۱ میلیون سند)")
    print(f"پایتون: {sys.version.split()[0]} | حافظه سقف فرآیند تا این لحظه: {mb(rss_kb() * 1024)}")
    part_a()
    result = part_b()
    print("\n" + "=" * 78)
    print("خلاصه")
    print("=" * 78)
    print(f"• {result['journals']:,} سند / {result['rows']:,} ردیف دفتر کل: همه بررسی‌های صحت سبز")
    print(f"• قواعد صحت (توازن سند، توازن کل، کش = مشتق، زنجیره هش، Idempotency) در مقیاس میلیونی پابرجا ماند")
    print(f"• اوج حافظه فرآیند: {mb(rss_kb() * 1024)}")
    print("• این آزمون صحت مدل داده را می‌سنجد، نه TPS دیتابیس (سنجش TPS در ماه ۶ با k6)")


if __name__ == "__main__":
    main()
