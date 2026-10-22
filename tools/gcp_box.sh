#!/usr/bin/env bash
# A spot VM on Google Cloud for tools/cloud_fill.sh (Plan 11 wide fill). Ubuntu 24.04, one 50 GB pd-standard
# disk (DISK_GB/DISK_TYPE; the regional SSD quota is 250 GB, a box needs ~10 GB); a spot VM can be preempted at any time, so `cloud_fill.sh start` is re-run after a stop.
#
#   tools/gcp_box.sh create [name] [machine-type] [zone]   default fill-1 n2d-standard-32 us-central1-a
#   tools/gcp_box.sh host   [name] [zone]                  prints the ssh host for cloud_fill.sh (after `config-ssh`)
#   tools/gcp_box.sh status [name] [zone]
#   tools/gcp_box.sh start  [name] [zone]                  after a preemption (STOP) the VM restarts; disk kept
#   tools/gcp_box.sh delete [name] [zone]
# GCP_PROJECT=<id> targets another project (quotas are per project); GCP_ZONE=<zone> another zone (default us-central1-a).
set -euo pipefail
cmd="${1:-}"; name="${2:-fill-1}"
PROJ="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
Z="${GCP_ZONE:-us-central1-a}"  # default zone for every command; create/host/status/start/delete also take it positionally
export CLOUDSDK_CORE_PROJECT="$PROJ"
case "$cmd" in
  create)
    mt="${3:-n2d-standard-32}"; zone="${4:-$Z}"
    gcloud compute instances create "$name" --zone "$zone" --machine-type "$mt" \
      --provisioning-model=SPOT --instance-termination-action=STOP \
      --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
      --boot-disk-size="${DISK_GB:-50}GB" --boot-disk-type="${DISK_TYPE:-pd-standard}" --no-restart-on-failure \
      --metadata=enable-oslogin=false
    gcloud compute config-ssh --quiet >/dev/null
    echo "ssh host: $name.$zone.$PROJ"
    ;;
  host) zone="${3:-$Z}"; gcloud compute config-ssh --quiet >/dev/null; echo "$name.$zone.$PROJ" ;;
  status) zone="${3:-$Z}"; gcloud compute instances describe "$name" --zone "$zone" --format='value(status,machineType.basename(),scheduling.provisioningModel)' ;;
  start) zone="${3:-$Z}"; gcloud compute instances start "$name" --zone "$zone" ;;
  delete) zone="${3:-$Z}"; gcloud compute instances delete "$name" --zone "$zone" --quiet ;;
  *) sed -n '2,9p' "$0"; exit 1 ;;
esac
