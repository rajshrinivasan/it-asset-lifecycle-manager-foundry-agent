"""
tools.py — CMDB read/write functions called by the agent via FunctionTool.

Each function reads from and/or writes to cmdb.json, which acts as the
system of record for IT assets. This is the key distinction from Project 02:
these functions have side effects — they mutate persistent state.

All functions return JSON strings so the agent receives structured results.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path

CMDB_PATH = Path(__file__).parent / "cmdb.json"

VALID_STATUSES = {"available", "checked_out", "in_repair", "retired"}


# ---------------------------------------------------------------------------
# CMDB persistence helpers
# ---------------------------------------------------------------------------

def _load_cmdb() -> dict:
    return json.loads(CMDB_PATH.read_text(encoding="utf-8"))


def _save_cmdb(data: dict) -> None:
    CMDB_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Read functions
# ---------------------------------------------------------------------------

def search_assets(query: str = "", status: str = "all") -> str:
    """
    Search assets by keyword (matches asset_id, type, make, model, assigned_to)
    and optionally filter by status.
    """
    data = _load_cmdb()
    results = []

    for asset in data["assets"].values():
        if status != "all" and asset["status"] != status:
            continue
        if query:
            searchable = " ".join([
                asset["asset_id"], asset["type"], asset["make"],
                asset["model"], asset.get("assigned_to") or "",
            ]).lower()
            if query.lower() not in searchable:
                continue
        results.append({
            "asset_id": asset["asset_id"],
            "type": asset["type"],
            "make": asset["make"],
            "model": asset["model"],
            "status": asset["status"],
            "assigned_to": asset["assigned_to"],
            "location": asset["location"],
        })

    return json.dumps({
        "query": query,
        "status_filter": status,
        "count": len(results),
        "assets": results,
    })


def get_asset_details(asset_id: str) -> str:
    """Return the full record for a single asset including its full history."""
    data = _load_cmdb()
    asset = data["assets"].get(asset_id)
    if not asset:
        return json.dumps({"error": f"Asset '{asset_id}' not found in CMDB."})
    return json.dumps(asset)


# ---------------------------------------------------------------------------
# Write functions — each mutates cmdb.json
# ---------------------------------------------------------------------------

def checkout_asset(asset_id: str, employee_id: str, employee_name: str) -> str:
    """Assign an available asset to an employee. Fails if asset is not available."""
    data = _load_cmdb()
    asset = data["assets"].get(asset_id)

    if not asset:
        return json.dumps({"success": False, "error": f"Asset '{asset_id}' not found."})
    if asset["status"] != "available":
        return json.dumps({
            "success": False,
            "error": f"Asset '{asset_id}' cannot be checked out. Current status: {asset['status']}.",
        })

    asset["status"] = "checked_out"
    asset["assigned_to"] = employee_name
    asset["employee_id"] = employee_id
    asset["history"].append({
        "action": "checked_out",
        "date": _now(),
        "by": employee_id,
        "note": f"Checked out to {employee_name}",
    })

    _save_cmdb(data)
    return json.dumps({
        "success": True,
        "message": f"Asset {asset_id} ({asset['make']} {asset['model']}) checked out to {employee_name} ({employee_id}).",
        "asset_id": asset_id,
        "assigned_to": employee_name,
        "date": _now(),
    })


def checkin_asset(asset_id: str, return_condition: str = "good") -> str:
    """Return a checked-out asset to available inventory."""
    data = _load_cmdb()
    asset = data["assets"].get(asset_id)

    if not asset:
        return json.dumps({"success": False, "error": f"Asset '{asset_id}' not found."})
    if asset["status"] not in ("checked_out", "in_repair"):
        return json.dumps({
            "success": False,
            "error": f"Asset '{asset_id}' is not checked out. Current status: {asset['status']}.",
        })

    previous_holder = asset.get("assigned_to") or "unknown"
    asset["status"] = "available"
    asset["location"] = "IT Store Room A"
    asset["history"].append({
        "action": "checked_in",
        "date": _now(),
        "by": asset.get("employee_id") or "IT-ADMIN",
        "note": f"Returned by {previous_holder}. Condition: {return_condition}.",
    })
    asset["assigned_to"] = None
    asset["employee_id"] = None

    _save_cmdb(data)
    return json.dumps({
        "success": True,
        "message": f"Asset {asset_id} returned to inventory. Condition noted: {return_condition}.",
        "asset_id": asset_id,
        "previous_holder": previous_holder,
        "date": _now(),
    })


def flag_for_repair(asset_id: str, issue_description: str) -> str:
    """Flag an asset for repair and record the reported issue."""
    data = _load_cmdb()
    asset = data["assets"].get(asset_id)

    if not asset:
        return json.dumps({"success": False, "error": f"Asset '{asset_id}' not found."})
    if asset["status"] == "retired":
        return json.dumps({"success": False, "error": f"Asset '{asset_id}' is retired and cannot be flagged for repair."})

    asset["status"] = "in_repair"
    asset["repair_notes"] = issue_description
    asset["history"].append({
        "action": "flagged_for_repair",
        "date": _now(),
        "by": asset.get("employee_id") or "IT-ADMIN",
        "note": issue_description,
    })

    _save_cmdb(data)
    return json.dumps({
        "success": True,
        "message": f"Asset {asset_id} flagged for repair. Issue logged: '{issue_description}'.",
        "asset_id": asset_id,
        "repair_notes": issue_description,
        "date": _now(),
    })


def retire_asset(asset_id: str, reason: str) -> str:
    """Permanently retire an asset from service. This action is irreversible."""
    data = _load_cmdb()
    asset = data["assets"].get(asset_id)

    if not asset:
        return json.dumps({"success": False, "error": f"Asset '{asset_id}' not found."})
    if asset["status"] == "retired":
        return json.dumps({"success": False, "error": f"Asset '{asset_id}' is already retired."})
    if asset["status"] == "checked_out":
        return json.dumps({
            "success": False,
            "error": f"Asset '{asset_id}' is currently checked out to {asset['assigned_to']}. Check it in before retiring.",
        })

    asset["status"] = "retired"
    asset["assigned_to"] = None
    asset["employee_id"] = None
    asset["location"] = "IT Disposal Bin"
    asset["history"].append({
        "action": "retired",
        "date": _now(),
        "by": "IT-ADMIN",
        "note": reason,
    })

    _save_cmdb(data)
    return json.dumps({
        "success": True,
        "message": f"Asset {asset_id} ({asset['make']} {asset['model']}) has been retired. Reason: {reason}.",
        "asset_id": asset_id,
        "date": _now(),
    })


def create_procurement_request(
    asset_type: str,
    quantity: int,
    justification: str,
    priority: str = "normal",
) -> str:
    """Raise a procurement request for new assets."""
    data = _load_cmdb()

    request_id = "PR-" + str(uuid.uuid4()).replace("-", "")[:6].upper()
    request = {
        "request_id": request_id,
        "asset_type": asset_type,
        "quantity": quantity,
        "justification": justification,
        "priority": priority,
        "status": "pending_approval",
        "raised_on": _now(),
        "raised_by": "IT-AGENT",
    }

    data["procurement_requests"].append(request)
    _save_cmdb(data)

    return json.dumps({
        "success": True,
        "message": f"Procurement request {request_id} created for {quantity}x {asset_type}.",
        "request": request,
    })


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

FUNCTION_MAP = {
    "search_assets": search_assets,
    "get_asset_details": get_asset_details,
    "checkout_asset": checkout_asset,
    "checkin_asset": checkin_asset,
    "flag_for_repair": flag_for_repair,
    "retire_asset": retire_asset,
    "create_procurement_request": create_procurement_request,
}
