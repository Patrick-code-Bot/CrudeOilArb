#!/bin/bash
# Emergency script to close all PAXG and XAUT positions on Bybit
# Uses Bybit REST API directly via curl

# Load environment variables
source .env

if [ -z "$BYBIT_API_KEY" ] || [ -z "$BYBIT_API_SECRET" ]; then
    echo "ERROR: BYBIT_API_KEY or BYBIT_API_SECRET not found in .env"
    exit 1
fi

API_KEY="$BYBIT_API_KEY"
API_SECRET="$BYBIT_API_SECRET"
BASE_URL="https://api.bybit.com"

echo "================================================================================"
echo "BYBIT POSITION CLOSER - Emergency Position Cleanup"
echo "================================================================================"
echo ""
echo "⚠️  WARNING: This will fetch and display all open PAXG and XAUT positions!"
echo ""

# Function to generate HMAC signature
generate_signature() {
    local timestamp=$1
    local params=$2
    echo -n "${timestamp}${API_KEY}5000${params}" | openssl dgst -sha256 -hmac "$API_SECRET" | awk '{print $2}'
}

# Get current timestamp in milliseconds
TIMESTAMP=$(date +%s)000

# Build query parameters for getting positions
RECV_WINDOW="5000"
PARAMS="category=linear&settleCoin=USDT"

# Generate signature
SIGNATURE=$(generate_signature "$TIMESTAMP" "$PARAMS")

# Fetch positions
echo "Fetching open positions..."
echo ""

RESPONSE=$(curl -s -X GET \
    "${BASE_URL}/v5/position/list?${PARAMS}" \
    -H "X-BAPI-API-KEY: ${API_KEY}" \
    -H "X-BAPI-TIMESTAMP: ${TIMESTAMP}" \
    -H "X-BAPI-SIGN: ${SIGNATURE}" \
    -H "X-BAPI-RECV-WINDOW: ${RECV_WINDOW}")

# Check if curl succeeded
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to fetch positions from Bybit"
    exit 1
fi

# Parse response and display positions
echo "Response from Bybit:"
echo "$RESPONSE" | python3 -m json.tool

echo ""
echo "================================================================================"
echo "MANUAL CLOSURE REQUIRED"
echo "================================================================================"
echo ""
echo "Due to Python environment restrictions, please close positions manually:"
echo ""
echo "1. Go to Bybit Positions page:"
echo "   https://www.bybit.com/trade/usdt/PAXGUSDT"
echo ""
echo "2. Find your open PAXG and XAUT positions"
echo ""
echo "3. Click 'Close' on each position"
echo ""
echo "4. Select 'Market Order' for immediate execution"
echo ""
echo "5. Confirm the closure"
echo ""
echo "6. Verify both positions show 0.00"
echo ""
echo "After closing all positions, restart the strategy with:"
echo "   python3 run_live.py"
echo ""
echo "================================================================================"
