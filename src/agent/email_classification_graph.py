from pathlib import Path
import asyncio
import re
from typing import List
from urllib.parse import quote

import httpx
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from openai import AsyncOpenAI

from src.agent.config import Config
from src.agent.graph_schemas import (
    EmailClassificationOutput,
    EmailClassificationRequest,
    EmailClassificationResult,
    EmailClassificationState,
)
from src.agent.logger import get_logger

# Timeout constants
WEB_SEARCH_TIMEOUT = 10.0  # seconds
LLM_TIMEOUT = 60.0  # seconds
HTTP_TIMEOUT = httpx.Timeout(10.0)

logger = get_logger(__name__)
config = Config()

client = AsyncOpenAI(
    base_url=config.OPENAI_BASE_URL,
    api_key=config.OPENROUTER_API_KEY,
)

PROMPT_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT_FILE = PROMPT_DIR / "email_classification_system_prompt.txt"
USER_PROMPT_FILE = PROMPT_DIR / "email_classification_user_prompt.txt"


def _read_prompt(path: Path, fallback: str) -> str:
    try:
        if path.exists():
            return path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Failed to read prompt file {path}: {exc}")
    return fallback


def _get_system_prompt() -> str:
    default_prompt = (
        "You classify inbound emails into business categories and return structured JSON only. "
        "Use the provided schema exactly. Confidence must be between 0.0 and 1.0."
    )
    return _read_prompt(SYSTEM_PROMPT_FILE, default_prompt)


def _get_user_prompt_template() -> str:
    default_template = (
        "Classify this inbound email inquiry.\n\n"
        "Subject:\n{email_subject}\n\n"
        "Body:\n{email_body}\n\n"
        "Attachment text:\n{attachment_text}\n\n"
        "Retrieved context:\n{retrieved_context}\n"
    )
    return _read_prompt(USER_PROMPT_FILE, default_template)


def _extract_candidate_company_name(text: str) -> str:
    if not text:
        return ""

    patterns = [
        r'"(?:company_name|company|organization|org|account)"\s*:\s*"([^"\n]{2,120})"',
        r"(?:company name|company|organization|org)\s*[:=]\s*([A-Za-z0-9&.,'\- ]{2,120})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" .,-\"")

    return ""


async def _fetch_public_company_context(company_name: str) -> str:
    if not company_name:
        return ""

    snippets: List[str] = []

    try:
        ddg_url = (
            "https://api.duckduckgo.com/?q="
            f"{quote(company_name)}&format=json&no_html=1&skip_disambig=1"
        )
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as async_client:
            ddg_resp = await asyncio.wait_for(
                async_client.get(ddg_url),
                timeout=WEB_SEARCH_TIMEOUT
            )
            if ddg_resp.status_code == 200:
                data = ddg_resp.json() or {}
                abstract_text = str(data.get("AbstractText") or "").strip()
                abstract_source = str(data.get("AbstractSource") or "").strip()
                related_topics = data.get("RelatedTopics") or []
                if abstract_text:
                    snippets.append(f"DuckDuckGo ({abstract_source or 'public'}): {abstract_text[:1200]}")
                elif related_topics:
                    first_topic = related_topics[0] if isinstance(related_topics, list) else {}
                    first_text = str(first_topic.get("Text") or "").strip() if isinstance(first_topic, dict) else ""
                    if first_text:
                        snippets.append(f"DuckDuckGo related: {first_text[:1000]}")
    except asyncio.TimeoutError:
        logger.warning(f"DuckDuckGo context fetch timed out after {WEB_SEARCH_TIMEOUT}s")
    except Exception as exc:
        logger.warning(f"DuckDuckGo context fetch failed: {type(exc).__name__}: {exc}")

    try:
        wiki_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(company_name)}"
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as async_client:
            wiki_resp = await asyncio.wait_for(
                async_client.get(wiki_url),
                timeout=WEB_SEARCH_TIMEOUT
            )
            if wiki_resp.status_code == 200:
                wiki = wiki_resp.json() or {}
                extract = str(wiki.get("extract") or "").strip()
                if extract:
                    snippets.append(f"Wikipedia: {extract[:1200]}")
    except asyncio.TimeoutError:
        logger.warning(f"Wikipedia context fetch timed out after {WEB_SEARCH_TIMEOUT}s")
    except Exception as exc:
        logger.warning(f"Wikipedia context fetch failed: {type(exc).__name__}: {exc}")

    return "\n\n".join(snippets)


async def build_query_text(state: EmailClassificationState):
    query_text = "\n\n".join(
        part
        for part in [state.email_subject, state.email_body, state.attachment_text]
        if part and part.strip()
    ).strip()
    return {
        "query_text": query_text,
        "status": "query text prepared",
    }


