import json
import httpx
import os
import io
import re
import base64
import tempfile
import time
import pathlib
from typing import List, Dict, Any, Optional, AsyncGenerator
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Request, UploadFile, File, Form, BackgroundTasks, Depends, status
from fastapi.responses import StreamingResponse, Response
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
from collections import defaultdict
from time import time
from msal import ConfidentialClientApplication
from src.agent.minio_config import MinIOManager
from src.agent.logger import get_logger
from src.agent.config import Config
from src.agent.auth import get_current_user, User
from src.agent.auth_routes import auth_router
from src.agent.file_hash_manager import get_file_hash_manager

logging = get_logger(__name__)
config = Config()


HTTP_TIMEOUT = httpx.Timeout(300.0, connect=10.0)
CONTENT_TYPE_JSON = "application/json"

# Global state management
conversation_thread_map: Dict[str, str] = {}
checkpoint_map: Dict[str, str] = {}
conversation_files: Dict[str, List[Dict[str, Any]]] = {}  # Track files by conversation ID

class StreamEventHandler:
    """Handler for processing streaming events from LangGraph"""
    
    def __init__(self, conversation_id: str, thread_id: str):
        self.conversation_id = conversation_id
        self.thread_id = thread_id
        self.current_tasks = {}  # Track current tasks by ID
        self.retrieved_documents = []  # Store retrieved documents across the conversation
        self.current_node_context = None  # Track the currently active node
    
    async def handle_event(self, event_type: str, data: Any) -> AsyncGenerator[str, None]:
        """Handle different types of streaming events"""
        try:
            # Only log important events to reduce overhead
            if event_type in ["messages/complete", "debug"]:
                if event_type == "messages/complete" and isinstance(data, list):
                    async for event in self._handle_messages_complete(data):
                        yield event
                elif event_type == "debug" and isinstance(data, dict):
                    async for event in self._handle_debug_event(data):
                        yield event
            else:
                # Forward unhandled events for debugging (reduced logging)
                yield f"event: data\ndata: {json.dumps({'unhandled': data, 'event_type': event_type})}\n\n"
        except Exception as e:
            logging.error(f"Error handling event {event_type}: {e}")
            yield f"event: error\ndata: {json.dumps({'error': f'Event handling error: {str(e)}'})}\n\n"
    
    async def _handle_messages_complete(self, messages: List[Dict]) -> AsyncGenerator[str, None]:
        """Handle complete message events - only process GenerateFinalResponse messages"""
        
        for message in messages:
            if isinstance(message, dict) and message.get("content"):
                content = message.get("content", "")
                
                # Include retrieved documents if available
                message_data = {
                    'content': content, 
                    'complete': True,
                    'debug_node': self.current_node_context
                }
                
                if self.retrieved_documents:
                    message_data['sources'] = self.retrieved_documents
                
                yield f"event: message\ndata: {json.dumps(message_data)}\n\n"
    
    async def _handle_debug_event(self, debug_data: Dict) -> AsyncGenerator[str, None]:
        """Handle debug events including interrupts, checkpoints, and tasks"""
        event_type = debug_data.get("type")
        
        if event_type == "task":
            async for event in self._handle_task(debug_data):
                yield event
        elif event_type == "task_result":
            async for event in self._handle_task_result(debug_data):
                yield event
        elif event_type == "checkpoint":
            async for event in self._handle_checkpoint(debug_data):
                yield event
        elif event_type == "task_complete":
            async for event in self._handle_task_completion(debug_data):
                yield event
    
    async def _handle_task(self, debug_data: Dict) -> AsyncGenerator[str, None]:
        """Handle task processing events"""
        task_payload = debug_data.get("payload", {})
        task_id = task_payload.get("id")
        task_name = task_payload.get("name")
        
        if task_name and task_id:
            # Update current node context
            self.current_node_context = task_name
            
            # Store task info for later use in task_result
            self.current_tasks[task_id] = {
                'name': task_name,
                'started_at': debug_data.get('timestamp'),
                'step': debug_data.get('step')
            }
            
            step_info = {
                'node': task_name,
                'status': 'processing',
                'timestamp': debug_data.get('timestamp'),
                'step': debug_data.get('step'),
                'task_id': task_id
            }
            yield f"event: task_start\ndata: {json.dumps(step_info)}\n\n"
            logging.info(f"Starting task '{task_name}' (Step {debug_data.get('step')})")
    
    async def _handle_task_result(self, debug_data: Dict) -> AsyncGenerator[str, None]:
        """Handle task result events with interrupts and outputs"""
        task_payload = debug_data.get("payload", {})
        task_id = task_payload.get("id")
        task_name = task_payload.get("name")
        result = task_payload.get("result", [])
        error = task_payload.get("error")
        interrupts = task_payload.get("interrupts", [])
        
        # Get stored task info
        task_info = self.current_tasks.get(task_id, {})
        
        if task_name:
            # Handle interrupts first
            for interrupt in interrupts:
                interrupt_value = interrupt.get("value", {})
                question = interrupt_value.get("question", "")
                
                if question:
                    logging.info(f"Found interrupt in task '{task_name}' with question: {question}")
                    yield f"event: interrupt\ndata: {json.dumps({'question': question, 'conversation_id': self.conversation_id, 'thread_id': self.thread_id, 'task': task_name})}\n\n"
            
            # Format task result output
            output_data = {
                'node': task_name,
                'status': 'error' if error else 'completed',
                'timestamp': debug_data.get('timestamp'),
                'step': debug_data.get('step'),
                'task_id': task_id,
                'result': result,
                'error': error
            }
            
            # Extract meaningful results for display
            formatted_results = []
            if isinstance(result, list):
                for item in result:
                    if isinstance(item, list) and len(item) >= 2:
                        key, value = item[0], item[1]
                        if key == "status":
                            formatted_results.append(f"Status: {value}")
                        elif key == "history_rewritten_question":
                            formatted_results.append(f"Rewritten Question: {value}")
                        elif key == "alternative_queries":
                            if isinstance(value, list):
                                formatted_results.append(f"Alternative Queries: {len(value)} generated")
                        elif key == "retrieved_documents":
                            if isinstance(value, list):
                                formatted_results.append(f"Retrieved Documents: {len(value)} documents")
                                # Store retrieved documents for use in messages/complete events
                                self.retrieved_documents = value
                                # Store sources for frontend display
                                output_data['sources'] = value
                                logging.info(f"Stored {len(value)} retrieved documents for later use")
                        elif key == "LLM_msg":
                            formatted_results.append(f"Response Generated: {len(str(value))} characters")
                            # Store the final message for frontend display
                            output_data['final_message'] = value
                            # If this is GenerateFinalResponse, also send as message event for reliability
                            if task_name == "GenerateFinalResponse":
                                logging.info(f"Sending GenerateFinalResponse message via task_result: {str(value)[:100]}...")
                                # Send the message immediately via task_result event
                                message_data = {
                                    'content': value,
                                    'complete': True,
                                    'node': 'GenerateFinalResponse',
                                    'sources': self.retrieved_documents if self.retrieved_documents else []
                                }
                                yield f"event: final_message\ndata: {json.dumps(message_data)}\n\n"
                        else:
                            formatted_results.append(f"{key}: {str(value)[:100]}..." if len(str(value)) > 100 else f"{key}: {value}")
            
            if formatted_results:
                output_data['formatted_output'] = formatted_results
            
            yield f"event: task_result\ndata: {json.dumps(output_data)}\n\n"
            
            if error:
                logging.error(f"Task '{task_name}' failed: {error}")
            else:
                logging.info(f"Task '{task_name}' completed")
            
            # Clear current node context when task completes
            if self.current_node_context == task_name:
                self.current_node_context = None
            
            # Clean up stored task info
            if task_id in self.current_tasks:
                del self.current_tasks[task_id]
    
    async def _handle_checkpoint(self, debug_data: Dict) -> AsyncGenerator[str, None]:
        """Handle checkpoint events"""
        checkpoint_data = debug_data.get("payload", {})
        checkpoint_info = checkpoint_data.get("checkpoint", {})
        checkpoint_id = checkpoint_info.get("checkpoint_id")
        step = debug_data.get("step", -1)
        
        # Extract next tasks from checkpoint
        next_tasks = checkpoint_data.get("next", [])
        
        if checkpoint_id:
            checkpoint_map[self.conversation_id] = checkpoint_id
            
            checkpoint_event = {
                'checkpoint_id': checkpoint_id, 
                'conversation_id': self.conversation_id,
                'step': step,
                'next_tasks': next_tasks,
                'timestamp': debug_data.get('timestamp')
            }
            
            logging.info(f"Step {step}: Checkpoint {checkpoint_id} created, next tasks: {next_tasks}")
            yield f"event: checkpoint\ndata: {json.dumps(checkpoint_event)}\n\n"
    
    async def _handle_task_completion(self, debug_data: Dict) -> AsyncGenerator[str, None]:
        """Handle task completion events - This appears to be deprecated in favor of task_result"""
        task_payload = debug_data.get("payload", {})
        task_name = task_payload.get("name")
        
        if task_name:
            step_info = {
                'node': task_name,
                'status': 'completed',
                'timestamp': debug_data.get('timestamp'),
                'step': debug_data.get('step'),
            }
            yield f"event: task_complete\ndata: {json.dumps(step_info)}\n\n"
            logging.info(f"Step {debug_data.get('step')}: Task '{task_name}' completed")

