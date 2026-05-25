#!/usr/bin/env python3
"""
POL Stages 5-7 — read-only probes against convergence VM `echo-summit` (staging).

Iain spec: REPLY_TO_ADRYANN_V51_GREEN_WITH_POL §"PROOF-OF-LIFE GATE".

ENDPOINT MAP (verified against clients_uhy/demo/api/main.py @ b5a5bdd):
- /api/v1/deliverables/{deliverable_id}            GET   ✓ (main.py:6026)
- /api/v1/deliverables/{deliverable_id}/download/{filename}  GET  (signed-URL)
- /api/v1/preparations/{prep_id}/deliverables      GET   (main.py:6122)
- /api/v1/clients                                  GET   (main.py:3470)
- /api/v1/clients/{client_id}/employees            GET   (main.py:3697)
- /api/v1/clients/{client_id}/hitl-summary         GET   (main.py:4172)
- /api/v1/clients/{client_id}/duplicate-suspects   GET   (main.py:3350) — HITL surface
- /api/v1/query                                    POST  (main.py:3889) — body: QueryRequest
- /api/v1/search                                   POST  (main.py:3880) — body: SearchRequest

Iain's spec used wrong paths/methods in several spots; this script uses
the actual convergence routes.

Stages:
- 5: GET /preparations/{prep_id}/deliverables -> pick newest -> GET /deliverables/{id}
     -> follow signed download_url -> sha256-verify against file_hash
- 6: GET /clients/{client_id}/employees, POST /query, GET /clients
- 7: GET /clients/{client_id}/hitl-summary, GET /clients/{client_id}/duplicate-suspects
     (POST /clients/{client_id}/duplicate-suspects/{event_id}/resolve intentionally
      skipped — state-mutating; awaits explicit operator approval)

Usage:
  export BEARER_TOKEN=...
  python3 exec_pol.py
  python3 exec_pol.py --stage 5
  python3 exec_pol.py --target complete_automation_2025

Exit codes:
  0 = all read-only checks green
  1 = any check failed
  2 = config error
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VM_BASE = "https://echo-summit.westus3.cloudapp.azure.com"
HERE = Path(__file__).parent
REPORT_DEFAULT = HERE / f"pol_report_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"

# Targets — use ACTUAL client names as they appear in /preparations and /clients.
# Iain's spec used short names ("Complete Automation", "Akervall") but VM returns
# legal_name with corporate suffix. We resolve case-insensitive contains-match.
TARGETS = {
    "complete_automation_2025": {"client_name_match": "complete automation", "tax_year": 2025},
    "akervall_2025":            {"client_name_match": "akervall",            "tax_year": 2025},
    "bridge_2025":              {"client_name_match": "bridge organics",     "tax_year": 2025},
}


def _bearer() -> str:
    tok = os.environ.get("BEARER_TOKEN")
    if not tok:
        print("ERROR BEARER_TOKEN not set. Source from action profile:\n"
              "  export BEARER_TOKEN=$(python3 -c \"import json; "
              "print(json.load(open('workspaces/uhy-prod/actions/generate-workbook/adopt_profile.json'))"
              "['security_params']['authorization'].split()[1])\")", file=sys.stderr)
        sys.exit(2)
    return tok


def _req(method: str, url: str, *, bearer: str | None = None,
         body: dict | None = None, want_json: bool = True,
         timeout: int = 30) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            parsed = None
            if want_json and raw:
                try:
                    parsed = json.loads(raw.decode())
                except json.JSONDecodeError:
                    parsed = {"__raw_head__": raw[:300].decode(errors="replace")}
            return {"ok": True, "status": resp.status, "elapsed_ms": elapsed_ms,
                    "body": parsed, "raw_len": len(raw),
                    "raw": raw if not want_json else None}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            body_parsed = json.loads(raw.decode()) if raw else None
        except json.JSONDecodeError:
            body_parsed = {"__raw_head__": raw[:300].decode(errors="replace") if raw else ""}
        return {"ok": False, "status": e.code,
                "elapsed_ms": int((time.monotonic() - t0) * 1000),
                "body": body_parsed, "error": str(e)}
    except (urllib.error.URLError, TimeoutError) as e:
        return {"ok": False, "status": -1,
                "elapsed_ms": int((time.monotonic() - t0) * 1000),
                "body": None, "error": f"{type(e).__name__}: {e}"}


def _find_prep_with_deliverable(bearer: str, name_match: str, tax_year: int) -> tuple[dict | None, list]:
    """Find a prep matching name+year that has at least one deliverable. Walks all
    preps if needed (server doesn't filter on client_name)."""
    list_r = _req("GET", f"{VM_BASE}/api/v1/preparations", bearer=bearer)
    if not list_r["ok"]:
        return None, []
    preps = list_r["body"] or []
    candidates = [p for p in preps
                  if name_match.lower() in (p.get("client_name") or "").lower()
                  and int(p.get("tax_year") or 0) == int(tax_year)]
    # walk newest-first (created_at desc) so we hit the most-recent run
    candidates.sort(key=lambda p: p.get("created_at") or "", reverse=True)
    for p in candidates:
        d_r = _req("GET", f"{VM_BASE}/api/v1/preparations/{p['id']}/deliverables", bearer=bearer)
        if not d_r["ok"]:
            continue
        payload = d_r["body"]
        delivs_list: list = []
        if isinstance(payload, dict):
            delivs_list = payload.get("deliverables") or []
        elif isinstance(payload, list):
            delivs_list = payload
        if delivs_list:
            return p, delivs_list
    return (candidates[0] if candidates else None), []


def _find_client_by_name(bearer: str, name_match: str) -> dict | None:
    r = _req("GET", f"{VM_BASE}/api/v1/clients", bearer=bearer)
    if not r["ok"] or not isinstance(r["body"], list):
        return None
    for c in r["body"]:
        legal = (c.get("legal_name") or c.get("name") or "").lower()
        if name_match.lower() in legal:
            return c
    return None


def _stage5(bearer: str, target: dict) -> dict:
    """Stage 5: pick a deliverable -> GET signed URL -> download -> sha256-verify."""
    out = {"stage": 5, "target": target, "steps": [], "verdict": "PENDING"}

    prep, delivs = _find_prep_with_deliverable(bearer,
                                                target["client_name_match"],
                                                target["tax_year"])
    if not prep:
        out["verdict"] = "FAIL"
        out["error"] = f"no prep found for {target}"
        return out
    out["prep_id"] = prep["id"]
    out["prep_client_name"] = prep.get("client_name")
    out["prep_tax_year"] = prep.get("tax_year")
    out["prep_status"] = prep.get("status")

    if not delivs:
        out["verdict"] = "FAIL"
        out["error"] = "prep has no deliverables (list returned empty)"
        out["note"] = ("ALL 32 staging preps show status=in_progress at probe time; "
                       "no completed deliverables on staging.")
        return out
    out["deliverable_count"] = len(delivs)
    deliv_summary = delivs[0]
    deliv_id = deliv_summary.get("id")
    out["deliverable_id"] = deliv_id

    # 5.1 GET /deliverables/{id}
    sig_r = _req("GET", f"{VM_BASE}/api/v1/deliverables/{deliv_id}", bearer=bearer)
    out["steps"].append({"step": "get_deliverable_metadata",
                         "endpoint": f"GET /api/v1/deliverables/{deliv_id[:18]}…",
                         "status": sig_r["status"],
                         "elapsed_ms": sig_r["elapsed_ms"]})
    if not sig_r["ok"]:
        out["verdict"] = "FAIL"
        out["error"] = f"get_deliverable returned {sig_r['status']}"
        out["body"] = sig_r.get("body")
        return out
    deliv_meta = sig_r["body"] or {}
    out["deliverable_meta_keys"] = sorted(list(deliv_meta.keys()))
    out["filename"] = deliv_meta.get("filename")
    out["file_size"] = deliv_meta.get("file_size")
    out["file_hash_advertised"] = deliv_meta.get("file_hash")
    download_url = deliv_meta.get("download_url")
    out["download_url_present"] = bool(download_url)
    if not download_url:
        out["verdict"] = "FAIL"
        out["error"] = "no download_url in deliverable metadata"
        return out

    # 5.2 follow signed download URL (no bearer per spec — HMAC is auth)
    # download_url may be relative — make absolute
    if download_url.startswith("/"):
        download_url = VM_BASE + download_url
    dl_r = _req("GET", download_url, bearer=None, want_json=False, timeout=60)
    out["steps"].append({"step": "download_signed_url_no_bearer",
                         "endpoint": "GET (signed URL)",
                         "status": dl_r["status"],
                         "elapsed_ms": dl_r["elapsed_ms"],
                         "bytes": dl_r.get("raw_len")})
    if not dl_r["ok"]:
        out["verdict"] = "FAIL"
        out["error"] = f"signed URL download failed: {dl_r['status']}"
        out["error_body"] = dl_r.get("body")
        return out

    # 5.3 sha256-verify
    raw = dl_r.get("raw") or b""
    computed = hashlib.sha256(raw).hexdigest()
    out["sha256_computed"] = computed
    file_hash = deliv_meta.get("file_hash")
    if file_hash:
        match = (computed.lower() == file_hash.lower())
        out["sha256_match"] = match
        if not match:
            out["verdict"] = "FAIL"
            out["error"] = f"sha256 MISMATCH: advertised={file_hash} computed={computed}"
            return out
    else:
        out["sha256_match"] = "SKIP_NO_HASH_ADVERTISED"

    out["verdict"] = "PASS"
    return out


def _stage6(bearer: str, target: dict, client_id: int | None) -> dict:
    """Stage 6: main query surfaces."""
    out = {"stage": 6, "target": target, "client_id": client_id, "checks": []}

    # 6.1 GET /clients (list)
    r = _req("GET", f"{VM_BASE}/api/v1/clients", bearer=bearer)
    body = r.get("body")
    out["checks"].append({
        "endpoint": "GET /api/v1/clients",
        "status": r["status"], "elapsed_ms": r["elapsed_ms"],
        "row_count": len(body) if isinstance(body, list) else None,
        "body_type": type(body).__name__,
        "verdict": "PASS" if r["ok"] else "FAIL",
    })

    # 6.2 GET /clients/{client_id}/employees
    if client_id is not None:
        r = _req("GET", f"{VM_BASE}/api/v1/clients/{client_id}/employees", bearer=bearer)
        body = r.get("body")
        out["checks"].append({
            "endpoint": f"GET /api/v1/clients/{client_id}/employees",
            "status": r["status"], "elapsed_ms": r["elapsed_ms"],
            "body_type": type(body).__name__,
            "row_count": (len(body) if isinstance(body, list)
                          else len((body or {}).get("employees", [])) if isinstance(body, dict) else None),
            "body_top_keys": sorted(list(body.keys()))[:15] if isinstance(body, dict) else None,
            "verdict": "PASS" if r["ok"] else "FAIL",
        })
    else:
        out["checks"].append({
            "endpoint": "GET /api/v1/clients/{client_id}/employees",
            "verdict": "SKIP",
            "reason": "no client_id resolved for target",
        })

    # 6.3 POST /query — NL query
    if client_id is not None:
        q_body = {"query": f"list employees for {target['client_name_match']}",
                  "client_id": client_id, "tax_year": target["tax_year"]}
    else:
        q_body = {"query": "list all clients"}
    r = _req("POST", f"{VM_BASE}/api/v1/query", bearer=bearer, body=q_body, timeout=60)
    body = r.get("body")
    out["checks"].append({
        "endpoint": "POST /api/v1/query",
        "request_body": q_body,
        "status": r["status"], "elapsed_ms": r["elapsed_ms"],
        "body_type": type(body).__name__,
        "body_top_keys": sorted(list(body.keys()))[:15] if isinstance(body, dict) else None,
        "verdict": "PASS" if r["ok"] else "FAIL",
        "error_body": body if not r["ok"] else None,
    })

    # 6.4 POST /search — semantic search
    s_body = {"query": "research and development expenses", "top_k": 5}
    if client_id is not None:
        s_body["client_id"] = client_id
    r = _req("POST", f"{VM_BASE}/api/v1/search", bearer=bearer, body=s_body, timeout=60)
    body = r.get("body")
    out["checks"].append({
        "endpoint": "POST /api/v1/search",
        "request_body": s_body,
        "status": r["status"], "elapsed_ms": r["elapsed_ms"],
        "body_type": type(body).__name__,
        "body_top_keys": sorted(list(body.keys()))[:15] if isinstance(body, dict) else None,
        "result_count": (body or {}).get("count") if isinstance(body, dict) else None,
        "verdict": "PASS" if r["ok"] else "FAIL",
        "error_body": body if not r["ok"] else None,
    })

    fails = [c for c in out["checks"] if c.get("verdict") == "FAIL"]
    out["verdict"] = "PASS" if not fails else "FAIL"
    return out


def _stage7(bearer: str, target: dict, client_id: int | None) -> dict:
    """Stage 7: HITL CPA review surface (read-only).

    Per convergence VM, HITL surface is two parts:
      (a) /clients/{id}/hitl-summary — flagged employees + open events
      (b) /clients/{id}/duplicate-suspects — duplicate detection HITL queue

    The state-mutating endpoints (PATCH /employees/{id}, POST /duplicate-suspects/.../resolve)
    are intentionally NOT exercised here; await explicit operator approval.
    """
    out = {"stage": 7, "target": target, "client_id": client_id, "checks": []}

    if client_id is None:
        out["checks"].append({"endpoint": "ALL", "verdict": "SKIP",
                              "reason": "no client_id resolved for target"})
        out["verdict"] = "SKIP"
        return out

    # 7.1 GET /clients/{id}/hitl-summary
    r = _req("GET", f"{VM_BASE}/api/v1/clients/{client_id}/hitl-summary", bearer=bearer)
    body = r.get("body")
    out["checks"].append({
        "endpoint": f"GET /api/v1/clients/{client_id}/hitl-summary",
        "status": r["status"], "elapsed_ms": r["elapsed_ms"],
        "body_top_keys": sorted(list(body.keys()))[:15] if isinstance(body, dict) else None,
        "flagged_employee_count": (body or {}).get("flagged_employee_count") if isinstance(body, dict) else None,
        "open_event_count": (body or {}).get("open_event_count") if isinstance(body, dict) else None,
        "verdict": "PASS" if r["ok"] else "FAIL",
        "error_body": body if not r["ok"] else None,
    })

    # 7.2 GET /clients/{id}/duplicate-suspects (HITL queue)
    r = _req("GET", f"{VM_BASE}/api/v1/clients/{client_id}/duplicate-suspects", bearer=bearer)
    body = r.get("body")
    out["checks"].append({
        "endpoint": f"GET /api/v1/clients/{client_id}/duplicate-suspects",
        "status": r["status"], "elapsed_ms": r["elapsed_ms"],
        "body_type": type(body).__name__,
        "row_count": (len(body) if isinstance(body, list)
                      else len((body or {}).get("suspects", [])) if isinstance(body, dict) else None),
        "body_top_keys": sorted(list(body.keys()))[:15] if isinstance(body, dict) else None,
        "verdict": "PASS" if r["ok"] else "FAIL",
        "error_body": body if not r["ok"] else None,
    })

    # 7.3 GET /clients/{id}/publish-status (HITL gate — readiness to publish)
    r = _req("GET", f"{VM_BASE}/api/v1/clients/{client_id}/publish-status", bearer=bearer)
    body = r.get("body")
    out["checks"].append({
        "endpoint": f"GET /api/v1/clients/{client_id}/publish-status",
        "status": r["status"], "elapsed_ms": r["elapsed_ms"],
        "body_top_keys": sorted(list(body.keys()))[:15] if isinstance(body, dict) else None,
        "verdict": "PASS" if r["ok"] else "FAIL",
        "error_body": body if not r["ok"] else None,
    })

    out["mutating_step_skipped"] = (
        "POST /api/v1/clients/{client_id}/duplicate-suspects/{event_id}/resolve "
        "and PATCH /api/v1/employees/{employee_id} not exercised here. "
        "Re-run with explicit user go-ahead to validate state-mutation + re-projection.")

    fails = [c for c in out["checks"] if c.get("verdict") == "FAIL"]
    out["verdict"] = "PASS" if not fails else "FAIL"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", type=int, choices=[5, 6, 7], help="run a single stage")
    ap.add_argument("--target", choices=list(TARGETS.keys()),
                    default="complete_automation_2025",
                    help="target client (default complete_automation_2025)")
    ap.add_argument("--json", type=Path, default=REPORT_DEFAULT,
                    help="write structured report (default timestamped file)")
    args = ap.parse_args()

    bearer = _bearer()
    target = TARGETS[args.target]
    print(f"POL run — VM={VM_BASE}", flush=True)
    print(f"target={target} stages={args.stage or 'ALL (5,6,7)'}", flush=True)
    print(f"output report: {args.json}", flush=True)

    # Resolve client_id (used by stages 6+7)
    client = _find_client_by_name(bearer, target["client_name_match"])
    if client:
        print(f"resolved client: id={client.get('id')} "
              f"legal_name={client.get('legal_name')!r}", flush=True)
    else:
        print("WARN no matching client in /api/v1/clients — stages 6+7 will SKIP", flush=True)
    client_id = client.get("id") if client else None

    report = {"run_at": datetime.now(timezone.utc).isoformat(),
              "vm_base": VM_BASE, "target": target,
              "resolved_client": client, "stages": []}

    if args.stage is None or args.stage == 5:
        print("\n=== Stage 5: deliverable download + sha256 ===", flush=True)
        s5 = _stage5(bearer, target)
        report["stages"].append(s5)
        print(f"  verdict: {s5['verdict']}", flush=True)
        if s5["verdict"] != "PASS":
            print(f"  error: {s5.get('error')}", flush=True)
        else:
            print(f"  filename: {s5.get('filename')}", flush=True)
            print(f"  bytes: {s5.get('file_size')} sha256={s5.get('sha256_match')}", flush=True)

    if args.stage is None or args.stage == 6:
        print("\n=== Stage 6: main query surfaces ===", flush=True)
        s6 = _stage6(bearer, target, client_id)
        report["stages"].append(s6)
        for c in s6["checks"]:
            print(f"  [{c.get('verdict','?')}] {c['endpoint']}  "
                  f"-> {c.get('status')} ({c.get('elapsed_ms')}ms)", flush=True)
            if c.get("verdict") == "FAIL" and c.get("error_body"):
                print(f"    err: {str(c['error_body'])[:200]}", flush=True)

    if args.stage is None or args.stage == 7:
        print("\n=== Stage 7: HITL surface (read-only) ===", flush=True)
        s7 = _stage7(bearer, target, client_id)
        report["stages"].append(s7)
        for c in s7["checks"]:
            print(f"  [{c.get('verdict','?')}] {c['endpoint']}  "
                  f"-> {c.get('status')} ({c.get('elapsed_ms')}ms)", flush=True)
            if c.get("verdict") == "FAIL" and c.get("error_body"):
                print(f"    err: {str(c['error_body'])[:200]}", flush=True)

    args.json.write_text(json.dumps(report, indent=2, default=str))
    print(f"\n=== report written: {args.json} ===", flush=True)

    verdicts = [s.get("verdict") for s in report["stages"]]
    overall = "PASS" if all(v == "PASS" for v in verdicts) else "FAIL"
    print(f"=== OVERALL: {overall}  stage_verdicts={verdicts} ===", flush=True)
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
