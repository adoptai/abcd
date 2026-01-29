"""Module to handle authentication with AdoptAI API"""

import os
import requests
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


def get_bearer_token(
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    api_endpoint: Optional[str] = None
) -> str:
    """
    Get authentication token from Adopt API.
    
    Args:
        client_id: The Adopt client ID (defaults to ADOPT_CLIENT_ID env var)
        client_secret: The Adopt client secret (defaults to ADOPT_CLIENT_SECRET env var)
        api_endpoint: The Adopt API endpoint (defaults to ADOPT_API_ENDPOINT env var)
        
    Returns:
        The bearer token as a string
        
    Raises:
        ValueError: If authentication fails or credentials are missing
    """
    # Get credentials from environment variables if not provided
    client_id = client_id or os.getenv('ADOPT_CLIENT_ID')
    client_secret = client_secret or os.getenv('ADOPT_CLIENT_SECRET')
    api_endpoint = api_endpoint or os.getenv('ADOPT_API_ENDPOINT', 'https://connect.adopt.ai')
    
    # Validate credentials
    if not client_id:
        raise ValueError(
            "ADOPT_CLIENT_ID is required. "
            "Please set the ADOPT_CLIENT_ID environment variable."
        )
    
    if not client_secret:
        raise ValueError(
            "ADOPT_CLIENT_SECRET is required. "
            "Please set the ADOPT_CLIENT_SECRET environment variable."
        )
    
    # Authenticate with Adopt API to get bearer token
    auth_url = f"{api_endpoint}/v1/auth/token"
    
    auth_payload = {
        'clientId': client_id,
        'secret': client_secret,
    }
    
    try:
        auth_response = requests.post(auth_url, json=auth_payload, timeout=30)
        
        if auth_response.status_code != 200:
            raise ValueError(
                f"Authentication failed with status code {auth_response.status_code}: "
                f"{auth_response.text}"
            )
        
        # Extract access token from response
        auth_data = auth_response.json()
        access_token = auth_data.get('access_token')
        
        if not access_token:
            raise ValueError(
                f"No access token received from authentication response: {auth_data}"
            )
        
        return access_token
        
    except requests.exceptions.RequestException as e:
        raise ValueError(f"Network error during authentication: {e}")

