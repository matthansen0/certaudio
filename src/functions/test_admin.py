"""
Unit tests for the admin portal authorization boundary and job validation.

Run:  python -m pytest src/functions/test_admin.py -v
"""

import base64
import json
import pathlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import azure.functions as func
import pytest

import admin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _principal_header(
    user_id: str = "aad-abc123",
    user_details: str = "user@example.com",
) -> dict:
    payload = {
        "identityProvider": "aad",
        "userId": user_id,
        "userDetails": user_details,
        "userRoles": ["authenticated"],
    }
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    return {"x-ms-client-principal": encoded}


def _request(
    *,
    method: str = "GET",
    headers: dict | None = None,
    route_params: dict | None = None,
    body: dict | None = None,
) -> func.HttpRequest:
    return func.HttpRequest(
        method=method,
        url="https://localhost",
        headers=headers or {},
        route_params=route_params or {},
        body=json.dumps(body).encode() if body else b"",
    )


def _body(response: func.HttpResponse) -> dict:
    return json.loads(response.get_body().decode())


# Routes are exposed as blueprint FunctionBuilders; unwrap to the plain callable.
def _fn(name):
    return getattr(admin, name)._function._func


# ---------------------------------------------------------------------------
# Authorization boundary
# ---------------------------------------------------------------------------

ADMIN_ROUTES = [
    ("get_admins", "GET", None, None),
    ("post_admin", "POST", None, {"userDetails": "x@example.com"}),
    ("delete_admin", "DELETE", {"adminId": "someone"}, None),
    ("get_jobs", "GET", None, None),
    ("post_job", "POST", None, {"certificationId": "dp-700"}),
    ("get_job", "GET", {"jobId": "j1"}, None),
    ("cancel_job", "POST", {"jobId": "j1"}, None),
    ("get_voices", "GET", None, None),
    ("get_courses", "GET", None, None),
    ("get_course", "GET", {"certificationId": "dp-700"}, None),
    ("patch_course", "PATCH", {"certificationId": "dp-700"}, {"published": False}),
    ("delete_course", "DELETE", {"certificationId": "dp-700"}, None),
    ("get_estimate", "GET", {"certificationId": "dp-700"}, None),
]


@pytest.mark.parametrize("name,method,route_params,body", ADMIN_ROUTES)
def test_admin_routes_reject_anonymous(name, method, route_params, body):
    """No principal header means no access, whatever the route."""
    req = _request(method=method, route_params=route_params, body=body)
    response = _fn(name)(req)
    assert response.status_code == 401


@pytest.mark.parametrize("name,method,route_params,body", ADMIN_ROUTES)
def test_admin_routes_reject_authenticated_non_admin(name, method, route_params, body):
    """A signed-in user who is not on the admin list gets 403, not 500."""
    req = _request(
        method=method,
        headers=_principal_header(),
        route_params=route_params,
        body=body,
    )
    with patch.object(admin.admin_store, "is_admin", return_value=False):
        response = _fn(name)(req)
    assert response.status_code == 403


def _declared_http_routes(decorator_api) -> list[str]:
    routes = []
    for builder in decorator_api._function_builders:
        trigger = builder._function.get_trigger()
        route = getattr(trigger, "route", None)
        if route is not None:
            routes.append(str(route))
    return routes


def test_no_route_uses_a_reserved_prefix():
    """The Functions host silently refuses to serve any route starting with "admin".

    It still registers and lists, so calling the handler directly cannot catch
    this -- only inspecting the declared route can.
    """
    import function_app

    routes = _declared_http_routes(admin.bp) + _declared_http_routes(function_app.app)
    assert routes, "Route introspection returned nothing; the assertion is vacuous"

    offenders = [r for r in routes if r.lower().lstrip("/").startswith("admin")]
    assert not offenders, (
        f"These routes collide with the host's built-in admin API and will 404: {offenders}"
    )


# ---------------------------------------------------------------------------
# Bootstrap claim
# ---------------------------------------------------------------------------

def test_claim_requires_authentication():
    response = _fn("claim_admin")(_request(method="POST", body={"token": "t"}))
    assert response.status_code == 401


def test_claim_rejects_wrong_token():
    req = _request(method="POST", headers=_principal_header(), body={"token": "wrong"})
    with patch.dict("os.environ", {"ADMIN_BOOTSTRAP_TOKEN": "correct"}):
        response = _fn("claim_admin")(req)
    assert response.status_code == 403


