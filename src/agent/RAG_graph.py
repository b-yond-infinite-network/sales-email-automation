from pathlib import Path
from typing import List

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_weaviate import WeaviateVectorStore
from openai import AsyncOpenAI

from src.agent.config import Config
from src.agent.graph_schemas import (
    EmailClassificationOutput,
    EmailClassificationRequest,
    EmailClassificationResult,
    EmailClassificationState,
)
from src.agent.logger import get_logger
from src.agent.ingest import OpenRouterEmbeddings, WeaviateManager

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
            embeddings = OpenRouterEmbeddings(
                model=config.EMBEDDING_MODEL_NAME,
                api_key=config.OPENROUTER_API_KEY,
            )
        else:
            return {
                "retrieved_context": "",
                "status": f"invalid embedding model type: {config.EMBEDDING_MODEL_TYPE}",
            }

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
                snippets.append(f"[{idx}] source={source}\n{doc.page_content[:1200]}")

        return {
            "retrieved_context": "\n\n".join(snippets),
            "status": f"retrieved {len(snippets)} context documents",
        }
    except Exception as exc:
        logger.warning(f"Context retrieval failed: {exc}")
        return {
            "retrieved_context": "",
            "status": "retrieval failed; continuing with email-only context",
        }


async def classify_email(state: EmailClassificationState):
    system_prompt = _get_system_prompt()
    user_prompt_template = _get_user_prompt_template()

    user_prompt = user_prompt_template.format(
        email_subject=state.email_subject or "",
        email_body=state.email_body or "",
        attachment_text=state.attachment_text or "",
        retrieved_context=state.retrieved_context or "",
    )

    try:
        response = await client.beta.chat.completions.parse(
            model=config.LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format=EmailClassificationResult,
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
    except Exception as exc:
        logger.error(f"Email classification failed: {exc}")
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
        fallback_payload["error"] = f"classification_failed: {exc}"
        return {
            "classification": fallback_payload,
            "status": "email classification failed; fallback returned",
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

memory = MemorySaver()
email_classification_graph = email_classification_graph_builder.compile(checkpointer=memory)

# Backward-compatible export name for older references.
RAG_graph = email_classification_graph
