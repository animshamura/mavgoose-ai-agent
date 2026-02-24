import requests
import time
import os
from dotenv import load_dotenv

load_dotenv()

#URL = os.getenv("PUBLIC_URL") + "/voice"
URL = "http://localhost:8000/voice"

# ---- FIRST CALL (NO SPEECH) ----
print("Simulating first call...\n")

response = requests.post(URL, data={})

print("First Call XML:")
print(response.text)
print("-" * 50)

# ---- CONVERSATION LOOP ----
while True:
    text = input("You: ")

    if text.lower() in ["exit", "quit"]:
        break

    data = {
        "SpeechResult": text
    }

    response = requests.post(URL, data=data)

    print("\nRaw XML Response:")
    print(response.text)
    print("-" * 50)

    time.sleep(1)
