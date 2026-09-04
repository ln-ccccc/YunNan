#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CANDIDATE_IMAGE="${1:-yunnan-inference-worker:candidate}"
CANONICAL_IMAGE="${2:-${INFERENCE_IMAGE:-yunnan-inference-worker:current}}"
CHECKPOINT_ARGUMENT="${3:-backend/model/mmseg_config/model.inference.pth}"
DOCKER_COMMAND="${DOCKER:-docker}"

normalize_image_reference() {
  local reference="$1"
  local final_component
  reference="${reference#docker.io/}"
  reference="${reference#index.docker.io/}"
  reference="${reference#library/}"
  final_component="${reference##*/}"
  if [[ "${final_component}" != *:* && "${reference}" != *@* ]]; then
    reference="${reference}:latest"
  fi
  printf '%s\n' "${reference}"
}

if [[ -z "${CANDIDATE_IMAGE}" || -z "${CANONICAL_IMAGE}" ]] ||
   [[ "$(normalize_image_reference "${CANDIDATE_IMAGE}")" == "$(normalize_image_reference "${CANONICAL_IMAGE}")" ]]; then
  printf 'INFERENCE_IMAGE_TAG_CONFLICT: candidate and canonical image must differ\n' >&2
  exit 2
fi

if [[ "${CHECKPOINT_ARGUMENT}" = /* ]]; then
  CHECKPOINT_CANDIDATE="${CHECKPOINT_ARGUMENT}"
else
  CHECKPOINT_CANDIDATE="${REPO_ROOT}/${CHECKPOINT_ARGUMENT}"
fi
if [[ ! -f "${CHECKPOINT_CANDIDATE}" ]]; then
  printf 'MODEL_CHECKPOINT_MISSING\n' >&2
  exit 2
fi
CHECKPOINT_PATH="$(realpath -- "${CHECKPOINT_CANDIDATE}")"

cd "${REPO_ROOT}"

"${DOCKER_COMMAND}" build --progress=plain \
  --file "${REPO_ROOT}/docker/Dockerfile.inference-gpu" \
  --target inference-worker \
  --tag "${CANDIDATE_IMAGE}" \
  "${REPO_ROOT}"

"${DOCKER_COMMAND}" run --rm --entrypoint python \
  "${CANDIDATE_IMAGE}" /app/docker/check-inference-image.py

"${DOCKER_COMMAND}" run --rm --entrypoint python \
  --env INFERENCE_ACCELERATOR=cpu \
  --env INFERENCE_CPU_FALLBACK=true \
  --volume "${CHECKPOINT_PATH}:/app/backend/model/mmseg_config/model.inference.pth:ro" \
  "${CANDIDATE_IMAGE}" /app/docker/check-inference-runtime.py

IMAGE_METADATA="$("${DOCKER_COMMAND}" image inspect --format '{{.Id}} {{.Size}}' "${CANDIDATE_IMAGE}")"
printf 'Verified candidate image: %s\n' "${IMAGE_METADATA}"

"${DOCKER_COMMAND}" image tag "${CANDIDATE_IMAGE}" "${CANONICAL_IMAGE}"
printf 'Canonical image tagged after verification: %s\n' "${CANONICAL_IMAGE}"
