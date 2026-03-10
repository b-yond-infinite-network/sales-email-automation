import httpx
import asyncio
import signal
from typing import List, Dict, Any, Optional, Set, Tuple
from datetime import datetime, timedelta, timezone
from msal import ConfidentialClientApplication
from src.agent.logger import get_logger
from src.agent.config import Config
from src.agent.excel_tracker import EmailClassificationExcelTracker

logging = get_logger(__name__)
config = Config()

# HTTP timeout constants - these ensure requests don't hang indefinitely
HTTP_TIMEOUT = httpx.Timeout(30.0, read=30.0)
# Graph run timeout with explicit read timeout to catch hanging connections
GRAPH_RUN_WAIT_TIMEOUT = 120.0  # 2 minutes - allow time for LLM processing

# State management
processed_email_ids: Set[str] = set()
shutdown_event = asyncio.Event()

class EmailPoller:
    """Email polling service that checks for new emails and processes them."""
    
    def __init__(self):
        self.session: Optional[httpx.AsyncClient] = None
        self.running = False
        self.excel_tracker = EmailClassificationExcelTracker()
        
    async def get_access_token(self) -> Optional[str]:
        """Acquire an access token using MSAL for Microsoft Graph API."""
        try:
            missing_fields = [
                key
                for key, value in {
                    "CLIENT_ID": config.CLIENT_ID,
                    "CLIENT_SECRET": config.CLIENT_SECRET,
                    "TENANT_ID": config.TENANT_ID,
                }.items()
                if not value
            ]
            if missing_fields:
                logging.error(
                    "MSAL config missing required fields: %s. Check .env loading and runtime working directory.",
                    ", ".join(missing_fields),
                )
                return None

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

    async def get_inbox_messages(self, access_token: str) -> List[Dict[str, Any]]:
        """Fetch messages from the inbox."""
        try:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            
            # Get messages from the last 2 hours to ensure we don't miss any
            # This provides some overlap in case the service was down
            since_time = datetime.now(timezone.utc) - timedelta(hours=2)
            since_time_str = since_time.isoformat().replace("+00:00", "Z")
            
            # Query for unread messages received since the specified time
            url = f"https://graph.microsoft.com/v1.0/users/{config.MAIL_USER}/mailFolders/Inbox/messages"
            params = {
                "$filter": f"isRead eq false and receivedDateTime ge {since_time_str}",
                "$select": "id,subject,receivedDateTime,isRead,from",
                "$orderby": "receivedDateTime desc",
                "$top": config.MAX_EMAILS_PER_POLL
            }
            
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                
                data = response.json()
                messages = data.get("value", [])
                
                logging.info(f"Retrieved {len(messages)} unread messages from inbox")
                return messages
                
        except Exception as e:
            logging.error(f"Error fetching inbox messages: {str(e)}")
            return []

    async def mark_email_as_read(self, message_id: str, access_token: str) -> bool:
        """Mark an email as read in Microsoft Graph."""
        try:
            url = f"https://graph.microsoft.com/v1.0/users/{config.MAIL_USER}/messages/{message_id}"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            payload = {"isRead": True}
            
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                response = await client.patch(url, headers=headers, json=payload)
                response.raise_for_status()
                
            logging.debug(f"Successfully marked email {message_id} as read")
            return True
            
        except Exception as e:
            logging.error(f"Error marking email {message_id} as read: {str(e)}")
            return False

    async def process_email_via_graph(self, message_id: str) -> bool:
        """Process an email using ingestion and classification graphs."""
        try:
            session = self.session
            if session is None:
                logging.error("Email poller session is not initialized")
                return False

            conversation_id = f"email_{message_id}"
            
            # Create thread for this email
            try:
                thread_response = await asyncio.wait_for(
                    session.post(
                        f"{config.GRAPH_API_BASE_URL}/threads",
                        json={},
                        headers={"Content-Type": "application/json"},
                    ),
                    timeout=10.0  # Quick timeout for thread creation
                )
            except asyncio.TimeoutError:
                logging.error(f"Timeout creating thread for email {message_id}")
                return False
            except Exception as e:
                logging.error(f"Failed to create thread for email {message_id}: {e}")
                return False
            
            if thread_response.status_code != 200:
                logging.error(f"Failed to create thread for email {message_id}: {thread_response.text}")
                return False
            
            try:
                thread_data = thread_response.json()
            except Exception as e:
                logging.error(f"Invalid thread response for email {message_id}: {e}")
                return False
                
            thread_id = thread_data.get("thread_id")
            
            if not thread_id:
                logging.error(f"No thread ID returned for email {message_id}")
                return False
            
            logging.info(f"Created thread {thread_id} for email ingestion of message ID: {message_id}")
            
            logging.info(f"Starting email ingestion for message ID: {message_id}")

            ingestion_result = await self._run_graph_wait(
                session=session,
                thread_id=thread_id,
                assistant_id=config.EMAIL_GRAPH_ID,
                graph_input={
                    "email_id": message_id,
                    "conversation_id": conversation_id,
                },
            )

            if ingestion_result is None:
                logging.error(f"Email ingestion failed or timed out for message ID: {message_id}")
                return False

            email_content = ingestion_result.get("email_content", "")
            sender_email = ingestion_result.get("sender", "")
            if not email_content:
                logging.warning(f"Ingestion returned no email_content for message ID: {message_id}")
                return True

            subject, body, attachment_text = self._parse_ingested_email_content(email_content)

            logging.info(f"Starting email classification for message ID: {message_id}")
            classification_result = await self._run_graph_wait(
                session=session,
                thread_id=thread_id,
                assistant_id=config.CLASSIFICATION_GRAPH_ID,
                graph_input={
                    "email_subject": subject,
                    "email_body": body,
                    "attachment_text": attachment_text,
                    "sender_email": sender_email,
                    "conversation_id": conversation_id,
                },
            )

            if classification_result is None:
                logging.error(f"Email classification failed or timed out for message ID: {message_id}")
                return False

            classification = classification_result.get("classification", {})
            logging.info(
                "Classification complete for %s: action=%s company=%s salesperson=%s confidence=%s",
                message_id,
                classification.get("action"),
                classification.get("company_name"),
                classification.get("salesperson"),
                classification.get("confidence"),
            )
            
            # Append to Excel file with error handling
            created_at = datetime.now(timezone.utc).isoformat()
            status = classification_result.get("status", "success")
            try:
                excel_success = self.excel_tracker.append_email(
                    thread_id=thread_id,
                    created_at=created_at,
                    email_id=message_id,
                    sender=sender_email,
                    email_content=email_content,
                    classification=classification,
                    status=status,
                )
            except Exception as e:
                logging.error(f"Failed to append email to Excel tracker: {e}")
                excel_success = False
            
            if not excel_success:
                logging.warning(f"Failed to write classification results to Excel for {message_id}")
                # Don't fail the whole operation if Excel tracking fails
                return True
            
            logging.info(f"Email {message_id} successfully processed and added to tracking")
            return True
                
        except Exception as e:
            logging.error(f"Error processing email {message_id}: {str(e)}")
            return False

    async def _run_graph_wait(
        self,
        session: httpx.AsyncClient,
        thread_id: str,
        assistant_id: str,
        graph_input: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        endpoint_url = f"{config.GRAPH_API_BASE_URL}/threads/{thread_id}/runs/wait"
        payload = {
            "assistant_id": assistant_id,
            "input": graph_input,
            "config": {"configurable": {"thread_id": thread_id}},
        }

        try:
            # Use asyncio.wait_for to enforce strict timeout
            response = await asyncio.wait_for(
                session.post(
                    endpoint_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=GRAPH_RUN_WAIT_TIMEOUT,
                ),
                timeout=GRAPH_RUN_WAIT_TIMEOUT + 5  # Add 5s buffer for cleanup
            )
        except asyncio.TimeoutError:
            logging.error(
                "Graph run timed out after %.0fs (assistant=%s, thread_id=%s). "
                "The LangGraph API may be overloaded or the email is taking too long to process.",
                GRAPH_RUN_WAIT_TIMEOUT,
                assistant_id,
                thread_id,
            )
            return None
        except httpx.ReadTimeout:
            logging.error(
                "Graph run read timeout after %.0fs (assistant=%s, thread_id=%s). "
                "Possible connection issue with LangGraph API.",
                GRAPH_RUN_WAIT_TIMEOUT,
                assistant_id,
                thread_id,
            )
            return None
        except httpx.ConnectError as e:
            logging.error(
                "Failed to connect to LangGraph API (assistant=%s, thread_id=%s): %s. "
                "Check if the API server is running and accessible.",
                assistant_id,
                thread_id,
                str(e),
            )
            return None
        except httpx.RequestError as e:
            logging.error(
                "Graph request failed (assistant=%s, thread_id=%s): %s",
                assistant_id,
                thread_id,
                str(e),
            )
            return None
        except Exception as e:
            logging.error(
                "Unexpected error during graph run (assistant=%s, thread_id=%s): %s",
                assistant_id,
                thread_id,
                str(e),
            )
            return None

        if response.status_code != 200:
            logging.error(
                "Graph run failed (assistant=%s, status=%s): %s",
                assistant_id,
                response.status_code,
                response.text[:500],  # Limit error message length
            )
            return None

        try:
            return response.json()
        except Exception as e:
            logging.error(
                "Graph run returned non-JSON response (assistant=%s): %s",
                assistant_id,
                str(e),
            )
            return None

    def _parse_ingested_email_content(self, email_content: str) -> Tuple[str, str, str]:
        subject = ""
        body = email_content
        attachment_text = ""

        if email_content.startswith("Subject:"):
            subject_end = email_content.find("\n\n")
            if subject_end != -1:
                subject_line = email_content[:subject_end]
                subject = subject_line.replace("Subject:", "", 1).strip()
                body = email_content[subject_end + 2 :]

        body_marker = "Body:\n"
        body_start = body.find(body_marker)
        if body_start != -1:
            body = body[body_start + len(body_marker) :]

        attachment_marker = "\n\n---\nExtracted Attachment Content\n---\n"
        attachment_idx = body.find(attachment_marker)
        if attachment_idx != -1:
            attachment_text = body[attachment_idx + len(attachment_marker) :].strip()
            body = body[:attachment_idx].strip()
        else:
            body = body.strip()

        return subject, body, attachment_text

    async def poll_and_process_emails(self):
        """Main polling loop that checks for new emails and processes them."""
        logging.info("Starting email polling loop")
        
        while not shutdown_event.is_set():
            try:
                # Get access token
                access_token = await self.get_access_token()
                if not access_token:
                    logging.error("No access token available, skipping this poll cycle")
                    await asyncio.sleep(config.POLLING_INTERVAL_SECONDS)
                    continue
                
                # Get unread messages
                messages = await self.get_inbox_messages(access_token)
                
                if not messages:
                    logging.info("No new emails to process")
                else:
                    logging.info(f"Found {len(messages)} new emails to process")
                    
                    # Process each message
                    for message in messages:
                        if shutdown_event.is_set():
                            break
                            
                        message_id = message.get("id")
                        subject = message.get("subject", "No Subject")
                        received_time = message.get("receivedDateTime", "Unknown")
                        
                        if not message_id:
                            continue
                            
                        # Skip if already processed
                        if message_id in processed_email_ids:
                            logging.debug(f"Email {message_id} already processed, skipping")
                            continue
                        
                        logging.info(f"Processing email: {subject} (ID: {message_id}, Received: {received_time})")
                        
                        # Process the email
                        success = await self.process_email_via_graph(message_id)
                        
                        if success:
                            # Mark as read in Microsoft Graph
                            marked_read = await self.mark_email_as_read(message_id, access_token)
                            
                            if marked_read:
                                # Add to processed set only after successfully marking as read
                                processed_email_ids.add(message_id)
                                logging.info(f"Successfully processed and marked email {message_id} as read")
                            else:
                                logging.warning(f"Email {message_id} processed but failed to mark as read - will retry next cycle")
                        else:
                            logging.error(f"Failed to process email {message_id}")
                        
                        # Small delay between processing emails to avoid overwhelming the system
                        await asyncio.sleep(2)
                
                # Wait for next polling cycle
                logging.info(f"Email polling cycle completed. Next check in {config.POLLING_INTERVAL_SECONDS} seconds")
                
                # Use wait_for to allow interruption during sleep
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=config.POLLING_INTERVAL_SECONDS)
                    break  # shutdown_event was set
                except asyncio.TimeoutError:
                    continue  # timeout reached, continue polling
                    
            except Exception as e:
                logging.error(f"Error in email polling loop: {str(e)}")
                # Wait a bit before retrying to avoid rapid failure loops
                await asyncio.sleep(60)
        
        logging.info("Email polling loop stopped")

    async def start(self):
        """Start the email polling service."""
        if self.running:
            logging.warning("Email poller is already running")
            return
        
        self.running = True
        logging.info("Starting Email Polling Microservice")
        
        # Initialize HTTP session
        self.session = httpx.AsyncClient(timeout=HTTP_TIMEOUT)
        
        try:
            # Start the polling loop
            await self.poll_and_process_emails()
        finally:
            # Cleanup
            if self.session:
                await self.session.aclose()
            self.running = False
            logging.info("Email Polling Microservice stopped")

    async def stop(self):
        """Stop the email polling service."""
        logging.info("Stopping Email Polling Microservice...")
        shutdown_event.set()
        
        if self.session:
            await self.session.aclose()
        
        self.running = False

# Signal handlers for graceful shutdown
def signal_handler(signum, frame):
    """Handle shutdown signals."""
    logging.info(f"Received signal {signum}, initiating graceful shutdown...")
    shutdown_event.set()

async def main():
    """Main entry point for the email polling microservice."""
    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Wait 5 minutes before starting to ensure all services are ready
    # TODO: change this back to 5 minutes in production, currently set to 5 seconds for testing
    startup_delay = 300
    logging.info(f"Email Poller starting in {startup_delay} seconds...")
    
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=startup_delay)
        logging.info("Shutdown signal received during startup delay, exiting...")
        return
    except asyncio.TimeoutError:
        logging.info("Startup delay completed, initializing email poller...")
    
    # Create and start the email poller
    poller = EmailPoller()
    
    try:
        await poller.start()
    except KeyboardInterrupt:
        logging.info("Received keyboard interrupt")
    except Exception as e:
        logging.error(f"Unexpected error in main: {str(e)}")
    finally:
        await poller.stop()
        logging.info("Email Polling Microservice shutdown complete")

if __name__ == "__main__":
    # Run the microservice
    asyncio.run(main())