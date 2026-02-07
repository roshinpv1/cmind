import os
import httpx
import base64
import time
from datetime import datetime, timedelta
from typing import Optional

class ApigeeTokenManager:
    def __init__(self):
        self.token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None

    async def get_token(self) -> str:
        # Check if we have a valid cached token
        if self.token and self.token_expiry and datetime.now() < self.token_expiry:
            return self.token

        # Fetch new token
        token = await self._fetch_token()
        
        # Cache the token (assuming 1 hour expiry as per TS logic)
        self.token = token
        self.token_expiry = datetime.now() + timedelta(hours=1)
        
        return self.token

    async def _fetch_token(self) -> str:
        login_url = os.environ.get("APIGEE_NONPROD_LOGIN_URL")
        consumer_key = os.environ.get("APIGEE_CONSUMER_KEY")
        consumer_secret = os.environ.get("APIGEE_CONSUMER_SECRET")

        if not all([login_url, consumer_key, consumer_secret]):
            raise ValueError("Apigee OAuth credentials not configured. Required: APIGEE_NONPROD_LOGIN_URL, APIGEE_CONSUMER_KEY, APIGEE_CONSUMER_SECRET")

        try:
            # Prepare OAuth 2.0 client credentials request
            auth_str = f"{consumer_key}:{consumer_secret}"
            base64_auth = base64.b64encode(auth_str.encode()).decode()
            
            # Note: TS logic had NODE_TLS_REJECT_UNAUTHORIZED='0', we use verify=False if needed, 
            # but better to handle it properly. For now following the spirit of the provided logic.
            async with httpx.AsyncClient(verify=False) as client:
                response = await client.post(
                    login_url,
                    headers={
                        "Authorization": f"Basic {base64_auth}",
                        "Content-Type": "application/x-www-form-urlencoded"
                    },
                    data={"grant_type": "client_credentials"}
                )

                if response.status_code != 200:
                    raise Exception(f"Apigee OAuth failed: {response.status_code} - {response.text}")

                token_data = response.json()
                access_token = token_data.get("access_token")
                
                if not access_token:
                    raise Exception("No access_token received from Apigee OAuth")

                return access_token

        except Exception as e:
            print(f"Apigee token fetch failed: {e}")
            raise Exception(f"Failed to fetch Apigee token: {str(e)}")

class EnterpriseTokenManager:
    def __init__(self):
        self.token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None

    async def get_token(self) -> str:
        if self.token and self.token_expiry and datetime.now() < self.token_expiry:
            return self.token

        token = os.environ.get("ENTERPRISE_LLM_TOKEN")
        if not token:
            raise ValueError("ENTERPRISE_LLM_TOKEN environment variable not set")

        self.token = token
        self.token_expiry = datetime.now() + timedelta(hours=1)
        return self.token

    def clear_token(self):
        self.token = None
        self.token_expiry = None
