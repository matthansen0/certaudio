#!/usr/bin/env bash
set -euo pipefail

readonly PYTHON_VERSION="3.11"
readonly FUNCTIONS_CORE_TOOLS_VERSION="4.12.1"
readonly SWA_CLI_VERSION="2.0.10"
readonly AZURITE_VERSION="3.36.0"

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ -f .venv/pyvenv.cfg ]] && ! grep -Eq "^version = ${PYTHON_VERSION}([.]|$)" .venv/pyvenv.cfg; then
  echo "Rebuilding .venv because it does not use Python ${PYTHON_VERSION}."
  rm -rf .venv
fi

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi

source .venv/bin/activate

if [[ "$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" != "$PYTHON_VERSION" ]]; then
  echo "Expected Python ${PYTHON_VERSION}, found $(python --version)." >&2
  exit 1
fi

python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements-dev.txt
python -m pip check

npm install -g \
  "azure-functions-core-tools@${FUNCTIONS_CORE_TOOLS_VERSION}" \
  "@azure/static-web-apps-cli@${SWA_CLI_VERSION}" \
  "azurite@${AZURITE_VERSION}"

python -c "import azure.functions, azure.identity, azure.cosmos, azure.storage.blob, promptflow"
az version --output none
func --version
swa --version
azurite --version
az bicep version
