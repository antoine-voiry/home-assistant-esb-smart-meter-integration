#!/usr/bin/env bash
# Run all tests with coverage

set -e

echo "🧪 Running ESB Smart Meter Integration Tests..."
echo ""

# Activate virtual environment
source .venv/bin/activate

# Run tests with pytest
python -m pytest tests/ -v --cov=custom_components.esb_smart_meter --cov-report=term-missing --cov-report=html

echo ""
echo "✅ Tests completed!"
echo ""
echo "📊 Coverage report generated in htmlcov/index.html"