class ThreadManager:
    """Manager for handling thread operations"""
    
    @staticmethod
    async def get_or_create_thread(client: httpx.AsyncClient, conversation_id: str) -> Optional[str]:
        """Get existing thread or create a new one"""
        # Check if we have an existing thread
        if conversation_id and conversation_id in conversation_thread_map:
            thread_id = conversation_thread_map[conversation_id]
            logging.info(f"Using existing thread ID {thread_id} for conversation ID {conversation_id}")
            return thread_id
        
        # Create new thread
        return await ThreadManager.create_new_thread(client, conversation_id)
    
    @staticmethod
    async def create_new_thread(client: httpx.AsyncClient, conversation_id: str = None) -> Optional[str]:
        """Always create a new thread"""
        try:
            thread_response = await client.post(
                f"{config.GRAPH_API_BASE_URL}/threads",
                json={},
                headers={"Content-Type": CONTENT_TYPE_JSON},
            )
            
            if thread_response.status_code == 200:
                thread_data = thread_response.json()
                thread_id = thread_data.get("thread_id")
                if thread_id and conversation_id:
                    conversation_thread_map[conversation_id] = thread_id
                    logging.info(f"Created new thread ID {thread_id} for conversation ID {conversation_id}")
                elif thread_id:
                    logging.info(f"Created new thread ID {thread_id} (no conversation mapping)")
                return thread_id
            else:
                logging.error(f"Failed to create thread: {thread_response.text}")
        except Exception as e:
            logging.error(f"Error creating thread: {str(e)}")
        
        return None

