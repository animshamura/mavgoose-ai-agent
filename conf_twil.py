import os
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

account_sid = os.environ["TWILIO_ACCOUNT_SID"]
auth_token = os.environ["TWILIO_AUTH_TOKEN"]

client = Client(account_sid, auth_token)

# Replace with your ngrok HTTPS URL
NGROK_URL = os.environ["PUBLIC_URL"]

# Replace with your Twilio phone number
TWILIO_NUMBER = os.environ["TWILIO_PHONE_NUMBER"]

incoming_number = client.incoming_phone_numbers.list(
    phone_number=TWILIO_NUMBER
)[0]

incoming_number.update(
    voice_url=NGROK_URL,
    voice_method="POST"
)

print("Webhook updated successfully!")
