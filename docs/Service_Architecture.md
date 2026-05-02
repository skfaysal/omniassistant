# Service Architecture — End-to-End Flow

## Abstracted View

```
User types in Streamlit
        │
        ▼
  AgentClient (httpx)
  POST /{agent_id}/stream
        │
        ▼
  FastAPI service.py
  ┌─────────────────────────┐
  │  auth → _handle_input   │
  │  agent.astream()        │
  │  message_generator()    │
  │  SSE yield              │
  └─────────────────────────┘
        │  text/event-stream (SSE)
        ▼
  AgentClient._parse_stream_line()
  yields ChatMessage | str tokens
        │
        ▼
  draw_messages() in Streamlit
  renders tokens live, then full messages
```

---

## Components

### 1. `schema/` — Shared Data Contracts

All communication between layers uses these Pydantic models:

| Model | Purpose |
|---|---|
| `UserInput` | message + thread_id + user_id + model + agent_config |
| `StreamInput` | extends UserInput, adds `stream_tokens: bool` |
| `ChatMessage` | unified message envelope: type (human/ai/tool/custom), content, tool_calls, run_id |
| `ServiceMetadata` | returned by `/info` — available agents, models, defaults |
| `Feedback` | run_id + key + score, forwarded to LangSmith |

---

### 2. `agents/` — LangGraph Graphs

Each agent is a compiled LangGraph `StateGraph` (or `@entrypoint` Pregel).  
`agents.py` is the **registry** — a dict mapping string keys to `Agent(description, graph_like)`.

`get_agent(agent_id)` returns the compiled graph.  
`get_all_agent_info()` returns the list of `AgentInfo` for the `/info` endpoint.

Agents use:
- `core.get_model(settings)` — to pick the LLM
- `core.settings` — to read env config
- `schema` — for shared types like `AgentInfo`, `TaskData`

---

### 3. `memory/` — Checkpointers & Stores

During **startup** (`lifespan`), the service initialises two memory backends and attaches them to every agent graph:

| Component | What it does | Scope |
|---|---|---|
| `checkpointer` (saver) | Persists message history per `thread_id` | Short-term / per-conversation |
| `store` | Key-value store for facts across threads | Long-term / per-user |

Backends are selected via env vars:
- SQLite (default, local dev)
- PostgreSQL (production)
- MongoDB

Without these, agents would have no memory between turns.

---

### 4. `service/service.py` — The FastAPI App

This is the core of the system. Here is what it does in detail.

#### Startup — `lifespan()`

```python
@asynccontextmanager
async def lifespan(app):
    async with initialize_database() as saver, initialize_store() as store:
        await saver.setup()
        await store.setup()
        for agent in get_all_agent_info():
            graph = get_agent(agent.key)
            graph.checkpointer = saver   # attach short-term memory
            graph.store = store           # attach long-term memory
        yield
```

Runs once on boot. Every agent graph gets memory wired in before the server accepts requests.

#### Auth — `verify_bearer()`

If `AUTH_SECRET` is set in env, every router request must carry `Authorization: Bearer <secret>`.  
If not set, auth is skipped entirely (open service).

---

#### API Endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | Liveness check, optionally checks Langfuse connection |
| GET | `/info` | Returns available agents, models, defaults |
| POST | `/invoke` or `/{agent_id}/invoke` | Run agent, wait for full response, return last `ChatMessage` |
| POST | `/stream` or `/{agent_id}/stream` | Run agent, stream SSE events back in real time |
| POST | `/feedback` | Forward thumbs-up/down score to LangSmith |
| POST | `/history` | Return full message list for a `thread_id` |

---

#### `_handle_input()` — Shared Pre-processing

Called by both `/invoke` and `/stream` before touching the graph.

1. Generates a `run_id` (uuid7 for LangSmith ordering)
2. Resolves `thread_id` and `user_id` (creates new ones if absent)
3. Builds `RunnableConfig` with `configurable = {thread_id, user_id, model, ...agent_config}`
4. Optionally attaches `LangfuseCallbackHandler` for tracing
5. Checks existing graph state for **interrupts**:
   - If the agent is paused at an interrupt → wraps message in `Command(resume=message)` to continue
   - Otherwise → wraps message in `{"messages": [HumanMessage(...)]}`

This is how human-in-the-loop / approval flows work: the agent pauses, the next user message resumes it.

---

#### `/invoke` — Blocking Response

```python
response_events = await agent.ainvoke(**kwargs, stream_mode=["updates", "values"])
response_type, response = response_events[-1]
```

Waits for full completion. Returns a single `ChatMessage` (last AI message or interrupt value).  
Simple but loses all intermediate tool call steps.

---

#### `/stream` — SSE Streaming (the main path)

Returns a `StreamingResponse` backed by `message_generator()`.

```python
return StreamingResponse(message_generator(user_input, agent_id), media_type="text/event-stream")
```

Each yielded line is:
```
data: {"type": "token",   "content": "Hello"}
data: {"type": "message", "content": {ChatMessage JSON}}
data: {"type": "error",   "content": "..."}
data: [DONE]
```

---

#### `message_generator()` — Stream Processing Engine

This is the most complex part. It calls:

```python
async for stream_event in agent.astream(**kwargs,
    stream_mode=["updates", "messages", "custom"],
    subgraphs=True
):
```

LangGraph emits three kinds of events simultaneously:

