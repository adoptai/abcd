"""Upload a Suralink ZIP to convergence VM v0.4+ and poll ingest + harness pipelines.

Replaces eval_and_commit.py per Iain REPLY_TO_ADRYANN_DELTAS_ACK_CHANGE2_GO_20260525 §3.

Endpoint chain:
  1. POST /api/v1/clients/upload-zip
       Multipart: file=<zip>, dir_name=<folder>, tax_year=<YYYY>, auto_ingest=true,
                  force_replace=<bool>
       Responses:
         200 + body.idempotent=true -> success, take ingest_job_id
         202                        -> success, take ingest_job_id
         409                        -> escalate via existing triage HITL pattern
  2. GET /api/v1/ingest/{ingest_job_id}/poll until status in {completed, failed}
       Terminal-OK : status=completed -> returns harness_job_id
       Terminal-bad: status=failed    -> escalate
  3. GET /api/v1/workbook/from-harness-async/{harness_job_id} until status in
       {succeeded, failed}
       Terminal-OK : status=succeeded -> preparations[0] has prep_id+federal_credit
                                        +total_qre (per Finding #5 in PROBE_FINDINGS_ARCH_D)
       Terminal-bad: status=failed    -> escalate

Poll cadence: 5s for the first 30s, then 15s thereafter, max 45 min wall-clock.

Emits a single JSON line on stdout with the same field-set the legacy
eval_and_commit.py emitted, so the downstream vmEvalResult JQ + triageFormatted
JQ + routeOnTriage CONDITION + triageHitl ESCALATE chain remains unchanged.

Env:
  INBOX_S3_URI              - s3:// URI of the original zip in Suralink inbox
  WORKSTREAM_ID             - Adopt workstream ID (forwarded for Pipeline B context)
  VM_API_URL, VM_BEARER     - echo-summit endpoint and bearer token
  RESOLVED_CLIENT_NAME      - Human-confirmed entity name (HITL path, optional)
  RESOLVED_TAX_YEAR         - Human-confirmed tax year   (HITL path, optional)
  FORCE_REPLACE             - "true" / "false" (default "false"; HITL re-trigger
                              flips this to "true" when operator confirms overwrite)
  DOCSTORE_KEY, DOCSTORE_SECRETKEY, DOCSTORE_REGION  - S3 access
"""
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import boto3


INBOX_S3_URI   = os.environ["INBOX_S3_URI"]
WORKSTREAM_ID  = os.environ.get("WORKSTREAM_ID", "")
VM_API_URL     = os.environ["VM_API_URL"].rstrip("/")
VM_BEARER      = os.environ["VM_BEARER"]
FORCE_REPLACE  = (os.environ.get("FORCE_REPLACE", "false") or "false").lower() == "true"
DOCSTORE_KEY   = os.environ["DOCSTORE_KEY"]
DOCSTORE_SEC   = os.environ["DOCSTORE_SECRETKEY"]
DOCSTORE_REG   = os.environ.get("DOCSTORE_REGION", "us-east-1")

POLL_INIT_S          = 5
POLL_INIT_DURATION_S = 30
POLL_LATE_S          = 15
POLL_MAX_S           = 45 * 60   # 45 min wall-clock


def _emit_and_exit(payload):
    """Always exit 0 — downstream WiddleExecutor reads triage_status off stdout."""
    print(json.dumps(payload))
    sys.exit(0)


def _resolve_folder(zip_stem):
    """Priority: HITL-confirmed (client + year) > parsed-from-zip-filename.

    Returns (folder_name, client_name, tax_year_str). folder_name follows the
    VM convention 'ClientName_TY YYYY' (e.g. 'Bridge Organics_TY 2025').
    """
    rc = (os.environ.get("RESOLVED_CLIENT_NAME") or "").strip()
    ry = (os.environ.get("RESOLVED_TAX_YEAR")   or "").strip()
    if rc and "{" not in rc and ry and re.match(r"^\d{4}$", ry) and "{" not in ry:
        name = f"{rc}_TY {ry}"
        print(f"[resolve] folder from HITL-confirmed: {name!r}", file=sys.stderr)
        return name, rc, ry

    m = re.match(r"^(.+?)_TY\s*(\d{4})", zip_stem, re.IGNORECASE)
    if m:
        client = m.group(1).strip()
        year   = m.group(2)
    else:
        m2 = re.match(r"^(.+?)\s*[-\u2014]?\s*TY\s*(\d{4})", zip_stem, re.IGNORECASE)
        if m2:
            client = m2.group(1).strip()
            year   = m2.group(2)
        else:
            client = zip_stem
            year   = ""
    name = f"{client}_TY {year}" if year else client
    print(f"[resolve] folder from zip filename: {name!r}", file=sys.stderr)
    return name, client, year


