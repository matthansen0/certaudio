#!/usr/bin/env bash
set -euo pipefail

# Install Azure Functions Core Tools v4
npm install -g azure-functions-core-tools@4 --unsafe-perm true

# Install SWA CLI (optional but useful for local SWA + API proxy)
npm install -g @azure/static-web-apps-cli

# Python deps (best-effort; don't fail the whole container if one extra isn't needed)
python -m pip install --upgrade pip

if [ -f "src/functions/requirements.txt" ]; then
  python -m pip install -r src/functions/requirements.txt
fi

if [ -f "src/pipeline/requirements.txt" ]; then
  python -m pip install -r src/pipeline/requirements.txt
fi