def test_claim_rejects_when_token_not_configured():
    """An unset token must not degrade into an empty-string match."""
    req = _request(method="POST", headers=_principal_header(), body={"token": ""})
    with patch.dict("os.environ", {"ADMIN_BOOTSTRAP_TOKEN": ""}):
        response = _fn("claim_admin")(req)
    assert response.status_code == 409


def test_claim_rejects_second_attempt():
    req = _request(method="POST", headers=_principal_header(), body={"token": "correct"})
    with patch.dict("os.environ", {"ADMIN_BOOTSTRAP_TOKEN": "correct"}), \
            patch.object(admin.admin_store, "is_bootstrap_claimed", return_value=True):
        response = _fn("claim_admin")(req)
    assert response.status_code == 409


def test_claim_succeeds_with_correct_token():
    req = _request(method="POST", headers=_principal_header(), body={"token": "correct"})
    record = {"id": "aad-abc123", "userDetails": "user@example.com"}
    with patch.dict("os.environ", {"ADMIN_BOOTSTRAP_TOKEN": "correct"}), \
            patch.object(admin.admin_store, "is_bootstrap_claimed", return_value=False), \
            patch.object(admin.admin_store, "claim_bootstrap", return_value=record):
        response = _fn("claim_admin")(req)
    assert response.status_code == 200
    assert _body(response)["claimed"] is True


# ---------------------------------------------------------------------------
# Job submission validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "body,expected_fragment",
    [
        ({"certificationId": ""}, "required"),
        ({"certificationId": "dp-700", "mode": "destroy"}, "mode must be"),
        ({"certificationId": "dp-700", "audioFormat": "video"}, "audioFormat must be"),
        # OData filter injection via the certification id.
        ({"certificationId": "x' or true or '"}, "lowercase"),
        # Path traversal into blob prefixes.
        ({"certificationId": "../../etc"}, "lowercase"),
        ({"voices": ["en-US-GuyNeural"], "certificationId": "dp-700"}, "voices must be an object"),
        ({"certificationId": "dp-700", "voices": {"evil": "en-US-AndrewNeural"}}, "voice role"),
        # Was accepted then silently ignored: the API took primary/secondary
        # while the orchestrator reads instructional/podcastHost/podcastExpert.
        ({"certificationId": "dp-700", "voices": {"primary": "en-US-GuyNeural"}}, "voice role"),
        ({"certificationId": "dp-700", "voices": {"instructional": "'; DROP"}}, "invalid voice name"),
    ],
)
def test_post_job_rejects_invalid_input(body, expected_fragment):
    req = _request(method="POST", headers=_principal_header(), body=body)
    with patch.object(admin.admin_store, "is_admin", return_value=True):
        response = _fn("post_job")(req)
    assert response.status_code == 400
    assert expected_fragment in _body(response)["error"]


def test_post_job_rejects_concurrent_run():
    req = _request(
        method="POST", headers=_principal_header(), body={"certificationId": "dp-700"}
    )
    with patch.object(admin.admin_store, "is_admin", return_value=True), \
            patch.object(admin.admin_store, "active_job", return_value={"jobId": "busy"}):
        response = _fn("post_job")(req)
    assert response.status_code == 409
    assert _body(response)["jobId"] == "busy"


def test_post_job_accepts_valid_request():
    req = _request(
        method="POST",
        headers=_principal_header(),
        body={
            "certificationId": "DP-700",  # normalised to lowercase
            "mode": "generate",
            "audioFormat": "podcast",
            "voices": {"podcastHost": "en-US-Ava:DragonHDLatestNeural"},
        },
    )
    job = {"jobId": "job-1", "status": "queued"}
    with patch.object(admin.admin_store, "is_admin", return_value=True), \
            patch.object(admin.admin_store, "active_job", return_value=None), \
            patch.object(admin.admin_store, "create_job", return_value=job) as create, \
            patch.object(admin, "_queue_client"):
        response = _fn("post_job")(req)
    assert response.status_code == 202
    assert create.call_args.kwargs["certification_id"] == "dp-700"


