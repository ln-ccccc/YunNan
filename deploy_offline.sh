#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' \
  'ERROR: This legacy Linux deployment entrypoint is disabled.' \
  'Use deploy_offline.ps1 for the Windows complete migration package.' \
  'For a Linux new deployment, follow docs/offline_deployment_guide.md; that flow does not restore migrated volumes.'
exit 1
