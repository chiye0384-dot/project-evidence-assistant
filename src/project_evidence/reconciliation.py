"""Local, human-reviewed daily usage reconciliation; never calls a provider."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .spending import PROFILE, INPUT_MICROYUAN_PER_TOKEN, OUTPUT_MICROYUAN_PER_TOKEN

CHINA = timezone(timedelta(hours=8))


class ReconciliationError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _checked(proof):
    keys = {"schema_version", "source", "model", "timezone", "reservation_id", "task_id",
            "observed_at", "total_requests", "days", "screenshot_sha256"}
    if (not isinstance(proof, dict) or set(proof) != keys
            or type(proof["schema_version"]) is not int or proof["schema_version"] != 1
            or proof["source"] != "user_provided_deepseek_dashboard"
            or proof["model"] != "deepseek-flash" or proof["timezone"] != "Asia/Shanghai"):
        raise ReconciliationError("invalid_proof")
    for name in ("reservation_id", "task_id"):
        if not isinstance(proof[name], str) or not re.fullmatch(r"[0-9a-f-]{36}", proof[name]):
            raise ReconciliationError("invalid_proof")
    if type(proof["total_requests"]) is not int or proof["total_requests"] < 1:
        raise ReconciliationError("invalid_proof")
    try:
        observed = datetime.fromisoformat(proof["observed_at"])
        if observed.tzinfo is None or observed > datetime.now(timezone.utc) + timedelta(seconds=30):
            raise ValueError
    except (TypeError, ValueError):
        raise ReconciliationError("invalid_proof") from None
    hashes = proof["screenshot_sha256"]
    if (not isinstance(hashes, list) or not 2 <= len(hashes) <= 5
            or any(not isinstance(s, str) or not re.fullmatch(r"[0-9a-f]{64}", s) for s in hashes)
            or len(set(hashes)) != len(hashes)):
        raise ReconciliationError("invalid_proof")
    days = proof["days"]
    if not isinstance(days, list) or not 1 <= len(days) <= 31:
        raise ReconciliationError("invalid_proof")
    dates = set()
    for day in days:
        if not isinstance(day, dict) or set(day) != {"date", "input_cache_hit", "input_cache_miss", "output"}:
            raise ReconciliationError("invalid_proof")
        try:
            date = datetime.strptime(day["date"], "%Y-%m-%d").date().isoformat()
        except (TypeError, ValueError):
            raise ReconciliationError("invalid_proof") from None
        if date != day["date"] or date in dates:
            raise ReconciliationError("invalid_proof")
        dates.add(date)
        if any(type(day[k]) is not int or not 0 <= day[k] <= 1_000_000_000
               for k in ("input_cache_hit", "input_cache_miss", "output")):
            raise ReconciliationError("invalid_proof")
    serialized = json.dumps(proof, sort_keys=True, separators=(",", ":"))
    return observed, hashlib.sha256(serialized.encode()).hexdigest()


def _audited_unmetered_requests(connection):
    """Only explicit zero-day audits explain local attempts absent from supplier counts."""
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='model_reconciliations'").fetchone():
        return set()
    rows = connection.execute("""SELECT s.id,s.state,s.charged,s.input_tokens,s.output_tokens,r.result_json
        FROM model_reconciliations r JOIN model_spend s ON s.id=r.reservation_id
        WHERE json_extract(r.result_json,'$.method') IN
        ('human_reviewed_exclusive_zero_usage_day','human_reviewed_exclusive_rejected_request_totals')""").fetchall()
    unmetered = set()
    for row in rows:
        if tuple(row)[1:5] != ("settled", 0, 0, 0):
            raise ReconciliationError("ledger_conflict")
        result = json.loads(row[5])
        if result["method"] == 'human_reviewed_exclusive_zero_usage_day':
            unmetered.add(row[0])
        elif type(result.get("supplier_request_counted")) is not bool:
            raise ReconciliationError("ledger_conflict")
        elif result["supplier_request_counted"] is False:
            unmetered.add(row[0])
    return unmetered


def _plan(connection, proof):
    if isinstance(proof, dict) and proof.get("schema_version") == 3:
        from .rejected_request_reconciliation import plan
        return plan(connection, proof)
    if isinstance(proof, dict) and proof.get("schema_version") == 2:
        from .zero_usage_reconciliation import plan
        return plan(connection, proof)
    observed, digest = _checked(proof)
    connection.row_factory = sqlite3.Row
    target = connection.execute("SELECT * FROM model_spend WHERE id=?", (proof["reservation_id"],)).fetchone()
    if target is None or target["task_id"] != proof["task_id"]:
        raise ReconciliationError("request_mismatch")
    if connection.execute("SELECT 1 FROM sqlite_master WHERE name='model_reconciliations'").fetchone():
        audit = connection.execute("SELECT * FROM model_reconciliations WHERE reservation_id=?", (target["id"],)).fetchone()
        if audit:
            if audit["proof_sha256"] != digest:
                raise ReconciliationError("receipt_conflict")
            result = json.loads(audit["result_json"])
            if (target["input_tokens"], target["output_tokens"], target["charged"], target["state"]) != (
                    result["input_tokens"], result["output_tokens"], result["charge_microyuan"], result["state"]):
                raise ReconciliationError("ledger_conflict")
            return dict(result, already_applied=True)
    rows = connection.execute("""SELECT s.*, g.profile FROM model_spend s
        JOIN model_grants g ON g.id=s.grant_id ORDER BY s.created_at,s.id""").fetchall()
    all_count = connection.execute("SELECT count(*) FROM model_spend").fetchone()[0]
    unmetered = _audited_unmetered_requests(connection)
    unknown = [r for r in rows if r["state"] != "settled"]
    if (len(rows) != all_count or len(rows) - len(unmetered) != proof["total_requests"]
            or any(r["profile"] != PROFILE for r in rows)
            or len(unknown) != 1 or unknown[0]["id"] != target["id"]
            or target["state"] != "uncertain"):
        raise ReconciliationError("scope_mismatch")
    if connection.execute("SELECT 1 FROM agent_tasks WHERE status IN ('queued','running') LIMIT 1").fetchone():
        raise ReconciliationError("active_task")
    known = {}
    for row in rows:
        when = datetime.fromisoformat(row["created_at"])
        if when.tzinfo is None or when > observed:
            raise ReconciliationError("snapshot_too_old")
        date = when.astimezone(CHINA).date().isoformat()
        amounts = known.setdefault(date, [0, 0])
        if row["state"] == "settled":
            if any(type(row[k]) is not int or row[k] < 0 for k in ("input_tokens", "output_tokens")):
                raise ReconciliationError("invalid_ledger")
            amounts[0] += row["input_tokens"]
            amounts[1] += row["output_tokens"]
    remote = {d["date"]: [d["input_cache_hit"] + d["input_cache_miss"], d["output"]] for d in proof["days"]}
    if set(known) != set(remote):
        raise ReconciliationError("date_scope_mismatch")
    target_date = datetime.fromisoformat(target["created_at"]).astimezone(CHINA).date().isoformat()
    for date in known:
        if date != target_date and known[date] != remote[date]:
            raise ReconciliationError("other_day_mismatch")
    input_tokens, output_tokens = [remote[target_date][i] - known[target_date][i] for i in (0, 1)]
    if not 0 <= input_tokens <= 2_000_000 or not 0 <= output_tokens <= 1_000_000:
        raise ReconciliationError("invalid_difference")
    charge = input_tokens * INPUT_MICROYUAN_PER_TOKEN + output_tokens * OUTPUT_MICROYUAN_PER_TOKEN
    return {"reservation_id": target["id"], "task_id": target["task_id"],
            "method": "human_reviewed_exclusive_daily_difference", "proof_sha256": digest,
            "input_tokens": input_tokens, "output_tokens": output_tokens, "charge_microyuan": charge,
            "original_reserved_microyuan": target["reserved"],
            "state": "settled" if charge <= target["reserved"] else "violation",
            "already_applied": False}


def preview(database: Path, proof: dict):
    """A read-only preview does not create tables or change reservation state."""
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        return _plan(connection, proof)


def apply(database: Path, proof: dict, *, confirmed_exclusive_scope=False):
    """Local operator only; no HTTP route and no inference from account balance."""
    if confirmed_exclusive_scope is not True:
        raise ReconciliationError("scope_confirmation_required")
    with sqlite3.connect(database.resolve().as_uri() + "?mode=rw", uri=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        result = _plan(connection, proof)  # Recheck snapshot atomically before mutation.
        if result["already_applied"]:
            return result
        before = dict(connection.execute("SELECT * FROM model_spend WHERE id=?", (result["reservation_id"],)).fetchone())
        connection.execute("""CREATE TABLE IF NOT EXISTS model_reconciliations (
            reservation_id TEXT PRIMARY KEY REFERENCES model_spend(id),
            proof_sha256 TEXT NOT NULL, applied_at TEXT NOT NULL,
            proof_json TEXT NOT NULL, before_json TEXT NOT NULL, result_json TEXT NOT NULL)""")
        connection.execute("INSERT INTO model_reconciliations VALUES (?,?,?,?,?,?)", (
            result["reservation_id"], result["proof_sha256"], datetime.now(timezone.utc).isoformat(),
            json.dumps(proof, sort_keys=True), json.dumps(before, sort_keys=True), json.dumps(result, sort_keys=True)))
        updated = connection.execute("""UPDATE model_spend SET state=?,charged=?,input_tokens=?,output_tokens=?
            WHERE id=? AND state='uncertain'""", (result["state"], result["charge_microyuan"],
                result["input_tokens"], result["output_tokens"], result["reservation_id"]))
        if updated.rowcount != 1:
            raise ReconciliationError("ledger_conflict")
        return result
