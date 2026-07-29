#!/usr/bin/env bash
set -euo pipefail

: "${PIPELINE_MODE:?PIPELINE_MODE is required}"

job_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
runtime_archive="${job_root}/runtime.tar.gz"

if [[ ! -f "$runtime_archive" ]]; then
  printf 'Bundled Python runtime is missing: %s\n' "$runtime_archive" >&2
  exit 1
fi

runtime_root="$(mktemp -d)"
trap 'rm -rf "$runtime_root"' EXIT
tar -xzf "$runtime_archive" -C "$runtime_root"

export PYTHONPATH="${runtime_root}:${runtime_root}/python_packages${PYTHONPATH:+:${PYTHONPATH}}"

pipeline_args=("$PIPELINE_MODE")
if [[ "$PIPELINE_MODE" != "validate" ]]; then
  : "${CERTIFICATION_ID:?CERTIFICATION_ID is required}"
  : "${AUDIO_FORMAT:?AUDIO_FORMAT is required}"
  : "${INSTRUCTIONAL_VOICE:?INSTRUCTIONAL_VOICE is required}"
  : "${PODCAST_HOST_VOICE:?PODCAST_HOST_VOICE is required}"
  : "${PODCAST_EXPERT_VOICE:?PODCAST_EXPERT_VOICE is required}"
  pipeline_args+=(
    --certification-id "$CERTIFICATION_ID"
    --audio-format "$AUDIO_FORMAT"
    --instructional-voice "$INSTRUCTIONAL_VOICE"
    --podcast-host-voice "$PODCAST_HOST_VOICE"
    --podcast-expert-voice "$PODCAST_EXPERT_VOICE"
  )
fi

if [[ "${PIPELINE_FORCE:-false}" == "true" ]]; then
  if [[ "$PIPELINE_MODE" == "generate" ]]; then
    pipeline_args+=(--force-regenerate)
  else
    pipeline_args+=(--force-refresh)
  fi
fi

python -m tools.run_pipeline "${pipeline_args[@]}"