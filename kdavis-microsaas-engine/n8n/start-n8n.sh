#!/usr/bin/env bash
# Start n8n with microsaas-engine environment variables.
# Update RESEND_API_KEY and PRODUCT_DOMAIN before going live.

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
nvm use 22

export API_BASE_URL="http://localhost:8000"
export RESEND_API_KEY="${RESEND_API_KEY:-FILL_IN}"
export PRODUCT_DOMAIN="${PRODUCT_DOMAIN:-localhost}"
export APP_URL="${APP_URL:-http://localhost:3000}"

# n8n config
export N8N_PORT=5678
export N8N_PROTOCOL=http
export N8N_HOST=localhost
export EXECUTIONS_PROCESS=main

echo "Starting n8n on http://localhost:5678"
n8n start
