set -euo pipefail
cd "$(dirname "$0")/.."
export CAROOT="$PWD/.intermediate/tls/ca"
mkdir -p "$CAROOT"
phone_ip="$(ipconfig getifaddr en0)"
test -n "$phone_ip"
mkcert -cert-file .intermediate/tls/server.pem -key-file .intermediate/tls/server-key.pem "$phone_ip" localhost 127.0.0.1
chmod 600 .intermediate/tls/server-key.pem "$CAROOT/rootCA-key.pem"
export TLS_CERT="$PWD/.intermediate/tls/server.pem"
export TLS_KEY="$PWD/.intermediate/tls/server-key.pem"
export HOST=0.0.0.0 PORT=3443
printf '手机访问地址：https://%s:3443\n' "$phone_ip"
cd frontend
exec npm start
