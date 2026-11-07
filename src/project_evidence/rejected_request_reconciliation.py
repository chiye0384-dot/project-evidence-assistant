"""Fresh exclusive dashboard totals for one rejected request; never HTTP-only zeroing."""
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3

from .reconciliation import CHINA, ReconciliationError, _audited_unmetered_requests
from .spending import PROFILE


def plan(connection, proof):
    fields = {"schema_version", "source", "model", "timezone", "reservation_id", "task_id",
              "observed_at", "total_requests", "day", "exclusive_project_usage",
              "no_other_calls_after_failure", "dashboard_statistics_updated", "user_reply_sha256"}
    if (not isinstance(proof, dict) or set(proof) != fields or type(proof["schema_version"]) is not int
            or proof["schema_version"] != 3 or proof["source"] != "user_transcribed_deepseek_dashboard"
            or proof["model"] != "deepseek-flash" or proof["timezone"] != "Asia/Shanghai"
            or any(proof[key] is not True for key in ("exclusive_project_usage", "no_other_calls_after_failure",
                                                     "dashboard_statistics_updated"))
            or type(proof["total_requests"]) is not int or not 1 <= proof["total_requests"] <= 1_000_000_000):
        raise ReconciliationError("invalid_rejected_request_proof")
    for key, pattern in (("reservation_id", r"[0-9a-f-]{36}"), ("task_id", r"[0-9a-f-]{36}"),
                         ("user_reply_sha256", r"[0-9a-f]{64}")):
        if not isinstance(proof[key], str) or re.fullmatch(pattern, proof[key]) is None:
            raise ReconciliationError("invalid_rejected_request_proof")
    day = proof["day"]
    if (not isinstance(day, dict) or set(day) != {"date", "requests", "input_cache_hit", "input_cache_miss", "output"}
            or any(type(day[k]) is not int or not 0 <= day[k] <= 1_000_000_000
                   for k in ("requests", "input_cache_hit", "input_cache_miss", "output"))):
        raise ReconciliationError("invalid_rejected_request_proof")
    try:
        observed = datetime.fromisoformat(proof["observed_at"])
        date = datetime.strptime(day["date"], "%Y-%m-%d").date().isoformat()
        if observed.tzinfo is None or observed > datetime.now(timezone.utc) or date != day["date"]:
            raise ValueError
    except (ValueError, TypeError):
        raise ReconciliationError("invalid_rejected_request_proof") from None
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
            if (target["state"], target["charged"], target["input_tokens"], target["output_tokens"]) != ("settled", 0, 0, 0):
                raise ReconciliationError("ledger_conflict")
            return dict(result, already_applied=True)
    rows = connection.execute("SELECT s.*,g.profile FROM model_spend s JOIN model_grants g ON g.id=s.grant_id").fetchall()
    unknown = [row for row in rows if row["state"] != "settled"]
    if (len(rows) != connection.execute("SELECT count(*) FROM model_spend").fetchone()[0]
            or any(row["profile"] != PROFILE for row in rows) or len(unknown) != 1
            or unknown[0]["id"] != target["id"] or target["state"] != "uncertain"):
        raise ReconciliationError("scope_mismatch")
    if connection.execute("SELECT 1 FROM agent_tasks WHERE status IN ('queued','running') LIMIT 1").fetchone():
        raise ReconciliationError("active_task")
    task_row = connection.execute("SELECT status,payload_json FROM agent_tasks WHERE id=?", (target["task_id"],)).fetchone()
    if task_row is None:
        raise ReconciliationError("unpriced_rejected_request_required")
    task = json.loads(task_row["payload_json"])
    counts = task.get("counters", {})
    errors = [event for event in task.get("events", []) if event.get("kind") == "model_error"]
    if (task_row["status"] != "failed" or task.get("status") != "failed"
            or task.get("execution_mode") != "real" or task.get("result") is not None
            or task.get("evidence") or counts.get("model_calls") != 1 or counts.get("tool_calls") != 0
            or counts.get("reported_input_tokens", 0) != 0 or counts.get("reported_output_tokens", 0) != 0
            or len(errors) != 1 or not all(label in errors[0].get("message", "")
                                          for label in ("cost_uncertain", "response.non_200", "response.http_400"))):
        raise ReconciliationError("unpriced_rejected_request_required")
    unmetered = _audited_unmetered_requests(connection)
    known_input = known_output = known_day_count = 0
    for row in rows:
        when = datetime.fromisoformat(row["created_at"])
        if when.tzinfo is None or when > observed:
            raise ReconciliationError("snapshot_too_old")
        row_date = when.astimezone(CHINA).date().isoformat()
        if row["id"] == target["id"]:
            if row_date != date or (observed - when).total_seconds() < 300:
                raise ReconciliationError("updated_post_failure_snapshot_required")
        else:
            if any(type(row[key]) is not int or row[key] < 0 for key in ("input_tokens", "output_tokens", "charged")):
                raise ReconciliationError("invalid_ledger")
            if row_date == date:
                known_input += row["input_tokens"]
                known_output += row["output_tokens"]
                known_day_count += row["id"] not in unmetered
    if day["input_cache_hit"] + day["input_cache_miss"] != known_input or day["output"] != known_output:
        raise ReconciliationError("nonzero_or_inconsistent_usage_difference")
    counted = proof["total_requests"] - (len(rows) - len(unmetered) - 1)
    if counted not in (0, 1) or day["requests"] != known_day_count + counted:
        raise ReconciliationError("supplier_request_count_mismatch")
    return {"reservation_id": target["id"], "task_id": target["task_id"],
            "method": "human_reviewed_exclusive_rejected_request_totals", "proof_sha256": digest,
            "input_tokens": 0, "output_tokens": 0, "charge_microyuan": 0,
            "original_reserved_microyuan": target["reserved"], "state": "settled", "already_applied": False,
            "supplier_request_counted": bool(counted), "supplier_counted_requests": proof["total_requests"],
            "local_attempts_including_rejected": len(rows), "original_single_request_receipt_available": False,
            "known_day_input_tokens": known_input, "known_day_output_tokens": known_output}
