#!/usr/bin/env bash
# Deletes the PII lab resource group, then purges the soft-deleted Foundry resource so that its
# name can be reused. Asks for confirmation. Usage: scripts/teardown.sh
set -euo pipefail
cd "$(dirname "$0")/.."
read_env() { [ -f "$1" ] && grep -E "^$2=" "$1" | cut -d= -f2- | tr -d '\r" ' || true; }
SUB="${AZURE_SUBSCRIPTION_ID:-$(read_env .env AZURE_SUBSCRIPTION_ID)}"
SUB="${SUB:-$(read_env ../hosted-agents/.env AZURE_SUBSCRIPTION_ID)}"
redact() { sed "s/$SUB/<subscription>/g"; }
RG=$(sed -n 's/^resource_group *= *"\(.*\)"/\1/p' config/lab.toml)
LOC=$(sed -n 's/^region *= *"\(.*\)"/\1/p' config/lab.toml)

[ "$(az group exists -n "$RG" --subscription "$SUB")" = "true" ] || { echo "$RG does not exist."; exit 0; }
az resource list -g "$RG" --subscription "$SUB" --query "[].{type:type,name:name}" -o table | redact
ACCOUNTS=$(az cognitiveservices account list -g "$RG" --subscription "$SUB" --query "[].name" -o tsv)
read -r -p "Delete resource group $RG and everything above? Type the group name to confirm: " ANSWER
[ "$ANSWER" = "$RG" ] || { echo "Cancelled."; exit 1; }
az group delete -n "$RG" --subscription "$SUB" --yes 2>&1 | redact
for NAME in $ACCOUNTS; do
  az cognitiveservices account purge -g "$RG" -n "$NAME" -l "$LOC" --subscription "$SUB" 2>&1 | redact
done
sed -i '/^PII_FOUNDRY_ENDPOINT=/d' .env 2>/dev/null || true
echo "Deleted and purged."
