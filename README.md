# 🤖 Nanobot-Lite

> **Ultra-lightweight AI agent for Telegram — built for Termux on 32-bit Android**

A stripped-down, Termux-compatible rebuild of [HKUDS/nanobot](https://github.com/HKUDS/nanobot). No WebUI, no extra channels, no native extensions — just a lean Telegram bot with Claude brain + tools.

## ⚡ What's Inside

```
✅ Telegram bot          — python-telegram-bot
✅ Claude (Anthropic)    — Official SDK
✅ Web search            — DuckDuckGo
✅ Shell execution       — with safety filters
✅ File operations       — read/write/edit/list
✅ Session memory        — file-based, persistent
✅ ~15 dependencies      — all pure Python or have ARM32 wheels
✅ Python 3.11+          — runs in Termux on 32-bit Android
```

## 📱 Termux Setup (32-bit Android)

### 1. Install Termux

Download from [F-Droid](https://f-droid.org/) (recommended) or GitHub. **Do NOT use Google Play** — it's outdated.

### 2. Update Termux

```bash
termux-setup-storage  # Allow storage access
pkg update && pkg upgrade -y
```

### 3. Install Python

```bash
pkg install python -y
python --version  # Should be 3.11+
```

### 4. Install Nanobot-Lite

```bash
# Navigate to where you want nanobot-lite
cd ~/nanobot_workspace
pip install nanobot-lite
```

Or install from this repo:
```bash
pip install git+https://github.com/YOUR_USERNAME/nanobot-lite.git
```

### 5. Get Your API Keys

**Anthropic (Claude brain):**
1. Go to [console.anthropic.com](https://console.anthropic.com/)
2. Create an API key
3. Copy it

**Telegram Bot:**
1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot`
3. Follow the prompts, get your bot token

### 6. Configure

Run the setup wizard:
```bash
nanobot-lite setup
```

Or manually create `~/.nanobot_lite/config.yaml`:

```yaml
telegram:
  enabled: true
  bot_token: "YOUR_TELEGRAM_BOT_TOKEN"
  allowed_users: []  # Leave empty to allow everyone, or add Telegram user IDs

agent:
  name: "Nanobot-Lite"
  model: "claude-sonnet-4-20250514"
  max_tokens: 4096
  temperature: 0.7
  system_prompt: |
    You are Nanobot-Lite, a helpful AI assistant.
    You have access to tools for web search, shell commands, and file operations.
    Be concise, helpful, and safe.

tools:
  workspace_dir: ~/nanobot_workspace
  shell_enabled: true
  shell_timeout: 30
  restrict_to_workspace: true
```

### 7. Set API Key & Run

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
nanobot-lite run
```

**That's it!** Your Telegram bot is live. Message it from your phone.

### 8. Keep It Running (Optional)

Use `tmux` or `nohup` to keep it running in the background:

```bash
# Install tmux
pkg install tmux -y

# Run in background
tmux new-session -d -s nanobot 'export ANTHROPIC_API_KEY="sk-ant-..." && nanobot-lite run'

# Attach to check on it
tmux attach -t nanobot
```

Or use Termux's built-in background feature (long-press home button).

## 🛠️ Commands

| Command | Description |
|---|---|
| `/start` | Start the bot |
| `/help` | Show help |
| `/reset` | Reset your conversation |
| `/stats` | Show session statistics |

## 🔧 CLI Reference

```bash
nanobot-lite run              # Start the bot
nanobot-lite setup            # Interactive setup wizard
nanobot-lite session --list   # List all sessions
nanobot-lite session --stats <key>  # Show session stats
nanobot-lite session --delete <key>  # Delete a session
nanobot-lite shell "ls -la"   # Quick shell command
nanobot-lite version           # Show version
```

## 🔒 Security

- **Workspace restriction** — File operations are confined to `workspace_dir`
- **Blocked commands** — Dangerous shell commands are blocked (fork bombs, `rm -rf /`, etc.)
- **User allowlist** — Restrict bot access to specific Telegram users
- **No code execution** — Tools are sandboxed

## 📁 File Structure

```
~/.nanobot_lite/
├── config.yaml       # Your configuration
├── sessions/         # Chat history (one JSON file per session)
└── nanobot.log       # Log file
```

## 🔄 Dependencies (All ARM32-compatible)

All dependencies have pre-built wheels for ARM32 (Android/Termux):

- `pydantic` — Config validation
- `anthropic` — Claude SDK
- `python-telegram-bot` — Telegram API
- `httpx` — HTTP client
- `websockets` — WebSocket support
- `loguru` — Logging
- `typer` — CLI
- `croniter` — Cron scheduling
- `ddgs` — DuckDuckGo search
- `readability-lxml` — Web page extraction
- `prompt-toolkit` — CLI input
- `rich` — Terminal formatting
- `jinja2` — Templating
- `pyyaml` — YAML config
- `json-repair` — JSON fixing
- `chardet` — Character detection
- `colorama` — Terminal colors

## 🚫 What's NOT Included (vs nanobot)

Removed for Termux compatibility:

- ❌ React WebUI (requires Node.js)
- ❌ API server (requires aiohttp + more deps)
- ❌ Discord, Slack, Matrix, WhatsApp channels
- ❌ MCP server support
- ❌ Skills system
- ❌ Many LLM providers (Anthropic only)
- ❌ tiktoken (no ARM32 wheel — using heuristic token estimation)
- ❌ boto3, pypdf, openpyxl (no ARM32 wheels)

## 🐛 Troubleshooting

**"Module not found" errors:**
```bash
pip install --force-reinstall nanobot-lite
```

**Bot not responding:**
- Check your bot token is correct
- Make sure you've started a chat with your bot
- Check logs: `tail -f ~/.nanobot_lite/nanobot.log`

**API errors:**
- Verify your `ANTHROPIC_API_KEY` is set and valid
- Check your Anthropic API credits

**Termux background dying:**
- Install Termux Boot: `pkg install termux-boot`
- Configure to start on boot

## 📄 License

MIT
