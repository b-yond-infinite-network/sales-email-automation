# Antoni Test System (Email Classification + Routing)

This folder is an isolated scaffold copied from the existing project so you can build the new inbound-email automation workflow without modifying the original codebase.

## What was copied

- Core orchestration/runtime files:
  - `langgraph.json`
  - `docker-compose.yml`
  - `Dockerfile.agent`
  - `Dockerfile.email-poller`
  - `pyproject.toml`
  - `poetry.lock`
  - `.env.example`
- Agent modules copied into `src/agent/`:
  - `email_poller.py`
  - `RAG_graph.py`
  - `ingestion_graph.py`
  - `ingest.py`
  - `graph_schemas.py`
  - `config.py`
  - `server.py`
  - `minio_config.py`
  - `logger.py`
  - `auth.py`
  - `auth_routes.py`
  - `file_hash_manager.py`

## Important current gap

`langgraph.json` references:

- `./src/agent/email_ingestion_graph.py:email_ingestion_graph`

That file does **not** yet exist in this scaffold and should be the first new implementation file for the new workflow.

---

## Target workflow to build

1. **Pull**
   - `email_poller.py` monitors unread inbox emails via Microsoft Graph.
2. **Analyze**
   - Fetch full email body + attachments.
   - Extract attachment text (PDF/PPTX first).
3. **Classify**
   - Use OpenRouter LLM with **structured output** (`Pydantic`) to return:
     - `interested: bool`
     - `confidence: float`
     - `domain: str`
     - `suggested_recipient: Optional[str]`
     - `reasoning: str`
4. **Act**
   - If interested: forward to selected salesperson.
   - If not interested (or low confidence): send automatic response.
   - Mark email as read.

---

## Next development steps

### 1) Create `src/agent/email_ingestion_graph.py`

Implement LangGraph nodes in this order:

- `get_email_messages`
- `download_attachments`
- `extract_attachment_text`
- `retrieve_rag_context`
- `classify_email`
- `take_action`
- `mark_as_read`

Compile graph as `email_ingestion_graph` using `MemorySaver` checkpointer.

### 2) Extend state + schemas in `graph_schemas.py`

Add models for:

- `EmailClassificationDecision` (structured LLM output)
- `EmailIngestionState` additional fields:
  - `email_content`
  - `attachments_text`
  - `retrieved_context`
  - `interested`
  - `confidence`
  - `inquiry_domain`
  - `suggested_recipient`
  - `classification_reasoning`
  - `auto_reply_message`
  - `action_taken`

### 3) Add routing config in `config.py`

Add:

- `AUTO_FORWARD_IF_INTERESTED`
- `AUTO_REPLY_IF_NOT_INTERESTED`
- `DEFAULT_SALES_RECIPIENT`
- `SALES_ROUTING_JSON` (domain keyword → recipient email)

### 4) Update `.env` (or `.env.example`) for new settings

Add values for the four routing keys above.

### 5) Reuse RAG retrieval path from `RAG_graph.py`

For `retrieve_rag_context`:

- Use Weaviate similarity search with current embedding config.
- Build concise context snippets with source metadata.
- Pass those snippets into classification prompt.

### 6) Implement prompt engineering for classifier node

Use:

- **System prompt**: B-Yond expertise evaluator + routing assistant behavior.
- **User prompt**: include email content + attachment text + retrieved context + routing map.
- OpenAI-compatible parse call with `response_format=EmailClassificationDecision`.

### 7) Action node with Graph API

Implement two Graph calls:

- Forward message to salesperson (`/messages/{id}/forward`)
- Reply to sender (`/messages/{id}/reply`)

Select action based on classification output + config flags.

### 8) Enable tracing/debugging

Use LangSmith env already in `.env`:

- `LANGSMITH_TRACING=true`
- `LANGSMITH_API_KEY`
- `LANGSMITH_PROJECT=Antoni Test`

### 9) Validation checklist

- Import checks for `langgraph`, `openai`, `weaviate`, `msal`.
- Dry-run a known email ID through graph.
- Verify action path:
  - interested → forward + mark read
  - not interested → reply + mark read
- Confirm event traces in LangSmith.

---

## Suggested implementation order (fastest path)

1. Create `email_ingestion_graph.py` with stub nodes returning mock values.
2. Wire graph into `langgraph.json` target (same name already configured).
3. Replace stubs one-by-one with real Graph API + RAG + LLM calls.
4. Run `docker compose up --build` and test with a single inbox email.

---

## Notes

- This scaffold was created to avoid changing the original repository while building the new flow.
- Existing files in the original path were not modified by this scaffold setup.
