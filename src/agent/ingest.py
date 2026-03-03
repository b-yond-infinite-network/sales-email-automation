import asyncio
from concurrent.futures import ThreadPoolExecutor
import pprint
from typing import Any, Dict, List, Literal, Optional, Union
import os

import torch
import weaviate
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_openai import OpenAIEmbeddings
from langchain_weaviate import WeaviateVectorStore
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from openai import OpenAI

from src.agent.config import Config
from src.agent.logger import get_logger

config = Config()
logger = get_logger(__name__)


class OpenRouterEmbeddings(Embeddings):
    """OpenRouter embeddings using OpenAI-compatible API."""

    def __init__(self, model: str, api_key: str):
        self.model = model
        self.api_key = api_key
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )
        logger.info(f"Initialized OpenRouter embeddings - Model: {model}")

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed multiple documents."""
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=texts,
                encoding_format="float"
            )
            return [embedding.embedding for embedding in response.data]
        except Exception as e:
            logger.error(f"Error embedding documents: {e}")
            raise

    def embed_query(self, text: str) -> List[float]:
        """Embed a single query."""
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=[text],
                encoding_format="float"
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Error embedding query: {e}")
            raise


class WeaviateManager:
    def __init__(self, config: Config) -> None:
        self.config = config

    def _get_headers(self) -> Dict[str, str]:
        """Get the headers for the Weaviate client."""
        if self.config.EMBEDDING_MODEL_TYPE == "openai":
            return {"X-OpenAI-Api-Key": self.config.OPENAI_API_KEY}
        return {}

    def get_client(self, secure: bool = False) -> weaviate.WeaviateClient:
        """Get the Weaviate client."""
        headers = self._get_headers()
        client = weaviate.connect_to_custom(
            http_host=self.config.WEAVIATE_HTTP_HOST,
            http_port=int(self.config.WEAVIATE_HTTP_PORT),
            http_secure=secure,
            grpc_host=self.config.WEAVIATE_GRPC_HOST,
            grpc_port=int(self.config.WEAVIATE_GRPC_PORT),
            grpc_secure=secure,
            headers=headers,
            auth_credentials=weaviate.classes.init.Auth.api_key(api_key=self.config.WEAVIATE_API_KEYS or ""),
        )

        if not client.is_live():
            raise ConnectionError("Weaviate server is not live.")
        return client

    async def get_client_async(self, secure: bool = False) -> weaviate.WeaviateClient:
        """Get the Weaviate client asynchronously."""
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=2) as executor:
            return await loop.run_in_executor(executor, self.get_client, secure)

    def create_collection(self, client: weaviate.WeaviateClient) -> None:
        """Create a Weaviate collection."""
        vectorizer_config = self._get_vectorizer_config()
        client.collections.create(name=self.config.WEAVIATE_INDEX_NAME, vectorizer_config=vectorizer_config)

    async def create_collection_async(self, client: weaviate.WeaviateClient) -> None:
        """Create a Weaviate collection asynchronously."""
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            await loop.run_in_executor(executor, self.create_collection, client)

    def delete_collection(self, client: weaviate.WeaviateClient) -> None:
        """Delete a Weaviate collection."""
        client.collections.delete(self.config.WEAVIATE_INDEX_NAME)

    async def delete_collection_async(self, client: weaviate.WeaviateClient) -> None:
        """Delete a Weaviate collection asynchronously."""
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            await loop.run_in_executor(executor, self.delete_collection, client)

    def _get_vectorizer_config(self) -> Any:
        """Get the vectorizer configuration."""
        if self.config.EMBEDDING_MODEL_TYPE == "openai":
            return weaviate.classes.config.Configure.Vectorizer.text2vec_openai(
                model="text-embedding-3-small"
            )
        return weaviate.classes.config.Configure.Vectorizer.none()


class WeaviateDataLoader:
    def __init__(self, config: Config, weaviate_manager: WeaviateManager) -> None:
        self.config = config
        self.weaviate_manager = weaviate_manager
        self.embeddings = self._get_embedding_model()
        # self.processor = StructuredDocumentProcessor(
        #     splitter_type=TextSplitterType(self.config.RAG_SPLITTER_TYPE),
        #     chunk_size=self.config.RAG_CHUNK_SIZE,
        #     chunk_overlap=self.config.RAG_CHUNK_OVERLAP,
        # )

    def _get_embedding_model(self):
        """Get the embedding model."""
        if self.config.EMBEDDING_MODEL_TYPE == "huggingface":
            logger.info("Using HuggingFace embeddings.")
            logger.warning("It may take a while to download the model.")
            logger.debug(f"Model name: {self.config.EMBEDDING_MODEL_NAME}")
            logger.debug(f"CUDA available: {torch.cuda.is_available()}")
            return HuggingFaceEmbeddings(
                model_name=self.config.EMBEDDING_MODEL_NAME,
                show_progress=False,
                model_kwargs={"trust_remote_code": True},
                cache_folder=self.config.EMBEDDING_MODEL_CACHE_DIR,
            )
        elif self.config.EMBEDDING_MODEL_TYPE == "openai":
            logger.info("Using OpenAI embeddings.")
            from pydantic import SecretStr

            return OpenAIEmbeddings(
                model="text-embedding-3-small",
                api_key=SecretStr(self.config.OPENAI_API_KEY),
            )
        elif self.config.EMBEDDING_MODEL_TYPE == "openrouter":
            logger.info(f"Using OpenRouter embeddings with model: {self.config.EMBEDDING_MODEL_NAME}")
            return OpenRouterEmbeddings(
                model=self.config.EMBEDDING_MODEL_NAME,
                api_key=self.config.OPENROUTER_API_KEY,
            )
        raise ValueError("Invalid embedding model type.")

    async def _get_embedding_model_async(self):
        """Get the embedding model asynchronously (for future use)."""
        # For now, just return the sync version since model initialization is typically fast
        # This method is reserved for future async model loading if needed
        return self._get_embedding_model()

    def _sanitize_metadata(self, metadata: Dict) -> Dict:
        """Sanitize metadata by removing null values and ensuring string fields are strings."""
        if not metadata:
            return {}
            
        sanitized = {}
        for key, value in metadata.items():
            try:
                if isinstance(value, list):
                    # Handle list values (like ExtractedData_list)
                    sanitized_list = []
                    for item in value:
                        if isinstance(item, dict):
                            sanitized_item = self._sanitize_metadata(item)
                            if sanitized_item:  # Only add non-empty dictionaries
                                sanitized_list.append(sanitized_item)
                        elif item is not None:
                            sanitized_list.append(item)
                    sanitized[key] = sanitized_list
                elif isinstance(value, dict):
                    # Recursively sanitize nested dictionaries
                    sanitized_dict = self._sanitize_metadata(value)
                    if sanitized_dict:  # Only add non-empty dictionaries
                        sanitized[key] = sanitized_dict
                elif value is not None:
                    # Convert to string if it's a string field, otherwise keep as is
                    if isinstance(value, str) or key.endswith('_link') or key.endswith('_path'):
                        sanitized[key] = str(value) if value else ""
                    else:
                        sanitized[key] = value
                # Skip None values entirely
            except Exception as e:
                logger.warning(f"Error sanitizing metadata key '{key}' with value '{value}': {e}")
                # Skip problematic values
                continue
        return sanitized

    async def ingest_framework_stories(self, stories_dict: Dict[str, str], 
                               source_metadata: Optional[Dict[str, Any]] = None) -> None:
        """Ingest framework stories into Weaviate as a single document.
        
        Args:
            stories_dict: Dictionary containing framework stories with keys like 'star_story', 'hero_story', 'pas_story'
            source_metadata: Optional metadata dictionary to include with the document
        """
        
        # Prepare metadata with source information
        base_metadata = self._sanitize_metadata(source_metadata or {})
        
        logger.debug(f"Sanitized metadata: {base_metadata}")
        
        star_story = stories_dict.get("star_story", "")
        hero_story = stories_dict.get("hero_story", "")
        pas_story = stories_dict.get("pas_story", "")
        success_stories_list = stories_dict.get("success_stories_list", [])
        
        if not any([star_story and star_story.strip(), 
                   hero_story and hero_story.strip(), 
                   pas_story and pas_story.strip(),
                   success_stories_list]):
            logger.warning("No valid framework stories to ingest.")
            return
        
        # Prepare all documents for batch ingestion
        documents = []
        document_metadata = {
            **base_metadata,
            "star_story": star_story.strip() if star_story else "",
            "hero_story": hero_story.strip() if hero_story else "",
            "pas_story": pas_story.strip() if pas_story else "",
        }
        # Process each individual success story as a separate document
        for story in success_stories_list:
            if story and story.strip():
                # Create document for this individual story
                document_metadata_copy = self._sanitize_metadata(document_metadata.copy())
                document = Document(
                    page_content=story.strip(),
                    metadata=document_metadata_copy
                )
                documents.append(document)
        

        if not documents:
            logger.warning("No valid documents created from framework stories.")
            return

        # Ingest all documents at once using async operations
        with self.weaviate_manager.get_client() as client:
            # Check if collection exists asynchronously
            collection_exists = await self._check_collection_exists_async(client)
            
            if collection_exists:
                if self.config.INGESTION_OVERWRITE:
                    logger.info("Deleting existing collection.")
                    await self.weaviate_manager.delete_collection_async(client)
                    collection_exists = False
                else:
                    logger.info("Collection already exists; adding new documents.")

            if not collection_exists:
                logger.info("Creating collection.")
                await self.weaviate_manager.create_collection_async(client)

            logger.info(f"Ingesting {len(documents)} documents ({len(success_stories_list)} success stories + framework stories) into Weaviate.")
            
            # Run the blocking vector store operation in a thread pool
            await self._ingest_documents_async(documents, client)

    async def _check_collection_exists_async(self, client: weaviate.WeaviateClient) -> bool:
        """Check if collection exists asynchronously."""
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            return await loop.run_in_executor(
                executor, 
                lambda: client.collections.exists(self.config.WEAVIATE_INDEX_NAME)
            )

    async def _ingest_documents_async(self, documents: List[Document], client: weaviate.WeaviateClient) -> None:
        """Ingest documents into Weaviate asynchronously."""
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            await loop.run_in_executor(
                executor,
                lambda: WeaviateVectorStore.from_documents(
                    documents,
                    self.embeddings,
                    client=client,
                    index_name=self.config.WEAVIATE_INDEX_NAME,
                    text_key=self.config.WEAVIATE_TEXT_KEY,
                )
            )
        logger.info(f"Successfully ingested {len(documents)} documents into Weaviate.")


def create_ingest_manager():
    """Create and return a configured WeaviateDataLoader instance."""
    config = Config()
    logger.info(f"Configuration:\n{pprint.pformat(config.model_dump())}")
    weaviate_manager = WeaviateManager(config)
    return WeaviateDataLoader(config, weaviate_manager)


def main():
    """Main function for testing purposes."""
    loader = create_ingest_manager()
    logger.info("Weaviate Data Loader initialized successfully.")

if __name__ == "__main__":
    main()
    
    
    