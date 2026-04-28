import requests
import os
from typing import Optional

KEYCLOAK_URL = os.environ.get("KEYCLOAK_TOKEN_URL", "http://keycloak.iam.svc.cluster.local:8080/realms/travel-realm/protocol/openid-connect/token")
CLIENT_ID = os.environ.get("OAUTH_CLIENT_ID", "orchestrator-client")
CLIENT_SECRET = os.environ.get("OAUTH_CLIENT_SECRET", "super-secret-key")

def exchange_jwt(subject_token: str, target_audience: str) -> Optional[str]:
    """
    Executes an OAuth2 Token Exchange operation (RFC 8693).
    The LangGraph orchestrator intercepts the original frontend JWT
    and exchanges it for a sub-scoped token specifically restricted 
    prevent lateral movement across unrelated east-west endpoints.
    """
    payload = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token": subject_token,
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "audience": target_audience
    }

    try:
        response = requests.post(KEYCLOAK_URL, data=payload, timeout=5)
        response.raise_for_status()
        data = response.json()
        print(f"Token Exchange success for aud: {target_audience}")
        return data.get("access_token")
    except requests.exceptions.RequestException as e:
        print(f"Token Exchange failed: {e}")
        return None

def invoke_agent_securely(agent_url: str, audience_id: str, original_token: str, payload_data: dict) -> dict:
    """
    Simulates the LangGraph wrapper mechanism routing execution across clusters.
    """
    # 1. Exchange the generic user token for a targeted agent-specific token.
    scoped_token = exchange_jwt(original_token, audience_id)
    if not scoped_token:
        raise PermissionError(f"OAuth evaluation refused for target audience: {audience_id}")

    # 2. Transmit payload towards the intended agent fronted by BIG-IP
    headers = {
        "Authorization": f"Bearer {scoped_token}",
        "Content-Type": "application/json"
    }

    response = requests.post(agent_url, json=payload_data, headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()
