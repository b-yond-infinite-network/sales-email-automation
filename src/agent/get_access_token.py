import os
from dotenv import load_dotenv
from msal import ConfidentialClientApplication
from pathlib import Path

# A standalone script if you need an access token for postman or other testing purposes. 

# Load .env file
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

class GraphClient:
    def get_access_token(self):
        client_id = os.getenv("CLIENT_ID")
        client_secret = os.getenv("CLIENT_SECRET")
        tenant_id = os.getenv("TENANT_ID")
        
        if not all([client_id, client_secret, tenant_id]):
            raise ValueError(f"Missing credentials: CLIENT_ID={client_id}, CLIENT_SECRET={bool(client_secret)}, TENANT_ID={tenant_id}")
        
        msal_app = ConfidentialClientApplication(
            client_id=client_id,
            client_credential=client_secret,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
        )
 
        result = msal_app.acquire_token_silent(
            scopes=["https://graph.microsoft.com/.default"],
            account=None,
        )
        if not result:
            result = msal_app.acquire_token_for_client(
                scopes=["https://graph.microsoft.com/.default"]
            )
 
        if not result:
            return None

        return result.get("access_token")
 
token = GraphClient().get_access_token()
print(f"{token} ..." if token else "NO TOKEN")