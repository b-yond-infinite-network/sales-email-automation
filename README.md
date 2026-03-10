# Email Automation — B-Yond Inbox Classifier

Polls a Microsoft 365 inbox via the Graph API and automatically classifies inbound form-submission emails using an LLM. Results are logged to an Excel file.

## How it works

```
Microsoft 365 Inbox
        │
        ▼
  email-poller          polls on a configurable interval
        │
        ▼
email-classification-agent   LangGraph graph hosted on langgraph-api
        │
        ├── extracts JSON payload from the email body
        ├── researches the company online
        └── returns structured classification (qualify/disqualify, company type, countries, etc.)
        │
        ▼
  Excel tracker         appends each result to output/email_classifications.xlsx
```

The poller also triggers the **email-ingestion-agent** graph for emails that contain attachments (PDFs, PowerPoints), which are extracted and passed to the LLM alongside the email body.

## Services

| Service | Description |
|---|---|
| `email-poller` | Polls the inbox, deduplicates seen emails, calls the classification graph |
| `langgraph-api` | Hosts the LangGraph graphs (`email-classification-agent`, `email-ingestion-agent`) |
| `langgraph-postgres` | Persistent state store for LangGraph checkpoints |
| `langgraph-redis` | In-memory state for LangGraph |

## Prerequisites

- Docker & Docker Compose
- A Microsoft 365 account with an **App Registration** in Azure AD (for Graph API access)
- An [OpenRouter](https://openrouter.ai) API key (or OpenAI-compatible endpoint)

## Setup

### 1. Azure App Registration

In Azure Active Directory, register an app and grant it the following **application** (not delegated) permissions:

- `Mail.Read`
- `Mail.ReadBasic`

Note down the **Client ID**, **Client Secret**, and **Tenant ID**.

### 2. Environment variables

Create a `.env` file in the project root:

```env
# Microsoft Graph / MSAL
CLIENT_ID=
CLIENT_SECRET=
TENANT_ID=
MAIL_USER=inbox@example.com          # mailbox to poll

# LLM (OpenRouter or OpenAI-compatible)
OPENROUTER_API_KEY=
LLM_MODEL=google/gemini-2.5-flash   # or any OpenRouter model

# LangGraph Postgres
POSTGRES_PASSWORD=

# LangSmith (optional tracing)
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=email-automation
```

A full list of supported config keys is in [`src/agent/config.py`](src/agent/config.py).

### 3. Run

```bash
docker compose up --build
```

On first run Docker will build the two images (`Dockerfile.agent` and `Dockerfile.email-poller`).

## Configuration

Key polling settings in `.env` (or environment):

| Variable | Default | Description |
|---|---|---|
| `POLLING_INTERVAL_SECONDS` | `3600` | How often to check the inbox |
| `MAX_EMAILS_PER_POLL` | `50` | Max emails processed per poll cycle |
| `LLM_MODEL` | `google/gemini-2.5-flash` | Model used for classification |
| `USE_RULE_BASED` | `false` | Skip LLM and use rule-based classification |

## Output

Classified emails are appended to `output/email_classifications.xlsx` with columns including:

- Thread ID, Email ID, Sender, Subject
- Action (`qualify` / `disqualify`)
- Company Name, Company Type, Operation Countries
- Contact Name, Contact Email, Salesperson
- Confidence score, Date of Contact

## Customising the classifier

Edit the prompt files in `src/agent/prompts/`:

- `email_classification_system_prompt.txt` — system instructions for the LLM
- `email_classification_user_prompt.txt` — user message template (supports `{email_subject}`, `{email_body}`, `{attachment_text}`)

## Utilities

`src/agent/get_access_token.py` is a standalone script for testing Graph API authentication:

```bash
python src/agent/get_access_token.py
```

Prints a short preview of the acquired token (or `NO TOKEN` on failure).

## Project structure

```
src/agent/
├── email_poller.py               # Entry point — polls inbox, dispatches to graphs
├── email_classification_graph.py # LangGraph classification graph
├── email_ingestion_graph.py      # LangGraph ingestion graph (attachments)
├── graph_schemas.py              # Pydantic models / LangGraph state
├── config.py                     # All configuration (pydantic-settings)
├── excel_tracker.py              # Appends results to Excel
├── logger.py                     # Centralised logging
├── get_access_token.py           # Standalone token testing utility
└── prompts/                      # LLM prompt templates
```
