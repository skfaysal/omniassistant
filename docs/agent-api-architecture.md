# Agent API — Architecture & Flow

## Overview

The agent API is a **FastAPI + LangGraph** server that exposes a multi-agent system over HTTP.
It is started by running `src/run_service.py` and listens on `http://localhost:8080` by default.

A Streamlit UI (`src/streamlit_app.py`) connects to this server as a client using `AgentClient`.

---

## Directory Map

```
src/
├── run_service.py              Entry point — boots Uvicorn
├── run_agent.py                Dev utility — invoke agent directly (no HTTP)
├── run_client.py               Dev utility — invoke agent over HTTP
├── streamlit_app.py            Chat UI (Streamlit)
│
├── service/
│   ├── __init__.py             Re-exports `app`
│   ├── service.py              FastAPI app, all HTTP endpoints, lifespan
│   └── utils.py                LangChain → ChatMessage converters
│
├── agents/
│   ├── __init__.py             Public exports (get_agent, get_all_agent_info, etc.)
│   ├── agents.py               Agent registry + DEFAULT_AGENT
│   ├── orchestration_agent.py  Supervisor graph wiring math + research agents
│   ├── math_agent.py           StateGraph: arithmetic tools (add/subtract/multiply/divide)
│   ├── research_agent.py       StateGraph: web search tool
│   ├── safeguard.py            (Safety / guardrail utilities)
│   ├── tools.py                (Shared tool definitions)
│   ├── utils.py                CustomData helper for streaming custom events
│   └── prompts/
│       ├── math_agent_prompt.py
│       ├── research_agent_prompt.py
│       └── orchestration_prompt.py
│
├── core/
│   ├── __init__.py             Re-exports get_model, settings
│   ├── llm.py                  LLM factory (get_model) for all providers
│   └── settings.py             Pydantic-settings — reads all config from .env
│
├── memory/
│   ├── __init__.py             initialize_database() + initialize_store()
│   ├── sqlite.py               AsyncSqliteSaver + InMemoryStore (default)
│   ├── postgres.py             AsyncPostgresSaver + AsyncPostgresStore
│   └── mongodb.py              AsyncMongoDBSaver
│
├── schema/
│   ├── __init__.py
│   ├── schema.py               Pydantic models: UserInput, ChatMessage, Feedback, etc.
│   ├── models.py               LLM model name enums (OpenAI, Anthropic, etc.)
│   └── task_data.py            TaskData schema for custom streaming events
│
└── client/
    ├── __init__.py
    └── client.py               AgentClient — HTTP wrapper used by Streamlit + run_client.py
```

---

## Startup Flow — `python src/run_service.py`

### Step 1 — Module-level imports (synchronous, before server starts)

```
run_service.py
  ├── load_dotenv()
  │     reads .env → sets env vars into os.environ
  │
  ├── from core import settings
  │     pydantic-settings reads env vars into typed Settings object
  │     (HOST, PORT, DEFAULT_MODEL, DATABASE_TYPE, AUTH_SECRET, etc.)
  │
  └── uvicorn.run("service:app", ...)
        Python imports "service" module → service/__init__.py → service/service.py
        │
        ├── from agents import ...
        │     → agents/__init__.py → agents/agents.py
        │           → from agents.orchestration_agent import orchestration_agent
        │                 │
        │                 ├── from agents.math_agent import math_agent
        │                 │     ├── get_model(DEFAULT_MODEL)     builds LLM client
        │                 │     ├── model.bind_tools([add, subtract, multiply, divide])
        │                 │     ├── StateGraph(MessagesState).compile()
        │                 │     └── math_agent.name = "sub-agent-math_expert"
        │                 │
        │                 ├── from agents.research_agent import research_agent
        │                 │     ├── get_model(DEFAULT_MODEL)
        │                 │     ├── model.bind_tools([web_search])
        │                 │     ├── StateGraph(MessagesState).compile()
        │                 │     └── research_agent.name = "sub-agent-research_expert"
        │                 │
        │                 └── create_supervisor([research_agent, math_agent], ...).compile()
        │                       orchestration_agent is ready (no checkpointer yet)
        │
        └── FastAPI app = FastAPI(lifespan=lifespan)
              lifespan is registered but NOT called yet
```

> All three compiled graphs (math, research, orchestration) are built once at import time
> and reused for every request. The LLM clients are also cached via `@cache` in `llm.py`.

---

### Step 2 — Uvicorn binds the port

```
Uvicorn starts → binds 0.0.0.0:8080
  └── triggers FastAPI lifespan() startup
```

---

