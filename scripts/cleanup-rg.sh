#!/usr/bin/env bash
set -euo pipefail

# Cleanup duplicate CertAudio deployments in a shared RG.
#
# By default, this script ONLY prints what it would delete.
# To actually delete, run with DELETE=true.
#
# Usage:
#   ./scripts/cleanup-rg.sh <resource-group> <keepSuffix>
#   DELETE=true ./scripts/cleanup-rg.sh <resource-group> <keepSuffix>
#
# Example:
#   ./scripts/cleanup-rg.sh rg-certaudio-dev 21074004683

rg="${1:-${AZURE_RESOURCE_GROUP:-}}"
keep_suffix="${2:-}"

if [[ -z "$rg" || -z "$keep_suffix" ]]; then
  echo "Usage: $0 <resource-group> <keepSuffix>" >&2
  echo "Example: $0 rg-certaudio-dev 21074004683" >&2
  exit 2
fi

keep_short="${keep_suffix:0:10}"

echo "RG=$rg"
echo "KEEP_SUFFIX=$keep_suffix"
echo "KEEP_SHORT=$keep_short"

echo ""
echo "== Candidate resources (tagged project resources not matching KEEP suffix) =="
project_query="[?tags.project=='certification-audio-platform' && contains(name, '$keep_suffix')==\`false\` && contains(name, '$keep_short')==\`false\`].{name:name,type:type,location:location,id:id}"

# Print candidates
az resource list -g "$rg" --query "$project_query" -o table || true

# Collect IDs
project_ids=$(az resource list -g "$rg" --query "[?tags.project=='certification-audio-platform' && contains(name, '$keep_suffix')==\`false\` && contains(name, '$keep_short')==\`false\`].id" -o tsv || true)

echo ""
echo "== Candidate alert rules (Failure Anomalies) for non-KEEP suffixes =="
alert_query="[?type=='microsoft.alertsmanagement/smartDetectorAlertRules' && starts_with(name, 'Failure Anomalies - certaudio-dev-insights-') && contains(name, '$keep_suffix')==\`false\`].{name:name,type:type,location:location,id:id}"
az resource list -g "$rg" --query "$alert_query" -o table || true
alert_ids=$(az resource list -g "$rg" --query "[?type=='microsoft.alertsmanagement/smartDetectorAlertRules' && starts_with(name, 'Failure Anomalies - certaudio-dev-insights-') && contains(name, '$keep_suffix')==\`false\`].id" -o tsv || true)

all_ids="$project_ids"
if [[ -n "$alert_ids" ]]; then
  all_ids="$all_ids $alert_ids"
fi

if [[ -z "${all_ids// }" ]]; then
  echo ""
  echo "No matching resources found to clean up."
  exit 0
fi

echo ""
if [[ "${DELETE:-false}" != "true" ]]; then
  echo "Dry run only. To delete the above resources, run:" 
  echo "  DELETE=true $0 $rg $keep_suffix"
  exit 0
fi

echo "Deleting resources..."
# shellcheck disable=SC2086
az resource delete --ids $all_ids --only-show-errors

echo "Done."