"""Admin portal API and the queue worker that runs content generation.

Registered as a blueprint on the main function app. Every route here requires
an authenticated Static Web Apps principal; SWA-managed EasyAuth on the Function
App is what prevents the x-ms-client-principal header being forged by calling
the Function hostname directly.
"""

import hmac
import json
import logging
import os
import re

import azure.functions as func
from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.storage.queue import QueueClient

import admin_store
from pipeline.orchestrator import RUNNERS

bp = func.Blueprint()
logger = logging.getLogger(__name__)

QUEUE_NAME = "content-jobs"
VALID_MODES = ("generate", "refresh")
VALID_FORMATS = ("instructional", "podcast")

# certificationId is interpolated into an AI Search OData filter and used as a
# Cosmos/blob path segment downstream, so it is constrained at the boundary
# rather than escaped at each use site.
CERTIFICATION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
# Azure TTS short names, e.g. "en-US-AndrewMultilingualNeural".
VOICE_NAME_RE = re.compile(r"^[A-Za-z]{2,3}-[A-Za-z0-9]{2,8}-[A-Za-z0-9]{1,48}$")
VALID_VOICE_ROLES = ("primary", "secondary")


def _json(payload: dict, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload), status_code=status, mimetype="application/json"
    )


def _get_user(req: func.HttpRequest):
    # Imported lazily so the blueprint does not create a circular import at load.
    from function_app import _get_swa_user

    return _get_swa_user(req)


def _require_admin(req: func.HttpRequest):
    """Return (user, None) when the caller is an admin, else (None, response)."""
    user = _get_user(req)
    if not user:
        return None, _json({"error": "Not authenticated"}, 401)
    if not admin_store.is_admin(user):
        return None, _json({"error": "Forbidden"}, 403)
    return user, None


def _queue_client() -> QueueClient:
    # Reuse the host storage connection the Functions runtime already uses for
    # the queue trigger, rather than introducing a second account setting.
    queue_service_uri = os.environ["AzureWebJobsStorage__queueServiceUri"].rstrip("/")
    return QueueClient(
        account_url=queue_service_uri,
        queue_name=QUEUE_NAME,
        credential=DefaultAzureCredential(),
    )


