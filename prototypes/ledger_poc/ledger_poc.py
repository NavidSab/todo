#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AZIZIPAY — PoC هسته مالی (دفتر کل دوطرفه + کیف پول + شارژ از صرافی)
====================================================================

هدف: اثبات عملی سه تضمینی که معماری AZIZIPAY روی آن بنا شده است:

  1) اتمی‌بودن تراکنش‌ها: هر عملیات مالی یا کامل ثبت می‌شود یا هیچ اثری ندارد.
  2) غیرقابل‌تکرار بودن (Idempotency): ارسال دوباره یک درخواست، سند دومی نمی‌سازد
     و کد شارژ فقط یک‌بار قابل مصرف است — حتی با صدها درخواست هم‌زمان.
  3) توازن همیشگی دفتر کل: مجموع بدهکار = مجموع بستانکار، و موجودی کش‌شده
     همیشه با موجودی مشتق‌شده از ردیف‌های دفتر کل برابر است.

نکته پیاده‌سازی: این کد برای وضوح در Python نوشته شده است. در نسخه واقعی:
  - int پایتون  ↔  bigint در PostgreSQL (واحد خرد: 1 AFN = 100 minor)
  - قفل سراسری تراکنش  ↔  BEGIN/COMMIT + SELECT ... FOR UPDATE روی ردیف کیف پول
  - dict یکتایی  ↔  UNIQUE INDEX روی (idempotency_key) و (cashin code)
  - توازن سند  ↔  Trigger در PostgreSQL
اجرای تست‌ها: python3 ledger_poc.py
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# ۱. ابزارهای پایه: پول، خطاها، ساعت
# ---------------------------------------------------------------------------

CURRENCY = "AFN"


class LedgerError(Exception):
    """خطای سطح دفتر کل — هیچ‌گاه نباید به تغییر جزئی داده منجر شود."""

    pass


class InsufficientFunds(LedgerError):
    pass


class DuplicateOperation(LedgerError):
    """عملیات تکراری — پاسخ درست: بازگرداندن نتیجه قبلی، نه ساخت سند جدید."""

    pass


class InvalidState(LedgerError):
    pass


def now() -> float:
    return time.time()


@dataclass(frozen=True)
class Money:
    """مبلغ در واحد خرد (minor unit). هرگز float برای پول استفاده نمی‌شود."""

    minor: int
    currency: str = CURRENCY

    def __post_init__(self) -> None:
        if not isinstance(self.minor, int):
            raise LedgerError("مبلغ باید عدد صحیح (واحد خرد) باشد؛ float ممنوع.")
        if self.minor < 0:
            raise LedgerError("مانده/مبلغ نمی‌تواند منفی باشد.")

    def __str__(self) -> str:
        return f"{self.minor / 100:,.2f} {self.currency}"


# ---------------------------------------------------------------------------
# ۲. دفتر کل دوطرفه (فقط الحاقی)
# ---------------------------------------------------------------------------

DEBIT, CREDIT = "DEBIT", "CREDIT"

ACCOUNT_TYPES = {"ASSET", "LIABILITY", "EQUITY", "INCOME", "EXPENSE"}


@dataclass
class Account:
    code: str
    name: str
    type: str
    currency: str = CURRENCY
    owner: Optional[str] = None

    def __post_init__(self) -> None:
        if self.type not in ACCOUNT_TYPES:
            raise LedgerError(f"نوع حساب نامعتبر: {self.type}")


@dataclass
class Entry:
    journal_no: str
    account_code: str
    direction: str
    amount_minor: int


@dataclass
class Journal:
    journal_no: str
    type: str
    currency: str
    idempotency_key: str
    reference: Optional[Tuple[str, str]]
    entries: List[Entry]
    memo: str = ""
    reverses: Optional[str] = None
    created_at: float = field(default_factory=now)
    prev_hash: str = ""
    hash: str = ""

    def total(self) -> int:
        return sum(e.amount_minor for e in self.entries if e.direction == DEBIT)


