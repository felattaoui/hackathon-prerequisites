#!/usr/bin/env bash
# Explicit, idempotent creation of the kit resource group (inspects it if it already exists).
# Usage: scripts/create-rg.sh            (reads AZURE_SUBSCRIPTION_ID from .env, config from config/kit.toml)
set -euo pipefail
cd "$(dirname "$0")/.."
SUB="${AZURE_SUBSCRIPTION_ID:-$(grep -E '^AZURE_SUBSCRIPTION_ID=' .env | cut -d= -f2- | tr -d '\r" ')}"
RG=$(sed -n 's/^resource_group *= *"\(.*\)"/\1/p' config/kit.toml)
LOC=$(sed -n 's/^lab_region *= *"\(.*\)"/\1/p' config/kit.toml)
if [ "$(az group exists -n "$RG" --subscription "$SUB")" = "true" ]; then
  echo "Resource group $RG already exists: inspecting only (nothing replaced)."
  az group show -n "$RG" --subscription "$SUB" --query "{name:name,location:location,tags:tags}" -o table
  az resource list -g "$RG" --subscription "$SUB" --query "[].{type:type,name:name,location:location}" -o table
else
  az group create -n "$RG" -l "$LOC" --subscription "$SUB" \
    --tags kit=hapq purpose=hosted-agent-prereq-lab managed-by=hackathon-prerequisites-kit \
    --query "{name:name,location:location,state:properties.provisioningState}" -o table
fi
