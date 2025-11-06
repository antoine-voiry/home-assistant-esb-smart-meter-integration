#!/bin/bash
# Simple wrapper to run test_auth.py with credentials

echo "ESB Smart Meter Authentication Test"
echo "===================================="
echo ""
read -p "Enter your ESB username/email: " USERNAME
read -sp "Enter your ESB password: " PASSWORD
echo ""
read -p "Enter your MPRN: " MPRN
echo ""
echo "Running test..."
echo ""

source venv/bin/activate
python test_auth.py "$USERNAME" "$PASSWORD" "$MPRN"