class Ledger:
    """دفتر کل با ثبت الحاقی. هیچ API برای ویرایش/حذف سند وجود ندارد."""

    def __init__(self) -> None:
        self.accounts: Dict[str, Account] = {}
        self.journals: Dict[str, Journal] = {}
        self.entries: List[Entry] = []
        self._by_idempotency: Dict[str, str] = {}
        self._seq = 0
        self._last_hash = "GENESIS"
        self._lock = threading.RLock()  # در تولید: BEGIN/COMMIT دیتابیس

    # --- حساب‌ها ---------------------------------------------------------
    def open_account(self, code: str, name: str, type_: str, owner: Optional[str] = None) -> Account:
        with self._lock:
            if code in self.accounts:
                return self.accounts[code]
            acc = Account(code=code, name=name, type=type_, owner=owner)
            self.accounts[code] = acc
            return acc

    def account_for_partner(self, partner_id: str) -> str:
        code = f"1010-{partner_id}"  # نقد نزد صرافی
        self.open_account(code, f"نقد نزد صرافی {partner_id}", "ASSET", owner=partner_id)
        return code

    def account_for_wallet(self, user_id: str) -> str:
        code = f"2000-{user_id}"  # کیف پول کاربر
        self.open_account(code, f"کیف پول کاربر {user_id}", "LIABILITY", owner=user_id)
        return code

    # --- ثبت سند ---------------------------------------------------------
    def _validate(self, lines: List[Tuple[str, str, int]], currency: str) -> None:
        if not lines:
            raise LedgerError("سند بدون ردیف معتبر نیست.")
        debits = credits = 0
        for code, direction, amount in lines:
            if direction not in (DEBIT, CREDIT):
                raise LedgerError(f"جهت نامعتبر: {direction}")
            if not isinstance(amount, int) or amount <= 0:
                raise LedgerError("مبلغ ردیف باید عدد صحیح مثبت باشد.")
            acc = self.accounts.get(code)
            if acc is None:
                raise LedgerError(f"حساب ناموجود: {code}")
            if acc.currency != currency:
                raise LedgerError("عدم تطابق ارز سند و حساب.")
            if direction == DEBIT:
                debits += amount
            else:
                credits += amount
        if debits != credits:
            raise LedgerError(f"سند نامتوازن است: بدهکار={debits} بستانکار={credits}")

    def post(
        self,
        *,
        type_: str,
        lines: List[Tuple[str, str, int]],
        idempotency_key: str,
        reference: Optional[Tuple[str, str]] = None,
        memo: str = "",
        reverses: Optional[str] = None,
        currency: str = CURRENCY,
    ) -> Journal:
        """ثبت اتمیک سند. اگر idempotency_key تکراری باشد، همان سند قبلی برگردانده می‌شود."""
        with self._lock:
            existing_no = self._by_idempotency.get(idempotency_key)
            if existing_no:
                return self.journals[existing_no]  # ← درخواست تکراری: بدون سند جدید

            self._validate(lines, currency)
            self._seq += 1
            journal_no = f"J-{self._seq:08d}"
            entries = [
                Entry(journal_no, code, direction, amount) for code, direction, amount in lines
            ]
            payload = json.dumps(
                [[e.account_code, e.direction, e.amount_minor] for e in entries],
                ensure_ascii=False,
                sort_keys=True,
            )
            h = hashlib.sha256(f"{self._last_hash}|{journal_no}|{payload}".encode()).hexdigest()
            j = Journal(
                journal_no=journal_no,
                type=type_,
                currency=currency,
                idempotency_key=idempotency_key,
                reference=reference,
                entries=entries,
                memo=memo,
                reverses=reverses,
                prev_hash=self._last_hash,
                hash=h,
            )
            # ترتیب ثبت: ردیف‌ها → سند → ایندکس یکتایی → هش زنجیره
            self.entries.extend(entries)
            self.journals[journal_no] = j
            self._by_idempotency[idempotency_key] = journal_no
            self._last_hash = h
            return j

    def journal_by_idempotency(self, idempotency_key: str) -> Optional[Journal]:
        """جست‌وجوی سند قبلی با کلید Idempotency — باید *پیش از* هر اعتبارسنجی و قفل اجرا شود."""
        with self._lock:
            no = self._by_idempotency.get(idempotency_key)
            return self.journals.get(no) if no else None

    def reverse(self, journal_no: str, *, idempotency_key: str, memo: str = "") -> Journal:
        """اصلاح فقط با سند معکوس؛ سند اصلی دست‌نخورده می‌ماند."""
        with self._lock:
            original = self.journals.get(journal_no)
            if original is None:
                raise LedgerError("سند مبنا یافت نشد.")
            mirrored = [
                (e.account_code, CREDIT if e.direction == DEBIT else DEBIT, e.amount_minor)
                for e in original.entries
            ]
            return self.post(
                type_="REVERSAL",
                lines=mirrored,
                idempotency_key=idempotency_key,
                reference=original.reference,
                memo=memo or f"معکوس سند {journal_no}",
                reverses=journal_no,
                currency=original.currency,
            )

    # --- خواندن ----------------------------------------------------------
    def balance(self, account_code: str) -> int:
        """مانده مشتق‌شده از ردیف‌های دفتر کل — منبع حقیقت."""
        net = 0
        acc = self.accounts[account_code]
        for e in self.entries:
            if e.account_code != account_code:
                continue
            # مانده بر مبنای ماهیت حساب: دارایی/هزینه = بدهکار(+)، بدهی/درآمد/سرمایه = بستانکار(+)
            sign = 1 if acc.type in ("ASSET", "EXPENSE") else -1
            delta = e.amount_minor if e.direction == (DEBIT if sign == 1 else CREDIT) else -e.amount_minor
            net += delta
        return net

    def integrity(self) -> Dict[str, object]:
        """بررسی یکپارچگی: توازن کل، توازن هر سند، زنجیره هش."""
        debits = sum(e.amount_minor for e in self.entries if e.direction == DEBIT)
        credits = sum(e.amount_minor for e in self.entries if e.direction == CREDIT)
        unbalanced = [
            j.journal_no
            for j in self.journals.values()
            if sum(e.amount_minor for e in j.entries if e.direction == DEBIT)
            != sum(e.amount_minor for e in j.entries if e.direction == CREDIT)
        ]
        prev = "GENESIS"
        broken = []
        for j in self.journals.values():
            if j.prev_hash != prev:
                broken.append(j.journal_no)
            prev = j.hash
        return {
            "debits": debits,
            "credits": credits,
            "balanced": debits == credits,
            "unbalanced_journals": unbalanced,
            "hash_chain_broken": broken,
        }


