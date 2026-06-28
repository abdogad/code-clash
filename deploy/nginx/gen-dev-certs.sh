#!/usr/bin/env bash
# Self-signed cert for local verification of the TLS edge. NOT for production —
# the real host gets certs from Let's Encrypt (certbot --webroot -w /var/www/certbot).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p certs

if command -v openssl >/dev/null 2>&1; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
    -keyout certs/privkey.pem -out certs/fullchain.pem \
    -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
else
  # Fallback for boxes without the openssl CLI (uses the python cryptography lib).
  python3 - <<'PY'
import datetime, ipaddress
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
now = datetime.datetime.now(datetime.timezone.utc)
cert = (
    x509.CertificateBuilder()
    .subject_name(name).issuer_name(name)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=365))
    .add_extension(x509.SubjectAlternativeName(
        [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
    .sign(key, hashes.SHA256())
)
with open("certs/privkey.pem", "wb") as f:
    f.write(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
with open("certs/fullchain.pem", "wb") as f:
    f.write(cert.public_bytes(serialization.Encoding.PEM))
PY
fi
echo "wrote certs/fullchain.pem + certs/privkey.pem"