# FastAPI Application Setup
app = FastAPI(title="Success Stories Knowledge base Backend", version="1.0.0")

# Add CORS middleware first, before routes
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # Credentials must be disabled when using wildcard origins
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Include authentication routes
app.include_router(auth_router)


security = HTTPBasic()
        
current_file = Path(__file__)
current_dir = str(current_file.parent.resolve())

templates = Jinja2Templates(directory=f"{current_dir}/templates/")
app.mount(
    "/_next/static",
    StaticFiles(directory=f"{current_dir}/templates/dist/_next/static"),
    name="next_static",
)
app.mount(
    "/images",
    StaticFiles(directory=f"{current_dir}/templates/dist/images"),
    name="static_images",
)

logging.info("Application setup complete")


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/", include_in_schema=False)
def index(request: Request):
    return templates.TemplateResponse("dist/index.html", context={"request": request, "result": "result"})


@app.post("/ingest")
async def ingest_graph(
    file: UploadFile = File(...),
    conversation_id: str = Form(...),
    llm_model: str = Form(default=""),
    current_user: User = Depends(get_current_user)
) -> StreamingResponse:
    """Ingest single file using LangGraph - accepts one file directly from UI with duplicate detection"""

    # Security validation
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided or file has no name")

    # File size limit (50MB)
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB in bytes
    if file.size and file.size > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB")

    # File type validation (basic check)
    allowed_extensions = {'.pdf', '.doc', '.docx', '.ppt', '.pptx'}
    file_extension = Path(file.filename).suffix.lower()
    if file_extension not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"File type not allowed. Allowed types: {', '.join(allowed_extensions)}")


    async def stream_response() -> AsyncGenerator[str, None]:
        yield f"event: start\ndata: {json.dumps({'status': 'connected'})}\n\n"

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            try:
                # Get or create thread for this conversation
                thread_id = await ThreadManager.get_or_create_thread(client, conversation_id)

                if not thread_id:
                    yield f"event: error\ndata: {json.dumps({'error': 'Failed to create/get thread for ingestion'})}\n\n"
                    return

                logging.info(f"Using thread {thread_id} for ingestion of {file.filename} in conversation {conversation_id}")

                # Validate file
                if not file.filename:
                    yield f"event: error\ndata: {json.dumps({'error': 'No file provided or file has no name'})}\n\n"
                    return

                # Process the uploaded file
                content = await file.read()

                # Calculate file hash for duplicate detection
                hash_manager = get_file_hash_manager()
                file_hash = await hash_manager.calculate_file_hash(content)
                logging.info(f"Calculated file hash for {file.filename}: {file_hash[:16]}...")

                # Encode content as base64
                encoded_content = base64.b64encode(content).decode('utf-8')

                file_data = {
                    "filename": file.filename,
                    "content": encoded_content,
                    "size": len(content),
                    "content_type": file.content_type or "application/octet-stream",
                    "file_hash": file_hash  # Include hash for duplicate detection in graph
                }

                # Store file data for this conversation
                if conversation_id not in conversation_files:
                    conversation_files[conversation_id] = []

                # Add file info to conversation (without content for memory efficiency)
                file_info = {
                    "filename": file.filename,
                    "size": len(content),
                    "content_type": file.content_type or "application/octet-stream",
                    "file_hash": file_hash[:16] + "...",  # Store abbreviated hash for display
                    "uploaded_at": json.dumps({"timestamp": "now"}),  # You might want to use proper datetime
                    "thread_id": thread_id
                }
                conversation_files[conversation_id].append(file_info)

                logging.info(f"Processed and hashed file {file.filename} ({len(content)} bytes) for conversation {conversation_id}")

                # Build payload with single file in a list
                payload = {
                    "input": {
                        "llm_model": llm_model,
                        "files": [file_data]  # Wrap single file in a list (now includes file_hash)
                    },
                    "config": {"configurable": {"thread_id": thread_id}},
                    "stream_mode": ["debug", "messages"],
                    "stream_subgraphs": True,
                    "assistant_id": config.INGESTION_GRAPH_ID,
                    "multitask_strategy": "rollback",
                    "checkpoint_during": True
                }

                logging.info(f"Sending file {file.filename} to ingestion graph for conversation {conversation_id}")

                # Stream the response
                async for event in _stream_langgraph_response(client, thread_id, payload, conversation_id):
                    yield event

            except Exception as e:
                error_msg = f"Unexpected error: {str(e)}"
                logging.error(error_msg)
                yield f"event: error\ndata: {json.dumps({'error': error_msg})}\n\n"

    return StreamingResponse(stream_response(), media_type="text/event-stream")

