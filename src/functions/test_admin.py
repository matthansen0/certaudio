"""
Unit tests for the admin portal authorization boundary and job validation.

Run:  python -m pytest src/functions/test_admin.py -v
"""

import base64
import json
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
    return getattr(admin, name)._function.get_user_function()


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
        ({"voices": ["en-US-AndrewNeural"], "certificationId": "dp-700"}, "voices must be an object"),
        ({"certificationId": "dp-700", "voices": {"evil": "en-US-AndrewNeural"}}, "voice role"),
        ({"certificationId": "dp-700", "voices": {"primary": "'; DROP"}}, "invalid voice name"),
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
            "voices": {"primary": "en-US-AndrewMultilingualNeural"},
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


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

# The Functions host serves its own management API under /admin and refuses to
# start any function whose route begins with a reserved segment. The failure is
# silent from the app's point of view: the function registers, then the host
# logs "conflicts with one or more built in routes" and never serves it. Calling
# handlers directly in tests cannot catch that, so assert the routes instead.
RESERVED_ROUTE_PREFIXES = ("admin", "runtime")


def _declared_routes():
    routes = []
    for name in dir(admin):
        obj = getattr(admin, name)
        fn = getattr(obj, "_function", None)
        if fn is None:
            continue
        trigger = fn.get_trigger()
        route = getattr(trigger, "route", None)
        if route:
            routes.append((name, route))
    return routes


def test_no_route_uses_a_reserved_prefix():
    offenders = [
        (name, route)
        for name, route in _declared_routes()
        if route.split("/")[0].lower() in RESERVED_ROUTE_PREFIXES
    ]
    assert not offenders, (
        f"These routes would be rejected by the Functions host: {offenders}"
    )


def test_admin_routes_are_registered():
    """Guard against the blueprint silently losing its routes."""
    routes = {route for _, route in _declared_routes()}
    assert "portal/status" in routes
    assert "portal/jobs" in routes
