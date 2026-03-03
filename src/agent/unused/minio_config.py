import asyncio
from concurrent.futures import ThreadPoolExecutor
from minio import Minio
from minio.error import S3Error
import io
from typing import Dict, List, Literal, Optional, Union

from src.agent.logger import get_logger
from src.agent.config import Config

logger = get_logger(__name__)
config = Config()

class MinIOManager:
    """Singleton class to manage MinIO client connections with async operations."""
    
    _instance = None
    _client = None
    _executor = ThreadPoolExecutor(max_workers=4)  # Limit concurrent operations
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MinIOManager, cls).__new__(cls)
        return cls._instance
    
    def get_client(self) -> Minio:
        """Get or create MinIO client instance."""
        if self._client is None:
            try:
                self._client = Minio(
                    config.MINIO_ENDPOINT,
                    access_key=config.MINIO_ACCESS_KEY,
                    secret_key=config.MINIO_SECRET_KEY,
                    secure=config.MINIO_SECURE
                )
                if not self._client.bucket_exists(config.MINIO_BUCKET_NAME):
                    self._client.make_bucket(config.MINIO_BUCKET_NAME)
                    logger.info(f"Created MinIO bucket: {config.MINIO_BUCKET_NAME}")

            except Exception as e:
                logger.error(f"Failed to initialize MinIO client: {e}")
                raise
        return self._client
    
    async def get_client_async(self) -> Minio:
        """Get or create MinIO client instance asynchronously."""
        if self._client is None:
            # Run blocking MinIO operations in thread pool
            loop = asyncio.get_event_loop()
            try:
                await loop.run_in_executor(self._executor, self._init_client)
            except Exception as e:
                logger.error(f"Failed to initialize MinIO client: {e}")
                raise
        return self._client
    
    def _init_client(self):
        """Initialize MinIO client in a separate thread."""
        self._client = Minio(
            config.MINIO_ENDPOINT,
            access_key=config.MINIO_ACCESS_KEY,
            secret_key=config.MINIO_SECRET_KEY,
            secure=config.MINIO_SECURE
        )
        if not self._client.bucket_exists(config.MINIO_BUCKET_NAME):
            self._client.make_bucket(config.MINIO_BUCKET_NAME)
            logger.info(f"Created MinIO bucket: {config.MINIO_BUCKET_NAME}")
    
    def list_objects(self, prefix: str = "") -> List[str]:
        """List objects in the MinIO bucket with optional prefix filter."""
        try:
            client = self.get_client()
            objects = client.list_objects(config.MINIO_BUCKET_NAME, prefix=prefix, recursive=True)
            return [obj.object_name for obj in objects]
        except Exception as e:
            logger.error(f"Failed to list MinIO objects: {e}")
            return []
    
    async def list_objects_async(self, prefix: str = "") -> List[str]:
        """List objects in the MinIO bucket with optional prefix filter asynchronously."""
        try:
            client = await self.get_client_async()
            loop = asyncio.get_event_loop()
            objects = await loop.run_in_executor(
                self._executor,
                lambda: list(client.list_objects(config.MINIO_BUCKET_NAME, prefix=prefix, recursive=True))
            )
            return [obj.object_name for obj in objects]
        except Exception as e:
            logger.error(f"Failed to list MinIO objects: {e}")
            return []
    
    def get_object(self, object_name: str) -> Optional[bytes]:
        """Get object content from MinIO (synchronous - use get_object_async for better performance)."""
        try:
            client = self.get_client()
            response = client.get_object(config.MINIO_BUCKET_NAME, object_name)
            return response.read()
        except S3Error as e:
            logger.error(f"S3 error getting object {object_name}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to get object {object_name}: {e}")
            return None
    
    def put_object(self, object_name: str, data: bytes, content_type: str = "application/octet-stream") -> bool:
        """Put object to MinIO (synchronous - use put_object_async for better performance)."""
        try:
            client = self.get_client()
            client.put_object(
                config.MINIO_BUCKET_NAME,
                object_name,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type
            )
            logger.info(f"Successfully uploaded object: {object_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to put object {object_name}: {e}")
            return False
            return None
        except Exception as e:
            logger.error(f"Failed to get object {object_name}: {e}")
            return None
    
    async def get_object_async(self, object_name: str) -> Optional[bytes]:
        """Get object content from MinIO asynchronously."""
        try:
            client = await self.get_client_async()
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                self._executor,
                lambda: client.get_object(config.MINIO_BUCKET_NAME, object_name)
            )
            return response.read()
        except S3Error as e:
            logger.error(f"S3 error getting object {object_name}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to get object {object_name}: {e}")
            return None
    
    def put_object(self, object_name: str, data: bytes, content_type: str = "application/octet-stream") -> bool:
        """Put object to MinIO."""
        try:
            client = self.get_client()
            client.put_object(
                config.MINIO_BUCKET_NAME,
                object_name,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type
            )
            logger.info(f"Successfully uploaded object: {object_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to put object {object_name}: {e}")
            return False
    
    async def put_object_async(self, object_name: str, data: bytes, content_type: str = "application/octet-stream") -> bool:
        """Put object to MinIO asynchronously."""
        try:
            client = await self.get_client_async()
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self._executor,
                lambda: client.put_object(
                    config.MINIO_BUCKET_NAME,
                    object_name,
                    io.BytesIO(data),
                    length=len(data),
                    content_type=content_type
                )
            )
            logger.info(f"Successfully uploaded object: {object_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to put object {object_name}: {e}")
            return False
    
    async def delete_object_async(self, object_name: str) -> bool:
        """Delete object from MinIO asynchronously."""
        try:
            client = await self.get_client_async()
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self._executor,
                lambda: client.remove_object(config.MINIO_BUCKET_NAME, object_name)
            )
            logger.info(f"Successfully deleted object: {object_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete object {object_name}: {e}")
            return False
    
    def delete_object(self, object_name: str) -> bool:
        """Delete object from MinIO (synchronous - use delete_object_async for better performance)."""
        try:
            client = self.get_client()
            client.remove_object(config.MINIO_BUCKET_NAME, object_name)
            logger.info(f"Successfully deleted object: {object_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete object {object_name}: {e}")
            return False
    
    async def object_exists_async(self, object_name: str) -> bool:
        """Check if object exists in MinIO asynchronously."""
        try:
            client = await self.get_client_async()
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self._executor,
                lambda: client.stat_object(config.MINIO_BUCKET_NAME, object_name)
            )
            return True
        except Exception as e:
            logger.debug(f"Object {object_name} does not exist or error occurred: {e}")
            return False
    
    def object_exists(self, object_name: str) -> bool:
        """Check if object exists in MinIO (synchronous - use object_exists_async for better performance)."""
        try:
            client = self.get_client()
            client.stat_object(config.MINIO_BUCKET_NAME, object_name)
            return True
        except Exception as e:
            logger.debug(f"Object {object_name} does not exist or error occurred: {e}")
            return False