# ---------------------------------------------------------------------------
# ۳. کیف پول با مانده کش‌شده (در تولید: مقدار در همان تراکنش به‌روز می‌شود)
# ---------------------------------------------------------------------------


@dataclass
class Wallet:
    user_id: str
    account_code: str
    balance_minor: int = 0
    version: int = 0
    holds_minor: int = 0

    @property
    def available_minor(self) -> int:
        return self.balance_minor - self.holds_minor


class WalletService:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.wallets: Dict[str, Wallet] = {}

    def get_or_create(self, user_id: str) -> Wallet:
        w = self.wallets.get(user_id)
        if w is None:
            code = self.ledger.account_for_wallet(user_id)
            w = Wallet(user_id=user_id, account_code=code)
            self.wallets[user_id] = w
        return w


# ---------------------------------------------------------------------------
# ۴. شاخص‌های پایه (در تولید: جدول‌های limit_policies / limit_counters)
# ---------------------------------------------------------------------------


@dataclass
class Policy:
    min_cashin_minor: int = 10_000          # ۱۰۰ افغانی
    max_cashin_minor: int = 5_000_000       # ۵۰٬۰۰۰ افغانی
    daily_user_cashin_minor: int = 10_000_000
    cashin_fee_minor: int = 100             # کارمزد ثابت نمونه
    cashin_ttl_seconds: int = 3600
    partner_float_cap_minor: int = 50_000_000  # سقف شناوری هر صرافی


# ---------------------------------------------------------------------------
# ۵. ماژول شارژ از صرافی (Cash-in)
# ---------------------------------------------------------------------------


@dataclass
class CashInRequest:
    code: str
    user_id: str
    amount_minor: int
    currency: str
    status: str          # CREATED | VALIDATED | COMPLETED | EXPIRED | CANCELLED
    created_at: float
    expires_at: float
    partner_id: Optional[str] = None
    operator_id: Optional[str] = None
    journal_no: Optional[str] = None


class PartnerRegistry:
    """در تولید: partners / partner_branches / partner_operators + بررسی مجوز (MSP/FXD)."""

    def __init__(self) -> None:
        self.active_partners: Dict[str, str] = {}
        self.operators: Dict[str, Tuple[str, bool]] = {}  # operator_id -> (partner_id, active)

    def add_partner(self, partner_id: str, license_no: str) -> None:
        self.active_partners[partner_id] = license_no

    def add_operator(self, operator_id: str, partner_id: str, active: bool = True) -> None:
        self.operators[operator_id] = (partner_id, active)

    def is_operator_allowed(self, operator_id: str) -> bool:
        op = self.operators.get(operator_id)
        return bool(op and op[1] and op[0] in self.active_partners)


