
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("FEATHERLESS_API_KEY")

if not api_key:
    raise ValueError("API key not found in root .env")

response = httpx.get(
    "https://api.featherless.ai/v1/models",
    headers={
        "Authorization": f"Bearer {api_key}"
    },
    params={
        "available_on_current_plan": "true"
    },
    timeout=30
)

response.raise_for_status()

models = response.json().get("data", [])

print("\nAvailable models:\n")

for model in models:
    if "instruct" in model["id"].lower():
        print(model["id"])
