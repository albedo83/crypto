#!/usr/bin/env bash
set -euo pipefail

echo "=== Crypto Collector Setup ==="

# PostgreSQL 17 + TimescaleDB
echo "[1/6] Installing PostgreSQL 17..."
if ! command -v psql &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq gnupg lsb-release curl

    # PostgreSQL APT repo
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo gpg --dearmor -o /usr/share/keyrings/postgresql.gpg
    echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" | sudo tee /etc/apt/sources.list.d/pgdg.list
    sudo apt-get update -qq
    sudo apt-get install -y -qq postgresql-17 postgresql-client-17
else
    echo "  PostgreSQL already installed: $(psql --version)"
fi

echo "[2/6] Installing TimescaleDB..."
if ! dpkg -l | grep -q timescaledb; then
    # TimescaleDB APT repo
    curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/timescaledb.gpg
    echo "deb [signed-by=/usr/share/keyrings/timescaledb.gpg] https://packagecloud.io/timescale/timescaledb/ubuntu/ $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/timescaledb.list
    sudo apt-get update -qq
    sudo apt-get install -y -qq timescaledb-2-postgresql-17
    # Configure
    sudo timescaledb-tune --quiet --yes --pg-config=/usr/lib/postgresql/17/bin/pg_config
else
    echo "  TimescaleDB already installed"
fi

echo "[3/6] Starting PostgreSQL..."
sudo systemctl enable postgresql
sudo systemctl start postgresql

echo "[4/6] Creating database and user..."
sudo -u postgres psql -v ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'crypto') THEN
        CREATE ROLE crypto WITH LOGIN PASSWORD 'crypto_pwd_2024';
    END IF;
END
$$;

SELECT 'CREATE DATABASE crypto OWNER crypto'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'crypto')\gexec

GRANT ALL PRIVILEGES ON DATABASE crypto TO crypto;
SQL

# Enable TimescaleDB extension
sudo -u postgres psql -d crypto -c "CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;"
sudo -u postgres psql -d crypto -c "GRANT ALL ON SCHEMA public TO crypto;"
sudo -u postgres psql -d crypto -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO crypto;"
sudo -u postgres psql -d crypto -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO crypto;"

echo "[5/6] Setting up Python environment..."
cd /home/crypto
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -e "." -q

echo "[6/6] Running migrations..."
source .venv/bin/activate
alembic upgrade head

echo ""
echo "=== Setup complete ==="
echo "  DB: crypto @ localhost:5432"
echo "  User: crypto"
echo "  Activate venv: source /home/crypto/.venv/bin/activate"