| stream_mode | What it carries | Used for |
|---|---|---|
| `updates` | Full node output — AI messages, tool messages, interrupts | Complete messages to show in chat |
| `messages` | Raw `AIMessageChunk` objects token by token | Live token streaming |
| `custom` | Agent-defined custom data (e.g. background task progress) | Specialised UI updates |

**Processing `updates`:**
- Iterates each `(node_name, updates)` pair
- Special-cases `__interrupt__` nodes → wraps value in `AIMessage`
- Special-cases langgraph-supervisor nodes (filters handoff/handback tool messages to avoid duplicates)
- Converts `BaseMessage` → `ChatMessage` via `langchain_to_chat_message()`
- Yields `data: {"type": "message", ...}`

**Processing `messages` (token streaming):**
- Only if `stream_tokens=True`
- Drops non-`AIMessageChunk` events (tool nodes emit garbage here)
- Drops chunks tagged `skip_stream`
- Strips tool-use content blocks (Anthropic models stream these inline)
- Yields `data: {"type": "token", "content": "<token text>"}`

**Processing `custom`:**
- Passes event through directly as a `ChatMessage`

---

### 5. `client/client.py` — AgentClient

HTTP wrapper around the service. Streamlit uses this — it never talks to the FastAPI service directly.

Key methods:

| Method | Transport | Returns |
|---|---|---|
| `ainvoke()` | httpx async POST | single `ChatMessage` |
| `astream()` | httpx async streaming POST | `AsyncGenerator[ChatMessage \| str]` |
| `stream()` | httpx sync streaming POST | `Generator[ChatMessage \| str]` |
| `get_history()` | httpx sync POST | `ChatHistory` |
| `acreate_feedback()` | httpx async POST | void |

`_parse_stream_line()` decodes each SSE line:
- `"type": "token"` → yields the raw string (for live rendering)
- `"type": "message"` → validates into `ChatMessage` and yields it
- `"type": "error"` → yields an AI ChatMessage with the error text
- `"[DONE]"` → returns `None` to stop the generator

---

### 6. `streamlit_app.py` — Frontend

On load:
1. Creates/restores `user_id` from session state or URL params
2. Instantiates `AgentClient` (calls `/info` to fetch available agents/models)
3. Restores message history via `get_history()` if `thread_id` is in the URL

On user message submit:
1. Appends `HumanMessage` to session, renders it immediately
2. Calls `agent_client.astream(message, model, thread_id, user_id)`
3. Passes the async generator to `draw_messages(stream, is_new=True)`

#### `draw_messages()` — Rendering Logic

Consumes the `AsyncGenerator[ChatMessage | str]` from `AgentClient.astream()`:

- **`str` token** → appends to `streaming_content`, updates a `st.empty()` placeholder in real time
- **`ChatMessage` type=`"ai"`** → if streaming was in progress, replaces the placeholder with the final text; shows tool call expanders
- **`ChatMessage` type=`"tool"`** → fills in the matching tool call expander with the result
- **tool call with `transfer_to_*`** → recursively calls `handle_sub_agent_msgs()` to render sub-agent output in a nested status container

This is why tokens appear live and tool calls show as collapsible status boxes.

---

## Full Flow: User types → LLM responds in Streamlit

```
1. User types "What's the weather in Tokyo?" → st.chat_input()

2. streamlit_app.py
   └── agent_client.astream(message, thread_id=X, user_id=Y)

3. client.py  (AgentClient.astream)
   └── httpx POST /research-assistant/stream
       body: StreamInput{message, thread_id, user_id, stream_tokens=True}

4. service.py  (FastAPI /stream endpoint)
   └── verify_bearer()  ← auth check
   └── StreamingResponse(message_generator(...))

5. service.py  (_handle_input)
   └── build RunnableConfig{thread_id, user_id, run_id, callbacks}
   └── check agent state for interrupts
   └── wrap message → {"messages": [HumanMessage("What's the weather...")]}

6. LangGraph  (research_assistant graph)
   └── node: agent  → calls LLM with tools list
   └── LLM responds: AIMessage with tool_call{web_search, args={query: "Tokyo weather"}}
   └── node: tools  → executes web_search, returns ToolMessage
   └── node: agent  → calls LLM again with tool result
   └── LLM streams final answer token by token

7. service.py  (message_generator)
   stream_mode="updates":
     node=agent  → yields ChatMessage{type=ai, tool_calls=[web_search...]}
     node=tools  → yields ChatMessage{type=tool, content="22°C, sunny"}
     node=agent  → yields ChatMessage{type=ai, content="The weather in Tokyo is 22°C..."}
   stream_mode="messages":
     → yields token "The", " weather", " in", " Tokyo"...  as SSE type=token events

8. client.py  (astream)
   └── _parse_stream_line() on each SSE line
   └── yields: ChatMessage{ai, tool_calls} → ChatMessage{tool} → str tokens... → ChatMessage{ai, full}

9. streamlit_app.py  (draw_messages)
   ├── ChatMessage{ai, tool_calls}  → renders "🛠️ Tool Call: web_search" expander (running)
   ├── ChatMessage{tool}            → fills expander with "22°C, sunny" (complete)
   ├── str "The ", "weather ", ...  → streams into st.empty() placeholder live
   └── ChatMessage{ai, full text}   → replaces placeholder with final answer

10. User sees the answer appear word by word.
    A star-rating feedback widget appears below the message.
    Clicking it calls /feedback → LangSmith records the score against the run_id.
```
