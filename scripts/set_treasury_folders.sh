#!/usr/bin/env bash
# Set spec.drive.folder_id in the treasury reconciliation manifests after you've
# created + shared the Drive folders with each agent's service account, then
# print the redeploy commands.
#
# Usage:
#   ./scripts/set_treasury_folders.sh bank-sap=<FOLDER_ID> debt=<ID> po=<ID> mapping=<ID>
#   # any subset is fine; only the slugs you pass are edited.
#
# Folder ids come from each folder's Drive URL: …/folders/<FOLDER_ID>.
# The kyriba orchestrator is identity-only (no folder), so it is not listed here.
#
# Idempotent — re-running with a new id just overwrites the previous value.

set -euo pipefail

AGENTS_DIR="src/companies/treasury/agents"

# slug -> manifest filename (case stmt: portable to macOS bash 3.2, no assoc arrays)
manifest_for() {
  case "$1" in
    bank-sap) echo "bank-sap-reconciliation.yaml" ;;
    debt)     echo "debt-reconciliation.yaml" ;;
    po)       echo "po-reconciliation.yaml" ;;
    mapping)  echo "mapping-classification.yaml" ;;
    *)        echo "" ;;
  esac
}

[[ $# -gt 0 ]] || { echo "usage: $0 bank-sap=<ID> debt=<ID> po=<ID> mapping=<ID>" >&2; exit 1; }

edited=()
for pair in "$@"; do
  slug="${pair%%=*}"
  fid="${pair#*=}"
  file="$(manifest_for "$slug")"
  [[ -n "$file" ]]   || { echo "✗ unknown slug '$slug' (expected: bank-sap debt po mapping)" >&2; exit 1; }
  [[ -n "$fid" && "$fid" != "$slug" ]] || { echo "✗ missing folder id for '$slug' (use slug=<ID>)" >&2; exit 1; }
  [[ "$fid" != "REPLACE_WITH_FOLDER_ID" ]] || { echo "✗ '$slug' got the placeholder, not a real id" >&2; exit 1; }

  path="${AGENTS_DIR}/${file}"
  grep -q '^\s*folder_id:' "$path" || { echo "✗ no folder_id: line in ${path}" >&2; exit 1; }
  # Replace just the value on the folder_id line, preserving indentation; drop any trailing comment.
  perl -i -pe "s{^(\s*folder_id:\s*).*\$}{\${1}${fid}}" "$path"
  echo "✓ ${file}: folder_id = ${fid}"
  edited+=("$file")
done

echo ""
echo "Redeploy the edited agents (backend must be running):"
for f in "${edited[@]}"; do
  echo "  forgeos deploy ${AGENTS_DIR}/${f}"
done
