import os
from msal import ConfidentialClientApplication
 
class GraphClient:
    def get_access_token(self):
        msal_app = ConfidentialClientApplication(
            client_id=os.getenv("CLIENT_ID"),
            client_credential=os.getenv("CLIENT_SECRET"),
            authority=f"https://login.microsoftonline.com/{os.getenv('TENANT_ID')}",
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