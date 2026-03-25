import os
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv

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
        
        token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        payload = {
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }

        body = urlencode(payload).encode("utf-8")
        request = Request(
            token_url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=30) as response:
                response_data = response.read().decode("utf-8", errors="ignore")
                result = json.loads(response_data or "{}")
                return result.get("access_token")
        except Exception as exc:
            print(f"Token request failed: {exc}")
            return None
 
token = GraphClient().get_access_token()
print(f"{token} ..." if token else "NO TOKEN")