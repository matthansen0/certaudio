# Serves src/web with stubbed /api/portal/* responses so the admin portal can be
# eyeballed without Azure. Not shipped: excluded from the SWA deployment by name.
import json
import http.server
import socketserver
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "web"

JOB = {
    "jobId": "job-1", "mode": "generate", "certificationId": "dp-700",
    "audioFormat": "instructional", "status": "running", "phase": "synthesise",
    "progress": {"current": 42, "total": 120, "message": "Synthesising episode 42"},
    "startedAt": "2026-07-30T09:12:00+00:00", "createdAt": "2026-07-30T09:11:00+00:00",
}

ROUTES = {
    "/api/portal/status": {"authenticated": True, "isAdmin": True, "bootstrapClaimed": True},
    "/api/portal/voices": {"region": "eastus", "voices": [
        {"shortName": "en-US-Andrew:DragonHDLatestNeural", "displayName": "Andrew", "isDragonHD": True},
        {"shortName": "en-US-Ava:DragonHDLatestNeural", "displayName": "Ava", "isDragonHD": True},
        {"shortName": "en-US-GuyNeural", "displayName": "Guy", "isDragonHD": False},
    ]},
    "/api/portal/courses": {
        "courses": [
            {"id": "dp-700", "displayName": "DP-700: Fabric Data Engineer",
             "audioFormat": "instructional", "episodeCount": 68, "published": True,
             "lastGeneratedAt": "2026-07-26T04:10:00+00:00"},
            {"id": "az-104", "displayName": "AZ-104: Azure Administrator",
             "audioFormat": "podcast", "episodeCount": 0, "published": False,
             "lastGeneratedAt": None},
        ],
        "rates": {"dragonHdPerMChar": 22, "neuralPerMChar": 15,
                  "gptInputPerMTok": 2.5, "gptOutputPerMTok": 10},
    },
    "/api/portal/jobs": {"jobs": [JOB, {
        "jobId": "job-0", "mode": "index", "certificationId": "az-104",
        "audioFormat": "instructional", "status": "failed",
        "error": "No learning paths resolved for az-104",
        "startedAt": "2026-07-29T18:00:00+00:00"}]},
    "/api/portal/admins": {"admins": [
        {"id": "aad-1", "userDetails": "owner@example.com", "addedBy": "bootstrap"},
        {"id": "aad-2", "userDetails": "second@example.com", "addedBy": "owner@example.com"},
    ]},
}

COURSE_DETAIL = {
    "course": {
        "id": "dp-700", "displayName": "DP-700: Fabric Data Engineer",
        "examUrl": "https://learn.microsoft.com/en-us/credentials/certifications/exams/dp-700/",
        "published": True, "audioFormat": "instructional", "unitCount": 68,
        "episodeCount": 68, "totalWords": 95000, "totalDurationSeconds": 21600,
        "lastDiscoveryAt": "2026-07-28T10:00:00+00:00",
        "lastGeneratedAt": "2026-07-26T04:10:00+00:00",
        "lastEstimateUsd": 17.2, "lastActualUsd": 16.4,
        "measuredCharsPerEpisode": 8100,
        "voices": {"instructional": "en-US-Andrew:DragonHDLatestNeural"},
        "discoveryReport": {
            "examFound": True, "examTitle": "Implementing Data Engineering Solutions",
            "resolvedPaths": 6, "resolvedStandaloneModules": 1,
            "sources": {"studyGuide": {"paths": 6}}, "warnings": [
                "1 curated UID(s) from the curated list are no longer in the catalog"],
            "unitsDiscovered": 267, "unitsFailed": 3,
            "coverageGrade": "B", "coverageScore": 84.2,
            "topicsCovered": 40, "topicsSupplemented": 6, "topicsUncovered": 2,
            "gaps": [{"skill": "Monitor and optimize", "topic": "Configure alerts on a lakehouse"},
                     {"skill": "Ingest data", "topic": "Use Fabric mirroring"}],
        },
    },
    "jobs": [],
    "rates": {"dragonHdPerMChar": 22, "neuralPerMChar": 15,
              "gptInputPerMTok": 2.5, "gptOutputPerMTok": 10},
    "defaults": {"wordsPerEpisode": 1400, "charsPerWord": 6,
                 "gptInputTokensPerEpisode": 9000, "gptOutputTokensPerEpisode": 2200},
}


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def _send(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ROUTES:
            return self._send(ROUTES[self.path])
        if self.path.startswith("/api/portal/courses/"):
            return self._send(COURSE_DETAIL)
        return super().do_GET()

    def log_message(self, *args):
        pass


with socketserver.TCPServer(("127.0.0.1", 8899), Handler) as httpd:
    httpd.serve_forever()
