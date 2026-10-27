"""
Unit tests for SWA authentication helpers and authenticated progress endpoints.

Run:  python -m pytest src/functions/test_auth.py -v
"""

import base64
import fnmatch
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import azure.functions as func
import pytest

# ---------------------------------------------------------------------------
# Import helpers & endpoints under test
# ---------------------------------------------------------------------------
from function_app import (
    _get_swa_user,
    get_episodes,
    get_me,
    get_my_progress,
    list_certifications,
    update_my_progress,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_principal_header(
    user_id: str = "aad-abc123",
    user_details: str = "user@example.com",
    identity_provider: str = "aad",
) -> str:
    """Build a base64-encoded x-ms-client-principal header value."""
    payload = {
        "identityProvider": identity_provider,
        "userId": user_id,
        "userDetails": user_details,
        "userRoles": ["authenticated", "anonymous"],
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


def _make_request(
    *,
    method: str = "GET",
    headers: dict | None = None,
    route_params: dict | None = None,
    body: dict | None = None,
) -> func.HttpRequest:
    """Build a minimal Azure Functions HttpRequest for testing."""
    return func.HttpRequest(
        method=method,
        url="https://localhost",
        headers=headers or {},
        route_params=route_params or {},
        body=json.dumps(body).encode() if body else b"",
    )


# =========================================================================
# _get_swa_user
# =========================================================================

class TestGetSwaUser:
    """Tests for the SWA client-principal parser."""

    def test_valid_header(self):
        header = _make_principal_header()
        req = _make_request(headers={"x-ms-client-principal": header})
        user = _get_swa_user(req)
        assert user is not None
        assert user["userId"] == "aad-abc123"
        assert user["userDetails"] == "user@example.com"
        assert user["identityProvider"] == "aad"

    def test_missing_header(self):
        req = _make_request()
        assert _get_swa_user(req) is None

    def test_empty_header(self):
        req = _make_request(headers={"x-ms-client-principal": ""})
        assert _get_swa_user(req) is None

    def test_invalid_base64(self):
        req = _make_request(headers={"x-ms-client-principal": "not-base64!!!"})
        assert _get_swa_user(req) is None

    def test_valid_base64_but_not_json(self):
        bad = base64.b64encode(b"this is not json").decode()
        req = _make_request(headers={"x-ms-client-principal": bad})
        assert _get_swa_user(req) is None

    def test_json_missing_user_id(self):
        payload = {"identityProvider": "aad", "userDetails": "x@y.com"}
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()
        req = _make_request(headers={"x-ms-client-principal": encoded})
        assert _get_swa_user(req) is None

    def test_preserves_identity_provider(self):
        header = _make_principal_header(identity_provider="google")
        req = _make_request(headers={"x-ms-client-principal": header})
        user = _get_swa_user(req)
        assert user["identityProvider"] == "google"


# =========================================================================
# Catalog endpoints
# =========================================================================

@pytest.mark.parametrize("endpoint", [list_certifications, get_episodes])
@pytest.mark.parametrize("principal", [
    None,
    "not-base64!!!",
    base64.b64encode(b"not json").decode(),
    base64.b64encode(b'{"userRoles": ["anonymous"]}').decode(),
    base64.b64encode(b"[]").decode(),
])
def test_catalog_rejects_unauthenticated_before_cosmos(endpoint, principal):
    headers = {"x-ms-client-principal": principal} if principal else {}
    with patch("function_app.get_cosmos_client") as cosmos:
        response = endpoint(_make_request(headers=headers))

    assert response.status_code == 401
    assert json.loads(response.get_body()) == {"error": "Not authenticated"}
    assert response.headers["Cache-Control"] == "private, no-store"
    cosmos.assert_not_called()


@patch("function_app.get_cosmos_client")
def test_authenticated_certifications_preserve_visibility_and_names(cosmos):
    database = cosmos.return_value.get_database_client.return_value
    episodes = MagicMock()
    episodes.query_items.return_value = ["az-900", "ai-102", "test"]
    courses = MagicMock()
    courses.query_items.return_value = [
        {"id": "test", "published": False},
        {"id": "ai-102", "displayName": "AI Engineer"},
    ]
    database.get_container_client.side_effect = lambda name: {
        "episodes": episodes, "courses": courses,
    }[name]
    response = list_certifications(_make_request(
        headers={"x-ms-client-principal": _make_principal_header()},
    ))

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert json.loads(response.get_body()) == {"certifications": [
        {"id": "ai-102", "name": "AI Engineer"},
        {"id": "az-900", "name": "AZ-900: Azure Fundamentals"},
    ]}


@patch("function_app.get_cosmos_client")
def test_authenticated_episodes_preserve_grouping_and_metrics(cosmos):
    container = cosmos.return_value.get_database_client.return_value.get_container_client.return_value
    container.query_items.return_value = [{
        "id": "ep-001", "sequenceNumber": 1, "title": "Introduction",
        "skillDomain": "AI", "durationSeconds": 360,
    }]
    response = get_episodes(_make_request(
        headers={"x-ms-client-principal": _make_principal_header()},
        route_params={"certificationId": "ai-102", "format": "instructional"},
    ))

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    body = json.loads(response.get_body())
    assert body["certificationId"] == "ai-102"
    assert body["format"] == "instructional"
    assert body["totalEpisodes"] == 1
    assert body["totalDurationSeconds"] == 360
    assert body["totalHours"] == 0.1
    assert body["domains"]["AI"]["episodeCount"] == 1
    assert body["domains"]["AI"]["episodes"][0]["id"] == "ep-001"


def test_authenticated_episodes_still_validate_route_parameters():
    response = get_episodes(_make_request(
        headers={"x-ms-client-principal": _make_principal_header()},
    ))
    assert response.status_code == 400


@pytest.mark.parametrize("route", [
    "/api/certifications",
    "/api/episodes/ai-102/instructional",
    "/api/episodes/az-900/podcast",
])
def test_swa_catalog_rules_require_authentication_before_public_wildcard(route):
    config_path = Path(__file__).parents[1] / "web" / "staticwebapp.config.json"
    config = json.loads(config_path.read_text())
    rule = next(rule for rule in config["routes"]
                if fnmatch.fnmatchcase(route, rule["route"]))
    assert rule["allowedRoles"] == ["authenticated"]


def test_swa_health_check_remains_accessible_without_user_signin():
    config_path = Path(__file__).parents[1] / "web" / "staticwebapp.config.json"
    config = json.loads(config_path.read_text())
    rule = next(rule for rule in config["routes"]
                if fnmatch.fnmatchcase("/api/healthz", rule["route"]))
    assert "anonymous" in rule["allowedRoles"]


# =========================================================================
# GET /api/me
# =========================================================================

class TestGetMe:
    """Tests for the /api/me identity endpoint."""

    def test_authenticated(self):
        header = _make_principal_header(user_details="alice@contoso.com")
        req = _make_request(headers={"x-ms-client-principal": header})
        resp = get_me(req)
        body = json.loads(resp.get_body())
        assert resp.status_code == 200
        assert body["authenticated"] is True
        assert body["userId"] == "aad-abc123"
        assert body["userDetails"] == "alice@contoso.com"

    def test_anonymous(self):
        req = _make_request()
        resp = get_me(req)
        body = json.loads(resp.get_body())
        assert resp.status_code == 200
        assert body["authenticated"] is False


# =========================================================================
# GET /api/me/progress/{certificationId}
# =========================================================================

class TestGetMyProgress:
    """Tests for the authenticated progress GET endpoint."""

    def test_unauthenticated_returns_401(self):
        req = _make_request(route_params={"certificationId": "ai-102"})
        resp = get_my_progress(req)
        assert resp.status_code == 401

    @patch("function_app.get_cosmos_client")
    def test_returns_existing_progress(self, mock_cosmos):
        container = MagicMock()
        container.read_item.return_value = {
            "id": "aad-abc123-ai-102",
            "userId": "aad-abc123",
            "certificationId": "ai-102",
            "progress": {"ep-001": {"completed": True, "position": 100}},
        }
        mock_cosmos.return_value.get_database_client.return_value.get_container_client.return_value = container

        header = _make_principal_header()
        req = _make_request(
            headers={"x-ms-client-principal": header},
            route_params={"certificationId": "ai-102"},
        )
        resp = get_my_progress(req)
        body = json.loads(resp.get_body())
        assert resp.status_code == 200
        assert body["progress"]["ep-001"]["completed"] is True

    @patch("function_app.get_cosmos_client")
    def test_returns_empty_progress_if_not_found(self, mock_cosmos):
        container = MagicMock()
        container.read_item.side_effect = Exception("Not found")
        mock_cosmos.return_value.get_database_client.return_value.get_container_client.return_value = container

        header = _make_principal_header()
        req = _make_request(
            headers={"x-ms-client-principal": header},
            route_params={"certificationId": "ai-102"},
        )
        resp = get_my_progress(req)
        body = json.loads(resp.get_body())
        assert resp.status_code == 200
        assert body["progress"] == {}


# =========================================================================
# POST /api/me/progress/{certificationId}
# =========================================================================

class TestUpdateMyProgress:
    """Tests for the authenticated progress POST endpoint."""

    def test_unauthenticated_returns_401(self):
        req = _make_request(
            method="POST",
            route_params={"certificationId": "ai-102"},
            body={"episodeId": "001", "completed": True, "position": 0},
        )
        resp = update_my_progress(req)
        assert resp.status_code == 401

    @patch("function_app.get_cosmos_client")
    def test_single_episode_update(self, mock_cosmos):
        container = MagicMock()
        container.read_item.side_effect = Exception("Not found")
        mock_cosmos.return_value.get_database_client.return_value.get_container_client.return_value = container

        header = _make_principal_header()
        req = _make_request(
            method="POST",
            headers={"x-ms-client-principal": header},
            route_params={"certificationId": "ai-102"},
            body={"episodeId": "ep-001", "completed": True, "position": 300},
        )
        resp = update_my_progress(req)
        body = json.loads(resp.get_body())
        assert resp.status_code == 200
        assert body["success"] is True
        assert body["progress"]["ep-001"]["completed"] is True
        assert body["progress"]["ep-001"]["position"] == 300
        container.upsert_item.assert_called_once()

    @patch("function_app.get_cosmos_client")
    def test_bulk_merge_keeps_most_complete(self, mock_cosmos):
        """Bulk merge should keep completed=True and max position."""
        container = MagicMock()
        container.read_item.return_value = {
            "id": "aad-abc123-ai-102",
            "userId": "aad-abc123",
            "certificationId": "ai-102",
            "progress": {
                "ep-001": {"completed": True, "position": 100},
                "ep-002": {"completed": False, "position": 500},
            },
        }
        mock_cosmos.return_value.get_database_client.return_value.get_container_client.return_value = container

        header = _make_principal_header()
        incoming = {
            "ep-001": {"completed": False, "position": 200},  # was True → stays True
            "ep-002": {"completed": True, "position": 100},   # was 500 → stays 500
            "ep-003": {"completed": True, "position": 50},    # new
        }
        req = _make_request(
            method="POST",
            headers={"x-ms-client-principal": header},
            route_params={"certificationId": "ai-102"},
            body={"progress": incoming},
        )
        resp = update_my_progress(req)
        body = json.loads(resp.get_body())

        assert resp.status_code == 200
        p = body["progress"]
        # ep-001: completed stays True (was True, incoming False)
        assert p["ep-001"]["completed"] is True
        # ep-001: position takes max (200 > 100)
        assert p["ep-001"]["position"] == 200
        # ep-002: completed becomes True (incoming True wins)
        assert p["ep-002"]["completed"] is True
        # ep-002: position takes max (500 > 100)
        assert p["ep-002"]["position"] == 500
        # ep-003: new episode
        assert p["ep-003"]["completed"] is True
        assert p["ep-003"]["position"] == 50

    @patch("function_app.get_cosmos_client")
    def test_missing_episode_id_returns_400(self, mock_cosmos):
        container = MagicMock()
        container.read_item.side_effect = Exception("Not found")
        mock_cosmos.return_value.get_database_client.return_value.get_container_client.return_value = container

        header = _make_principal_header()
        req = _make_request(
            method="POST",
            headers={"x-ms-client-principal": header},
            route_params={"certificationId": "ai-102"},
            body={"completed": True},  # missing episodeId and progress
        )
        resp = update_my_progress(req)
        assert resp.status_code == 400
