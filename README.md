# 🤖 Nanobot-Lite v0.6.0

> **Advanced AI agent for Telegram — runs natively on Termux 32-bit Android**

Built from scratch for constrained hardware. Pure Python, zero native deps.

---

## ✨ What's New in v0.6.0

- **curl | bash bootstrap** — no pip required to install. Works on a fresh Termux.
- **Deep self-healing engine** — 5-pass iterative error recovery (80+ patterns, AST auto-fix, LLM-guided healing, per-error-type circuit breakers)
- **10 async timeout guards** — agent loop, LLM calls, Telegram send all protected from hangs
- **Lazy loguru fallback** — 11 modules work without loguru installed
- **Per-error circuit breaker** — tracks `(tool × error_type)` pairs, not just per-tool
- **HealthMonitor** — disk-persisted, survives restarts
- **RollbackManager** — rolling backups, auto-restore on failure
- **7 self-diagnostic sections** — health, circuits, memory, system, workspace, runtime, env
- **13 slash commands** — `/help`, `/search`, `/shell`, `/stats`, `/sessions`, `/clear`, `/export`, `/uptime`, `/sysinfo`, `/id`, `/menu`, `/ping`, `/version`, `/config`, `/health`, `/stop`, `/exec`
- **9 advanced tools** — Calculator, System Info, Date/Time, URL Fetcher, Encode/Decode, UUID Generator, Currency Converter, Text Stats, File Hash
- **Context compression** — Automatically compacts long conversations
- **Retry with backoff** — LLM calls retry 3x with exponential backoff
- **Multi-tool chaining** — Execute multiple tools per turn
- **Rate limiting** — Token bucket per user (20 msgs/min, 50 turns/hr)
- **User allowlisting** — Restrict access by Telegram user ID
- **Interactive setup wizard** — 7-step guided config with API validation
- **Session export** — Export chat history to text file

---

## 🚀 Install (curl — no pip needed)

```bash
# One line. Works on fresh Termux, Linux, macOS.
bash -c "$(curl -fsSL https://raw.githubusercontent.com/tundefund0-gif/nanobot-lite/main/install.sh)"
```

Or download and run manually:

```bash
curl -fsSL https://raw.githubusercontent.com/tundefund0-gif/nanobot-lite/main/install.sh -o install.sh
chmod +x install.sh
./install.sh
```

**What it does:**
1. Detects platform (Termux / macOS / Linux)
2. Installs Python + pip if missing (via `pkg` on Termux, `brew` on macOS, `apt` on Linux)
3. Creates an isolated virtual environment at `~/nanobot_env`
4. Clones / updates the repo to `~/nanobot-lite`
5. Installs all Python dependencies
6. Runs the interactive setup wizard
7. Adds a `nanobot` alias to your shell

---

## 🔄 Upgrade

```bash
# Pull latest and reinstall
cd ~/nanobot-lite && git pull origin main
~/nanobot_env/bin/pip install -e . --quiet

# Or run the bootstrap again (safe — detects existing install)
bash -c "$(curl -fsSL https://raw.githubusercontent.com/tundefund0-gif/nanobot-lite/main/install.sh)"
```

---

## 🏃 Run

```bash
# Activate the alias (if not yet in session)
source ~/.bashrc

# Start the bot
nanobot-lite run

# Or directly
~/nanobot_env/bin/python -m nanobot_lite run
```

---

## ⚙️ Manual Setup

### 1. Get your credentials

**Telegram Bot Token:**
1. Open Telegram → search **@BotFather**
2. Send `/newbot` → follow prompts → copy **bot token**

**Anthropic API Key:**
1. Go to **https://console.anthropic.com/**
2. Create account → copy **API key** (`sk-ant-...`)

### 2. Configure

```bash
nanobot-lite setup
```

Or manually edit `~/.nanobot_lite/config.yaml`:

```yaml
telegram:
  enabled: true
  bot_token: "YOUR_BOT_TOKEN"
  allowed_users: []
  admin_user_id: ""
  reply_to_incoming: true

agent:
  name: "Nanobot-Lite"
  model: "claude-sonnet-4-20250514"
  max_tokens: 4096
  temperature: 0.7
  max_turns: 50
  system_prompt: |
    You are Nanobot-Lite, a helpful AI assistant with access to web search,
    shell commands, and file operations. Be concise, helpful, and safe.

memory:
  session_dir: ~/.nanobot_lite/sessions
  max_session_messages: 200

tools:
  workspace_dir: ~/nanobot_workspace
  shell_enabled: true
  limits:
    shell_timeout: 30
    restrict_to_workspace: true

log:
  level: INFO
```

