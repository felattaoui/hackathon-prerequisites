#!/usr/bin/env bash
# Creates (or updates) the PII lab resources, then writes the endpoint into pii/.env.
# Usage: scripts/deploy.sh
# Subscription: AZURE_SUBSCRIPTION_ID from pii/.env, else from ../hosted-agents/.env. Never printed.
set -euo pipefail
cd "$(dirname "$0")/.."
read_env() { [ -f "$1" ] && grep -E "^$2=" "$1" | cut -d= -f2- | tr -d '\r" ' || true; }
SUB="${AZURE_SUBSCRIPTION_ID:-$(read_env .env AZURE_SUBSCRIPTION_ID)}"
SUB="${SUB:-$(read_env ../hosted-agents/.env AZURE_SUBSCRIPTION_ID)}"
[ -n "$SUB" ] || { echo "AZURE_SUBSCRIPTION_ID not found (pii/.env or hosted-agents/.env)"; exit 1; }
redact() { sed "s/$SUB/<subscription>/g"; }
RG=$(sed -n 's/^resource_group *= *"\(.*\)"/\1/p' config/lab.toml)
LOC=$(sed -n 's/^region *= *"\(.*\)"/\1/p' config/lab.toml)

if [ "$(az group exists -n "$RG" --subscription "$SUB")" != "true" ]; then
  az group create -n "$RG" -l "$LOC" --subscription "$SUB" \
    --tags purpose=pii-lab managed-by=hackathon-prerequisites-pii \
    --query "{name:name,location:location}" -o table 2>&1 | redact
fi

PRINCIPAL=$(az ad signed-in-user show --query id -o tsv)
ENDPOINT=$(az deployment group create -g "$RG" --subscription "$SUB" -n pii-lab \
  -f infra/main.bicep -p location="$LOC" principalId="$PRINCIPAL" \
  --query properties.outputs.endpoint.value -o tsv 2> >(redact >&2))

touch .env
grep -q '^AZURE_SUBSCRIPTION_ID=' .env || echo "AZURE_SUBSCRIPTION_ID=$SUB" >> .env
sed -i '/^PII_FOUNDRY_ENDPOINT=/d' .env
echo "PII_FOUNDRY_ENDPOINT=$ENDPOINT" >> .env
echo "Deployed. Endpoint written to pii/.env. Role assignments can take a few minutes to apply."
az resource list -g "$RG" --subscription "$SUB" --query "[].{type:type,name:name,location:location}" -o table | redact
