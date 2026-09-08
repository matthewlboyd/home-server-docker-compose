#!/usr/bin/env bash
# Run on the Docker host before recreating containers. Prints .env assignments.
# Reads Docker image/container metadata; does not pull images, edit files, or
# start/stop containers. The new proxy image is resolved through its registry.
set -euo pipefail

image_pins=()
for container in traefik portainer pihole nginx ddclient; do
  case "$container" in
    traefik) variable=TRAEFIK_IMAGE ;;
    portainer) variable=PORTAINER_IMAGE ;;
    pihole) variable=PIHOLE_IMAGE ;;
    nginx) variable=NGINX_IMAGE ;;
    ddclient) variable=DDCLIENT_IMAGE ;;
  esac
  image_id=$(docker inspect --type container --format '{{.Image}}' "$container")
  image_reference=$(docker image inspect \
    --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}' "$image_id")
  if [[ ! "$image_reference" =~ ^[^[:space:]]+@sha256:[a-f0-9]{64}$ ]]; then
    printf 'No repository digest available for %s. Record a tested image tag manually.\n' "$container" >&2
    exit 1
  fi
  image_pins+=("${variable}=${image_reference}")
done

# Resolve an immutable reference for the socket proxy. Existing application
# images above are taken from running containers, never from their latest tags.
proxy_repository=lscr.io/linuxserver/socket-proxy
if ! proxy_digest=$(docker buildx imagetools inspect \
  "${proxy_repository}:latest" --format '{{.Manifest.Digest}}'); then
  printf 'The registry lookup for socket-proxy failed. Existing application pins follow:\n' >&2
  printf '%s\n' "${image_pins[@]}"
  printf 'Set SOCKET_PROXY_IMAGE to a verified image@sha256:... before deployment.\n' >&2
  exit 1
fi
if [[ ! "$proxy_digest" =~ ^sha256:[a-f0-9]{64}$ ]]; then
  printf 'The registry did not return a valid socket-proxy manifest digest.\n' >&2
  exit 1
fi
image_pins+=("SOCKET_PROXY_IMAGE=${proxy_repository}@${proxy_digest}")
printf '%s\n' "${image_pins[@]}"
