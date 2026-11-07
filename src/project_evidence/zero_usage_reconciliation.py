"""Narrow human-attested zero-day audit; HTTP errors alone never settle costs."""
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import sqlite3

from .reconciliation import CHINA, ReconciliationError, _audited_unmetered_requests
from .spending import PROFILE


def plan(connection, proof):
    fields = {"schema_version", "source", "model", "timezone", "reservation_id", "task_id",
              "observed_at", "total_requests", "day", "exclusive_project_usage",
              "no_other_calls_after_failure", "user_reply_sha256"}
    if (not isinstance(proof, dict) or set(proof) != fields or type(proof["schema_version"]) is not int
            or proof["schema_version"] != 2 or proof["source"] != "user_transcribed_deepseek_dashboard"
            or proof["model"] != "deepseek-flash" or proof["timezone"] != "Asia/Shanghai"
            or proof["exclusive_project_usage"] is not True or proof["no_other_calls_after_failure"] is not True
            or type(proof["total_requests"]) is not int or proof["total_requests"] < 1):
        raise ReconciliationError("invalid_zero_usage_proof")
    for field, pattern in (("reservation_id", r"[0-9a-f-]{36}"), ("task_id", r"[0-9a-f-]{36}"),
                           ("user_reply_sha256", r"[0-9a-f]{64}")):
        if not isinstance(proof[field], str) or re.fullmatch(pattern, proof[field]) is None:
            raise ReconciliationError("invalid_zero_usage_proof")
    day = proof["day"]
    if (not isinstance(day, dict) or set(day) != {"date", "requests", "input_cache_hit", "input_cache_miss", "output"}
            or any(type(day[k]) is not int or day[k] != 0 for k in ("requests", "input_cache_hit", "input_cache_miss", "output"))):
        raise ReconciliationError("zero_usage_day_required")
    try:
        date = datetime.strptime(day["date"], "%Y-%m-%d").date().isoformat()
        observed = datetime.fromisoformat(proof["observed_at"])
        if date != day["date"] or observed.tzinfo is None or observed > datetime.now(timezone.utc):
            raise ValueError
    except (ValueError, TypeError):
        raise ReconciliationError("invalid_zero_usage_proof") from None
    digest = hashlib.sha256(json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
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
            if (target["input_tokens"], target["output_tokens"], target["charged"], target["state"]) != (0, 0, 0, "settled"):
                raise ReconciliationError("ledger_conflict")
            return dict(result, already_applied=True)
    rows = connection.execute("SELECT s.*,g.profile FROM model_spend s JOIN model_grants g ON g.id=s.grant_id").fetchall()
    unmetered = _audited_unmetered_requests(connection)
    unknown = [row for row in rows if row["state"] != "settled"]
    if (len(rows) != connection.execute("SELECT count(*) FROM model_spend").fetchone()[0]
            or any(row["profile"] != PROFILE for row in rows)
            or len(unknown) != 1 or unknown[0]["id"] != target["id"] or target["state"] != "uncertain"
            or len(rows) - 1 - len(unmetered) != proof["total_requests"]):
        raise ReconciliationError("scope_mismatch")
    for row in rows:
        when = datetime.fromisoformat(row["created_at"])
        if when.tzinfo is None or when > observed:
            raise ReconciliationError("snapshot_too_old")
        same_day = when.astimezone(CHINA).date().isoformat() == date
        if (row["id"] == target["id"] and not same_day) or (row["id"] != target["id"] and same_day and row["id"] not in unmetered):
            raise ReconciliationError("zero_usage_day_scope_mismatch")
        if row["state"] == "settled" and any(type(row[k]) is not int or row[k] < 0 for k in ("input_tokens", "output_tokens", "charged")):
            raise ReconciliationError("invalid_ledger")
    if connection.execute("SELECT 1 FROM agent_tasks WHERE status IN ('queued','running') LIMIT 1").fetchone():
        raise ReconciliationError("active_task")
    row = connection.execute("SELECT status,payload_json FROM agent_tasks WHERE id=?", (proof["task_id"],)).fetchone()
    if row is None or row["status"] not in {"failed", "timeout"}:
        raise ReconciliationError("unpriced_terminal_request_required")
    task = json.loads(row["payload_json"])
    counters = task.get("counters", {})
    elapsed = counters.get("elapsed_seconds")
    limit = task.get("limits", {}).get("max_seconds")
    recorded_timeout = (row["status"] == "timeout" and type(elapsed) in (int, float) and type(limit) in (int, float)
                        and math.isfinite(elapsed) and math.isfinite(limit) and elapsed >= limit > 0
                        and any(event.get("kind") == "finished" and event.get("status") == "timeout" for event in task.get("events", [])))
    recorded_non_200 = any(event.get("kind") == "model_error" and "cost_uncertain" in event.get("message", "")
                           and "response.non_200" in event.get("message", "") for event in task.get("events", []))
    if (task.get("status") != row["status"] or task.get("execution_mode") != "real" or task.get("result") is not None
            or counters.get("model_calls") != 1 or counters.get("tool_calls") != 0
            or counters.get("reported_input_tokens", 0) != 0 or counters.get("reported_output_tokens", 0) != 0
            or not (recorded_non_200 or recorded_timeout)):
        raise ReconciliationError("unpriced_terminal_request_required")
    return {"reservation_id": target["id"], "task_id": target["task_id"],
            "method": "human_reviewed_exclusive_zero_usage_day", "proof_sha256": digest,
            "input_tokens": 0, "output_tokens": 0, "charge_microyuan": 0,
            "original_reserved_microyuan": target["reserved"], "state": "settled", "already_applied": False,
            "supplier_counted_requests": proof["total_requests"], "local_attempts_including_rejected": len(rows),
            "original_single_request_receipt_available": False}
