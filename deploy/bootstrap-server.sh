#!/usr/bin/env bash
# One-time setup for a fresh Ubuntu 24.04 server (e.g. a Hetzner Cloud box). Run as root:
#   ssh root@<server-ip> 'bash -s' < deploy/bootstrap-server.sh
# or copy the repo up first and run ./deploy/bootstrap-server.sh on the box.
#
# Installs Docker Engine + compose plugin and a host firewall. Ubuntu 24.04's
# systemd delegates the cgroup cpu+memory controllers to containers by default,
# which is what the deterministic judge needs.
set -euo pipefail

echo ">> Installing Docker Engine + compose plugin…"
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker

# Let a non-root deploy user drive docker without sudo (re-login to take effect).
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  usermod -aG docker "$SUDO_USER"
  echo ">> Added $SUDO_USER to the docker group (log out/in to use it)."
fi

echo ">> Kernel knobs the judge sandbox needs (user namespaces + perf)…"
# Ubuntu 24.04+ blocks unprivileged user namespaces, which bwrap --unshare-all
# needs from inside the judge container; re-enable host-wide. And Ubuntu ships
# perf_event_paranoid=4, which blocks the unprivileged perf_event_open tallyrun
# uses for instruction counting — drop it to 2 (Fedora's default) so the judge
# gets load-invariant instruction-based verdicts instead of degrading to CPU time.
cat > /etc/sysctl.d/99-codeclash-judge.conf <<'EOF'
kernel.apparmor_restrict_unprivileged_userns = 0
kernel.perf_event_paranoid = 2
EOF
sysctl --system >/dev/null

echo ">> Host firewall (ufw): allow SSH + HTTP + HTTPS only…"
# Allow SSH FIRST so enabling ufw can't lock you out.
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo ">> Done. Docker: $(docker --version)"
docker compose version
