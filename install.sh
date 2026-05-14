#!/bin/bash
# Nanobot-Lite: One-line installer for Termux
# Run this in Termux on your 32-bit Android phone!
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/tundefund0-gif/nanobot-lite/main/install.sh | bash
#

set -e

echo "==============================================="
echo "  🤖 NANOBOT-LITE v0.2.0 INSTALLER"
echo "==============================================="
echo ""

# Check if we're in Termux
if [ ! -d "/data/data/com.termux" ]; then
    echo "⚠️  Warning: This script is designed for Termux on Android."
    echo "   It might still work on other Linux environments."
    echo ""
fi

# Check Python version
PYTHON_VERSION=$(python3 -c "import sys; print(sys.version_info.major)" 2>/dev/null || echo "0")
if [ "$PYTHON_VERSION" -lt 3 ]; then
    echo "❌ Python 3.11+ required. Run: pkg install python"
    exit 1
fi
echo "✅ Python 3 detected"

# Update packages
echo ""
echo "📦 Updating packages..."
pkg update -y -q 2>/dev/null || true

# Install dependencies
echo ""
echo "📦 Installing system dependencies..."
pkg install -y git curl

# Create workspace
WORKSPACE="$HOME/nanobot_workspace"
echo ""
echo "📁 Creating workspace: $WORKSPACE"
mkdir -p "$WORKSPACE"

# Create nanobot-lite config dir
CONFIG_DIR="$HOME/.nanobot_lite"
mkdir -p "$CONFIG_DIR/sessions"

# Clone or use pip install
echo ""
echo "📥 Installing nanobot-lite..."

if [ -d "/root/nanobot-lite" ]; then
    # We're on this machine, use pip from source
    echo "Installing from local source..."
    cd /root/nanobot-lite
    pip install -e . -q 2>/dev/null || pip install . -q 2>/dev/null || true
else
    pip install nanobot-lite -q 2>/dev/null || \
    pip install git+https://github.com/tundefund0-gif/nanobot-lite.git -q 2>/dev/null || true
fi

# Create default config
CONFIG_FILE="$CONFIG_DIR/config.yaml"
if [ ! -f "$CONFIG_FILE" ]; then
    echo ""
    echo "⚙️  Creating default config..."
    cat > "$CONFIG_FILE" << 'EOF'
telegram:
  enabled: true
  bot_token: ""
  allowed_users: []

agent:
  name: "Nanobot-Lite"
  model: "claude-sonnet-4-20250514"
  max_tokens: 4096
  temperature: 0.7
  system_prompt: |
    You are Nanobot-Lite, a helpful AI assistant.
    You have access to tools for web search, shell commands, and file operations.
    Be concise, helpful, and safe.

memory:
  enabled: true
  session_dir: ~/.nanobot_lite/sessions
  max_session_messages: 200

tools:
  workspace_dir: ~/nanobot_workspace
  shell_enabled: true
  shell_timeout: 30
  restrict_to_workspace: true
EOF
    echo "   Config created: $CONFIG_FILE"
fi

echo ""
echo "==============================================="
echo "  ✅ Installation complete!"
echo "==============================================="
echo ""
echo "📝 Next steps:"
echo ""
echo "1. Get your Telegram bot token:"
echo "   - Open Telegram, message @BotFather"
echo "   - Send: /newbot"
echo "   - Follow prompts, copy the token"
echo ""
echo "2. Get your Anthropic API key:"
echo "   - Visit https://console.anthropic.com/"
echo "   - Create an account, copy your API key"
echo ""
echo "3. Edit config: nano ~/.nanobot_lite/config.yaml"
echo "   - Add your bot token and API key"
echo ""
echo "4. Run the bot:"
echo "   export ANTHROPIC_API_KEY='sk-ant-...' "
echo "   nanobot-lite run"
echo ""
echo "==============================================="
