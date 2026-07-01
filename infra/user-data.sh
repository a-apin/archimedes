#!/bin/bash
set -euxo pipefail

# ---------------------------------------------------------------
# Archimedes EC2 bootstrap — runs once on first boot via cloud-init
# Installs Docker, clones repo, starts services
# ---------------------------------------------------------------

export DEBIAN_FRONTEND=noninteractive

# System updates
apt-get update -y
apt-get upgrade -y

# Install Docker CE
apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null

apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Enable Docker, add ubuntu user to docker group
systemctl enable docker
systemctl start docker
usermod -aG docker ubuntu

# Install git (usually pre-installed, but be safe)
apt-get install -y git

# Create app directory
mkdir -p /opt/archimedes
chown ubuntu:ubuntu /opt/archimedes

# Clone the repo
su - ubuntu -c "git clone ${repo_url} /opt/archimedes"

# Generate a strong DB password at boot — never hardcode a credential in the
# repo (the previous "archimedes-hackathon-2026" literal was world-readable in
# git). hex is URL-safe, so it slots into DATABASE_URL without escaping.
# (Longer-term: pull this from AWS SSM Parameter Store at container start.)
#
# NOTE: this file is rendered by Terraform's templatefile() (see main.tf), which
# parses the WHOLE file (comments included) and treats $${...} as a template
# directive. Shell variable references are therefore doubled ($$ renders to a
# single $) so the rendered script keeps the shell expansion at boot. The only
# real Terraform template variable in this file is repo_url (used above).
DB_PASS="$(openssl rand -hex 24)"

# Create a placeholder .env on the server (team will populate the rest later).
# Unquoted heredoc so $${DB_PASS} expands at runtime; no other shell $refs here.
cat > /opt/archimedes/.env <<ENVEOF
# Archimedes production environment — populated by the team
# Do NOT commit this file. See .env.example for the template.
POSTGRES_USER=archimedes
POSTGRES_PASSWORD=$${DB_PASS}
POSTGRES_DB=archimedes
REDIS_URL=redis://redis:6379/0
DATABASE_URL=postgresql://archimedes:$${DB_PASS}@postgres:5432/archimedes
# The postgres/redis services are profile-gated (profiles: ["localdb"]) so the
# default compose run starts only the app against MANAGED stores. This freshly
# bootstrapped instance writes in-stack DATABASE_URL/REDIS_URL above, so it MUST
# enable the localdb profile or those services won't start and the app can't
# connect. Cutover to managed Aurora/ElastiCache is a deliberate manual step:
# edit this .env to the managed URLs and REMOVE this line, then recreate. (#780)
COMPOSE_PROFILES=localdb
ENVEOF
chmod 600 /opt/archimedes/.env
chown ubuntu:ubuntu /opt/archimedes/.env

# Start services (if docker-compose.yml exists)
if [ -f /opt/archimedes/docker-compose.yml ]; then
  su - ubuntu -c "cd /opt/archimedes && docker compose up --build -d"
fi

# Signal that cloud-init is done
echo "archimedes-bootstrap-complete" > /tmp/bootstrap-done
