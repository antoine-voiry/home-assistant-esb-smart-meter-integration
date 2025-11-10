#!/bin/bash

# Deploy ESB Smart Meter Integration to Home Assistant
# This script copies the custom component to the Home Assistant config volume

set -e  # Exit on error

SOURCE_DIR="./custom_components/esb_smart_meter"
TARGET_DIR="/Volumes/config/custom_components/esb_smart_meter"

echo "🚀 Deploying ESB Smart Meter Integration to Home Assistant..."

# Check if source directory exists
if [ ! -d "$SOURCE_DIR" ]; then
    echo "❌ Error: Source directory $SOURCE_DIR not found!"
    exit 1
fi

# Check if target volume is mounted
if [ ! -d "/Volumes/config" ]; then
    echo "❌ Error: /Volumes/config is not mounted!"
    echo "   Please ensure your Home Assistant config volume is mounted."
    exit 1
fi

# Create target directory if it doesn't exist
mkdir -p "$TARGET_DIR"

# Copy Python files
echo "📦 Copying Python files..."
cp -v "$SOURCE_DIR"/*.py "$TARGET_DIR/"

# Copy JSON files (manifest, strings, translations)
echo "📄 Copying configuration files..."
cp -v "$SOURCE_DIR"/*.json "$TARGET_DIR/"

# Copy translations directory if it exists
if [ -d "$SOURCE_DIR/translations" ]; then
    echo "🌍 Copying translations..."
    mkdir -p "$TARGET_DIR/translations"
    cp -v "$SOURCE_DIR/translations"/*.json "$TARGET_DIR/translations/"
fi

echo "✅ Deployment complete!"
echo ""
echo "📋 Next steps:"
echo "   1. Restart Home Assistant to load the updated integration"
echo "   2. Check the logs for any errors during startup"
echo ""
echo "💡 To restart Home Assistant:"
echo "   - Developer Tools > YAML > Restart"
echo "   - Or use: ha core restart (if using Home Assistant OS)"
