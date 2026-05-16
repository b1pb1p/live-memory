# 🔌 Live Memory Integration Guide for Cline (VS Code / VSCodium)

> **Version**: 1.2.0 | **Date**: 2026-03-27

This guide walks you step by step through connecting **Cline** (the AI agent in VS Code or VSCodium) to **Live Memory** to give it shared, persistent working memory.

---

## 📋 Table of Contents

- [Prerequisites](#-prerequisites)
- [Step 1 — Start Live Memory](#-step-1--start-live-memory)
- [Step 2 — Create a token for Cline](#-step-2--create-a-token-for-cline)
- [Step 3 — Configure Cline in VS Code / VSCodium](#-step-3--configure-cline-in-vs-code--vscodium)
- [Step 4 — Create a memory space](#-step-4--create-a-memory-space)
- [Step 5 — Give Cline its instructions](#-step-5--give-cline-its-instructions)
- [Recommended Workflow](#-recommended-workflow)
- [Custom Instructions for Cline](#-custom-instructions-for-cline)
- [Multi-agent: Cline + Claude + others](#-multi-agent-cline--claude--others)
- [Troubleshooting](#-troubleshooting)
- [With Claude Desktop](#-with-claude-desktop)

---

## 📦 Prerequisites

| Component                   | Version            | Check                               |
| --------------------------- | ------------------ | ----------------------------------- |
| **Docker**                  | ≥ 24.0             | `docker --version`                  |
| **Docker Compose**          | v2                 | `docker compose version`            |
| **VS Code** or **VSCodium** | Recent             | —                                   |
| **Cline extension**         | Recent             | Installed from the marketplace      |
| **Live Memory**             | Deployed & running | `curl http://localhost:8080/health` |

---

## 🚀 Step 1 — Start Live Memory

If Live Memory is not yet running:

```bash
cd /path/to/live-memory
cp .env.example .env
# Edit .env with your S3 credentials, LLMaaS settings, and ADMIN_BOOTSTRAP_KEY
docker compose build
docker compose up -d
```

**Check**:

```bash
# Should return {"status": "ok", ...}
curl -s http://localhost:8080/health | jq .
```

---

## 🔑 Step 2 — Create a token for Cline

Cline needs a **Bearer Token** with `read,write` permissions to read and write the memory.

### Option A — Via the CLI

```bash
cd /path/to/live-memory
export MCP_TOKEN=<your_ADMIN_BOOTSTRAP_KEY>

# Create a "write" token for Cline
python scripts/mcp_cli.py token create cline-agent read,write
```

The CLI will print something like:

```
Token created successfully!
  Name   : cline-agent
  Token  : lm_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0u1V2
  Perms  : read, write

⚠️  This token will NEVER be displayed again. Copy it now!
```

> **⚠️ IMPORTANT**: Copy this token immediately! It will never be shown again (only the SHA-256 hash is stored).

### Option B — Via the bootstrap key (temporary)

For a quick test, you can use the `ADMIN_BOOTSTRAP_KEY` defined in your `.env` directly. But **in production**, always create a dedicated token with minimal permissions.

---

## ⚙️ Step 3 — Configure Cline in VS Code / VSCodium

### 3.1 Open Cline's MCP settings

1. Open VS Code / VSCodium
2. Open the Cline panel (Cline icon in the sidebar)
3. Click the **⚙️ Settings** gear icon at the top of the Cline panel
4. Find **"MCP Servers"** or click the **MCP** tab
5. Click **"Edit MCP Settings"** (or the button to edit the JSON)

### 3.2 Add Live Memory as an MCP server

In the `cline_mcp_settings.json` file that opens, add the following configuration:

```json
{
  "mcpServers": {
    "live-memory": {
      "url": "http://localhost:8080/mcp",
      "headers": {
        "Authorization": "Bearer lm_YOUR_TOKEN_HERE"
      },
      "timeout": 600
    }
  }
}
```

> **Replace** `lm_YOUR_TOKEN_HERE` with the token from Step 2.
> **⚠️ The `timeout` parameter is critical**: LLM consolidation can take longer than 60 seconds (Cline's default timeout). Raising it to 600 seconds is mandatory, in line with your `.env` configuration.

### 3.3 Where is the config file located?

| OS                 | Typical location                                                                                                    |
| ------------------ | ------------------------------------------------------------------------------------------------------------------- |
| **macOS**          | `~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`     |
| **Linux**          | `~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`                         |
| **VSCodium macOS** | `~/Library/Application Support/VSCodium/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json` |
| **VSCodium Linux** | `~/.config/VSCodium/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`                     |

### 3.4 Verify the connection

After saving the config file:

1. **Restart Cline** (or reload VS Code with `Ctrl+Shift+P` → "Developer: Reload Window")
2. In the Cline panel, click the **MCP** tab
3. You should see **"live-memory"** with a green indicator ✅
4. Click it to view the **38 available tools**

### 3.5 Remote server (production)

If Live Memory is deployed on an HTTPS server:

```json
{
  "mcpServers": {
    "live-memory": {
      "url": "https://live-mem.your-domain.com/mcp",
      "headers": {
        "Authorization": "Bearer lm_YOUR_TOKEN_HERE"
      },
      "timeout": 600
    }
  }
}
```

---

## 📁 Step 4 — Create a memory space

Before Cline can write notes, you need a **memory space** with **rules** that define the Memory Bank structure.

### Via the CLI

```bash
python scripts/mcp_cli.py space create my-project \
  --rules-file ./rules/standard.md \
  -d "My development project"
```

### Through Cline directly

You can also ask Cline to create the space. Just tell it:

> *"Use the `space_create` tool to create a space called `my-project` with standard Memory Bank rules (projectbrief, activeContext, progress, techContext, systemPatterns, productContext)."*

Cline will invoke the MCP `space_create` tool.

### Example of standard rules

```markdown
# Memory Bank Rules

## Files to maintain

### projectbrief.md
Vision, goals, project scope.

### activeContext.md
Current focus, ongoing work, recent decisions, next steps.

### progress.md
What works, what's left to do, known issues.

### techContext.md
Technologies used, configuration, technical constraints.

### systemPatterns.md
Architecture, patterns, technical decisions, components.

### productContext.md
Why this project exists, problems solved, user experience.
```

---

## 📝 Step 5 — Give Cline its instructions

To make Cline use Live Memory automatically, add **Custom Instructions** to its settings.

### 5.1 Where to configure Custom Instructions

In Cline: **Settings** → **Custom Instructions** (or in your project's `.clinerules` file).

### 5.2 Recommended instructions (template `{SPACE}`)

Copy the content below into your agent's **Custom Instructions** (or into a `.clinerules` file at your project root). This template uses the `{SPACE}` placeholder — only **one value** needs to be configured:


```markdown
# Cline's Memory Bank — Live Memory MCP

My memory resets completely between sessions. I depend ENTIRELY on the Memory Bank to understand the project and continue effectively.

## 🔌 Configuration (to customize per project)

My persistent memory is managed by the **Live Memory** MCP server (`my-live-mem`).

> **⚙️ The only value to customize:**
>
> - **SPACE** = `my-project`       ← Replace with your space_id
>
> All instructions below use `{SPACE}` — I substitute it automatically with the value above.
> The agent name is **auto-detected** from the authentication token (no need to configure it).

## 📖 At the start of EVERY task (MANDATORY)

1. Call `space_rules("{SPACE}")` to read the rules (bank structure)
2. Call `bank_read_all("{SPACE}")` to load ALL consolidated context
3. Call `live_read(space_id="{SPACE}")` to read **unconsolidated notes**
4. Read the content carefully before starting
5. Identify the current focus in `activeContext.md`

> ⚠️ NEVER start working without having read the bank.
>
> 💡 **Why read live notes?** Between sessions, notes may have been written (by me or other agents) without being consolidated yet. These notes contain recent context not yet reflected in the bank files. Ignoring them = risking redoing work already done or missing recent decisions.

## 📝 During work

Write frequent, atomic notes via `live_note`:

live_note(space_id="{SPACE}", category="<category>", content="...")

The `agent` parameter is **auto-detected** from the token — no need to pass it.

**Categories**:
- `observation` — Factual findings, command results
- `decision` — Technical choices and their rationale
- `progress` — Advancement, completed work
- `issue` — Problems encountered, bugs
- `todo` — Identified tasks to do
- `insight` — Learnings, discovered patterns
- `question` — Points to clarify, pending decisions

## 🧠 At session end (or after a significant work block)

bank_consolidate(space_id="{SPACE}")

The LLM will consolidate **my own notes** (agent auto-detected from token) by updating the bank files according to the space rules.

> ℹ️ Only an admin can consolidate notes from all agents (`agent=""`).

## ⚠️ Strict rules

1. **NEVER write directly into the bank** — only the LLM consolidation does that
2. **Always pass `space_id="{SPACE}"`** in every call
3. **Write atomic notes after each significant step** — 1 note = 1 fact, 1 decision, or 1 task
4. **Consolidate at session end** — never quit without consolidating, but always after validating with the user
5. **Read the bank at startup** — never work without context

## 🔄 When to request an update

If the user says **"update memory bank"**:
1. Write `live_note` notes summarizing the current state of work
2. Call `bank_consolidate(space_id="{SPACE}")`
3. Verify the result with `bank_read_all("{SPACE}")`

## 📊 Useful commands

| Action                          | Command                                                                   |
| ------------------------------- | ------------------------------------------------------------------------- |
| Read full context               | `bank_read_all("{SPACE}")`                                                |
| Read rules                      | `space_rules("{SPACE}")`                                                  |
| Write a note                    | `live_note(space_id="{SPACE}", category="...", content="...")`            |
| Consolidate                     | `bank_consolidate(space_id="{SPACE}")`                                    |
| See recent notes                | `live_read(space_id="{SPACE}")`                                           |
| See another agent's notes       | `live_read(space_id="{SPACE}", agent="other-agent")`                      |
| Space info                      | `space_info("{SPACE}")`                                                   |
```

> 💡 **For a new project**: copy this file, change the `SPACE` line, that's it!

---

## 🔄 Recommended Workflow

### Typical development session workflow

```
┌────────────────────────────────────────────────┐
│  1. STARTUP                                    │
│     space_rules("my-project")                  │
│     bank_read_all("my-project")                │
│     live_read("my-project")                    │
│     → Cline reads rules + bank + live notes    │
├────────────────────────────────────────────────┤
│  2. WORK (loop)                                │
│     • Cline codes, analyzes, replies           │
│     • live_note("observation", "Build OK")     │
│     • live_note("decision", "Going with X")    │
│     • live_note("todo", "Tests to write")      │
│     • live_note("progress", "Auth done")       │
├────────────────────────────────────────────────┤
│  3. SESSION END                                │
│     bank_consolidate("my-project")             │
│     → LLM synthesizes notes into the bank      │
│     → Live notes deleted after success         │
└────────────────────────────────────────────────┘
```

### Consolidation frequency

| Situation                   | Recommendation                       |
| --------------------------- | ------------------------------------ |
| Short session (< 10 notes)  | Consolidate at session end           |
| Long session (> 20 notes)   | Consolidate every 15–20 notes        |
| Context switch              | Consolidate before switching topics  |
| End of day                  | Always consolidate                   |

### Real-time visualization

While Cline works, open the web UI to watch live:

```
http://localhost:8080/live
```

Notes will appear in real time in the **Live Timeline**, and the **Bank** updates after each consolidation.

---

## 📋 Custom Instructions for Cline

### Template version (recommended)

Copy the contents of the [`.clinerules/standard.memory.bank.md`](.clinerules/standard.memory.bank.md) file into your Custom Instructions or into a `.clinerules` file at your project root.

Then modify **only the `{SPACE}` value** to match your project. The agent name is auto-detected.

### Minimalist version (copy-paste into Custom Instructions)

If you want an ultra-short version, add this to global Custom Instructions:

```
You have access to Live Memory (MCP server).
- At startup: space_rules("{SPACE}"), bank_read_all("{SPACE}"), live_read("{SPACE}")
- During work: live_note(space_id="{SPACE}", category="...", content="...")
- At session end: bank_consolidate(space_id="{SPACE}")
Where {SPACE} = "my-project". The agent is auto-detected from the token.
```

---

## 👥 Multi-agent: Cline + Claude + others

Live Memory lets **multiple agents** collaborate on the same memory space.

### Scenario: Cline (dev) + Claude (review)

For two agents to collaborate, just create **two different tokens**:

1. Create the token for Cline (`admin_create_token name="cline-dev"`)
2. Create the token for Claude (`admin_create_token name="claude-review"`)
3. Configure each agent with its own token

The agent's identity is **automatically derived from its token** every time it calls `live_note` or `bank_consolidate`. They don't need to specify it.

### Agent-to-agent communication

Agents don't talk to each other directly. They communicate **through the shared space**:

```
Cline  → live_note(category="question", content="Should we support CSV?")
Claude → live_read(category="question")  ← sees Cline's question
Claude → live_note(category="decision", content="No, JSON only")
Cline  → live_read(category="decision")  ← sees Claude's answer
```

### Per-agent consolidation

Each agent consolidates **its own notes** without interfering with the others':

```
Cline  → bank_consolidate(space_id="my-project")  # Only consolidates cline-dev's notes
Claude → bank_consolidate(space_id="my-project")  # Only consolidates claude-review's notes
```

If an agent has **admin** rights, it can consolidate everyone's notes by calling `bank_consolidate` (which, for an admin, defaults to processing everyone).

---

## 🔍 Troubleshooting

### Cline doesn't see Live Memory tools

1. Check the server is running: `curl http://localhost:8080/health`
2. Check the JSON syntax in `cline_mcp_settings.json` (no trailing comma)
3. Reload VS Code (`Ctrl+Shift+P` → "Developer: Reload Window")
4. In Cline's MCP tab, check whether `live-memory` appears in red (connection error)

### "401 Unauthorized" error

- Token is wrong or revoked
- Make sure the header is `"Authorization": "Bearer lm_..."` (with the `lm_` prefix)
- The bootstrap key works for tests, but create a real token for normal use

### "Access denied to space" error

The token is restricted to certain spaces (`space_ids`). Either:
- Create a token without space restriction (empty `space_ids` parameter)
- Or add the space to the token: `admin_update_token(token_hash, space_ids="my-project", action="add")`

### Cline doesn't use Live Memory on its own

Add explicit **Custom Instructions** (see [Step 5](#-step-5--give-cline-its-instructions)). Without instructions, Cline doesn't know it should use these tools.

### Timeout error / Consolidation fails after 60 seconds

By default, Cline and Claude Desktop interrupt MCP requests after 60 seconds, which is often too short for a consolidation (the LLM can take several minutes).

1. Make sure you added `"timeout": 600` in your agent's MCP configuration, in line with the server timeout set in your `.env`.
2. You can follow the actual progress server-side in the logs:

```bash
docker compose logs -f live-mem-service --tail 20
```

### MCP won't connect behind a VPN

If Live Memory is on a remote server, check that:
- Port 443 (HTTPS) or 8080 (HTTP) is reachable
- The URL in the Cline config is correct (with `/mcp` at the end)
- Manual test: `curl -H "Authorization: Bearer lm_..." https://your-server/mcp`

---

## 🖥️ With Claude Desktop

Configuration is similar. Edit the `claude_desktop_config.json` file:

| OS          | Location                                                          |
| ----------- | ----------------------------------------------------------------- |
| **macOS**   | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| **Windows** | `%APPDATA%\Claude\claude_desktop_config.json`                     |
| **Linux**   | `~/.config/Claude/claude_desktop_config.json`                     |

```json
{
  "mcpServers": {
    "live-memory": {
      "url": "http://localhost:8080/mcp",
      "headers": {
        "Authorization": "Bearer lm_YOUR_TOKEN_HERE"
      },
      "timeout": 600
    }
  }
}
```

> **⚠️ Don't forget the `timeout` parameter** to allow long processing times during consolidation.

Restart Claude Desktop after the change. The 38 Live Memory tools will appear in the available tools list.

---

## 📊 Summary

| Step      | Action                                          | Time       |
| --------- | ----------------------------------------------- | ---------- |
| 1         | Start Live Memory (`docker compose up -d`)      | 1 min      |
| 2         | Create a token (`mcp_cli.py token create`)      | 30 sec     |
| 3         | Configure Cline (`cline_mcp_settings.json`)     | 2 min      |
| 4         | Create a space (`space_create`)                 | 30 sec     |
| 5         | Add the Custom Instructions                     | 2 min      |
| **Total** | **Ready to use**                                | **~6 min** |

---

*Live Memory integration guide v1.2.0 — [Full documentation](README.en.md)*