async def retrieve_context(state: EmailClassificationState):
    if not state.query_text:
        return {
            "retrieved_context": "",
            "status": "no query text available for retrieval",
        }

    try:
        if config.EMBEDDING_MODEL_TYPE == "huggingface":
            from langchain_huggingface import HuggingFaceEmbeddings

            embeddings = HuggingFaceEmbeddings(
                model_name=config.EMBEDDING_MODEL_NAME,
                show_progress=False,
                model_kwargs={"trust_remote_code": True},
                cache_folder=config.EMBEDDING_MODEL_CACHE_DIR,
            )
        elif config.EMBEDDING_MODEL_TYPE == "openai":
            from langchain_openai import OpenAIEmbeddings
            from pydantic import SecretStr

            embeddings = OpenAIEmbeddings(model="text-embedding-3-small", api_key=SecretStr(config.OPENAI_API_KEY))
        elif config.EMBEDDING_MODEL_TYPE == "openrouter":
            from src.agent.ingest import OpenRouterEmbeddings

            embeddings = OpenRouterEmbeddings(
                model=config.EMBEDDING_MODEL_NAME,
                api_key=config.OPENROUTER_API_KEY,
            )
        else:
            return {
                "retrieved_context": "",
                "status": f"invalid embedding model type: {config.EMBEDDING_MODEL_TYPE}",
            }

        from langchain_weaviate import WeaviateVectorStore

        from src.agent.ingest import WeaviateManager

        weaviate_manager = WeaviateManager(config)
        snippets: List[str] = []

        with weaviate_manager.get_client() as weaviate_client:
            if not weaviate_client.collections.exists(config.WEAVIATE_INDEX_NAME):
                return {
                    "retrieved_context": "",
                    "status": "weaviate collection not found",
                }

            vectorstore = WeaviateVectorStore(
                client=weaviate_client,
                index_name=config.WEAVIATE_INDEX_NAME,
                text_key=config.WEAVIATE_TEXT_KEY,
                embedding=embeddings,
            )

            docs = vectorstore.similarity_search(state.query_text[:3000], k=min(config.RAG_MAX_RETRIEVED_DOCS, 6))
            for idx, doc in enumerate(docs, start=1):
                metadata = getattr(doc, "metadata", {}) or {}
                source = metadata.get("source_document_link") or metadata.get("source_pdf_path") or metadata.get("source_ppt_path") or "unknown"
                snippets.append(f"[{idx}] source={source}\\n{doc.page_content[:1200]}")

        if snippets:
            return {
                "retrieved_context": "\n\n".join(snippets),
                "status": f"retrieved {len(snippets)} context documents",
            }

        company_name = _extract_candidate_company_name(state.query_text)
        web_context = await _fetch_public_company_context(company_name)
        if web_context:
            return {
                "retrieved_context": web_context,
                "status": "retrieved public web context fallback",
            }

        return {
            "retrieved_context": "",
            "status": "no retrieval context found",
        }
    except Exception as exc:
        logger.warning(f"Context retrieval failed: {exc}")
        return {
            "retrieved_context": "",
            "status": "retrieval failed; continuing with email-only context",
        }


async def classify_email(state: EmailClassificationState):
    # Validate required configuration
    if not config.OPENROUTER_API_KEY:
        logger.error("OPENROUTER_API_KEY not configured - cannot classify email")
        fallback = EmailClassificationResult(
            date_of_contact="",
            action="disqualify",
            company_name="",
            company_type="unknown",
            operation_countries=[],
            company_presence=[],
            current_projects=[],
            source="",
            email="",
            contact_name="",
            contact_last_name="",
            salesperson="none",
            confidence=0.0,
        )
        fallback_payload = fallback.model_dump()
        fallback_payload["error"] = "classification_skipped: missing API key"
        return {
            "classification": fallback_payload,
            "status": "email classification skipped: API key not configured",
        }

    system_prompt = _get_system_prompt()
    user_prompt_template = _get_user_prompt_template()

    user_prompt = user_prompt_template.format(
        email_subject=state.email_subject or "",
        email_body=state.email_body or "",
        attachment_text=state.attachment_text or "",
        retrieved_context=state.retrieved_context or "",
    )

    try:
        response = await asyncio.wait_for(
            client.beta.chat.completions.parse(
                model=config.LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=EmailClassificationResult,
            ),
            timeout=LLM_TIMEOUT
        )

        parsed = response.choices[0].message.parsed
        if not parsed:
            raise ValueError("No parsed response returned by model")

        result = parsed.model_dump()
        result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.0))))

        return {
            "classification": result,
            "status": "email classified successfully",
        }
    except asyncio.TimeoutError:
        logger.error(f"Email classification timeout after {LLM_TIMEOUT}s")
        fallback = EmailClassificationResult(
            date_of_contact="",
            action="disqualify",
            company_name="",
            company_type="unknown",
            operation_countries=[],
            company_presence=[],
            current_projects=[],
            source="",
            email="",
            contact_name="",
            contact_last_name="",
            salesperson="none",
            confidence=0.0,
        )
        fallback_payload = fallback.model_dump()
        fallback_payload["error"] = "classification_failed: timeout"
        return {
            "classification": fallback_payload,
            "status": f"email classification timed out after {LLM_TIMEOUT}s; fallback returned",
        }
    except Exception as exc:
        logger.error(f"Email classification failed: {type(exc).__name__}: {exc}", exc_info=True)
        fallback = EmailClassificationResult(
            date_of_contact="",
            action="disqualify",
            company_name="",
            company_type="unknown",
            operation_countries=[],
            company_presence=[],
            current_projects=[],
            source="",
            email="",
            contact_name="",
            contact_last_name="",
            salesperson="none",
            confidence=0.0,
        )
        fallback_payload = fallback.model_dump()
        fallback_payload["error"] = f"classification_failed: {type(exc).__name__}"
        return {
            "classification": fallback_payload,
            "status": f"email classification failed ({type(exc).__name__}); fallback returned",
        }


email_classification_graph_builder = StateGraph(
    EmailClassificationState,
    input_schema=EmailClassificationRequest,
    output_schema=EmailClassificationOutput,
)

email_classification_graph_builder.add_node("build_query_text", build_query_text)
email_classification_graph_builder.add_node("retrieve_context", retrieve_context)
email_classification_graph_builder.add_node("classify_email", classify_email)

email_classification_graph_builder.add_edge(START, "build_query_text")
email_classification_graph_builder.add_edge("build_query_text", "retrieve_context")
email_classification_graph_builder.add_edge("retrieve_context", "classify_email")
email_classification_graph_builder.add_edge("classify_email", END)

email_classification_graph = email_classification_graph_builder.compile()