@pytest.mark.parametrize(
    "exam_url",
    [
        "https://evil.example.com/steal",
        "http://learn.microsoft.com/en-us/x",  # plain http
        "file:///etc/passwd",
        "https://learn.microsoft.com.evil.example.com/x",
        "javascript:alert(1)",
    ],
)
def test_post_job_rejects_exam_urls_off_microsoft_learn(exam_url):
    """The worker fetches this URL server-side, so it must not be attacker-chosen."""
    req = _request(
        method="POST",
        headers=_principal_header(),
        body={"certificationId": "dp-700", "mode": "index", "examUrl": exam_url},
    )
    with patch.object(admin.admin_store, "is_admin", return_value=True), \
            patch.object(admin.admin_store, "active_job", return_value=None), \
            patch.object(admin.admin_store, "create_job") as create, \
            patch.object(admin, "_queue_client"):
        response = _fn("post_job")(req)

    assert response.status_code == 400
    create.assert_not_called()


def test_post_job_accepts_a_learn_exam_url():
    req = _request(
        method="POST",
        headers=_principal_header(),
        body={
            "certificationId": "dp-700",
            "mode": "index",
            "examUrl": "https://learn.microsoft.com/en-us/credentials/certifications/exams/dp-700/",
        },
    )
    job = {"jobId": "job-2", "status": "queued"}
    with patch.object(admin.admin_store, "is_admin", return_value=True), \
            patch.object(admin.admin_store, "active_job", return_value=None), \
            patch.object(admin.admin_store, "create_job", return_value=job) as create, \
            patch.object(admin, "_queue_client"):
        response = _fn("post_job")(req)

    assert response.status_code == 202
    assert "dp-700" in create.call_args.kwargs["exam_url"]


def test_index_is_a_valid_mode():
    assert "index" in admin.VALID_MODES


# ---------------------------------------------------------------------------
# Admin removal guards
# ---------------------------------------------------------------------------

def test_cannot_remove_last_admin():
    req = _request(
        method="DELETE", headers=_principal_header(), route_params={"adminId": "only"}
    )
    with patch.object(admin.admin_store, "is_admin", return_value=True), \
            patch.object(admin.admin_store, "list_admins", return_value=[{"id": "only"}]):
        response = _fn("delete_admin")(req)
    assert response.status_code == 409


def test_cannot_remove_self():
    req = _request(
        method="DELETE",
        headers=_principal_header(user_id="aad-abc123"),
        route_params={"adminId": "aad-abc123"},
    )
    admins = [{"id": "aad-abc123"}, {"id": "other"}]
    with patch.object(admin.admin_store, "is_admin", return_value=True), \
            patch.object(admin.admin_store, "list_admins", return_value=admins):
        response = _fn("delete_admin")(req)
    assert response.status_code == 409


def test_status_reports_anonymous_without_touching_cosmos():
    """An unauthenticated status probe must not query Cosmos."""
    with patch.object(admin.admin_store, "is_admin") as is_admin:
        response = _fn("admin_status")(_request())
    is_admin.assert_not_called()
    assert _body(response) == {"authenticated": False, "isAdmin": False}


def test_voice_roles_match_the_orchestrator():
    """The API and the pipeline must agree on voice role names.

    They disagreed once: the API validated primary/secondary while the
    orchestrator read instructional/podcastHost/podcastExpert, so an override
    was accepted and then silently dropped.
    """
    from pipeline.orchestrator import _voices

    assert set(_voices({})) == set(admin.VALID_VOICE_ROLES)


@pytest.mark.parametrize(
    "voice",
    [
        "en-US-Andrew:DragonHDLatestNeural",
        "en-US-Ava:DragonHDLatestNeural",
        "en-US-AndrewMultilingualNeural",
        "en-US-GuyNeural",
    ],
)
def test_default_voices_pass_validation(voice):
    """The shipped defaults must survive our own input validation.

    The Dragon HD names contain a colon, which an earlier pattern rejected.
    """
    assert admin.VOICE_NAME_RE.match(voice), voice


# ---------------------------------------------------------------------------
# Queue contract
# ---------------------------------------------------------------------------

class _FakeQueue:
    def __init__(self):
        self.sent = []

    def create_queue(self):
        pass

    def send_message(self, content):
        self.sent.append(content)


