#!/usr/bin/env bash
# Creates the staging server in Azure (UAE North) and installs the staging site on it; run again,
# it updates the existing server to the latest code instead. Run it in Azure Cloud Shell
# (https://shell.azure.com, choose Bash), so there is nothing to install:
#
#   curl -fsSL https://raw.githubusercontent.com/AbduSamadh/StudentSkillTracker/main/deploy/staging/azure-create.sh | bash
#
# Optional settings, as environment variables before `bash`: RG, LOCATION, VM_SIZE, BRANCH.
# Costs about US$40-50 a month while the server exists; see docs/staging.md to stop or delete it.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/AbduSamadh/StudentSkillTracker.git}"
BRANCH="${BRANCH:-main}"
RG="${RG:-stemtrack-staging}"
LOCATION="${LOCATION:-uaenorth}"
VM_SIZE="${VM_SIZE:-Standard_B2s}"
VM="stemtrack-staging"

command -v az >/dev/null || { echo "Run this in Azure Cloud Shell (https://shell.azure.com, Bash)." >&2; exit 1; }
az account show --output none 2>/dev/null || { echo "Sign in first: az login" >&2; exit 1; }

if az vm show --resource-group "$RG" --name "$VM" --output none 2>/dev/null; then
  fqdn="$(az vm show --show-details --resource-group "$RG" --name "$VM" --query fqdns --output tsv)"
  echo "The staging server already exists (https://${fqdn}). Updating it to the latest code;"
  echo "this takes about 5-10 minutes..."
  az vm run-command invoke --resource-group "$RG" --name "$VM" --command-id RunShellScript \
    --scripts "cd /opt/stemtrack && git pull --ff-only && deploy/staging/setup.sh" \
    --query "value[0].message" --output tsv | tail -n 25
  exit 0
fi

label="stemtrack-$(openssl rand -hex 3)"
domain="${label}.${LOCATION}.cloudapp.azure.com"
cloud_init="$(mktemp)"
cat > "$cloud_init" <<EOF
#cloud-config
runcmd:
  # A little swap, so building the images fits comfortably in 4 GB of memory.
  - [bash, -c, "fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile && echo '/swapfile none swap sw 0 0' >> /etc/fstab"]
  - [bash, -c, "curl -fsSL https://get.docker.com | sh"]
  - [git, clone, --branch, "${BRANCH}", "${REPO_URL}", /opt/stemtrack]
  - [bash, -c, "cd /opt/stemtrack && STAGING_DOMAIN=${domain} deploy/staging/setup.sh > /var/log/stemtrack-setup.log 2>&1"]
EOF

echo "Creating resource group ${RG} in ${LOCATION}..."
az group create --name "$RG" --location "$LOCATION" --output none
echo "Creating the server (${VM_SIZE}, Ubuntu 24.04). This takes 2-3 minutes..."
az vm create --resource-group "$RG" --name "$VM" \
  --image Canonical:ubuntu-24_04-lts:server:latest --size "$VM_SIZE" \
  --admin-username stemadmin --generate-ssh-keys \
  --public-ip-sku Standard --public-ip-address-dns-name "$label" \
  --os-disk-size-gb 64 --custom-data "$cloud_init" --output none
echo "Opening the web ports (80 and 443)..."
az vm open-port --resource-group "$RG" --name "$VM" --port 80,443 --priority 1010 --output none
rm -f "$cloud_init"

echo "The server is installing the staging site (about 10-15 minutes). Waiting for it..."
for _ in $(seq 1 90); do
  if curl -sf --max-time 10 "https://${domain}/api/v1/health" >/dev/null; then
    break
  fi
  printf '.'
  sleep 10
done
echo
if curl -sf --max-time 10 "https://${domain}/api/v1/health" >/dev/null; then
  echo "Ready: https://${domain}"
else
  echo "Not answering yet. Give it a few more minutes, then open https://${domain}"
  echo "If it is still not there after 30 minutes, show the setup log with:"
  echo "  az vm run-command invoke -g ${RG} -n ${VM} --command-id RunShellScript --scripts 'tail -n 40 /var/log/stemtrack-setup.log' --query 'value[0].message' -o tsv"
fi
cat <<EOF

School code: demo        Password for every account: Demo-Password-2026!
  Teacher sara.haddad@demo.school.example     Parent parent@family.example
  Leader  leader@demo.school.example  (authenticator key STEMLEADERDEMOSECRETKEYA)
  Admin   admin@demo.school.example   (authenticator key STEMADMINDEMOSECRETKEYAB)

Stop paying for the server when nobody is using it:  az vm deallocate -g ${RG} -n ${VM}
Start it again:                                       az vm start -g ${RG} -n ${VM}
Delete everything:                                    az group delete -n ${RG}
EOF
