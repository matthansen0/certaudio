"""Cosmos-backed storage for admin identities and generation jobs.

Containers are created by Bicep. The Cosmos SQL Data Contributor role covers
item operations but not container management, so nothing here tries to create
them at runtime.
"""

import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from azure.cosmos import CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from azure.identity import DefaultAzureCredential

BOOTSTRAP_DOC_ID = "__bootstrap__"
ACTIVE_JOB_STATES = ("queued", "running")

_client = None
_credential = None


def _get_credential():
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential


def _database():
    global _client
    if _client is None:
        _client = CosmosClient(os.environ["COSMOS_DB_ENDPOINT"], _get_credential())
    return _client.get_database_client(os.environ.get("COSMOS_DB_DATABASE", "certaudio"))


def _admins():
    return _database().get_container_client("admins")


def _jobs():
    return _database().get_container_client("jobs")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------- admins
def is_bootstrap_claimed() -> bool:
    try:
        _admins().read_item(BOOTSTRAP_DOC_ID, partition_key=BOOTSTRAP_DOC_ID)
        return True
    except CosmosResourceNotFoundError:
        return False


def list_admins() -> list[dict]:
    query = "SELECT * FROM c WHERE c.id != @bootstrap"
    return list(
        _admins().query_items(
            query=query,
            parameters=[{"name": "@bootstrap", "value": BOOTSTRAP_DOC_ID}],
            enable_cross_partition_query=True,
        )
    )


def is_admin(user: dict) -> bool:
    """Match on the stable SWA userId, falling back to the sign-in address.

    userId is preferred because it survives an email change; userDetails is kept
    so an admin can be pre-registered before their first sign-in.
    """
    if not user:
        return False
    user_id = (user.get("userId") or "").strip()
    details = (user.get("userDetails") or "").strip().lower()

    for record in list_admins():
        if user_id and record.get("userId") == user_id:
            return True
        if details and (record.get("userDetails") or "").strip().lower() == details:
            return True
    return False


def add_admin(user: dict, added_by: str) -> dict:
    record = {
        "id": user.get("userId") or str(uuid.uuid4()),
        "userId": user.get("userId", ""),
        "userDetails": user.get("userDetails", ""),
        "identityProvider": user.get("identityProvider", ""),
        "addedBy": added_by,
        "addedAt": _now(),
    }
    _admins().upsert_item(record)
    return record


def remove_admin(admin_id: str) -> bool:
    if admin_id == BOOTSTRAP_DOC_ID:
        return False
    try:
        _admins().delete_item(admin_id, partition_key=admin_id)
        return True
    except CosmosResourceNotFoundError:
        return False


def claim_bootstrap(user: dict) -> dict:
    """Register the first admin and burn the bootstrap token.

    The marker doc is written first: if the admin write then fails the token is
    still spent, which fails closed rather than leaving it reusable.
    """
    _admins().create_item(
        {
            "id": BOOTSTRAP_DOC_ID,
            "claimed": True,
            "claimedAt": _now(),
            "claimedBy": user.get("userDetails", ""),
        }
    )
    return add_admin(user, added_by="bootstrap")


# ----------------------------------------------------------------------- jobs
def active_job() -> Optional[dict]:
    query = "SELECT * FROM c WHERE c.status IN ('queued', 'running')"
    results = list(_jobs().query_items(query=query, enable_cross_partition_query=True))
    return results[0] if results else None


def create_job(
    mode: str,
    certification_id: str,
    audio_format: str,
    voices: dict,
    force: bool,
    requested_by: str,
) -> dict:
    job = {
        "id": str(uuid.uuid4()),
        "jobId": str(uuid.uuid4()),
        "mode": mode,
        "certificationId": certification_id,
        "audioFormat": audio_format,
        "voices": voices,
        "force": force,
        "status": "queued",
        "phase": "queued",
        "progress": {"current": 0, "total": 0, "message": "Waiting for worker"},
        "requestedBy": requested_by,
        "createdAt": _now(),
        "startedAt": None,
        "completedAt": None,
        "error": None,
        "result": None,
    }
    job["id"] = job["jobId"]
    _jobs().upsert_item(job)
    return job


def get_job(job_id: str) -> Optional[dict]:
    try:
        return _jobs().read_item(job_id, partition_key=job_id)
    except CosmosResourceNotFoundError:
        return None


def list_jobs(limit: int = 20) -> list[dict]:
    query = "SELECT * FROM c ORDER BY c.createdAt DESC OFFSET 0 LIMIT @limit"
    return list(
        _jobs().query_items(
            query=query,
            parameters=[{"name": "@limit", "value": limit}],
            enable_cross_partition_query=True,
        )
    )


def update_job(job_id: str, **fields) -> Optional[dict]:
    job = get_job(job_id)
    if not job:
        return None
    job.update(fields)
    _jobs().upsert_item(job)
    return job


def mark_running(job_id: str) -> Optional[dict]:
    return update_job(job_id, status="running", phase="starting", startedAt=_now())


def mark_succeeded(job_id: str, result: dict) -> Optional[dict]:
    return update_job(
        job_id,
        status="succeeded",
        phase="done",
        completedAt=_now(),
        result=result,
        progress={"current": 1, "total": 1, "message": "Complete"},
    )


def mark_failed(job_id: str, error: str) -> Optional[dict]:
    return update_job(
        job_id,
        status="failed",
        phase="failed",
        completedAt=_now(),
        error=error[:2000],
    )


def record_progress(job_id: str, phase: str, current: int, total: int, message: str) -> None:
    update_job(
        job_id,
        phase=phase,
        progress={"current": current, "total": total, "message": message},
    )