def test_host_queue_encoding_matches_the_producer():
    """A mismatch here dead-letters every job without ever invoking the trigger.

    The Storage Queues extension defaults to base64 while the Python SDK sends
    plain text, so the job document sits in "queued" forever with no error
    anywhere. The two sides have to be pinned together explicitly.
    """
    host = json.loads((pathlib.Path(__file__).parent / "host.json").read_text())
    assert host["extensions"]["queues"]["messageEncoding"] == admin.QUEUE_MESSAGE_ENCODING


def test_job_submission_enqueues_plain_json():
    queue = _FakeQueue()
    req = _request(
        method="POST",
        headers=_principal_header(),
        body={"mode": "index", "certificationId": "dp-700"},
    )
    with patch.object(admin.admin_store, "is_admin", return_value=True), \
            patch.object(admin.admin_store, "active_job", return_value=None), \
            patch.object(
                admin.admin_store,
                "create_job",
                return_value={"jobId": "job-1", "status": "queued"},
            ), \
            patch.object(admin, "_queue_client", return_value=queue):
        response = _fn("post_job")(req)

    assert response.status_code == 202
    # Decodes as UTF-8 JSON rather than base64, matching messageEncoding "none".
    assert json.loads(queue.sent[0]) == {"jobId": "job-1"}


# ---------------------------------------------------------------------------
# Stuck job recovery
# ---------------------------------------------------------------------------

def _aged(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def test_is_stale_flags_a_queued_job_no_worker_claimed():
    assert admin.admin_store.is_stale({"status": "queued", "createdAt": _aged(3600)})


def test_is_stale_ignores_a_freshly_queued_job():
    assert not admin.admin_store.is_stale({"status": "queued", "createdAt": _aged(5)})


def test_is_stale_ignores_running_jobs():
    """Generation legitimately runs for hours; only unclaimed jobs are stale."""
    assert not admin.admin_store.is_stale({"status": "running", "createdAt": _aged(86400)})


def test_stale_queued_job_does_not_block_new_submissions():
    """A job the worker never picked up must not lock the portal out forever."""
    stale = {"jobId": "orphan", "status": "queued", "createdAt": _aged(3600)}
    req = _request(
        method="POST",
        headers=_principal_header(),
        body={"mode": "index", "certificationId": "dp-700"},
    )
    with patch.object(admin.admin_store, "is_admin", return_value=True), \
            patch.object(admin.admin_store, "active_job", return_value=stale), \
            patch.object(admin.admin_store, "mark_cancelled") as mark_cancelled, \
            patch.object(
                admin.admin_store,
                "create_job",
                return_value={"jobId": "job-2", "status": "queued"},
            ), \
            patch.object(admin, "_queue_client", return_value=_FakeQueue()):
        response = _fn("post_job")(req)

    assert response.status_code == 202
    mark_cancelled.assert_called_once()
    assert mark_cancelled.call_args[0][0] == "orphan"


def test_running_job_still_blocks_new_submissions():
    running = {"jobId": "busy", "status": "running", "createdAt": _aged(86400)}
    req = _request(
        method="POST",
        headers=_principal_header(),
        body={"mode": "index", "certificationId": "dp-700"},
    )
    with patch.object(admin.admin_store, "is_admin", return_value=True), \
            patch.object(admin.admin_store, "active_job", return_value=running):
        response = _fn("post_job")(req)

    assert response.status_code == 409
    assert _body(response)["jobId"] == "busy"


def test_cancel_job_marks_an_active_job_cancelled():
    active = {"jobId": "j1", "status": "queued"}
    req = _request(
        method="POST", headers=_principal_header(), route_params={"jobId": "j1"}
    )
    with patch.object(admin.admin_store, "is_admin", return_value=True), \
            patch.object(admin.admin_store, "get_job", return_value=active), \
            patch.object(
                admin.admin_store,
                "mark_cancelled",
                return_value={"jobId": "j1", "status": "cancelled"},
            ) as mark_cancelled:
        response = _fn("cancel_job")(req)

    assert response.status_code == 200
    mark_cancelled.assert_called_once()


def test_cancel_job_rejects_a_finished_job():
    req = _request(
        method="POST", headers=_principal_header(), route_params={"jobId": "j1"}
    )
    done = {"jobId": "j1", "status": "succeeded"}
    with patch.object(admin.admin_store, "is_admin", return_value=True), \
            patch.object(admin.admin_store, "get_job", return_value=done):
        response = _fn("cancel_job")(req)

    assert response.status_code == 409