def _vm_get(path, timeout=30):
    req = urllib.request.Request(
        f"{VM_API_URL}{path}",
        headers={"Authorization": f"Bearer {VM_BEARER}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:600]
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"detail": body}


def _upload_zip(zip_path, folder_name, tax_year):
    """POST /api/v1/clients/upload-zip with multipart (urllib-only, no requests dep)."""
    boundary = b"----FormBoundaryUHYUploadPoll"
    zip_bytes = zip_path.read_bytes()
    upload_filename = f"{folder_name}.zip".encode()

    parts = [
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="file"; filename="' + upload_filename + b'"\r\n'
        b"Content-Type: application/zip\r\n\r\n"
        + zip_bytes + b"\r\n",
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="dir_name"\r\n\r\n'
        + folder_name.encode() + b"\r\n",
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="tax_year"\r\n\r\n'
        + (tax_year or "2025").encode() + b"\r\n",
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="auto_ingest"\r\n\r\ntrue\r\n',
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="force_replace"\r\n\r\n'
        + (b"true" if FORCE_REPLACE else b"false") + b"\r\n",
    ]
    if WORKSTREAM_ID and "{" not in WORKSTREAM_ID:
        parts.append(
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="workstream_id_hint"\r\n\r\n'
            + WORKSTREAM_ID.encode() + b"\r\n"
        )
    parts.append(b"--" + boundary + b"--\r\n")
    body = b"".join(parts)

    req = urllib.request.Request(
        f"{VM_API_URL}/api/v1/clients/upload-zip",
        data=body, method="POST",
        headers={
            "Authorization": f"Bearer {VM_BEARER}",
            "Content-Type": f"multipart/form-data; boundary={boundary.decode()}",
            "Content-Length": str(len(body)),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")[:600]
        try:
            return e.code, json.loads(body_text)
        except json.JSONDecodeError:
            return e.code, {"detail": body_text}


def _poll_until_terminal(path, terminal_ok, terminal_bad, label):
    """Poll a status endpoint until terminal status OR POLL_MAX_S elapsed.

    Returns (sc, body). On timeout returns (None, {"status": "timeout", ...}).
    """
    start    = time.monotonic()
    next_log = 30
    while True:
        elapsed = time.monotonic() - start
        if elapsed >= POLL_MAX_S:
            print(f"[{label}] timeout after {int(elapsed)}s", file=sys.stderr)
            return None, {"status": "timeout", "elapsed_seconds": int(elapsed)}

        sc, body = _vm_get(path, timeout=30)
        status = body.get("status", "") if isinstance(body, dict) else ""

        # Hard HTTP error (404, 5xx) — bubble up
        if sc not in (200, 202):
            print(f"[{label}] HTTP {sc}: {str(body)[:200]}", file=sys.stderr)
            return sc, body

        # Terminal
        if status in terminal_ok or status in terminal_bad:
            print(f"[{label}] terminal: {status} after {int(elapsed)}s", file=sys.stderr)
            return sc, body

        # Heartbeat log every ~30s so the run log doesn't go silent
        if elapsed >= next_log:
            print(f"[{label}] status={status!r} elapsed={int(elapsed)}s", file=sys.stderr)
            next_log = int(elapsed) + 30

        sleep_s = POLL_INIT_S if elapsed < POLL_INIT_DURATION_S else POLL_LATE_S
        time.sleep(sleep_s)


def _elapsed_from_events(events):
    """Return seconds between 'started' and last terminal event, or None."""
    started  = next((e for e in events if e.get("type") == "started"), None)
    terminal = next((e for e in reversed(events)
                     if e.get("type") in ("succeeded", "failed")), None)
    if not (started and terminal):
        return None
    try:
        s = datetime.fromisoformat(started["at"].replace("Z", "+00:00"))
        t = datetime.fromisoformat(terminal["at"].replace("Z", "+00:00"))
        return int((t - s).total_seconds())
    except Exception:
        return None


def main():
    if not INBOX_S3_URI.startswith("s3://"):
        _emit_and_exit({
            "triage_status": "error", "go_no_go": "NO_GO",
            "blockers": [{"severity": "BLOCKER", "check": "invalid_s3_uri",
                          "detail": f"INBOX_S3_URI must start with s3:// (got {INBOX_S3_URI[:60]!r})"}],
        })

    bucket, _, key = INBOX_S3_URI[5:].partition("/")
    zip_stem = Path(key).stem
    folder_name, parsed_client, parsed_year = _resolve_folder(zip_stem)

    tax_year = (os.environ.get("RESOLVED_TAX_YEAR") or "").strip()
    if not (tax_year and re.match(r"^\d{4}$", tax_year) and "{" not in tax_year):
        tax_year = parsed_year or ""

    # --- S3 download ----------------------------------------------------------
    s3 = boto3.client(
        "s3",
        aws_access_key_id=DOCSTORE_KEY,
        aws_secret_access_key=DOCSTORE_SEC,
        region_name=DOCSTORE_REG,
    )
    workdir = Path(tempfile.mkdtemp())
    zip_path = workdir / Path(key).name
    print(f"[s3] download s3://{bucket}/{key}", file=sys.stderr)
    s3.download_file(bucket, key, str(zip_path))
    print(f"[s3] downloaded {zip_path.stat().st_size:,} bytes", file=sys.stderr)

    # --- Step 1: POST /api/v1/clients/upload-zip ------------------------------
    print(
        f"[step1] POST /api/v1/clients/upload-zip  dir={folder_name!r}  "
        f"ty={tax_year!r}  force_replace={FORCE_REPLACE}",
        file=sys.stderr,
    )
    sc, upload_resp = _upload_zip(zip_path, folder_name, tax_year)

    if sc == 409:
        # Directory already exists — escalate via existing triage HITL.
        # Operator aborts; manual re-trigger with FORCE_REPLACE=true overwrites.
        detail = upload_resp.get("detail", upload_resp) if isinstance(upload_resp, dict) else upload_resp
        print(f"[step1] 409 dir already exists -> HITL (force_replace decision)", file=sys.stderr)
        _emit_and_exit({
            "triage_status": "HITL_REQUIRED", "go_no_go": "NO_GO",
            "recommendation_code": "FORCE_REPLACE_DECISION",
            "blockers": [{
                "severity": "BLOCKER", "check": "dir_already_exists",
                "detail": (
                    f"Directory {folder_name!r} already exists on VM. To overwrite, "
                    f"re-trigger this pipeline with FORCE_REPLACE=true. VM: "
                    f"{str(detail)[:280]}"
                ),
            }],
            "folder_name": folder_name,
            "evaluation_id": "",
        })

    if sc not in (200, 202):
        detail = upload_resp.get("detail", upload_resp) if isinstance(upload_resp, dict) else upload_resp
        _emit_and_exit({
            "triage_status": "error", "go_no_go": "NO_GO",
            "blockers": [{"severity": "BLOCKER", "check": "upload_zip_failed",
                          "detail": f"upload-zip HTTP {sc}: {str(detail)[:300]}"}],
            "folder_name": folder_name,
        })

    idempotent    = bool(upload_resp.get("idempotent", False))
    ingest_job_id = upload_resp.get("ingest_job_id") or upload_resp.get("job_id")
    if not ingest_job_id:
        _emit_and_exit({
            "triage_status": "error", "go_no_go": "NO_GO",
            "blockers": [{"severity": "BLOCKER", "check": "no_ingest_job_id",
                          "detail": f"upload-zip HTTP {sc} body missing ingest_job_id: {str(upload_resp)[:300]}"}],
            "folder_name": folder_name,
        })
    print(f"[step1] ingest_job_id={ingest_job_id} idempotent={idempotent}", file=sys.stderr)

    # --- Step 2: poll /ingest/{id}/poll ---------------------------------------
    sc2, ingest_body = _poll_until_terminal(
        f"/api/v1/ingest/{ingest_job_id}/poll",
        terminal_ok={"completed"},
        terminal_bad={"failed"},
        label=f"step2/ingest:{ingest_job_id}",
    )
    if sc2 != 200 or ingest_body.get("status") != "completed":
        detail = ingest_body.get("error") or ingest_body.get("detail") or str(ingest_body)
        _emit_and_exit({
            "triage_status": "VM_INGEST_FAILED", "go_no_go": "NO_GO",
            "recommendation_code": "VM_RETRY_REQUIRED",
            "blockers": [{
                "severity": "BLOCKER", "check": "vm_ingest_failed",
                "detail": (
                    f"Ingest job {ingest_job_id} did not complete: "
                    f"{str(detail)[:300]}. Documents were uploaded; operator can "
                    f"choose 'proceed' to index docs only or 'abort' to skip."
                ),
            }],
            "folder_name": ingest_body.get("canonical_dir_name") or folder_name,
            "evaluation_id": str(ingest_job_id),
            "client_id": ingest_body.get("client_id"),
        })

    harness_job_id = ingest_body.get("harness_job_id")
    client_id      = ingest_body.get("client_id")
    canonical_dir  = (ingest_body.get("canonical_dir_name")
                      or ingest_body.get("dir_name") or folder_name)

    if not harness_job_id:
        # Ingest completed but no harness chained — VM treated as skip/silent_zero
        print(f"[step2] completed but no harness_job_id -> GO with engine=skipped",
              file=sys.stderr)
        _emit_and_exit({
            "triage_status": "GO", "go_no_go": "go",
            "recommendation_code": "PROCEED",
            "folder_name": canonical_dir,
            "client_id": client_id,
            "evaluation_id": str(ingest_job_id),
            "harness_job_id": "",
            "engine_status": "skipped",
            "total_qre": None, "federal_credit": None, "s174_sre": None,
            "prep_run_id": None, "elapsed_seconds": None,
        })
    print(f"[step2] ingest completed  harness_job_id={harness_job_id} client_id={client_id}",
          file=sys.stderr)

    # --- Step 3: poll /workbook/from-harness-async/{harness_job_id} -----------
    sc3, harness_body = _poll_until_terminal(
        f"/api/v1/workbook/from-harness-async/{harness_job_id}",
        terminal_ok={"succeeded"},
        terminal_bad={"failed"},
        label=f"step3/harness:{harness_job_id}",
    )
    pipeline_status = harness_body.get("status", "")
    events          = harness_body.get("events") or []
    preparations    = harness_body.get("preparations") or []
    prep            = preparations[0] if preparations else {}
    total_qre       = prep.get("total_qre")
    federal_credit  = prep.get("federal_credit")
    prep_run_id     = prep.get("id") or prep.get("prep_run_id")
    elapsed         = _elapsed_from_events(events)

    if pipeline_status == "failed":
        fail_ev = next((e for e in reversed(events) if e.get("type") == "failed"), {}) or {}
        detail  = fail_ev.get("error") or fail_ev.get("detail") or "harness pipeline failed"
        _emit_and_exit({
            "triage_status": "VM_PIPELINE_FAILED", "go_no_go": "NO_GO",
            "recommendation_code": "VM_RETRY_REQUIRED",
            "blockers": [{
                "severity": "BLOCKER", "check": "vm_pipeline_failed",
                "detail": f"Harness pipeline {harness_job_id} failed: {str(detail)[:300]}",
            }],
            "folder_name": canonical_dir,
            "client_id": client_id,
            "evaluation_id": str(ingest_job_id),
            "harness_job_id": str(harness_job_id),
            "engine_status": "failed",
            "total_qre": total_qre, "federal_credit": federal_credit,
            "prep_run_id": prep_run_id, "elapsed_seconds": elapsed,
        })

    if pipeline_status != "succeeded":
        # Timeout / unknown non-terminal — surface as HITL for visibility
        _emit_and_exit({
            "triage_status": "VM_TIMEOUT", "go_no_go": "NO_GO",
            "recommendation_code": "VM_RETRY_REQUIRED",
            "blockers": [{
                "severity": "BLOCKER", "check": "vm_pipeline_timeout",
                "detail": (
                    f"Harness pipeline {harness_job_id} did not reach a terminal "
                    f"status in 45 min (last status={pipeline_status!r}). "
                    f"Operator can re-trigger or abort."
                ),
            }],
            "folder_name": canonical_dir,
            "client_id": client_id,
            "evaluation_id": str(ingest_job_id),
            "harness_job_id": str(harness_job_id),
            "engine_status": pipeline_status,
            "total_qre": total_qre, "federal_credit": federal_credit,
            "prep_run_id": prep_run_id, "elapsed_seconds": elapsed,
        })

    # --- Success path ---------------------------------------------------------
    if not preparations:
        engine_status = "engine_silent_zero"
    elif total_qre is None and federal_credit is None:
        engine_status = "engine_silent_zero"
    else:
        engine_status = "completed"

    print(
        f"[step3] succeeded  prep_id={prep_run_id} qre={total_qre} "
        f"credit={federal_credit} elapsed={elapsed}s engine_status={engine_status}",
        file=sys.stderr,
    )
    _emit_and_exit({
        "triage_status": "GO", "go_no_go": "go",
        "recommendation_code": "PROCEED",
        "folder_name": canonical_dir,
        "client_id": client_id,
        "evaluation_id": str(ingest_job_id),
        "harness_job_id": str(harness_job_id),
        "engine_status": engine_status,
        "total_qre": total_qre,
        "federal_credit": federal_credit,
        "s174_sre": None,
        "prep_run_id": prep_run_id,
        "elapsed_seconds": elapsed,
    })


if __name__ == "__main__":
    main()