### 3. Set API key and run

```bash
export ANTHROPIC_API_KEY="sk-ant-YOUR-KEY"
nanobot-lite run
```

---

## 📟 Commands

### Agent Commands
| Command | Description |
|---------|-------------|
| `/search <query>` | Web search via DuckDuckGo |
| `/shell <cmd>` | Execute shell command directly |
| `/sysinfo` | System info (CPU, RAM, disk) |

### Session Management
| Command | Description |
|---------|-------------|
| `/stats` | Bot statistics and top users |
| `/sessions` | List all active sessions |
| `/clear` | Clear your conversation |
| `/export` | Export chat history to file |

### Utility
| Command | Description |
|---------|-------------|
| `/ping` | Health check with latency |
| `/uptime` | Bot uptime |
| `/id` | Your Telegram user ID |
| `/menu` | Show interactive menu |
| `/version` | Version info |
| `/config` | Show current config |
| `/help` | All commands |

### Just type anything — the AI responds!

---

## 🛠️ Advanced Commands

### Health check
```bash
nanobot-lite health
```

### Personality builder
```bash
nanobot-lite persona
```

### Session management
```bash
nanobot-lite session --list          # List sessions
nanobot-lite session --stats <key>   # Session stats
nanobot-lite session --delete <key>   # Delete session
nanobot-lite session --export <key>   # Export to file
nanobot-lite session --clear-all     # Clear all sessions
```

### Direct shell
```bash
nanobot-lite shell "ls -la"
nanobot-lite shell "ps aux" --timeout 60
```

---

## 🔧 Tools Available

| Tool | Description |
|------|-------------|
| `shell` | Execute shell commands (sandboxed) |
| `read_file` | Read file contents |
| `write_file` | Write files |
| `edit_file` | Edit files with patch |
| `list_dir` | List directory contents |
| `web_search` | DuckDuckGo search |
| `calculator` | Math expressions |
| `system_info` | CPU, RAM, disk, OS |
| `datetime_info` | Current time/date |
| `fetch_url` | Get page content |
| `encode_decode` | Base64, URL, hex, MD5, SHA256 |
| `generate_id` | UUID, short ID, timestamp |
| `hash_file` | File hashing |
| `currency_convert` | Currency conversion |
| `text_stats` | Word/char/line count |

---

## 📦 Dependencies (5 packages, pure Python)

```
loguru          — Logging
typer           — CLI
questionary     — Interactive prompts
python-telegram-bot — Telegram bot
pyyaml          — Config
```

---

## 📁 Project Structure

```
nanobot-lite/
├── pyproject.toml
├── README.md
├── install.sh
└── nanobot_lite/
    ├── __init__.py          — Version 0.2.0
    ├── __main__.py
    ├── cli.py               — Advanced CLI (setup, run, persona, health, shell)
    ├── config/schema.py     — Dataclass-based config (no pydantic)
    ├── bus/
    │   ├── events.py        — Event types
    │   └── queue.py         — Message bus
    ├── agent/
    │   ├── loop.py          — Agent loop (retry, rate limiting, tool chaining)
    │   └── memory.py        — Session persistence
    ├── providers/
    │   ├── base.py          — LLM provider interface
    │   └── anthropic_provider.py  — Raw HTTP (no SDK)
    ├── channels/
    │   └── telegram.py      — Telegram bot (slash commands, menus)
    ├── tools/
    │   ├── base.py          — Tool registry
    │   ├── shell.py         — Shell tool
    │   ├── filesystem.py    — File tools
    │   ├── web.py           — Search tool
    │   └── advanced.py      — 9 advanced tools
    └── utils/
        └── helpers.py       — Web search (stdlib), helpers
```

---

## 🔒 Security

- Workspace restriction (files stay in `~/nanobot_workspace`)
- Command blocklist (fork bombs, `rm -rf /`, etc.)
- No inline code execution
- User allowlisting by Telegram ID
- Rate limiting per user

---

## 📱 Requirements

- Python 3.11+
- Termux on Android (32-bit ARM compatible)
- Telegram bot token
- Anthropic API key

---

**Built with ❤️ for Termux on Android**