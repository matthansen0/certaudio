"""Unit tests for the private Blob Storage audio proxy."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import azure.functions as func
from azure.core.exceptions import ResourceNotFoundError

from function_app import _parse_byte_range, get_audio, readyz


def _make_request(method: str = "GET", range_header: str | None = None):
    headers = {"Range": range_header} if range_header else {}
    return func.HttpRequest(
        method=method,
        url="https://localhost/api/audio/ai-102/podcast/1",
        headers=headers,
        route_params={
            "certificationId": "ai-102",
            "format": "podcast",
            "episodeNumber": "1",
        },
        body=b"",
    )


def _mock_blob(data: bytes = b"0123456789"):
    blob = MagicMock()
    blob.get_blob_properties.return_value = SimpleNamespace(
        size=len(data),
        content_settings=SimpleNamespace(content_type="audio/mpeg"),
    )
    downloader = MagicMock()
    downloader.readall.side_effect = lambda: data[
        blob.download_blob.call_args.kwargs["offset"]:
        blob.download_blob.call_args.kwargs["offset"]
        + blob.download_blob.call_args.kwargs["length"]
    ]
    blob.download_blob.return_value = downloader
    service = MagicMock()
    service.get_blob_client.return_value = blob
    return service, blob


@patch("function_app.get_blob_service")
def test_get_audio_returns_full_blob(mock_get_blob_service):
    service, blob = _mock_blob()
    mock_get_blob_service.return_value = service

    response = get_audio(_make_request())

    assert response.status_code == 200
    assert response.get_body() == b"0123456789"
    assert response.headers["Accept-Ranges"] == "bytes"
    assert response.headers["Content-Length"] == "10"
    blob.download_blob.assert_called_once_with(offset=0, length=10)
    service.get_blob_client.assert_called_once_with(
        container="audio",
        blob="ai-102/podcast/episodes/001.mp3",
    )


@patch("function_app.get_blob_service")
def test_get_audio_returns_requested_range(mock_get_blob_service):
    service, blob = _mock_blob()
    mock_get_blob_service.return_value = service

    response = get_audio(_make_request(range_header="bytes=2-5"))

    assert response.status_code == 206
    assert response.get_body() == b"2345"
    assert response.headers["Content-Range"] == "bytes 2-5/10"
    assert response.headers["Content-Length"] == "4"
    blob.download_blob.assert_called_once_with(offset=2, length=4)


def test_parse_byte_range_supports_open_and_suffix_ranges():
    assert _parse_byte_range("bytes=7-", 10) == (7, 9)
    assert _parse_byte_range("bytes=-3", 10) == (7, 9)
    assert _parse_byte_range("bytes=7-100", 10) == (7, 9)


@patch("function_app.get_blob_service")
def test_head_returns_metadata_without_downloading(mock_get_blob_service):
    service, blob = _mock_blob()
    mock_get_blob_service.return_value = service

    response = get_audio(_make_request(method="HEAD"))

    assert response.status_code == 200
    assert response.get_body() == b""
    assert response.headers["Content-Length"] == "10"
    blob.download_blob.assert_not_called()


@patch("function_app.get_blob_service")
def test_unsatisfiable_range_returns_416(mock_get_blob_service):
    service, blob = _mock_blob()
    mock_get_blob_service.return_value = service

    response = get_audio(_make_request(range_header="bytes=10-20"))

    assert response.status_code == 416
    assert response.headers["Content-Range"] == "bytes */10"
    blob.download_blob.assert_not_called()


@patch("function_app.get_blob_service")
def test_multiple_ranges_return_416(mock_get_blob_service):
    service, blob = _mock_blob()
    mock_get_blob_service.return_value = service

    response = get_audio(_make_request(range_header="bytes=0-1,4-5"))

    assert response.status_code == 416
    blob.download_blob.assert_not_called()


@patch("function_app.get_blob_service")
def test_missing_blob_returns_404(mock_get_blob_service):
    service = MagicMock()
    service.get_blob_client.return_value.get_blob_properties.side_effect = (
        ResourceNotFoundError("missing")
    )
    mock_get_blob_service.return_value = service

    response = get_audio(_make_request())

    assert response.status_code == 404


@patch("function_app.get_blob_service")
@patch("function_app.get_cosmos_client")
def test_readyz_checks_cosmos_and_blob(mock_get_cosmos_client, mock_get_blob_service):
    database = mock_get_cosmos_client.return_value.get_database_client.return_value
    container = mock_get_blob_service.return_value.get_container_client.return_value

    response = readyz(_make_request())

    assert response.status_code == 200
    database.read.assert_called_once_with()
    container.get_container_properties.assert_called_once_with()


@patch("function_app.get_cosmos_client")
def test_readyz_returns_503_without_leaking_error(mock_get_cosmos_client):
    mock_get_cosmos_client.side_effect = RuntimeError("private endpoint unavailable")

    response = readyz(_make_request())

    assert response.status_code == 503
    assert json.loads(response.get_body()) == {"status": "not ready"}
    assert b"private endpoint unavailable" not in response.get_body()