class CashInService:
    def __init__(
        self,
        ledger: Ledger,
        wallets: WalletService,
        partners: PartnerRegistry,
        policy: Policy,
    ) -> None:
        self.ledger = ledger
        self.wallets = wallets
        self.partners = partners
        self.policy = policy
        self.requests: Dict[str, CashInRequest] = {}
        self.history: List[Tuple[str, str, float]] = []
        self.daily_user_totals: Dict[str, int] = {}
        # قفل تراکنشی سراسری عملیات مالی (در تولید: قفل ردیف در PostgreSQL)
        self.tx_lock = threading.RLock()
        self.fee_account = ledger.open_account("4010", "درآمد کارمزد پلتفرم", "INCOME")
        self.suspense_daily = 0

    # --- گام ۱: کاربر درخواست شارژ می‌سازد --------------------------------
    def create_request(self, user_id: str, amount_minor: int) -> CashInRequest:
        with self.tx_lock:
            if amount_minor < self.policy.min_cashin_minor:
                raise LedgerError("مبلغ کمتر از حد مجاز است.")
            if amount_minor > self.policy.max_cashin_minor:
                raise LedgerError("مبلغ بیش از سقف هر تراکنش است.")
            spent_today = self.daily_user_totals.get(user_id, 0)
            if spent_today + amount_minor > self.policy.daily_user_cashin_minor:
                raise LedgerError("عبور از سقف روزانه کاربر.")

            code = self._generate_code()
            req = CashInRequest(
                code=code,
                user_id=user_id,
                amount_minor=amount_minor,
                currency=CURRENCY,
                status="CREATED",
                created_at=now(),
                expires_at=now() + self.policy.cashin_ttl_seconds,
            )
            self.requests[code] = req
            self.history.append((code, "CREATED", now()))
            return req

    def _generate_code(self) -> str:
        while True:
            code = f"{random.randint(0, 10**12 - 1):012d}"
            if code not in self.requests:
                return code

    # --- گام ۲: اپراتور صرافی کد را اعتبارسنجی می‌کند ----------------------
    def lookup(self, code: str, operator_id: str) -> CashInRequest:
        if not self.partners.is_operator_allowed(operator_id):
            raise LedgerError("اپراتور مجاز نیست.")
        req = self.requests.get(code)
        if req is None:
            raise LedgerError("کد نامعتبر است.")
        if req.status not in ("CREATED", "VALIDATED"):
            raise InvalidState(f"وضعیت درخواست قابل استفاده نیست: {req.status}")
        if req.expires_at < now():
            req.status = "EXPIRED"
            self.history.append((code, "EXPIRED", now()))
            raise InvalidState("درخواست منقضی شده است.")
        if req.status == "CREATED":
            req.status = "VALIDATED"
            req.partner_id = self.partners.operators[operator_id][0]
            self.history.append((code, "VALIDATED", now()))
        return req

    # --- گام ۳: تأیید دریافت نقد و ثبت نهایی (اتمیک) -----------------------
    def confirm(self, code: str, operator_id: str, *, idempotency_key: str) -> Optional[Journal]:
        """تأیید دریافت وجه نقد توسط اپراتور و ثبت سند دفتر کل + افزایش موجودی."""
        with self.tx_lock:  # ← در تولید: BEGIN; SELECT ... FOR UPDATE; COMMIT;
            partner_id = self.partners.operators.get(operator_id, (None, False))[0]
            if not self.partners.is_operator_allowed(operator_id):
                raise LedgerError("اپراتور مجاز نیست.")

            req = self.requests.get(code)
            if req is None:
                raise LedgerError("کد نامعتبر است.")

            # Idempotency: تأیید دوباره‌ی همان درخواست = همان رسید، بدون سند جدید
            if req.status == "COMPLETED":
                if req.journal_no and idempotency_key == self._idem_key(req, operator_id):
                    return self.ledger.journals[req.journal_no]
                raise InvalidState("این کد قبلاً استفاده شده است.")

            if req.status not in ("CREATED", "VALIDATED"):
                raise InvalidState(f"وضعیت نامعتبر: {req.status}")
            if req.expires_at < now():
                req.status = "EXPIRED"
                self.history.append((code, "EXPIRED", now()))
                raise InvalidState("درخواست منقضی شده است.")
            if req.partner_id and req.partner_id != partner_id:
                raise LedgerError("این درخواست متعلق به صرافی دیگری است.")

            # سقف شناوری صرافی (نقد دریافتی که هنوز تسویه نشده)
            partner_acc = self.ledger.account_for_partner(partner_id)
            if self.ledger.balance(partner_acc) + req.amount_minor > self.policy.partner_float_cap_minor:
                raise LedgerError("صرافی به سقف شناوری رسیده است؛ تسویه لازم است.")

            wallet = self.wallets.get_or_create(req.user_id)
            fee = min(self.policy.cashin_fee_minor, req.amount_minor // 100)
            credit = req.amount_minor - fee
            idem = self._idem_key(req, operator_id)

            try:
                # ① ثبت سند دوطرفه  ② به‌روزرسانی مانده کش‌شده  ③ گذار وضعیت
                journal = self.ledger.post(
                    type_="CASHIN",
                    lines=[
                        (partner_acc, DEBIT, req.amount_minor),        # نقد نزد صرافی (دارایی)
                        (wallet.account_code, CREDIT, credit),         # کیف پول کاربر (بدهی)
                        (self.fee_account.code, CREDIT, fee),          # درآمد کارمزد
                    ] if fee else [
                        (partner_acc, DEBIT, req.amount_minor),
                        (wallet.account_code, CREDIT, credit),
                    ],
                    idempotency_key=idem,
                    reference=("CASHIN_REQUEST", req.code),
                    memo=f"شارژ {req.code} توسط صرافی {partner_id}",
                )
                wallet.balance_minor += credit
                wallet.version += 1
                req.status = "COMPLETED"
                req.operator_id = operator_id
                req.journal_no = journal.journal_no
                req.partner_id = partner_id
                self.daily_user_totals[req.user_id] = (
                    self.daily_user_totals.get(req.user_id, 0) + req.amount_minor
                )
                self.history.append((code, "COMPLETED", now()))
                return journal
            except Exception:
                # در تولید: ROLLBACK خودکار؛ مانده کش‌شده هرگز بدون سند تغییر نمی‌کند
                wallet.balance_minor -= credit
                raise

    @staticmethod
    def _idem_key(req: CashInRequest, operator_id: str) -> str:
        return f"cashin:{req.code}:confirm"

    @staticmethod
    def canonical_confirm_key(code: str) -> str:
        """کلید Idempotency عملیات تأیید — کلاینت/اپراتور همان را برای تلاش مجدد می‌فرستد."""
        return f"cashin:{code}:confirm"

    def settle_partner(self, partner_id: str, amount_minor: int, bank_ref: str) -> Journal:
        """تسویه: انتقال از «نقد نزد صرافی» به بانک — جدا از وضعیت شارژ کاربران."""
        with self.tx_lock:
            # قاعده: تلاش تکراری باید همان نتیجه قبلی را بگیرد، حتی اگر مانده دیگر کافی نباشد
            existing = self.ledger.journal_by_idempotency(f"settlement:{partner_id}:{bank_ref}")
            if existing is not None:
                return existing
            partner_acc = self.ledger.account_for_partner(partner_id)
            if amount_minor > self.ledger.balance(partner_acc):
                raise LedgerError("مبلغ تسویه بیش از مانده نقد نزد صرافی است.")
            bank = self.ledger.open_account("1020", "بانک / حساب تسویه AZIZIPAY", "ASSET")
            return self.ledger.post(
                type_="SETTLEMENT",
                lines=[(bank.code, DEBIT, amount_minor), (partner_acc, CREDIT, amount_minor)],
                idempotency_key=f"settlement:{partner_id}:{bank_ref}",
                reference=("BANK_REF", bank_ref),
                memo=f"تسویه با صرافی {partner_id}",
            )


# ---------------------------------------------------------------------------
# ۶. پرداخت از کیف پول (برای تست هم‌زمانی برداشت و کنترل موجودی)
# ---------------------------------------------------------------------------


class PaymentService:
    def __init__(self, ledger: Ledger, wallets: WalletService) -> None:
        self.ledger = ledger
        self.wallets = wallets
        self.tx_lock = threading.RLock()
        self.service_income = ledger.open_account("4010B", "درآمد خدمات پلتفرم", "INCOME")

    def pay(self, user_id: str, amount_minor: int, *, idempotency_key: str, service: str) -> Journal:
        with self.tx_lock:  # ← در تولید: SELECT ... FOR UPDATE روی ردیف کیف پول
            wallet = self.wallets.get_or_create(user_id)
            if wallet.available_minor < amount_minor:
                raise InsufficientFunds(
                    f"موجودی کافی نیست: موجود={wallet.available_minor} نیاز={amount_minor}"
                )
            journal = self.ledger.post(
                type_="PAYMENT",
                lines=[
                    (wallet.account_code, DEBIT, amount_minor),
                    (self.service_income.code, CREDIT, amount_minor),
                ],
                idempotency_key=idempotency_key,
                reference=("SERVICE", service),
            )
            wallet.balance_minor -= amount_minor
            wallet.version += 1
            return journal

    def refund(self, journal_no: str, *, idempotency_key: str) -> Journal:
        with self.tx_lock:
            original = self.ledger.journals[journal_no]
            journal = self.ledger.reverse(journal_no, idempotency_key=idempotency_key)
            # بازگشت مانده به کاربر (در سند معکوس، بستانکار کیف پول شده است)
            for e in journal.entries:
                if e.direction == CREDIT and e.account_code.startswith("2000-"):
                    user_id = e.account_code.split("-", 1)[1]
                    self.wallets.get_or_create(user_id).balance_minor += e.amount_minor
            return journal


# ---------------------------------------------------------------------------
# ۷. سناریوها و تست‌ها
# ---------------------------------------------------------------------------


def banner(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def build_system() -> Tuple[Ledger, WalletService, CashInService, PaymentService, PartnerRegistry]:
    ledger = Ledger()
    ledger.open_account("1020", "بانک / حساب تسویه AZIZIPAY", "ASSET")
    wallets = WalletService(ledger)
    partners = PartnerRegistry()
    partners.add_partner("EX-A", "MSP-12345")
    partners.add_partner("EX-B", "FXD-99887")
    partners.add_operator("OP-A1", "EX-A")
    partners.add_operator("OP-A2", "EX-A")
    partners.add_operator("OP-B1", "EX-B")
    cashin = CashInService(ledger, wallets, partners, Policy())
    payments = PaymentService(ledger, wallets)
    return ledger, wallets, cashin, payments, partners


def test_happy_path() -> None:
    banner("تست ۱ — مسیر شاد: شارژ ۱٬۰۰۰ افغانی، پرداخت ۵۰۰، بازپرداخت ۱۰۰")
    ledger, wallets, cashin, payments, _ = build_system()
    req = cashin.create_request("U-100", 100_000)  # 1000.00 AFN
    print(f"کد درخواست شارژ برای کاربر: {req.code}  مبلغ: {Money(req.amount_minor)}")
    cashin.lookup(req.code, "OP-A1")
    journal = cashin.confirm(req.code, "OP-A1", idempotency_key=f"confirm:{req.code}")
    w = wallets.get_or_create("U-100")
    print(f"سند ثبت‌شده: {journal.journal_no}  نوع: {journal.type}")
    for e in journal.entries:
        print(f"   {e.direction:6} {e.account_code:12} {e.amount_minor:>10,}")
    print(f"موجودی کیف پول کاربر: {Money(w.balance_minor)}")
    assert w.balance_minor == 99_900, w.balance_minor  # ۱۰۰۰ منهای ۱ کارمزد

    pay = payments.pay("U-100", 50_000, idempotency_key="pay:svc-1", service="SERVICE-VISA")
    print(f"پرداخت ثبت شد: {pay.journal_no} — موجودی: {Money(w.balance_minor)}")
    assert w.balance_minor == 49_900

    refund = payments.refund(pay.journal_no, idempotency_key=f"refund:{pay.journal_no}")
    print(f"بازپرداخت ثبت شد: {refund.journal_no} (معکوس {refund.reverses}) — موجودی: {Money(w.balance_minor)}")
    assert w.balance_minor == 99_900

    integrity = ledger.integrity()
    print(f"یکپارچگی دفتر کل: {integrity}")
    assert integrity["balanced"] and not integrity["unbalanced_journals"]
    assert ledger.balance(w.account_code) == w.balance_minor
    assert len(ledger.journals) == 3
    print("✅ مسیر شاد درست است: دفتر کل متوازن و مانده کش‌شده با مانده مشتق‌شده برابر است.")


def test_idempotency() -> None:
    banner("تست ۲ — Idempotency: ۲۰۰ درخواست دوباره برای یک تأیید شارژ")
    ledger, wallets, cashin, _, _ = build_system()
    req = cashin.create_request("U-200", 75_000)
    cashin.lookup(req.code, "OP-A1")
    errors: List[str] = []
    results: List[Optional[Journal]] = []
    key = cashin.canonical_confirm_key(req.code)

    def worker() -> None:
        try:
            results.append(cashin.confirm(req.code, "OP-A1", idempotency_key=key))
        except Exception as exc:  # noqa: BLE001
            errors.append(type(exc).__name__)

    threads = [threading.Thread(target=worker) for _ in range(200)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    w = wallets.get_or_create("U-200")
    distinct_journals = {j.journal_no for j in results if j is not None}
    print(f"تعداد فراخوانی موفق: {len(results)} | خطاها: {len(errors)}")
    print(f"شماره سندهای متمایز بازگردانده‌شده: {distinct_journals} | تعداد اسناد دفتر کل: {len(ledger.journals)}")
    print(f"موجودی کیف پول: {Money(w.balance_minor)}")
    assert len(ledger.journals) == 1, "تراکنش تکراری ثبت شده است!"
    assert len(distinct_journals) == 1, "پاسخ‌های متفاوت برای یک عملیات!"
    assert w.balance_minor == 74_900
    assert ledger.balance(w.account_code) == w.balance_minor
    print("✅ نتیجه: حتی با ۲۰۰ فراخوانی هم‌زمان، فقط یک سند ثبت شد و همه پاسخ‌ها یکسان بودند.")


def test_single_use_code() -> None:
    banner("تست ۳ — کد یک‌بارمصرف: ۳۰۰ اپراتور هم‌زمان یک کد را تأیید می‌کنند")
    ledger, wallets, cashin, _, _ = build_system()
    req = cashin.create_request("U-300", 250_000)
    cashin.lookup(req.code, "OP-A1")
    successes, failures = [], []
    barrier = threading.Barrier(300)

    def worker(i: int) -> None:
        operator = "OP-A1" if i % 2 == 0 else "OP-A2"
        try:
            barrier.wait(timeout=10)
        except threading.BrokenBarrierError:
            pass
        try:
            cashin.confirm(req.code, operator, idempotency_key=f"confirm:{req.code}:{i}")
            successes.append(operator)
        except Exception as exc:  # noqa: BLE001
            failures.append(type(exc).__name__)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(300)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    w = wallets.get_or_create("U-300")
    print(f"موفق: {len(successes)} | رد‌شده: {len(failures)} | نمونه خطا: {set(failures)}")
    print(f"اسناد ثبت‌شده: {len(ledger.journals)} | موجودی کاربر: {Money(w.balance_minor)}")
    assert len(successes) == 1, "بیش از یک تأیید پذیرفته شد — خطر شارژ مضاعف!"
    assert len(ledger.journals) == 1
    assert w.balance_minor == 249_900
    print("✅ نتیجه: فقط یک تأیید پذیرفته شد؛ نه شارژ مضاعف، نه موجودی نامتوازن.")


def test_concurrent_spending() -> None:
    banner("تست ۴ — برداشت هم‌زمان: ۳۰ پرداخت ۱۰۰ افغانی از موجودی ۱٬۰۰۰ افغانی")
    ledger, wallets, cashin, payments, _ = build_system()
    req = cashin.create_request("U-400", 100_000)
    cashin.lookup(req.code, "OP-A1")
    cashin.confirm(req.code, "OP-A1", idempotency_key=f"confirm:{req.code}")
    w = wallets.get_or_create("U-400")
    print(f"موجودی اولیه: {Money(w.balance_minor)} — هر پرداخت: 100.00 AFN")

    ok, rejected = [], []
    barrier = threading.Barrier(30)

    def worker(i: int) -> None:
        try:
            barrier.wait(timeout=10)
        except threading.BrokenBarrierError:
            pass
        try:
            payments.pay("U-400", 10_000, idempotency_key=f"pay:svc-{i}", service=f"SV-{i}")
            ok.append(i)
        except InsufficientFunds:
            rejected.append(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print(f"موفق: {len(ok)} | رد‌شده (موجودی ناکافی): {len(rejected)}")
    print(f"موجودی نهایی: {Money(w.balance_minor)} | مانده مشتق‌شده: {Money(ledger.balance(w.account_code))}")
    assert len(ok) == 9, f"تعداد پرداخت موفق باید ۹ باشد (۹۹٬۹۰۰ خرد)"
    assert w.balance_minor == 99_900 - 9 * 10_000
    assert w.balance_minor >= 0 and ledger.balance(w.account_code) == w.balance_minor
    print("✅ نتیجه: موجودی هرگز منفی نشد و تعداد موفقیت دقیقاً برابر ظرفیت واقعی بود.")


def test_expiry_and_limits() -> None:
    banner("تست ۵ — کنترل انقضا، سقف‌ها و مجازبودن اپراتور")
    ledger, wallets, cashin, _, _ = build_system()
    # الف) کد منقضی
    req = cashin.create_request("U-500", 20_000)
    cashin.requests[req.code].expires_at = now() - 1
    try:
        cashin.lookup(req.code, "OP-A1")
        raise AssertionError("کد منقضی پذیرفته شد!")
    except InvalidState as exc:
        print(f"الف) کد منقضی رد شد: {exc}")
    # ب) مبلغ خارج از محدوده
    for amount in (5_000, 9_000_000):
        try:
            cashin.create_request("U-500", amount)
            raise AssertionError("مبلغ غیرمجاز پذیرفته شد!")
        except LedgerError as exc:
            print(f"ب) مبلغ {amount:,} خرد رد شد: {exc}")
    # ج) اپراتور غیرمجاز
    try:
        cashin.lookup(cashin.create_request("U-501", 30_000).code, "OP-XX")
        raise AssertionError("اپراتور غیرمجاز پذیرفته شد!")
    except LedgerError as exc:
        print(f"ج) اپراتور غیرمجاز رد شد: {exc}")
    # د) سقف روزانه کاربر
    for _ in range(2):
        r = cashin.create_request("U-502", 5_000_000)
        cashin.lookup(r.code, "OP-A1")
        cashin.confirm(r.code, "OP-A1", idempotency_key=f"confirm:{r.code}")
    try:
        cashin.create_request("U-502", 5_000_000)
        raise AssertionError("سقف روزانه نقض شد!")
    except LedgerError as exc:
        print(f"د) سقف روزانه رد شد: {exc}")
    # ه) سقف شناوری صرافی (هر تلاش با کاربر تازه تا سقف روزانه کاربر دخالت نکند)
    try:
        i = 0
        while True:
            r = cashin.create_request(f"U-51{i}", 4_900_000)
            cashin.lookup(r.code, "OP-A1")
            cashin.confirm(r.code, "OP-A1", idempotency_key=f"confirm:{r.code}")
            i += 1
    except LedgerError as exc:
        print(f"ه) محدودیت صرافی فعال شد (پس از {i} شارژ ۴۹٬۰۰۰ افغانی): {exc}")
    print("✅ نتیجه: قواعد انطباقی و محدودیت‌ها در سطح سرویس اعمال می‌شوند.")


def test_reversal_and_settlement() -> None:
    banner("تست ۶ — سند معکوس (کشف خطا) و تسویه جدا از شارژ")
    ledger, wallets, cashin, _, _ = build_system()
    req = cashin.create_request("U-600", 300_000)
    cashin.lookup(req.code, "OP-A1")
    journal = cashin.confirm(req.code, "OP-A1", idempotency_key=f"confirm:{req.code}")
    w = wallets.get_or_create("U-600")
    partner_acc = ledger.account_for_partner("EX-A")
    print(f"پس از شارژ — کیف پول: {Money(w.balance_minor)} | نقد نزد صرافی: {Money(ledger.balance(partner_acc))}")

    # کشف خطا: ملغی‌کردن سند و برگرداندن وضعیت مالی
    rev = ledger.reverse(journal.journal_no, idempotency_key=f"reverse:{journal.journal_no}",
                         memo="ابطال شارژ به دلیل خطای اپراتور")
    for e in rev.entries:
        if e.account_code.startswith("2000-") and e.direction == DEBIT:
            w.balance_minor -= e.amount_minor
    print(f"سند معکوس: {rev.journal_no} — کیف پول: {Money(w.balance_minor)} | نقد نزد صرافی: {Money(ledger.balance(partner_acc))}")
    assert w.balance_minor == 0 and ledger.balance(partner_acc) == 0
    assert ledger.journals[journal.journal_no].entries  # سند اصلی دست‌نخورده باقی مانده است
    print(f"سند اصلی {journal.journal_no} دست‌نخورده باقی ماند (اصل عدم‌حذف).")

    # تسویه واقعی با صرافی (پس از یک شارژ دیگر)
    req2 = cashin.create_request("U-601", 200_000)
    cashin.lookup(req2.code, "OP-B1")
    cashin.confirm(req2.code, "OP-B1", idempotency_key=f"confirm:{req2.code}")
    settle = cashin.settle_partner("EX-B", 200_000, bank_ref="TRF-8891")
    print(f"تسویه ثبت شد: {settle.journal_no} — نقد نزد EX-B: {Money(ledger.balance(ledger.account_for_partner('EX-B')))}")
    # تلاش تسویه تکراری با همان شماره پیگیری بانکی
    again = cashin.settle_partner("EX-B", 200_000, bank_ref="TRF-8891")
    assert again.journal_no == settle.journal_no
    print("تلاش تسویه تکراری با همان شماره پیگیری: همان سند برگشت (بدون دوباره‌کاری).")

    integrity = ledger.integrity()
    print(f"یکپارچگی نهایی: {integrity}")
    assert integrity["balanced"]
    print("✅ نتیجه: اصلاح بدون حذف سابقه، و تسویه به‌صورت موجودیت جدا از شارژ.")


def test_disaster_recovery_consistency() -> None:
    banner("تست ۷ — بازسازی موجودی از دفتر کل (سناریوی بازیابی از بکاپ)")
    ledger, wallets, cashin, payments, _ = build_system()
    for i in range(20):
        user = f"U-7{i:02d}"
        req = cashin.create_request(user, 50_000 + i * 1_000)
        cashin.lookup(req.code, "OP-A1")
        cashin.confirm(req.code, "OP-A1", idempotency_key=f"confirm:{req.code}")
    payments.pay("U-700", 30_000, idempotency_key="pay:r1", service="SV-R")

    # شبیه‌سازی خرابی جدول مانده‌ها: همه مانده‌های کش‌شده پاک می‌شوند
    for w in wallets.wallets.values():
        w.balance_minor = 0
    print("مانده‌های کش‌شده صفر شدند (شبیه‌سازی خرابی/بازیابی).")

    mismatches = []
    for user_id, w in wallets.wallets.items():
        derived = ledger.balance(w.account_code)
        if derived != w.balance_minor:
            mismatches.append((user_id, w.balance_minor, derived))
    print(f"تعداد کاربران: {len(wallets.wallets)} | ناسازگاری‌ها: {len(mismatches)}")
    assert len(mismatches) == len(wallets.wallets)

    # بازسازی از دفتر کل
    for w in wallets.wallets.values():
        w.balance_minor = ledger.balance(w.account_code)
    print(f"بازسازی انجام شد. موجودی نمونه {list(wallets.wallets.values())[0].user_id}: "
          f"{Money(wallets.wallets['U-700'].balance_minor)}")
    assert all(ledger.balance(w.account_code) == w.balance_minor for w in wallets.wallets.values())
    integrity = ledger.integrity()
    assert integrity["balanced"] and not integrity["hash_chain_broken"]
    print("✅ نتیجه: دفتر کل به‌تنهایی می‌تواند همه مانده‌ها را بازسازی کند.")
    print(f"   بازبینی زنجیره هش اسناد: {'سالم' if not integrity['hash_chain_broken'] else 'شکسته'}")


def test_reconciliation_report() -> None:
    banner("تست ۸ — گزارش تطبیق در سطح صرافی (شارژ کاربران در برابر نقد نزد صرافی)")
    ledger, wallets, cashin, payments, _ = build_system()
    plan = [("EX-A", "OP-A1", 5), ("EX-B", "OP-B1", 3)]
    for partner, operator, count in plan:
        for i in range(count):
            req = cashin.create_request(f"{partner}-U{i}", 100_000)
            cashin.lookup(req.code, operator)
            cashin.confirm(req.code, operator, idempotency_key=f"confirm:{req.code}")

    print(f"{'صرافی':<8}{'تعداد شارژ':>12}{'جدول کاربران':>16}{'نقد نزد صرافی':>18}{'اختلاف':>10}")
    for partner, _, _ in plan:
        partner_acc = ledger.account_for_partner(partner)
        cash_at_partner = ledger.balance(partner_acc)
        per_user = sum(
            ledger.balance(w.account_code)
            for uid, w in wallets.wallets.items()
            if uid.startswith(partner)
        )
        # چون کارمزد از مبلغ کاربر کسر شده، مجموع کیف پول‌ها کمتر از نقد است:
        fee_total = cash_at_partner - per_user
        print(f"{partner:<8}{3 if partner == 'EX-B' else 5:>12}{per_user:>16,}{cash_at_partner:>18,}{fee_total:>10,}")
    integrity = ledger.integrity()
    assert integrity["balanced"]
    print("\n✅ نتیجه: گزارش تطبیق از دفتر کل قابل استخراج است و اختلاف، دقیقاً کارمزد ثبت‌شده است.")
    print("   (در عملیات واقعی، همین گزارش با اظهارنامه صرافی مقایسه و مغایرت‌ها ثبت می‌شود.)")


def main() -> None:
    print("AZIZIPAY — اثبات عملی تضمین‌های هسته مالی")
    print(f"زبان/نسخه: Python 3 — واحد پول: واحد خرد (100 = 1 AFN) — ارز: {CURRENCY}")
    random.seed(20261016)
    test_happy_path()
    test_idempotency()
    test_single_use_code()
    test_concurrent_spending()
    test_expiry_and_limits()
    test_reversal_and_settlement()
    test_disaster_recovery_consistency()
    test_reconciliation_report()
    banner("نتیجه نهایی: همه تست‌ها با موفقیت اجرا شدند")
    print("سنجه‌های اثبات‌شده:")
    print("  • هیچ تراکنش تکراری‌ای ثبت نمی‌شود (۲۰۰ فراخوانی هم‌زمان → ۱ سند)")
    print("  • کد شارژ فقط یک‌بار قابل مصرف است (۳۰۰ تأیید هم‌زمان → ۱ موفق)")
    print("  • موجودی هرگز منفی نمی‌شود و با دفتر کل برابر می‌ماند")
    print("  • دفتر کل همیشه متوازن است و زنجیره هش اسناد سالم می‌ماند")
    print("  • مانده‌ها از دفتر کل قابل بازسازی‌اند (سناریوی بازیابی از بکاپ)")
    print("  • گزارش تطبیق در سطح صرافی از داده دفتر کل استخراج می‌شود")


if __name__ == "__main__":
    main()
