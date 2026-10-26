#!/usr/bin/env bash
# Provision the demo VPS. Does not run unless you execute it.
# Requires: az login, an SSH public key.
#
#   ./scripts/azure-vm.sh
#   AZURE_LOCATION=westeurope SSH_SOURCE=203.0.113.10/32 ./scripts/azure-vm.sh my-rg
#
# Until docker-compose.yml is on main, set the branch that has it:
#   GIT_REF=cursor/synthetic-default-feed-b2ad ./scripts/azure-vm.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RG="${1:-${AZURE_RESOURCE_GROUP:-gamelens-vm}}"
LOC="${AZURE_LOCATION:-eastus}"
ADMIN="${ADMIN_USERNAME:-azureuser}"
REPO="${GIT_REPO:-https://github.com/VictorXypCh/GameLens.git}"
REF="${GIT_REF:-main}"
SSH_SOURCE="${SSH_SOURCE:-*}"
KEY_FILE="${SSH_PUBLIC_KEY_FILE:-}"

if ! command -v az >/dev/null 2>&1; then
  echo "Azure CLI (az) is required." >&2
  exit 1
fi
if ! az account show >/dev/null 2>&1; then
  echo "Run az login first." >&2
  exit 1
fi

if [[ -z "$KEY_FILE" ]]; then
  if [[ -f "$HOME/.ssh/id_ed25519.pub" ]]; then
    KEY_FILE="$HOME/.ssh/id_ed25519.pub"
  elif [[ -f "$HOME/.ssh/id_rsa.pub" ]]; then
    KEY_FILE="$HOME/.ssh/id_rsa.pub"
  else
    echo "No SSH public key. Create one with: ssh-keygen -t ed25519" >&2
    echo "Or set SSH_PUBLIC_KEY_FILE to a .pub file." >&2
    exit 1
  fi
fi
if [[ ! -f "$KEY_FILE" ]]; then
  echo "SSH public key file not found: $KEY_FILE" >&2
  exit 1
fi
if grep -q "PRIVATE KEY" "$KEY_FILE"; then
  echo "That file is a private key. Pass the .pub file only." >&2
  exit 1
fi
if ! grep -qE '^(ssh-|ecdsa-)' "$KEY_FILE"; then
  echo "SSH_PUBLIC_KEY_FILE must be a public key line (ssh-ed25519 or ssh-rsa)." >&2
  exit 1
fi

PUB="$(tr -d '\r\n' < "$KEY_FILE")"
PARAMS="$(mktemp)"
trap 'rm -f "$PARAMS"' EXIT
python3 - "$PARAMS" "$LOC" "$ADMIN" "$PUB" "$REPO" "$REF" "$SSH_SOURCE" <<'PY'
import json, sys
path, loc, admin, pub, repo, ref, ssh_source = sys.argv[1:]
doc = {
    "location": {"value": loc},
    "adminUsername": {"value": admin},
    "sshPublicKey": {"value": pub},
    "repository": {"value": repo},
    "gitRef": {"value": ref},
    "sshSourceAddressPrefix": {"value": ssh_source},
}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(doc, handle)
PY

echo "Creating resource group $RG in $LOC"
az group create --name "$RG" --location "$LOC" --output none
echo "Deploying the VM (cloud-init installs Docker and starts the stack on first boot)"
az deployment group create \
  --resource-group "$RG" \
  --name gamelens-vm \
  --template-file "$ROOT/infra/vm.bicep" \
  --parameters @"$PARAMS" \
  --query properties.outputs \
  --output json

cat <<EOF

The site answers after cloud-init finishes (Docker install and image build, often 10 minutes on a B2s).
SSH in and follow it with: sudo tail -f /var/log/gamelens-setup.log

Stop compute charges when the demo is idle (the disk and public IP still cost a little):
  az vm deallocate --resource-group $RG --name \$(az deployment group show -g $RG -n gamelens-vm --query properties.outputs.vmName.value -o tsv)
Start it again:
  az vm start --resource-group $RG --name \$(az deployment group show -g $RG -n gamelens-vm --query properties.outputs.vmName.value -o tsv)
Delete everything, including the disk and IP:
  az group delete --name $RG --yes
EOF
