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
import time

import azure.functions as func
from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.storage.queue import QueueClient

import admin_store
from pipeline import cost
from pipeline.orchestrator import RUNNERS, _voices as _resolved_voices

bp = func.Blueprint()
logger = logging.getLogger(__name__)

# Routes are under "portal/", not "admin/": the Functions host reserves the
# "admin" route prefix for its own management API and refuses to serve any
# function whose route starts with it.
QUEUE_NAME = "content-jobs"
# The Storage Queues extension defaults to base64 but the Python SDK sends plain
# text, and a message the host cannot decode is dead-lettered without ever
# invoking the trigger — the job just sits in "queued" forever. host.json pins
# the host to this value; test_admin.py asserts the two still agree.
QUEUE_MESSAGE_ENCODING = "none"
VALID_MODES = ("index", "generate", "refresh")
VALID_FORMATS = ("instructional", "podcast")

# certificationId is interpolated into an AI Search OData filter and used as a
# Cosmos/blob path segment downstream, so it is constrained at the boundary
# rather than escaped at each use site.
CERTIFICATION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
# Azure TTS short names. The trailing segment may contain a colon, as the Dragon
# HD voices do: "en-US-Andrew:DragonHDLatestNeural".
VOICE_NAME_RE = re.compile(r"^[A-Za-z]{2,3}-[A-Za-z0-9]{2,8}-[A-Za-z0-9:]{1,48}$")
# Fetched server-side during discovery, so restrict it to the one host we scrape.
EXAM_URL_RE = re.compile(r"^https://learn\.microsoft\.com/[A-Za-z0-9\-._~/%?=&#]*$")
# Must match the keys pipeline.orchestrator._voices() reads, or an override is
# accepted here and then silently ignored.
VALID_VOICE_ROLES = ("instructional", "podcastHost", "podcastExpert")


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
@bp.route(route="portal/claim", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
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


@bp.route(route="portal/status", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
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
@bp.route(route="portal/admins", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_admins(req: func.HttpRequest) -> func.HttpResponse:
    _, error = _require_admin(req)
    if error:
        return error
    return _json({"admins": admin_store.list_admins()})


@bp.route(route="portal/admins", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
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
    route="portal/admins/{adminId}", methods=["DELETE"], auth_level=func.AuthLevel.ANONYMOUS
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
@bp.route(route="portal/jobs", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_jobs(req: func.HttpRequest) -> func.HttpResponse:
    _, error = _require_admin(req)
    if error:
        return error
    return _json({"jobs": admin_store.list_jobs()})


@bp.route(route="portal/jobs", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
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

    exam_url = (body.get("examUrl") or "").strip()
    if exam_url and not EXAM_URL_RE.match(exam_url):
        return _json(
            {"error": "examUrl must be an https://learn.microsoft.com/ URL"}, 400
        )

    # One generation at a time: these runs are long and compete for the B1 plan
    # with audio streaming.
    existing = admin_store.active_job()
    if existing and admin_store.is_stale(existing):
        admin_store.mark_cancelled(
            existing["jobId"], "No worker claimed this job; superseded by a new request"
        )
        logger.warning("Cancelled stale queued job %s", existing["jobId"])
        existing = None
    if existing:
        return _json(
            {"error": "A job is already running", "jobId": existing["jobId"]}, 409
        )

    # Recomputed here rather than trusted from the client, so the figure stored
    # against the job is comparable with the metered actual. An estimate is a
    # nicety: never block a run because the course lookup was unavailable.
    estimate = None
    if mode in ("generate", "refresh"):
        try:
            course = admin_store.get_course(certification_id)
            if course and course.get("unitCount"):
                estimate = cost.estimate(
                    episode_count=course["unitCount"],
                    audio_format=audio_format,
                    voices=_resolved_voices(voices),
                    measured_chars_per_episode=course.get("measuredCharsPerEpisode"),
                )
        except Exception as exc:
            logger.warning("Could not estimate cost for %s: %s", certification_id, exc)

    job = admin_store.create_job(
        mode=mode,
        certification_id=certification_id,
        audio_format=audio_format,
        voices=voices,
        force=bool(body.get("force")),
        requested_by=user.get("userDetails", ""),
        exam_url=exam_url,
        estimate=estimate,
    )

    client = _queue_client()
    try:
        client.create_queue()
    except ResourceExistsError:
        pass
    client.send_message(json.dumps({"jobId": job["jobId"]}))

    return _json({"jobId": job["jobId"], "status": job["status"]}, 202)


@bp.route(route="portal/jobs/{jobId}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_job(req: func.HttpRequest) -> func.HttpResponse:
    _, error = _require_admin(req)
    if error:
        return error

    job = admin_store.get_job(req.route_params.get("jobId"))
    if not job:
        return _json({"error": "Job not found"}, 404)
    return _json(job)


@bp.route(
    route="portal/jobs/{jobId}/cancel",
    methods=["POST"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def cancel_job(req: func.HttpRequest) -> func.HttpResponse:
    """Mark a job cancelled so it stops blocking new submissions.

    A running job is not interrupted mid-step; run_content_job checks the status
    on redelivery, and the record stops counting as active either way.
    """
    _, error = _require_admin(req)
    if error:
        return error

    job = admin_store.get_job(req.route_params.get("jobId"))
    if not job:
        return _json({"error": "Job not found"}, 404)
    if job["status"] not in admin_store.ACTIVE_JOB_STATES:
        return _json({"error": f"Job is already {job['status']}"}, 409)

    return _json({"job": admin_store.mark_cancelled(job["jobId"], "Cancelled by admin")})


# --------------------------------------------------------------------- voices
VOICE_CACHE_TTL_SECONDS = 3600
_voice_cache: dict = {"expires": 0.0, "payload": None}


@bp.route(route="portal/voices", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_voices(req: func.HttpRequest) -> func.HttpResponse:
    """List the en-* neural voices the deployed Speech region actually supports."""
    _, error = _require_admin(req)
    if error:
        return error

    now = time.monotonic()
    if _voice_cache["payload"] and now < _voice_cache["expires"]:
        return _json(_voice_cache["payload"])

    from pipeline.generate_episodes import _fetch_speech_voice_catalog

    try:
        region, catalog = _fetch_speech_voice_catalog()
    except Exception as exc:
        logger.warning("Voice list unavailable: %s", exc)
        return _json({"error": "Could not reach the Speech voice list"}, 502)

    voices = [
        {
            "shortName": v["ShortName"],
            "displayName": v.get("LocalName") or v.get("DisplayName") or v["ShortName"],
            "locale": v.get("Locale", ""),
            "gender": v.get("Gender", ""),
            "isDragonHD": "DragonHD" in v["ShortName"],
        }
        for v in catalog
        if v.get("ShortName", "").startswith("en-")
        and VOICE_NAME_RE.match(v.get("ShortName", ""))
    ]
    voices.sort(key=lambda v: (not v["isDragonHD"], v["shortName"]))

    payload = {"region": region, "voices": voices}
    _voice_cache["payload"] = payload
    _voice_cache["expires"] = now + VOICE_CACHE_TTL_SECONDS
    return _json(payload)


# -------------------------------------------------------------------- courses
@bp.route(route="portal/courses", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_courses(req: func.HttpRequest) -> func.HttpResponse:
    _, error = _require_admin(req)
    if error:
        return error
    return _json({"courses": admin_store.list_courses(), "rates": cost.RATES})


@bp.route(
    route="portal/courses/{certificationId}",
    methods=["GET"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def get_course(req: func.HttpRequest) -> func.HttpResponse:
    _, error = _require_admin(req)
    if error:
        return error

    cert_id = (req.route_params.get("certificationId") or "").lower()
    course = admin_store.get_course(cert_id)
    if not course:
        return _json({"error": "Course not found"}, 404)

    jobs = [j for j in admin_store.list_jobs(limit=100) if j.get("certificationId") == cert_id]
    return _json(
        {
            "course": course,
            "jobs": jobs[:20],
            # Served so the browser can recompute the estimate live without a
            # round trip, and without a second copy of the price list.
            "rates": cost.RATES,
            "defaults": {
                "wordsPerEpisode": cost.DEFAULT_WORDS_PER_EPISODE,
                "charsPerWord": cost.CHARS_PER_WORD,
                "gptInputTokensPerEpisode": cost.GPT_INPUT_TOKENS_PER_EPISODE,
                "gptOutputTokensPerEpisode": cost.GPT_OUTPUT_TOKENS_PER_EPISODE,
            },
        }
    )


@bp.route(
    route="portal/courses/{certificationId}",
    methods=["PATCH"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def patch_course(req: func.HttpRequest) -> func.HttpResponse:
    _, error = _require_admin(req)
    if error:
        return error

    cert_id = (req.route_params.get("certificationId") or "").lower()
    if not admin_store.get_course(cert_id):
        return _json({"error": "Course not found"}, 404)

    try:
        body = req.get_json() or {}
    except ValueError:
        return _json({"error": "Invalid JSON body"}, 400)

    fields = {}
    if "displayName" in body:
        fields["displayName"] = str(body["displayName"]).strip()[:200]
    if "examUrl" in body:
        exam_url = (body.get("examUrl") or "").strip()
        if exam_url and not EXAM_URL_RE.match(exam_url):
            return _json(
                {"error": "examUrl must be an https://learn.microsoft.com/ URL"}, 400
            )
        fields["examUrl"] = exam_url
    if "published" in body:
        fields["published"] = bool(body["published"])

    if not fields:
        return _json({"error": "Nothing to update"}, 400)
    return _json({"course": admin_store.upsert_course(cert_id, **fields)})


@bp.route(
    route="portal/courses/{certificationId}",
    methods=["DELETE"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def delete_course(req: func.HttpRequest) -> func.HttpResponse:
    _, error = _require_admin(req)
    if error:
        return error

    cert_id = (req.route_params.get("certificationId") or "").lower()
    if not CERTIFICATION_ID_RE.match(cert_id):
        return _json({"error": "Invalid certificationId"}, 400)
    if admin_store.active_job():
        return _json({"error": "Cannot delete while a job is running"}, 409)

    from course_teardown import purge_certification

    summary = purge_certification(cert_id)
    admin_store.delete_course(cert_id)
    return _json({"deleted": cert_id, "summary": summary})


# ------------------------------------------------------------------- estimates
@bp.route(
    route="portal/courses/{certificationId}/estimate",
    methods=["GET"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def get_estimate(req: func.HttpRequest) -> func.HttpResponse:
    _, error = _require_admin(req)
    if error:
        return error

    cert_id = (req.route_params.get("certificationId") or "").lower()
    course = admin_store.get_course(cert_id)
    if not course or not course.get("unitCount"):
        return _json({"error": "Run an index job first"}, 409)

    audio_format = req.params.get("audioFormat", "instructional")
    if audio_format not in VALID_FORMATS:
        return _json({"error": "Invalid audioFormat"}, 400)

    voices = {}
    for role in VALID_VOICE_ROLES:
        name = req.params.get(role)
        if name:
            if not VOICE_NAME_RE.match(name):
                return _json({"error": f"invalid voice name for {role}"}, 400)
            voices[role] = name

    return _json(
        cost.estimate(
            episode_count=course["unitCount"],
            audio_format=audio_format,
            voices=_resolved_voices(voices),
            measured_chars_per_episode=course.get("measuredCharsPerEpisode"),
        )
    )


# --------------------------------------------------------------- queue worker
def _record_course_outcome(job: dict, result: dict) -> None:
    """Fold a finished job into the course record the portal reads."""
    cert_id = job["certificationId"]
    fields: dict = {}

    if job["mode"] == "index":
        fields.update(
            lastDiscoveryAt=_now_iso(),
            discoveryBlobPath=result.get("discoveryBlobPath"),
            unitCount=result.get("unitCount", 0),
            totalWords=result.get("totalWords", 0),
            discoveryReport=result.get("discoveryReport"),
        )
        if job.get("examUrl"):
            fields["examUrl"] = job["examUrl"]
    else:
        voices = _resolved_voices(job.get("voices") or {})
        episodes = result.get("totalEpisodes", 0)
        fields.update(
            lastGeneratedAt=_now_iso(),
            lastJobId=job["jobId"],
            audioFormat=job["audioFormat"],
            voices=voices,
            episodeCount=episodes,
            totalDurationSeconds=round(result.get("totalDurationMinutes", 0) * 60),
        )
        if job.get("estimate"):
            fields["lastEstimateUsd"] = job["estimate"].get("totalUsd")

        usage = result.get("usage") or {}
        generated = result.get("episodesGenerated", 0)
        if usage.get("ttsChars"):
            actual = cost.actual_cost(
                tts_chars=usage.get("ttsChars", 0),
                gpt_input_tokens=usage.get("gptInputTokens", 0),
                gpt_output_tokens=usage.get("gptOutputTokens", 0),
                audio_format=job["audioFormat"],
                voices=voices,
            )
            fields["lastActualUsd"] = actual["totalUsd"]
            admin_store.update_job(job["jobId"], actualCost=actual)
            # Feeds the next estimate, so only measure episodes actually synthesised.
            if generated:
                fields["measuredCharsPerEpisode"] = round(
                    usage["ttsChars"] / generated
                )

    # The portal is not worth failing a multi-hour run over.
    try:
        admin_store.upsert_course(cert_id, **fields)
    except Exception as exc:
        logger.warning("Could not update course record for %s: %s", cert_id, exc)


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


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
            exam_url=job.get("examUrl") or None,
            progress=progress,
        )
        _record_course_outcome(job, result)
        admin_store.mark_succeeded(job_id, result)
        logger.info("Job %s succeeded: %s", job_id, result)
    except Exception as exc:  # surfaced to the admin UI via the job record
        logger.exception("Job %s failed", job_id)
        admin_store.mark_failed(job_id, str(exc))
        raise
