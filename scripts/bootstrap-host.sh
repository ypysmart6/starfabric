#!/usr/bin/env bash

set -Eeuo pipefail

readonly GO_VERSION="1.26.5"
readonly GO_ARCHIVE="go${GO_VERSION}.linux-amd64.tar.gz"
readonly GO_SHA256="5c2c3b16caefa1d968a94c1daca04a7ca301a496d9b086e17ad77bb81393f053"
readonly CLAB_VERSION="0.79.0"
readonly CLAB_PACKAGE="containerlab_${CLAB_VERSION}_linux_amd64.deb"
readonly CLAB_SHA256="a399d92a622b4664d8d1231bc9b7f53a1d210255a0306fa091c3f63779f65f13"
readonly DOWNLOAD_DIR="/tmp/starfabric-bootstrap"

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: run this script with sudo:" >&2
  echo "  sudo bash scripts/bootstrap-host.sh" >&2
  exit 1
fi

if [[ ! -r /etc/os-release ]]; then
  echo "ERROR: /etc/os-release is unavailable" >&2
  exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release

if [[ "${ID:-}" != "ubuntu" || "${VERSION_CODENAME:-}" != "jammy" ]]; then
  echo "ERROR: this bootstrap is pinned for Ubuntu 22.04 (jammy)" >&2
  exit 1
fi

if [[ "$(dpkg --print-architecture)" != "amd64" ]]; then
  echo "ERROR: this bootstrap is pinned for amd64" >&2
  exit 1
fi

readonly TARGET_USER="${SUDO_USER:-}"
if [[ -z "${TARGET_USER}" || "${TARGET_USER}" == "root" ]]; then
  echo "ERROR: invoke the script from your normal account using sudo" >&2
  exit 1
fi

echo "[1/6] Installing prerequisite packages"
apt-get update
apt-get install -y ca-certificates curl

echo "[2/6] Configuring Docker's official APT repository"
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
printf '%s\n' \
  "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu jammy stable" \
  > /etc/apt/sources.list.d/docker.list

echo "[3/6] Installing and starting Docker Engine"
apt-get update
apt-get install -y \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin
systemctl enable --now docker
usermod -aG docker "${TARGET_USER}"

echo "[4/6] Installing Go ${GO_VERSION}"
if [[ -e /usr/local/go ]]; then
  if [[ -x /usr/local/go/bin/go ]] && \
     [[ "$(/usr/local/go/bin/go version)" == "go version go${GO_VERSION} linux/amd64" ]]; then
    echo "Go ${GO_VERSION} is already installed; leaving it unchanged"
  else
    echo "ERROR: /usr/local/go already exists and was not modified" >&2
    echo "Move or remove it manually after inspecting its contents, then rerun." >&2
    exit 1
  fi
else
  install -m 0755 -d "${DOWNLOAD_DIR}"
  curl -fL "https://go.dev/dl/${GO_ARCHIVE}" \
    -o "${DOWNLOAD_DIR}/${GO_ARCHIVE}"
  printf '%s  %s\n' "${GO_SHA256}" "${DOWNLOAD_DIR}/${GO_ARCHIVE}" \
    | sha256sum --check --strict
  tar -C /usr/local -xzf "${DOWNLOAD_DIR}/${GO_ARCHIVE}"
fi

for binary in go gofmt; do
  target="/usr/local/go/bin/${binary}"
  link="/usr/local/bin/${binary}"

  if [[ -L "${link}" && "$(readlink -f "${link}")" == "${target}" ]]; then
    continue
  fi
  if [[ -e "${link}" || -L "${link}" ]]; then
    echo "ERROR: ${link} already exists; refusing to overwrite" >&2
    exit 1
  fi
  ln -s "${target}" "${link}"
done

echo "[5/6] Installing containerlab ${CLAB_VERSION}"
install -m 0755 -d "${DOWNLOAD_DIR}"
curl -fL \
  "https://github.com/srl-labs/containerlab/releases/download/v${CLAB_VERSION}/${CLAB_PACKAGE}" \
  -o "${DOWNLOAD_DIR}/${CLAB_PACKAGE}"
printf '%s  %s\n' "${CLAB_SHA256}" "${DOWNLOAD_DIR}/${CLAB_PACKAGE}" \
  | sha256sum --check --strict
apt-get install -y "${DOWNLOAD_DIR}/${CLAB_PACKAGE}"

if getent group clab_admins >/dev/null; then
  usermod -aG clab_admins "${TARGET_USER}"
fi

echo "[6/6] Verifying the installed toolchain"
docker --version
docker compose version
/usr/local/go/bin/go version
containerlab version
docker run --rm hello-world

echo
echo "Installation completed successfully."
echo "Log out and back in once so the docker/clab_admins group changes take effect."
echo "After logging back in, run: bash scripts/verify-host.sh"
