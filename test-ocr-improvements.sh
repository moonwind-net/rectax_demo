#!/bin/bash
# Test script to verify OCR improvements are working in WSL/Linux

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Environment Check ==="
echo "Hostname: $(hostname)"
echo "OS: $(lsb_release -d 2>/dev/null | cut -f2 || uname -s)"
echo "Working Dir: $(pwd)"
echo "Docker: $(docker --version)"
echo "Docker Compose: $(docker-compose --version)"
echo ""

echo "=== Checking if containers are built with latest code ==="
docker-compose ps -q paddle-ocr | xargs -I {} docker inspect {} --format='{{ .Created }}' | head -1 || echo "Container not running"
echo ""

echo "=== Testing OCR Processing (v3) ==="
if [ -f "uploads/IMG20260328145126.jpg" ]; then
    echo "Sending test image to paddle-ocr v3 (port 8011)..."
    RESPONSE=$(curl -sS -X POST -F "file=@uploads/IMG20260328145126.jpg" http://127.0.0.1:8011/ocr/)
    
    echo "Response:"
    echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
    echo ""
    
    # Check for improvements
    echo "=== Checking for improvements ==="
    if echo "$RESPONSE" | grep -q '"telephone"'; then
        echo "✓ Telephone number extraction implemented"
    else
        echo "✗ Telephone number extraction NOT found (still old code)"
    fi
    
    if echo "$RESPONSE" | grep -q '"store_info"'; then
        echo "✓ Store info extraction implemented"
    else
        echo "✗ Store info extraction NOT found (still old code)"
    fi
    
    if echo "$RESPONSE" | grep -q '"payment_method"'; then
        echo "✓ Payment method extraction present"
    else
        echo "✗ Payment method extraction NOT found"
    fi
    
    if echo "$RESPONSE" | grep -q '"registration_number"'; then
        echo "✓ Registration number field present"
    else
        echo "✗ Registration number field NOT found"
    fi
else
    echo "Test image not found: uploads/IMG20260328145126.jpg"
fi

echo ""
echo "=== Test Complete ==="
