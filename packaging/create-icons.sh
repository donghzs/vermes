#!/usr/bin/env bash
# Create icons for Windows (.ico) and macOS (.icns)
# Requires: ImageMagick (convert) or png2icn

set -e

cd "$(dirname "$0")"
SOURCE="../hermes_cli/web_dist/logo-256.png"

mkdir -p icons

if command -v convert &>/dev/null; then
    echo "Creating icons with ImageMagick..."

    # Windows .ico (multi-resolution)
    convert "$SOURCE" -resize 16x16 icons/vermes-16.png
    convert "$SOURCE" -resize 32x32 icons/vermes-32.png
    convert "$SOURCE" -resize 48x48 icons/vermes-48.png
    convert "$SOURCE" -resize 64x64 icons/vermes-64.png
    convert "$SOURCE" -resize 128x128 icons/vermes-128.png
    convert "$SOURCE" -resize 256x256 icons/vermes-256.png

    convert icons/vermes-16.png icons/vermes-32.png icons/vermes-48.png \
            icons/vermes-64.png icons/vermes-128.png icons/vermes-256.png \
            vermes.ico

    echo "✅ Created vermes.ico"

    # macOS .icns (via iconutil if on macOS)
    if [[ "$OSTYPE" == "darwin"* ]]; then
        mkdir -p icons/icon.iconset
        cp icons/vermes-16.png icons/icon.iconset/icon_16x16.png
        cp icons/vermes-32.png icons/icon.iconset/icon_16x16@2x.png
        cp icons/vermes-32.png icons/icon.iconset/icon_32x32.png
        cp icons/vermes-64.png icons/icon.iconset/icon_32x32@2x.png
        cp icons/vermes-128.png icons/icon.iconset/icon_128x128.png
        cp icons/vermes-256.png icons/icon.iconset/icon_128x128@2x.png
        cp icons/vermes-256.png icons/icon.iconset/icon_256x256.png

        iconutil -c icns icons/icon.iconset -o vermes.icns
        echo "✅ Created vermes.icns"
    fi

    echo ""
    echo "Icons ready in: $(pwd)"
else
    echo "❌ ImageMagick not found. Install with:"
    echo "   macOS: brew install imagemagick"
    echo "   Windows: choco install imagemagick"
    echo ""
    echo "Or manually create:"
    echo "   - vermes.ico (Windows, multi-resolution ICO)"
    echo "   - vermes.icns (macOS, ICNS format)"
fi