# ------------------------------------------------------------------ bootstrap
@bp.route(route="admin/claim", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def claim_admin(req: func.HttpRequest) -> func.HttpResponse:
    """Claim the first admin account using the one-time bootstrap token."""
    user = _get_user(req)
    if not user:
        return _json({"error": "Not authenticated"}, 401)

    expected = os.environ.get("ADMIN_BOOTSTRAP_TOKEN", "")
    if not expected:
        return _json({"error": "Bootstrap is not configured"}, 409)

    try:
        supplied = (req.get_json() or {}).get("token", "")
    except ValueError:
        supplied = ""

    if not hmac.compare_digest(str(supplied), expected):
        logger.warning("Rejected bootstrap claim from %s", user.get("userDetails"))
        return _json({"error": "Invalid token"}, 403)

    if admin_store.is_bootstrap_claimed():
        return _json({"error": "Bootstrap has already been claimed"}, 409)

    try:
        record = admin_store.claim_bootstrap(user)
    except ResourceExistsError:
        return _json({"error": "Bootstrap has already been claimed"}, 409)

    logger.info("Bootstrap claimed by %s", user.get("userDetails"))
    return _json({"claimed": True, "admin": record})


@bp.route(route="admin/status", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def admin_status(req: func.HttpRequest) -> func.HttpResponse:
    """Report whether the caller is an admin and whether bootstrap is available."""
    user = _get_user(req)
    if not user:
        return _json({"authenticated": False, "isAdmin": False})
    return _json(
        {
            "authenticated": True,
            "isAdmin": admin_store.is_admin(user),
            "bootstrapClaimed": admin_store.is_bootstrap_claimed(),
            "userDetails": user.get("userDetails", ""),
        }
    )


# --------------------------------------------------------------------- admins
@bp.route(route="admin/admins", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_admins(req: func.HttpRequest) -> func.HttpResponse:
    _, error = _require_admin(req)
    if error:
        return error
    return _json({"admins": admin_store.list_admins()})


@bp.route(route="admin/admins", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def post_admin(req: func.HttpRequest) -> func.HttpResponse:
    user, error = _require_admin(req)
    if error:
        return error

    try:
        body = req.get_json() or {}
    except ValueError:
        return _json({"error": "Invalid JSON body"}, 400)

    details = (body.get("userDetails") or "").strip()
    if not details:
        return _json({"error": "userDetails is required"}, 400)

    record = admin_store.add_admin(
        {"userId": body.get("userId", ""), "userDetails": details},
        added_by=user.get("userDetails", ""),
    )
    return _json({"admin": record}, 201)


@bp.route(
    route="admin/admins/{adminId}", methods=["DELETE"], auth_level=func.AuthLevel.ANONYMOUS
)
def delete_admin(req: func.HttpRequest) -> func.HttpResponse:
    user, error = _require_admin(req)
    if error:
        return error

    admin_id = req.route_params.get("adminId")
    remaining = [a for a in admin_store.list_admins() if a["id"] != admin_id]
    if not remaining:
        return _json({"error": "Cannot remove the last admin"}, 409)
    if admin_id == user.get("userId"):
        return _json({"error": "Cannot remove yourself"}, 409)

    if not admin_store.remove_admin(admin_id):
        return _json({"error": "Admin not found"}, 404)
    return _json({"removed": admin_id})


# ----------------------------------------------------------------------- jobs
@bp.route(route="admin/jobs", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_jobs(req: func.HttpRequest) -> func.HttpResponse:
    _, error = _require_admin(req)
    if error:
        return error
    return _json({"jobs": admin_store.list_jobs()})


@bp.route(route="admin/jobs", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def post_job(req: func.HttpRequest) -> func.HttpResponse:
    user, error = _require_admin(req)
    if error:
        return error

    try:
        body = req.get_json() or {}
    except ValueError:
        return _json({"error": "Invalid JSON body"}, 400)

    mode = body.get("mode", "generate")
    certification_id = (body.get("certificationId") or "").strip().lower()
    audio_format = body.get("audioFormat", "instructional")

    if mode not in VALID_MODES:
        return _json({"error": f"mode must be one of {', '.join(VALID_MODES)}"}, 400)
    if not certification_id:
        return _json({"error": "certificationId is required"}, 400)
    if not CERTIFICATION_ID_RE.match(certification_id):
        return _json(
            {"error": "certificationId must be lowercase letters, digits and hyphens"},
            400,
        )
    if audio_format not in VALID_FORMATS:
        return _json({"error": f"audioFormat must be one of {', '.join(VALID_FORMATS)}"}, 400)

    voices = body.get("voices") or {}
    if not isinstance(voices, dict):
        return _json({"error": "voices must be an object"}, 400)
    for role, name in voices.items():
        if role not in VALID_VOICE_ROLES:
            return _json(
                {"error": f"voice role must be one of {', '.join(VALID_VOICE_ROLES)}"},
                400,
            )
        if not isinstance(name, str) or not VOICE_NAME_RE.match(name):
            return _json({"error": f"invalid voice name for {role}"}, 400)

    # One generation at a time: these runs are long and compete for the B1 plan
    # with audio streaming.
    existing = admin_store.active_job()
    if existing:
        return _json(
            {"error": "A job is already running", "jobId": existing["jobId"]}, 409
        )

    job = admin_store.create_job(
        mode=mode,
        certification_id=certification_id,
        audio_format=audio_format,
        voices=voices,
        force=bool(body.get("force")),
        requested_by=user.get("userDetails", ""),
    )

    client = _queue_client()
    try:
        client.create_queue()
    except ResourceExistsError:
        pass
    client.send_message(json.dumps({"jobId": job["jobId"]}))

    return _json({"jobId": job["jobId"], "status": job["status"]}, 202)


@bp.route(route="admin/jobs/{jobId}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_job(req: func.HttpRequest) -> func.HttpResponse:
    _, error = _require_admin(req)
    if error:
        return error

    job = admin_store.get_job(req.route_params.get("jobId"))
    if not job:
        return _json({"error": "Job not found"}, 404)
    return _json(job)


# --------------------------------------------------------------- queue worker
@bp.queue_trigger(
    arg_name="msg", queue_name=QUEUE_NAME, connection="AzureWebJobsStorage"
)
def run_content_job(msg: func.QueueMessage) -> None:
    """Run a generation or refresh job.

    host.json sets functionTimeout to -1 and queue batchSize to 1, so a run can
    take hours and only one executes at a time.
    """
    job_id = json.loads(msg.get_body().decode("utf-8"))["jobId"]
    job = admin_store.get_job(job_id)
    if not job:
        logger.error("Queue message referenced unknown job %s", job_id)
        return

    if job["status"] in ("succeeded", "cancelled"):
        logger.info("Job %s already finished (%s); ignoring redelivery", job_id, job["status"])
        return

    admin_store.mark_running(job_id)
    logger.info(
        "Job %s starting: %s %s/%s",
        job_id, job["mode"], job["certificationId"], job["audioFormat"],
    )

    def progress(phase: str, current: int, total: int, message: str) -> None:
        logger.info("Job %s [%s] %s/%s %s", job_id, phase, current, total, message)
        admin_store.record_progress(job_id, phase, current, total, message)

    try:
        result = RUNNERS[job["mode"]](
            certification_id=job["certificationId"],
            audio_format=job["audioFormat"],
            voices=job.get("voices") or {},
            force=bool(job.get("force")),
            progress=progress,
        )
        admin_store.mark_succeeded(job_id, result)
        logger.info("Job %s succeeded: %s", job_id, result)
    except Exception as exc:  # surfaced to the admin UI via the job record
        logger.exception("Job %s failed", job_id)
        admin_store.mark_failed(job_id, str(exc))
        raise