@app.post("/chat/stream_log")
async def chat_stream_graph(
    request_data: dict = Body(...),
    current_user: User = Depends(get_current_user)
) -> StreamingResponse:
    """Stream chat responses from LangGraph"""
    #logging.info(f"Received chat request with data: {request_data}")

    async def stream_response() -> AsyncGenerator[str, None]:
        yield f"event: start\ndata: {json.dumps({'status': 'connected'})}\n\n"
        
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            try:
                # Extract conversation ID and get/create thread
                conversation_id = request_data.get("config", {}).get("metadata", {}).get("session_id", "")
                thread_id = await ThreadManager.get_or_create_thread(client, conversation_id)
                
                if not thread_id:
                    yield f"event: error\ndata: {json.dumps({'error': 'No valid thread ID available'})}\n\n"
                    return
                
                # Build payload
                input_data = request_data.get("input", {})
                payload = {
                    "input": {
                        "user_input": input_data.get("question", ""),
                        "llm_model": input_data.get("llm_model", "")
                    },
                    "config": {"configurable": {"thread_id": thread_id}},
                    "stream_mode": ["debug", "messages"],
                    "stream_subgraphs": True,
                    "assistant_id": config.RAG_GRAPH_ID,
                    "multitask_strategy": "rollback",
                    "checkpoint_during": True
                }
                
                # Stream the response
                async for event in _stream_langgraph_response(client, thread_id, payload, conversation_id):
                    yield event
                    
            except Exception as e:
                error_msg = f"Unexpected error: {str(e)}"
                logging.error(error_msg)
                yield f"event: error\ndata: {json.dumps({'error': error_msg})}\n\n"

    return StreamingResponse(stream_response(), media_type="text/event-stream")