### Step 3 — `lifespan()` startup (`service.py:67`)

```
lifespan(app)
  │
  ├── initialize_database()
  │     reads settings.DATABASE_TYPE (default: sqlite)
  │     returns AsyncSqliteSaver context manager
  │     → opens SQLite connection to settings.SQLITE_DB_PATH
  │
  ├── initialize_store()
  │     returns InMemoryStore wrapped in async context manager
  │     (Postgres store available if DATABASE_TYPE=postgres)
  │
  ├── saver.setup()
  │     creates SQLite tables:
  │       - checkpoints       (conversation state snapshots)
  │       - checkpoint_blobs  (message content)
  │       - checkpoint_writes (pending writes / in-flight steps)
  │
  └── for each agent in registry ("orchestration-agent"):
        agent.checkpointer = saver   ← thread-scoped memory (per thread_id)
        agent.store = store          ← long-term cross-thread memory

  yield  ←── server is READY
              logs: "Application startup complete"
              logs: "Uvicorn running on http://0.0.0.0:8080"
```

---

## Agent Architecture

### The Three Graphs

```
orchestration_agent  (supervisor)
  │
  ├── sub-agent-math_expert      (StateGraph)
  │     nodes:  agent → tools → agent (loop)
  │     tools:  add, subtract, multiply, divide
  │     prompt: MATH_AGENT_SYSTEM_PROMPT
  │
  └── sub-agent-research_expert  (StateGraph)
        nodes:  agent → tools → agent (loop)
        tools:  web_search
        prompt: RESEARCH_AGENT_SYSTEM_PROMPT
```

### Sub-agent Internal Loop (ReAct pattern)

```
Entry → [agent node]
              │
         LLM decides:
              ├── tool needed? → [tools node] → ToolNode executes → back to [agent node]
              └── done?        → END
```

### Orchestration / Supervisor Flow

```
User message arrives at orchestration_agent
  │
  ├── Supervisor LLM reads ORCHESTRATION_SYSTEM_PROMPT
  │     decides which sub-agent to delegate to
  │
  ├── Handoff tool call: transfer_to_math_expert  OR  transfer_to_research_expert
  │     → enters sub-agent node (runs the sub-agent's own ReAct loop)
  │
  ├── Sub-agent runs to completion
  │     → transfer_back_to_supervisor handback tool
  │
  └── Supervisor receives result, produces final answer
```

---

## HTTP Endpoints

All endpoints except `/health` require a Bearer token if `AUTH_SECRET` is set in `.env`.

### `GET /health`

Health check. Returns Langfuse connectivity status if tracing is enabled.

```json
{ "status": "ok", "langfuse": "connected" }
```

---

### `GET /info`

Returns service metadata: available agents, models, and defaults.

```json
{
  "agents": [{ "key": "orchestration-agent", "description": "..." }],
  "models": ["claude-haiku-4-5", "gpt-4o-mini", ...],
  "default_agent": "orchestration-agent",
  "default_model": "claude-haiku-4-5"
}
```

> This is the first call `AgentClient` makes on initialisation (Streamlit startup).

---

### `POST /invoke` · `POST /{agent_id}/invoke`

Send a message, wait for the **complete final response** (blocking).

**Request:**
```json
{
  "message": "What is 15% of 164000?",
  "thread_id": "abc-123",
  "user_id": "user-456",
  "model": "claude-haiku-4-5",
  "agent_config": {},
  "stream_tokens": true
}
```

**Response:** a single `ChatMessage`
```json
{
  "type": "ai",
  "content": "15% of 164,000 is 24,600.",
  "run_id": "...",
  "tool_calls": [],
  "response_metadata": {}
}
```

**Internal flow:**
```
invoke()
  └── _handle_input()
        ├── generate run_id (uuid7), thread_id, user_id
        ├── build RunnableConfig { thread_id, user_id, model, callbacks }
        ├── agent.aget_state() → check SQLite for interrupted tasks
        └── return input dict or Command(resume=...) if interrupted
  └── agent.ainvoke(stream_mode=["updates","values"])
        wait for all steps to complete
        return last message from state
```

---

### `POST /stream` · `POST /{agent_id}/stream`

Same as `/invoke` but returns a **Server-Sent Events (SSE)** stream.

**SSE event types:**

| Event | When | Payload |
|---|---|---|
| `token` | LLM streaming a word | `{ "type": "token", "content": "Hello" }` |
| `message` | A complete message (AI, tool call, tool result) | `{ "type": "message", "content": { ChatMessage } }` |
| `error` | Something went wrong | `{ "type": "error", "content": "..." }` |
| `[DONE]` | Stream complete | literal string `[DONE]` |

