"""
File Hash Manager for duplicate detection in the ingestion pipeline.

This module provides functionality to:
- Calculate SHA-256 hashes of uploaded files
- Track file hashes and metadata in MinIO
- Check for duplicate files before processing
- Maintain a persistent hash registry
"""

import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from src.agent.config import Config
from src.agent.logger import get_logger
from src.agent.minio_config import MinIOManager

logger = get_logger(__name__)
config = Config()

# Global thread pool for hash calculations
_hash_executor = ThreadPoolExecutor(max_workers=2)

# Hash registry file in MinIO
HASH_REGISTRY_OBJECT = "metadata/file_hashes_registry.json"


class FileHashMetadata:
    """Metadata for a hashed file."""

    def __init__(
        self,
        file_hash: str,
        filename: str,
        file_size: int,
        content_type: str,
        upload_timestamp: str,
        minio_object_path: str
    ):
        self.file_hash = file_hash
        self.filename = filename
        self.file_size = file_size
        self.content_type = content_type
        self.upload_timestamp = upload_timestamp
        self.minio_object_path = minio_object_path

    def to_dict(self) -> Dict:
        """Convert metadata to dictionary."""
        return {
            "file_hash": self.file_hash,
            "filename": self.filename,
            "file_size": self.file_size,
            "content_type": self.content_type,
            "upload_timestamp": self.upload_timestamp,
            "minio_object_path": self.minio_object_path
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "FileHashMetadata":
        """Create metadata from dictionary."""
        return cls(
            file_hash=data["file_hash"],
            filename=data["filename"],
            file_size=data["file_size"],
            content_type=data["content_type"],
            upload_timestamp=data["upload_timestamp"],
            minio_object_path=data["minio_object_path"]
        )


class FileHashManager:
    """Manager for file hash tracking and duplicate detection."""

    def __init__(self, minio_manager: MinIOManager):
        self.minio_manager = minio_manager
        self._registry_cache: Optional[Dict[str, Dict]] = None
        self._cache_lock = asyncio.Lock()

    def calculate_file_hash_sync(self, file_content: bytes) -> str:
        """
        Calculate SHA-256 hash of file content (synchronous).

        Args:
            file_content: Raw bytes of the file

        Returns:
            Hexadecimal string of the SHA-256 hash
        """
        hasher = hashlib.sha256()
        hasher.update(file_content)
        return hasher.hexdigest()

    async def calculate_file_hash(self, file_content: bytes) -> str:
        """
        Calculate SHA-256 hash of file content (asynchronous).

        Args:
            file_content: Raw bytes of the file

        Returns:
            Hexadecimal string of the SHA-256 hash
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _hash_executor,
            self.calculate_file_hash_sync,
            file_content
        )

    async def load_hash_registry(self) -> Dict[str, Dict]:
        """
        Load the hash registry from MinIO.

        Returns:
            Dictionary mapping file hashes to metadata
        """
        async with self._cache_lock:
            if self._registry_cache is not None:
                return self._registry_cache

            try:
                registry_content = await self.minio_manager.get_object_async(HASH_REGISTRY_OBJECT)

                if registry_content:
                    registry = json.loads(registry_content.decode('utf-8'))
                    self._registry_cache = registry
                    logger.info(f"Loaded hash registry with {len(registry)} entries")
                    return registry
                else:
                    # Registry doesn't exist yet, create empty one
                    logger.info("Hash registry not found, creating new empty registry")
                    self._registry_cache = {}
                    return {}

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse hash registry JSON: {e}")
                self._registry_cache = {}
                return {}
            except Exception as e:
                logger.error(f"Error loading hash registry: {e}")
                self._registry_cache = {}
                return {}

    async def save_hash_registry(self, registry: Dict[str, Dict]) -> bool:
        """
        Save the hash registry to MinIO.

        Args:
            registry: Dictionary mapping file hashes to metadata

        Returns:
            True if successful, False otherwise
        """
        try:
            registry_json = json.dumps(registry, indent=2, ensure_ascii=False)
            registry_bytes = registry_json.encode('utf-8')

            success = await self.minio_manager.put_object_async(
                HASH_REGISTRY_OBJECT,
                registry_bytes,
                "application/json"
            )

            if success:
                # Update cache
                async with self._cache_lock:
                    self._registry_cache = registry
                logger.info(f"Saved hash registry with {len(registry)} entries")
                return True
            else:
                logger.error("Failed to save hash registry to MinIO")
                return False

        except Exception as e:
            logger.error(f"Error saving hash registry: {e}")
            return False

    async def check_duplicate(self, file_hash: str) -> Tuple[bool, Optional[FileHashMetadata]]:
        """
        Check if a file with the given hash already exists.

        Args:
            file_hash: SHA-256 hash of the file

        Returns:
            Tuple of (is_duplicate, existing_metadata)
        """
        registry = await self.load_hash_registry()

        if file_hash in registry:
            metadata = FileHashMetadata.from_dict(registry[file_hash])
            logger.info(f"Duplicate file detected: {file_hash[:16]}... (original: {metadata.filename})")
            return True, metadata

        return False, None

    async def register_file(
        self,
        file_hash: str,
        filename: str,
        file_size: int,
        content_type: str,
        minio_object_path: str
    ) -> bool:
        """
        Register a new file in the hash registry.

        Args:
            file_hash: SHA-256 hash of the file
            filename: Original filename
            file_size: Size of the file in bytes
            content_type: MIME type of the file
            minio_object_path: Path where file is stored in MinIO

        Returns:
            True if successful, False otherwise
        """
        registry = await self.load_hash_registry()

        # Check if already exists
        if file_hash in registry:
            logger.warning(f"File hash {file_hash[:16]}... already registered, skipping")
            return True

        # Create metadata
        metadata = FileHashMetadata(
            file_hash=file_hash,
            filename=filename,
            file_size=file_size,
            content_type=content_type,
            upload_timestamp=datetime.utcnow().isoformat() + "Z",
            minio_object_path=minio_object_path
        )

        # Add to registry
        registry[file_hash] = metadata.to_dict()

        # Save registry
        success = await self.save_hash_registry(registry)

        if success:
            logger.info(f"Registered file: {filename} with hash {file_hash[:16]}...")
        else:
            logger.error(f"Failed to register file: {filename}")

        return success

    async def get_file_info(self, file_hash: str) -> Optional[FileHashMetadata]:
        """
        Get metadata for a file by its hash.

        Args:
            file_hash: SHA-256 hash of the file

        Returns:
            FileHashMetadata if found, None otherwise
        """
        registry = await self.load_hash_registry()

        if file_hash in registry:
            return FileHashMetadata.from_dict(registry[file_hash])

        return None

    async def get_all_files(self) -> List[FileHashMetadata]:
        """
        Get metadata for all registered files.

        Returns:
            List of FileHashMetadata objects
        """
        registry = await self.load_hash_registry()
        return [FileHashMetadata.from_dict(data) for data in registry.values()]

    async def remove_file(self, file_hash: str) -> bool:
        """
        Remove a file from the hash registry.

        Args:
            file_hash: SHA-256 hash of the file

        Returns:
            True if successful, False otherwise
        """
        registry = await self.load_hash_registry()

        if file_hash not in registry:
            logger.warning(f"File hash {file_hash[:16]}... not found in registry")
            return False

        # Remove from registry
        filename = registry[file_hash].get("filename", "unknown")
        del registry[file_hash]

        # Save registry
        success = await self.save_hash_registry(registry)

        if success:
            logger.info(f"Removed file from registry: {filename} ({file_hash[:16]}...)")
        else:
            logger.error(f"Failed to remove file from registry: {filename}")

        return success

    def invalidate_cache(self):
        """Invalidate the registry cache to force reload on next access."""
        self._registry_cache = None
        logger.debug("Hash registry cache invalidated")


# Global instance
_hash_manager_instance: Optional[FileHashManager] = None


def get_file_hash_manager() -> FileHashManager:
    """
    Get the global FileHashManager instance.

    Returns:
        FileHashManager instance
    """
    global _hash_manager_instance

    if _hash_manager_instance is None:
        minio_manager = MinIOManager()
        _hash_manager_instance = FileHashManager(minio_manager)
        logger.info("Initialized global FileHashManager instance")

    return _hash_manager_instance