@app.get("/view/{file_path:path}")
async def view_file(
    file_path: str,
    current_user: User = Depends(get_current_user)
):
    """View a file (PDF) in browser from MinIO storage"""
    try:
        # Initialize MinIO manager
        minio_manager = MinIOManager()

        # Get file content from MinIO
        file_content = minio_manager.get_object(file_path)

        if file_content is None:
            raise HTTPException(status_code=404, detail="File not found")

        # Determine content type based on file extension
        if file_path.lower().endswith('.pdf'):
            media_type = "application/pdf"
        else:
            # For non-PDF files, redirect to download
            raise HTTPException(status_code=400, detail="Only PDF files can be viewed in browser")

        # Get filename from path
        filename = file_path.split('/')[-1]

        # Return file content with inline disposition for browser viewing
        return Response(
            content=file_content,
            media_type=media_type,
            headers={
                "Content-Disposition": f"inline; filename={filename}",
                "Content-Length": str(len(file_content))
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error viewing file {file_path}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error viewing file: {str(e)}")


@app.get("/download/{file_path:path}")
async def download_file(
    file_path: str,
    current_user: User = Depends(get_current_user)
):
    """Download a file from MinIO storage"""
    try:
        # Initialize MinIO manager
        minio_manager = MinIOManager()

        # Get file content from MinIO
        file_content = minio_manager.get_object(file_path)

        if file_content is None:
            raise HTTPException(status_code=404, detail="File not found")

        # Determine content type based on file extension
        if file_path.lower().endswith('.pdf'):
            media_type = "application/pdf"
        elif file_path.lower().endswith(('.ppt', '.pptx')):
            media_type = "application/vnd.ms-powerpoint" if file_path.lower().endswith('.ppt') else "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        elif file_path.lower().endswith(('.doc', '.docx')):
            media_type = "application/msword" if file_path.lower().endswith('.doc') else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:
            media_type = "application/octet-stream"

        # Get filename from path
        filename = file_path.split('/')[-1]

        # Return file content with appropriate headers
        return Response(
            content=file_content,
            media_type=media_type,
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Length": str(len(file_content))
            }
        )

    except Exception as e:
        logging.error(f"Error downloading file {file_path}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error downloading file: {str(e)}")

async def get_access_token():
    """Acquire an access token using MSAL for Microsoft Graph API."""
    try:
        msal_app = ConfidentialClientApplication(
            client_id=config.CLIENT_ID,
            client_credential=config.CLIENT_SECRET,
            authority=config.MSAL_AUTHORITY,
        )
        result = msal_app.acquire_token_silent(
            scopes=config.MSAL_SCOPE,
            account=None,
        )
        if not result:
            logging.debug("No cached token found, acquiring new token from client credentials")
            result = msal_app.acquire_token_for_client(scopes=config.MSAL_SCOPE)
            
        if result and "access_token" in result:
            logging.debug("Successfully acquired access token")
            return result["access_token"]
        else:
            error_description = result.get("error_description", "Unknown error") if result else "No result returned"
            error_code = result.get("error", "unknown") if result else "no_result"
            logging.error(f"Failed to get access token. Error: {error_code}, Description: {error_description}")
            return None
    except Exception as e:
        logging.error(f"Exception occurred while acquiring access token: {str(e)}")
        return None
    
    
    
async def process_and_ingest_email_stream(message_id: str):
    """Process and ingest an email using the email ingestion graph via streaming API."""
    if not message_id:
        logging.error("No message ID provided for processing")
        return
    
    conversation_id = f"email_{message_id}"
    
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            # Get or create thread for this email
            thread_id = await ThreadManager.get_or_create_thread(client, conversation_id)
            
            if not thread_id:
                logging.error(f"Failed to create/get thread for email ingestion: {message_id}")
                return
            
            logging.info(f"Using thread {thread_id} for email ingestion of message ID: {message_id}")
            
            # Build payload for email ingestion
            payload = {
                "input": {
                    "email_id": message_id,
                    "conversation_id": conversation_id
                },
                "config": {"configurable": {"thread_id": thread_id}},
                "stream_mode": ["debug", "messages"],
                "stream_subgraphs": True,
                "assistant_id": config.EMAIL_GRAPH_ID,
                "multitask_strategy": "rollback",
                "checkpoint_during": True
            }
            
            logging.info(f"Starting email ingestion stream for message ID: {message_id}")
            
            # Process the stream (consume all events but don't return them since this runs in background)
            event_count = 0
            async for event in _stream_langgraph_response(client, thread_id, payload, conversation_id):
                event_count += 1
                if "event: final_message" in event or "event: error" in event:
                    logging.info(f"Email ingestion event for {message_id}: {event[:200]}...")
            
            logging.info(f"Email ingestion stream completed for message ID: {message_id} with {event_count} events")
                    
        except Exception as e:
            logging.error(f"Error processing email {message_id}: {str(e)}")
    
    
# Email notification subscription management
MAILBOX = "stories@reailize.com"
CLIENT_STATE_SECRET = "super-secret-string"
PUBLIC_NOTIFICATION_URL = config.PUBLIC_NOTIFICATION_URL

subscription_id = None
subscription_expiry = None

async def list_existing_subscriptions():
    """List existing subscriptions to check for duplicates."""
    access_token = await get_access_token()
    if not access_token:
        logging.error("Cannot list subscriptions: no access token available")
        return []
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://graph.microsoft.com/v1.0/subscriptions",
                headers=headers
            )
            r.raise_for_status()
            resp = r.json()
            subscriptions = resp.get("value", [])
            logging.info(f"Found {len(subscriptions)} existing subscriptions")
            for sub in subscriptions:
                logging.info(f"Subscription: {sub.get('id')} - Resource: {sub.get('resource')} - NotificationUrl: {sub.get('notificationUrl')}")
            return subscriptions
    except Exception as e:
        logging.error(f"Failed to list subscriptions: {e}")
        return []

