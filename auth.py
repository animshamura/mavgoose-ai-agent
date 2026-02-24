import os
import requests
from dotenv import load_dotenv

load_dotenv()

def get_auth_token():
    BASE_URL = os.getenv("API_BASE_URL")

    try:
        login_url = f"{BASE_URL}/auth/login/"

        response = requests.post(
            login_url,
            json={
                "email": os.getenv("ADMIN_EMAIL"),
                "password": os.getenv("ADMIN_PASSWORD")
            }
        )

        response.raise_for_status()
        data = response.json()

        # 🔥 FIXED HERE
        auth_token = data.get("tokens", {}).get("access")
        print(auth_token)
        
        if not auth_token:
            raise ValueError("Access token not found in response")
    
        return auth_token

    except Exception as e:
        print("❌ Auth error:", e)
        return None
