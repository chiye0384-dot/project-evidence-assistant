"""Conservative CNY reservations survive cancellation, restart and project deletion."""

from datetime import datetime, timezone, timedelta
import hashlib
import json
import re
import sqlite3
from uuid import uuid4

from .storage import ProjectStore

YUAN = 1_000_000
TOTAL_CEILING = 50 * YUAN
PROFILE = "deepseek-flash-2026-10-13"
# Verified official peak prices; cache/off-peak discounts are deliberately ignored.
INPUT_MICROYUAN_PER_TOKEN = 2
OUTPUT_MICROYUAN_PER_TOKEN = 8
# Above both common interpretations of the documented 1M model context.
INPUT_TOKEN_RESERVATION = 1_100_000
PRICE_VALID_UNTIL = "2026-10-14T00:00:00+00:00"


class SpendingError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__("Model spending gate stopped this request")


class SpendingLedger:
    def __init__(self, store: ProjectStore):
        self.store = store
        with store._connection() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS model_grants (
                id TEXT PRIMARY KEY, profile TEXT NOT NULL, total_limit INTEGER NOT NULL,
                trial_limit INTEGER NOT NULL, task_limit INTEGER NOT NULL,
                call_limit INTEGER NOT NULL, expires_at TEXT NOT NULL)""")
            # No project FK: deleting a learning project must not reset its spending.
            c.execute("""CREATE TABLE IF NOT EXISTS model_spend (
                id TEXT PRIMARY KEY, grant_id TEXT NOT NULL REFERENCES model_grants(id),
                task_id TEXT NOT NULL, reserved INTEGER NOT NULL, charged INTEGER,
                state TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER,
                created_at TEXT NOT NULL)""")
            c.execute("""CREATE TABLE IF NOT EXISTS model_price_rechecks (
                id TEXT PRIMARY KEY, profile TEXT NOT NULL, verified_at TEXT NOT NULL,
                valid_until TEXT NOT NULL, receipt_json TEXT NOT NULL)""")
            c.execute("""CREATE TABLE IF NOT EXISTS model_budget_authorizations (
                id TEXT PRIMARY KEY, grant_id TEXT NOT NULL REFERENCES model_grants(id),
                previous_trial_limit INTEGER NOT NULL, authorized_limit INTEGER NOT NULL,
                total_limit INTEGER NOT NULL, recorded_at TEXT NOT NULL, document_json TEXT NOT NULL)""")

    def apply_project_budget_authorization(self, document, *, confirmed):
        """Trusted local authorization update. Retains grant, spend and hard caps."""
        keys = {"grant_id", "profile", "currency", "total_limit_microyuan",
                "authorized_cumulative_limit_microyuan", "user_confirmation_sha256", "confirmed_at"}
        if (confirmed is not True or not isinstance(document, dict) or set(document) != keys
                or document["grant_id"] != PROFILE + "-initial" or document["profile"] != PROFILE
                or document["currency"] != "CNY"
                or type(document["total_limit_microyuan"]) is not int
                or type(document["authorized_cumulative_limit_microyuan"]) is not int
                or document["total_limit_microyuan"] != TOTAL_CEILING
                or document["authorized_cumulative_limit_microyuan"] != TOTAL_CEILING
                or not isinstance(document["user_confirmation_sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", document["user_confirmation_sha256"]) is None):
            raise SpendingError("not_authorized")
        try:
            checked = datetime.fromisoformat(document["confirmed_at"])
            if checked.tzinfo is None or checked > datetime.now(timezone.utc) + timedelta(seconds=30):
                raise ValueError
        except (ValueError, TypeError):
            raise SpendingError("not_authorized") from None
        payload = json.dumps(document, sort_keys=True, separators=(",", ":"))
        identifier = hashlib.sha256(payload.encode()).hexdigest()
        with self.store._connection() as c:
            c.execute("BEGIN IMMEDIATE")
            grant = c.execute("SELECT * FROM model_grants WHERE id=?", (document["grant_id"],)).fetchone()
            if grant is None:
                raise SpendingError("not_authorized")
            if (grant["profile"] != PROFILE or grant["total_limit"] != TOTAL_CEILING
                    or not 0 < grant["trial_limit"] <= TOTAL_CEILING):
                raise SpendingError("grant_conflict")
            if c.execute("SELECT 1 FROM model_spend WHERE state IN ('pending','uncertain','violation') LIMIT 1").fetchone():
                raise SpendingError("cost_uncertain")
            previous = grant["trial_limit"]
            c.execute("INSERT OR IGNORE INTO model_budget_authorizations VALUES (?,?,?,?,?,?,?)",
                      (identifier, grant["id"], previous, TOTAL_CEILING, grant["total_limit"],
                       datetime.now(timezone.utc).isoformat(), payload))
            c.execute("UPDATE model_grants SET trial_limit=? WHERE id=?", (TOTAL_CEILING, grant["id"]))
        return identifier

    def refresh_verified_price(self, document):
        """Trusted local unchanged-price proof; never grants scope or resets budget."""
        keys={"model","currency","input_microyuan_per_token","output_microyuan_per_token",
              "source_url","response_sha256","verified_at","valid_until"}
        if (not isinstance(document,dict) or set(document)!=keys or document['model']!='deepseek-flash'
                or document['currency']!='CNY'
                or type(document['input_microyuan_per_token']) is not int
                or type(document['output_microyuan_per_token']) is not int
                or document['input_microyuan_per_token']!=INPUT_MICROYUAN_PER_TOKEN
                or document['output_microyuan_per_token']!=OUTPUT_MICROYUAN_PER_TOKEN
                or document['source_url']!='https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'
                or not isinstance(document['response_sha256'],str)
                or not re.fullmatch(r'[0-9a-f]{64}',document['response_sha256'])):
            raise SpendingError('price_change_review')
        try:
            checked=datetime.fromisoformat(document['verified_at'])
            until=datetime.fromisoformat(document['valid_until'])
            current=datetime.now(timezone.utc)
            if (checked.tzinfo is None or until.tzinfo is None or checked>current+timedelta(seconds=30)
                    or not checked<until<=checked+timedelta(hours=24) or current>=until):
                raise ValueError
        except (ValueError,TypeError):
            raise SpendingError('price_expired') from None
        payload=json.dumps(document,sort_keys=True,separators=(',',':'))
        identifier=hashlib.sha256(payload.encode()).hexdigest()
        with self.store._connection() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute("SELECT 1 FROM model_spend WHERE state IN ('pending','uncertain','violation') LIMIT 1").fetchone():
                raise SpendingError('cost_uncertain')
            c.execute('INSERT OR IGNORE INTO model_price_rechecks VALUES (?,?,?,?,?)',
                      (identifier,PROFILE,checked.astimezone(timezone.utc).isoformat(),until.astimezone(timezone.utc).isoformat(),payload))
        return identifier

    @staticmethod
    def _price_until(c, grant):
        row=c.execute('SELECT max(valid_until) FROM model_price_rechecks WHERE profile=?',(grant['profile'],)).fetchone()
        original=datetime.fromisoformat(grant['expires_at'])
        return max(original,datetime.fromisoformat(row[0])) if row and row[0] else original

    def authorize(self, *, confirmed: bool, grant_id="first-trial", total_limit=50 * YUAN,
                  trial_limit=5 * YUAN, task_limit=3 * YUAN, call_limit=2_500_000,
                  expires_at=PRICE_VALID_UNTIL):
        """Trusted local startup only. No HTTP route grants paid-call permission."""
        if not confirmed:
            raise SpendingError("not_authorized")
        if any(type(n) is not int for n in (total_limit, trial_limit, task_limit, call_limit)):
            raise SpendingError("invalid_limits")
        if not 0 < call_limit <= task_limit <= trial_limit <= total_limit <= TOTAL_CEILING:
            raise SpendingError("invalid_limits")
        if datetime.fromisoformat(expires_at).tzinfo is None:
            raise SpendingError("invalid_limits")
        expected = (PROFILE, total_limit, trial_limit, task_limit, call_limit, expires_at)
        with self.store._connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT profile,total_limit,trial_limit,task_limit,call_limit,expires_at FROM model_grants WHERE id=?", (grant_id,)).fetchone()
            if row and tuple(row) != expected:
                raise SpendingError("grant_conflict")
            if not row:
                c.execute("INSERT INTO model_grants VALUES (?,?,?,?,?,?,?)", (grant_id, *expected))
        return grant_id

    @staticmethod
    def _amount(c, where="1=1", args=()):
        return c.execute(f"SELECT coalesce(sum(CASE WHEN charged IS NULL THEN reserved ELSE charged END),0) FROM model_spend WHERE {where}", args).fetchone()[0]

    def reserve(self, grant_id, task_id, max_output_tokens):
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 8000:
            raise SpendingError("invalid_limits")
        amount = INPUT_TOKEN_RESERVATION * INPUT_MICROYUAN_PER_TOKEN + max_output_tokens * OUTPUT_MICROYUAN_PER_TOKEN
        with self.store._connection() as c:
            c.execute("BEGIN IMMEDIATE")
            grant = c.execute("SELECT * FROM model_grants WHERE id=?", (grant_id,)).fetchone()
            if grant is None:
                raise SpendingError("not_authorized")
            if datetime.now(timezone.utc) >= self._price_until(c,grant):
                raise SpendingError("price_expired")
            # Any lost/invalid receipt stops subsequent calls, including restarts.
            if c.execute("SELECT 1 FROM model_spend WHERE state IN ('pending','uncertain','violation') LIMIT 1").fetchone():
                raise SpendingError("cost_uncertain")
            limits = (
                self._amount(c) + amount <= grant["total_limit"],
                self._amount(c, "grant_id=?", (grant_id,)) + amount <= grant["trial_limit"],
                self._amount(c, "task_id=?", (task_id,)) + amount <= grant["task_limit"],
                amount <= grant["call_limit"],
            )
            if not all(limits):
                raise SpendingError("spending_limit")
            identifier = str(uuid4())
            c.execute("INSERT INTO model_spend VALUES (?,?,?,?,NULL,'pending',NULL,NULL,?)",
                      (identifier, grant_id, task_id, amount, datetime.now(timezone.utc).isoformat()))
        return identifier

    def settle(self, identifier, input_tokens, output_tokens):
        if any(type(n) is not int or n < 0 for n in (input_tokens, output_tokens)):
            self.uncertain(identifier)
            raise SpendingError("cost_uncertain")
        charge = input_tokens * INPUT_MICROYUAN_PER_TOKEN + output_tokens * OUTPUT_MICROYUAN_PER_TOKEN
        with self.store._connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM model_spend WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise SpendingError("cost_uncertain")
            if row["state"] == "settled":
                if (row["input_tokens"], row["output_tokens"]) != (input_tokens, output_tokens):
                    raise SpendingError("receipt_conflict")
                return charge
            if row["state"] != "pending":
                raise SpendingError("cost_uncertain")
            state = "settled" if charge <= row["reserved"] else "violation"
            c.execute("UPDATE model_spend SET charged=?,state=?,input_tokens=?,output_tokens=? WHERE id=?",
                      (charge, state, input_tokens, output_tokens, identifier))
        if state == "violation":
            raise SpendingError("cost_uncertain")
        return charge

    def uncertain(self, identifier):
        with self.store._connection() as c:
            c.execute("UPDATE model_spend SET state='uncertain' WHERE id=? AND state='pending'", (identifier,))

    def summary(self, grant_id):
        with self.store._connection() as c:
            grant = c.execute("SELECT * FROM model_grants WHERE id=?", (grant_id,)).fetchone()
            encumbered = self._amount(c)
            accounted = c.execute("SELECT coalesce(sum(charged),0) FROM model_spend").fetchone()[0]
            unresolved = c.execute("SELECT count(*) FROM model_spend WHERE state IN ('pending','uncertain','violation')").fetchone()[0]
            trial = self._amount(c, "grant_id=?", (grant_id,))
            expiry=self._price_until(c,grant) if grant is not None else None
        if grant is None:
            return {"status": "not_authorized", "currency": "CNY"}
        return {"status": "cost_uncertain" if unresolved else "price_expired" if datetime.now(timezone.utc) >= expiry else "ready",
                "currency": "CNY", "profile": grant["profile"], "price_valid_until": expiry.isoformat(),
                "total_limit_microyuan": grant["total_limit"], "trial_limit_microyuan": grant["trial_limit"],
                "accounted_upper_bound_microyuan": accounted, "encumbered_microyuan": encumbered,
                "total_remaining_microyuan": max(0, grant["total_limit"] - encumbered),
                "trial_remaining_microyuan": max(0, grant["trial_limit"] - trial), "unresolved_requests": unresolved,
                "notice": "按核定高峰价计保守上限，未扣缓存／空闲优惠；供应商账单仍须核对。"}
