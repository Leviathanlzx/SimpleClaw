# SimpleClaw 🦞

A simplified, easy-to-understand AI agent framework — designed for learning and light use.

This build is derived from and inspired by the following independent projects:

* **[OpenClaw](https://github.com/OpenClaw/OpenClaw)**: The open-source AI assistant and automation platform.
* **[Nanobot](https://github.com/HKUDS/nanobot)**: The modular execution engine that serves as the technical backbone for this simplified version.

---

## Table of Contents

- [Quick Start](#quick-start)
- [Architecture Overview](#architecture-overview)
- [Project Structure](#project-structure)
- [Core Concepts](#core-concepts)
  - [Message Bus](#1-message-bus-buspy)
  - [Channels](#2-channels-channelspy)
  - [Agent Loop](#3-agent-loop-agentpy)
  - [Tools](#4-tools-toolspy)
  - [Memory](#5-memory-memorypy)
  - [Context Builder](#6-context-builder-contextpy)
  - [Skills](#7-skills-skillspy)
  - [Cron Service](#8-cron-service-cronpy)
  - [Heartbeat Service](#9-heartbeat-service-heartbeatpy)
- [Message Lifecycle](#message-lifecycle)
- [Cron & Heartbeat: Direct Processing](#cron--heartbeat-direct-processing)
- [Session Management](#session-management)
- [Configuration](#configuration)
- [Development](#development)

---

## Quick Start

### Setup with uv

1. Install [uv](https://github.com/astral-sh/uv).
2. Install dependencies:
   ```bash
   uv sync
   ```
3. Run the agent:
   ```bash
   uv run python main.py
   ```

### Setup with pip (Standard)

1. (Optional) Create a virtual environment:
   ```bash
   python -m venv venv
   # Windows
   .\venv\Scripts\activate
   # Linux/Mac
   source venv/bin/activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the agent:
   ```bash
   python main.py
   ```

### Docker

```bash
docker compose up --build
```

---

## Architecture Overview

SimpleClaw follows a **Bus-driven, Channel-Agent-Tool** architecture. All communication flows through a central message bus, keeping IO (channels) cleanly separated from logic (agent).

```mermaid
graph TB
    subgraph Channels ["🔌 Channels (IO)"]
        CLI["CLI"]
        TG["Telegram"]
        WC["WeCom"]
    end

    subgraph Bus ["📬 Message Bus"]
        IQ["Inbound Queue"]
        OQ["Outbound Queue"]
    end

    subgraph Agent ["🧠 Agent Core"]
        AL["AgentLoop"]
        CB["ContextBuilder"]
        TL["ToolRegistry"]
        PR["LLM Provider"]
    end

    subgraph Persistence ["💾 Persistence"]
        MEM["MemoryStore"]
        SK["SkillsLoader"]
    end

    subgraph Background ["⏰ Background Services"]
        CRON["CronService"]
        HB["HeartbeatService"]
    end

    CLI -->|"InboundMessage"| IQ
    TG -->|"InboundMessage"| IQ
    WC -->|"InboundMessage"| IQ

    IQ -->|"consume"| AL
    AL -->|"OutboundMessage"| OQ

    OQ -->|"dispatch"| CLI
    OQ -->|"dispatch"| TG
    OQ -->|"dispatch"| WC

    AL --- CB
    AL --- TL
    AL --- PR
    AL --- MEM
    CB --- MEM
    CB --- SK

    CRON -->|"process_direct()"| AL
    HB -->|"process_direct()"| AL
    CRON -.->|"OutboundMessage"| OQ
    HB -.->|"OutboundMessage"| OQ
```

---

## Project Structure

```
SimpleClaw/
├── main.py                  # Entry point, wires all components and starts services
├── core/
│   ├── bus.py               # Message bus with Inbound/Outbound async queues
│   ├── agent.py             # AgentLoop: think-act cycle + process_direct
│   ├── provider.py          # LLM backends (OpenAI/OpenRouter + Mock)
│   ├── tools.py             # ToolRegistry + built-in tools (exec, read, write...)
│   ├── memory.py            # MemoryStore + Session + LLM-driven consolidation
│   ├── context.py           # ContextBuilder: assembles system prompt and messages
│   ├── skills.py            # SkillsLoader: discovers SKILL.md definitions from disk
│   ├── config.py            # ConfigLoader with dataclass-based configuration
│   ├── cron.py              # CronService: scheduled task execution
│   ├── heartbeat.py         # HeartbeatService: periodic background checks
│   └── channels/
│       ├── base.py          # Abstract BaseChannel interface
│       ├── cli.py           # CLI channel (stdin/stdout)
│       ├── telegram.py      # Telegram Bot channel
│       └── wecom.py         # WeCom channel
├── workspace/               # Runtime data (mounted as volume in Docker)
│   ├── config.json          # Runtime configuration
│   ├── SOUL.md              # Agent identity and personality
│   ├── USER.md              # User context and preferences
│   ├── TOOLS.md             # Tool usage guidelines
│   ├── AGENTS.md            # Sub-agent registry
│   ├── HEARTBEAT.md         # Background tasks for heartbeat
│   ├── memory/
│   │   ├── MEMORY.md        # Long-term memory (overwritten on consolidation)
│   │   └── temp.json        # Session persistence backup
│   ├── history/
│   │   ├── HISTORY.md       # Consolidation summaries (append-only)
│   │   └── FULL_HISTORY.md  # Full debug log
│   └── skills/              # Skill definitions (SKILL.md per subfolder)
├── template/                # Default files copied into workspace on first run
├── skills/                  # Built-in skill definitions (copied to workspace)
├── configs/                 # Configuration templates
└── reference/               # Nanobot reference implementation
```

---

## Core Concepts

### 1. Message Bus (`bus.py`)

The **MessageBus** is two async queues that decouple all IO from the agent:

```mermaid
graph LR
    C1["Channel A"] -->|"publish_inbound()"| IQ["Inbound Queue"]
    C2["Channel B"] -->|"publish_inbound()"| IQ
    IQ -->|"consume_inbound()"| A["AgentLoop"]
    A -->|"publish_outbound()"| OQ["Outbound Queue"]
    OQ -->|"consume_outbound()"| D["Dispatcher"]
    D --> C1
    D --> C2
```

- **InboundMessage**: `channel` + `chat_id` + `content` + `metadata`
- **OutboundMessage**: `channel` + `chat_id` + `content` + `metadata`

Every channel uses the same message format, so the agent doesn't need to know *which* platform a message came from.

### 2. Channels (`channels/`)

Channels are the agent's "senses" — they bridge external platforms to the bus:

| Channel | Role | File |
|---------|------|------|
| **CLI** | stdin/stdout for local development | `channels/cli.py` |
| **Telegram** | Telegram Bot API polling | `channels/telegram.py` |
| **WeCom** | 企业微信 webhook | `channels/wecom.py` |

All channels extend `BaseChannel` which requires two methods:
- `start()` — listen for incoming messages, publish to `bus.inbound`
- `send(msg)` — deliver an outbound message to the platform

### 3. Agent Loop (`agent.py`)

The **AgentLoop** is the brain. It runs the **think-act cycle**:

```mermaid
graph TD
    START["Receive Message"] --> CTX["Build System Prompt<br/>(ContextBuilder)"]
    CTX --> BUDGET["Check Token Budget<br/>(maybe consolidate)"]
    BUDGET --> LLM["Call LLM<br/>(Provider)"]
    LLM --> TC{"Tool calls?"}
    TC -->|"Yes"| EXEC["Execute Tools<br/>(ToolRegistry)"]
    EXEC --> LLM
    TC -->|"No"| SAVE["Save to Session<br/>& Memory"]
    SAVE --> OUT["Publish OutboundMessage"]
    OUT --> START
```

Key methods:
- **`run()`** — main loop, consumes from bus inbound
- **`process_direct(content, session_key, channel, chat_id)`** — direct invocation (used by cron/heartbeat), bypasses the bus entirely
- **`_think_and_act()`** — inner LLM loop: call → tools → call → ... → final answer
- **`_maybe_consolidate()`** — shrinks context when token budget exceeded

### 4. Tools (`tools.py`)

The **ToolRegistry** manages callable functions exposed to the LLM:

```mermaid
graph LR
    LLM["LLM Response"] -->|"tool_calls"| TR["ToolRegistry.execute()"]
    TR --> T1["get_time()"]
    TR --> T2["exec_shell()"]
    TR --> T3["read_file()"]
    TR --> T4["write_file()"]
    TR --> T5["list_dir()"]
    TR --> T6["cron()"]
    TR --> T7["save_memory()"]
    TR -->|"result"| LLM
```

The registry also holds **channel context** (`set_context(channel, chat_id)`), which tools like `cron` read from automatically — eliminating the need for the LLM to manually pass routing parameters.

### 5. Memory (`memory.py`)

Two-layer persistent memory system:

```mermaid
graph TD
    subgraph Runtime ["Runtime"]
        S["Session<br/>(in-memory messages)"]
    end
    subgraph Disk ["Disk (workspace/)"]
        MEM["memory/MEMORY.md<br/>Long-term facts"]
        HIST["history/HISTORY.md<br/>Consolidation summaries"]
        FULL["history/FULL_HISTORY.md<br/>Full debug log"]
        TEMP["memory/temp.json<br/>Session backup"]
    end

    S -->|"consolidate()"| MEM
    S -->|"consolidate()"| HIST
    S -->|"append_full_log()"| FULL
    S -->|"save_session()"| TEMP
    TEMP -->|"load_full_session()"| S
```

**Consolidation flow**: When session tokens exceed the budget, old messages are summarized by the LLM into `MEMORY.md` (facts) + `HISTORY.md` (timeline), then removed from the active session.

### 6. Context Builder (`context.py`)

Assembles everything the LLM needs to see:

```
System Prompt = Identity + Bootstrap Files + Memory + Skills
                   │            │              │         │
                   │    SOUL.md, USER.md       │    Available skills
                   │    TOOLS.md, AGENTS.md    │    summary + always-on
                   │    HEARTBEAT.md           │    skill content
                   │                      MEMORY.md
                   │
              Runtime info, workspace paths,
              platform policy, guidelines
```

Each turn, the **last user message** is prepended with runtime context (current time, channel, chat_id) so the LLM always knows *when* and *where* it's responding.

### 7. Skills (`skills.py`)

Skills are **markdown-defined capabilities** that extend the agent without code changes:

```
workspace/skills/
├── cron/
│   └── SKILL.md          # Frontmatter (name, description) + instructions
├── memory/
│   └── SKILL.md
├── weather/
│   └── SKILL.md
└── ...
```

The `SkillsLoader` reads all `SKILL.md` files, parses frontmatter, and injects summaries into the system prompt. Skills marked `always: true` have their full content loaded every turn.

### 8. Cron Service (`cron.py`)

Scheduled task execution with a **callback pattern**:

```mermaid
sequenceDiagram
    participant User
    participant Agent
    participant CronService
    participant Bus

    User->>Agent: "每天早上9点提醒我站会"
    Agent->>CronService: add_task(message, cron_expr="0 9 * * *")<br/>auto-captures channel context

    Note over CronService: ⏰ 09:00 triggers...

    CronService->>Agent: on_job callback → process_direct()<br/>independent session "cron:abc123"
    Agent->>Agent: think-act cycle (LLM + tools)
    Agent-->>CronService: response text
    CronService->>Bus: publish_outbound → target channel
    Bus->>User: "早上好！今天的站会马上开始了..."
```

Key design choices:
- **Auto-context capture**: When adding a task, `target_channel` and `target_chat_id` are captured from `ToolRegistry._context` — LLM doesn't need to guess
- **Callback pattern**: Tasks fire via `on_job` callback → `agent.process_direct()`, not through the inbound bus
- **Independent sessions**: Each task gets its own session (`cron:{task_id}`) to avoid polluting user conversations

### 9. Heartbeat Service (`heartbeat.py`)

Periodic background check with **smart target selection**:

```mermaid
sequenceDiagram
    participant HB as HeartbeatService
    participant LLM
    participant Agent
    participant Bus

    Note over HB: ⏰ Every 30min...

    HB->>HB: Read HEARTBEAT.md
    HB->>LLM: Phase 1 (decide): skip or run?
    LLM-->>HB: action: "run", tasks: "..."

    HB->>HB: _pick_target(): find best channel<br/>(prefer telegram/wecom over cli)
    HB->>Agent: process_direct(tasks, "heartbeat:system", channel, chat_id)
    Agent-->>HB: response
    HB->>Bus: publish_outbound → target channel
```

- **Two-phase design**: Phase 1 is a cheap LLM call (skip/run decision). Phase 2 only runs if there are active tasks.
- **Smart target**: Scans active sessions to find the best external channel (Telegram, WeCom). Falls back to CLI.

---

## Message Lifecycle

Here is the complete journey of a user message through the system:

```mermaid
graph TD
    A["👤 User types in Telegram"] --> B["TelegramChannel.start()<br/>polls for updates"]
    B --> C["bus.publish_inbound(<br/>InboundMessage)"]
    C --> D["AgentLoop.run()<br/>consume_inbound()"]
    D --> E["tools.set_context(<br/>channel, chat_id)"]
    E --> F["ContextBuilder.<br/>build_system_prompt()"]
    F --> G["_maybe_consolidate()<br/>(if over token budget)"]
    G --> H["session.add_message(<br/>'user', content)"]
    H --> I["_think_and_act()"]

    I --> J["ContextBuilder.<br/>build_messages()"]
    J --> K["Provider.chat()<br/>(call LLM)"]
    K --> L{"tool_calls?"}
    L -->|"Yes"| M["ToolRegistry.execute()"]
    M --> N["Append tool result"]
    N --> K
    L -->|"No, final answer"| O["Save to session<br/>+ memory logs"]
    O --> P["bus.publish_outbound(<br/>OutboundMessage)"]
    P --> Q["_channel_dispatcher()"]
    Q --> R["TelegramChannel.send()"]
    R --> S["📱 User sees reply"]
```

---

## Cron & Heartbeat: Direct Processing

Cron and Heartbeat use **`process_direct()`** instead of publishing to the inbound bus. This solves the core problem: messages pushed to `bus.inbound` would compete with user messages and responses could be lost or misrouted.

```mermaid
graph LR
    subgraph Old ["❌ Old: via Bus"]
        CRON1["CronService"] -->|"publish_inbound"| BUS1["Bus Inbound"]
        BUS1 --> AGENT1["AgentLoop.run()"]
        AGENT1 -->|"response"| BUS2["Bus Outbound"]
    end

    subgraph New ["✅ New: Direct Processing"]
        CRON2["CronService"] -->|"on_job callback"| PD["agent.process_direct()"]
        PD -->|"independent session"| LLM2["LLM + Tools"]
        LLM2 -->|"response"| CRON2
        CRON2 -->|"publish_outbound"| BUS3["Bus Outbound"]
    end
```

Benefits:
1. **No bus contention** — cron/heartbeat don't compete with user messages
2. **Independent sessions** — each task has its own conversation history
3. **Correct routing** — responses go to the right channel every time
4. **Caller controls delivery** — the callback decides *how* and *where* to publish

---

## Session Management

Sessions are keyed by `"channel:chat_id"` and stored in `AgentLoop._sessions`:

| Session Key | Source | Purpose |
|-------------|--------|---------|
| `cli:user1` | CLI input | Primary interactive session |
| `telegram:12345` | Telegram user | Per-user Telegram session |
| `wecom:zhang_san` | WeCom user | Per-user WeCom session |
| `cron:abc123` | CronService | Isolated per-task session |
| `heartbeat:system` | HeartbeatService | Heartbeat processing session |

The primary session (`cli:user1`) is persisted to `temp.json` and restored on restart. Cron/heartbeat sessions are ephemeral.

---

## Configuration

Copy and edit the config file:

```bash
cp configs/config.example.json workspace/config.json
```

Key configuration sections:

```jsonc
{
  "llm": {
    "provider": "openrouter",
    "api_key": "sk-...",              // Your OpenRouter API key
    "model": "openai/gpt-4o-mini",    // Any OpenRouter model
    "base_url": "https://openrouter.ai/api/v1"
  },
  "agent": {
    "name": "SimpleClaw",
    "system_prompt": "",               // Optional extra instructions
    "max_loops": 10                    // Max tool-call rounds per message
  },
  "heartbeat": {
    "enabled": true,
    "interval_s": 1800                 // Check HEARTBEAT.md every 30 min
  },
  "telegram": {
    "enabled": false,
    "token": "",                       // Telegram Bot token
    "allowed_user_ids": []             // Empty = accept all
  },
  "wecom": {
    "enabled": false,
    "bot_id": "",
    "secret": ""
  }
}
```

---

## Development

- Add dependencies: `uv add <package>`
- Update dependencies: `uv lock --upgrade`
- Update `requirements.txt`: `uv export --format requirements-txt -o requirements.txt`

### Adding a new Channel

1. Create `core/channels/my_channel.py` extending `BaseChannel`
2. Implement `start()` (listen → `bus.publish_inbound`) and `send(msg)` (deliver outbound)
3. Wire it in `main.py` and add to `_channel_dispatcher()`

### Adding a new Tool

1. Write the function in `core/tools.py`
2. Register it in `setup_tools()` with name, description, and JSON Schema parameters
3. The LLM will automatically discover and use it

### Adding a new Skill

1. Create `workspace/skills/my-skill/SKILL.md` with frontmatter:
   ```markdown
   ---
   name: my-skill
   description: What this skill does
   ---
   Instructions for the agent...
   ```
2. The agent discovers it automatically on next startup