async def create_or_renew_subscription():
    """Create or renew email subscription for notifications."""
    global subscription_id, subscription_expiry

    # First, list existing subscriptions to check for conflicts
    await list_existing_subscriptions()

    # If we already have a subscription and it's still valid enough, don't recreate
    if subscription_id and subscription_expiry:
        if subscription_expiry - datetime.now(timezone.utc) > timedelta(hours=24):
            return

        # Renew (PATCH)
        access_token = await get_access_token()
        if not access_token:
            logging.error("Cannot renew subscription: no access token available")
            return
            
        new_expiry = datetime.now(timezone.utc) + timedelta(days=2)
        patch_url = f"https://graph.microsoft.com/v1.0/subscriptions/{subscription_id}"
        patch_payload = {
            "expirationDateTime": new_expiry.isoformat().replace("+00:00", "Z")
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        try:
            async with httpx.AsyncClient() as client:
                r = await client.patch(patch_url, headers=headers, json=patch_payload)
                r.raise_for_status()
                subscription_expiry = datetime.fromisoformat(
                    r.json()["expirationDateTime"].replace("Z", "+00:00")
                )
                logging.info("Subscription renewed successfully")
                return
        except Exception as e:
            logging.error(f"Failed to renew subscription: {e}")
            return

    # Create new subscription
    access_token = await get_access_token()
    if not access_token:
        logging.error("Cannot create subscription: no access token available")
        return
    
    resource = f"users/{MAILBOX}/mailFolders('Inbox')/messages"
    expiry = datetime.now(timezone.utc) + timedelta(days=2)

    payload = {
        "changeType": "created",
        "notificationUrl": PUBLIC_NOTIFICATION_URL,
        "resource": resource,
        "expirationDateTime": expiry.isoformat().replace("+00:00", "Z"),
        "clientState": CLIENT_STATE_SECRET,
        "latestSupportedTlsVersion": "v1_2",
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    logging.info(f"Creating subscription with payload: {payload}")
    
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://graph.microsoft.com/v1.0/subscriptions",
                headers=headers,
                json=payload,
            )
            r.raise_for_status()
            resp = r.json()
            subscription_id = resp["id"]
            subscription_expiry = datetime.fromisoformat(
                r.json()["expirationDateTime"].replace("Z", "+00:00")
            )
            logging.info("Subscription created successfully")
    except Exception as e:
        logging.error(f"Failed to create subscription: {e}")
        raise

@app.on_event("startup")
async def startup_event():
    """Initialize email subscription on startup."""
    try:
        await create_or_renew_subscription()
        logging.info("Email subscription created/renewed successfully")
    except Exception as e:
        logging.error(f"Failed to create/renew email subscription during startup: {str(e)}")
        # Don't fail the entire startup - continue without email subscriptions

@app.get("/notifications")
async def notifications_validation(request: Request):
    """Handle Microsoft Graph webhook validation."""
    validation_token = request.query_params.get("validationToken")
    if validation_token:
        # MUST return the token as plain text
        return Response(
            content=validation_token,
            media_type="text/plain",
            status_code=200,
        )
    return Response(status_code=200)

@app.post("/notifications")
async def notifications_listener(request: Request, background_tasks: BackgroundTasks):
    """Handle incoming email notifications from Microsoft Graph."""
    body = await request.json()

    for notif in body.get("value", []):
        # Security check
        if notif.get("clientState") != CLIENT_STATE_SECRET:
            continue

        message_id = None
        rd = notif.get("resourceData")
        if rd and "id" in rd:
            message_id = rd["id"]
        else:
            res = notif.get("resource", "")
            if "/Messages/" in res:
                message_id = res.split("/Messages/")[-1]

        if not message_id:
            continue

        # Get the full message using our fixed function
        try:
            # Process email in background using streaming API
            background_tasks.add_task(process_and_ingest_email_stream, message_id)
            logging.info(f"Received email notification for message {message_id}, started streaming processing")
        except Exception as e:
            logging.error(f"Error processing email notification: {str(e)}")

    return {"status": "ok"}

        
@app.get("/healthz")
async def healthz():
    """Health check endpoint."""
    return {
        "status": "up",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "main-server"
    }

    
async def _stream_langgraph_response(
    client: httpx.AsyncClient, 
    thread_id: str, 
    payload: Dict[str, Any], 
    conversation_id: str
) -> AsyncGenerator[str, None]:
    """Common function to stream responses from LangGraph"""
    endpoint_url = f"{config.GRAPH_API_BASE_URL}/threads/{thread_id}/runs/stream"
    logging.info(f"Using endpoint URL: {endpoint_url}")
    #logging.info(f"Sending payload to LangGraph: {payload}")
    
    try:
        async with client.stream(
            "POST",
            endpoint_url,
            json=payload,
            headers={"Content-Type": CONTENT_TYPE_JSON},
        ) as response:
            if response.status_code != 200:
                error_msg = f"External service returned status code: {response.status_code}"
                logging.error(error_msg)
                yield f"event: error\ndata: {json.dumps({'error': error_msg})}\n\n"
                return
            
            # Initialize event handler
            event_handler = StreamEventHandler(conversation_id, thread_id)
            current_event = None
            
            async for line in response.aiter_lines():
                line = line.strip()
                
                if not line:
                    continue

                if line.startswith("event:"):
                    current_event = line.replace("event: ", "", 1)
                    continue
                
                if line.startswith("data:"):
                    json_str = line.replace("data: ", "", 1)
                    if not json_str.strip():
                        continue
                    
                    try:
                        parsed = json.loads(json_str)
                        #logging.info(f"Event: {current_event}")
                        
                        # Process event through handler (removed debug data streaming for performance)
                        async for event in event_handler.handle_event(current_event, parsed):
                            yield event
                            
                    except json.JSONDecodeError as e:
                        logging.warning(f"Failed to parse line as JSON: {line} ({e})")
                
                current_event = None

            yield f"event: end\ndata: {json.dumps({'status': 'stream_ended'})}\n\n"
            
    except httpx.HTTPError as e:
        error_msg = f"HTTP error: {str(e)}"
        logging.error(error_msg)
        yield f"event: error\ndata: {json.dumps({'error': error_msg})}\n\n"
        
# @app.get("/config")
# async def get_config():
#     """Get current configuration (non-sensitive values only)."""
#     return {
#         "llm_provider": config.LLM_PROVIDER,
#         "default_model": config.DEFAULT_MODEL_NAME,
#         "weaviate_collection": config.WEAVIATE_COLLECTION_NAME,
#         "num_retrieved_tickets": config.NUM_RETRIEVED_TICKETS,
#         "with_query_rephrasing": config.WITH_QUERY_REPHRASING,
#         "thinking_budget": config.THINKING_BUDGET,
#     }
# Application entry point
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)