**Internal flow:**
```
stream()
  └── StreamingResponse(message_generator())
        └── agent.astream(stream_mode=["updates","messages","custom"], subgraphs=True)
              │
              each event is a tuple: (node_path, stream_mode, event)  [with subgraphs=True]
              │
              ├── stream_mode == "updates"
              │     for each node update:
              │       if node contains "supervisor" or "sub-agent" → special filtering
              │       otherwise → extract messages from state update
              │       yield as SSE "message" events
              │
              ├── stream_mode == "messages"  (token streaming)
              │     only AIMessageChunk, skip tool_use content
              │     yield as SSE "token" events
              │
              └── stream_mode == "custom"
                    custom events dispatched via CustomData.dispatch()
                    yield as SSE "message" events
```

---

### `POST /feedback`

Record thumbs up/down or star ratings to LangSmith.

```json
{
  "run_id": "...",
  "key": "human-feedback-stars",
  "score": 0.8,
  "kwargs": { "comment": "Great answer" }
}
```

---

### `POST /history`

Retrieve full conversation history for a `thread_id` from the SQLite checkpointer.

```json
{ "thread_id": "abc-123" }
```

Returns `ChatHistory` with all messages replayed from the checkpoint.

---

## Memory System

| Layer | Backend | Scope | Purpose |
|---|---|---|---|
| **Checkpointer** | SQLite (default) / Postgres / MongoDB | per `thread_id` | Conversation history, graph state snapshots, interrupt resumption |
| **Store** | InMemory (default) / Postgres | per `user_id` | Long-term facts, preferences across threads |

The checkpointer is what makes multi-turn conversation work: every graph step is persisted,
so the next message in the same `thread_id` picks up exactly where it left off.

---

## Configuration (`.env`)

Key settings read by `core/settings.py`:

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required for Claude models |
| `OPENAI_API_KEY` | — | Required for GPT models |
| `DEFAULT_MODEL` | `claude-haiku-4-5` | Model used when client does not specify |
| `HOST` | `0.0.0.0` | Bind address for Uvicorn |
| `PORT` | `8080` | Port for Uvicorn |
| `AGENT_URL` | `http://localhost:8080` | URL Streamlit connects to (must be localhost on Windows) |
| `DATABASE_TYPE` | `sqlite` | `sqlite` / `postgres` / `mongo` |
| `SQLITE_DB_PATH` | `./data/agent.db` | SQLite file path |
| `AUTH_SECRET` | — | If set, all requests require `Authorization: Bearer <secret>` |
| `LANGSMITH_TRACING` | `true` | Enable LangSmith tracing |
| `LANGFUSE_TRACING` | `false` | Enable Langfuse tracing |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

---

## Request Lifecycle (end-to-end)

```
Browser/Streamlit
      │  POST /stream  { message, thread_id, user_id, model }
      ▼
  Uvicorn (ASGI)
      │
      ▼
  verify_bearer()          check AUTH_SECRET header
      │
      ▼
  stream()                 service.py
      │
      ▼
  _handle_input()
      ├── build RunnableConfig
      └── check SQLite for interrupted state
      │
      ▼
  orchestration_agent.astream()    LangGraph Pregel execution
      │
      ├── Supervisor LLM → decides route
      ├── Handoff → math_agent OR research_agent
      │     └── sub-agent ReAct loop (LLM ↔ tools)
      ├── Handback → supervisor
      └── Supervisor final answer
      │
      ▼  (each step yields stream events)
  message_generator()      filters + formats events
      │
      ▼  SSE events
  Browser/Streamlit
      └── renders tokens + tool calls in real time
```

---

## Key Design Decisions

- **Graphs compiled at import time** — fast per-request performance; LLM clients are `@cache`d.
- **Checkpointer injected at startup** — the compiled graph is stateless; the saver/store are attached once in `lifespan()` and shared across all requests.
- **`subgraphs=True` in astream** — necessary to receive events from sub-agents (math, research) inside the supervisor graph.
- **`output_mode="full_history"`** — ensures sub-agent messages are included in history replays, not just the supervisor's final message.
- **`add_handoff_back_messages=True`** — the supervisor library inserts explicit "returning control" tool messages, which the Streamlit UI uses to close sub-agent status containers.
- **SSE over WebSocket** — simpler to implement and proxy; `[DONE]` sentinel signals stream end.
- **`WindowsSelectorEventLoopPolicy`** — required on Windows to prevent `RuntimeError: Event loop is closed` from async DB drivers (psycopg).
