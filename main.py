import os
import json
import pytz
import requests
from datetime import datetime
from pydantic import BaseModel
from fastapi import FastAPI, Request, Header, HTTPException, status, Depends
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from openai import OpenAI
from dotenv import load_dotenv
from rag import build_vectorstore, rebuild_vectorstore
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from twilio.base.exceptions import TwilioRestException
import secrets
from auth import get_auth_token
from twilio.rest import Client
from fastapi import BackgroundTasks
import asyncio
import httpx
from pydub import AudioSegment
from fastapi.staticfiles import StaticFiles

load_dotenv()
security = HTTPBearer()

BASE_URL = os.getenv("API_BASE_URL")
ID = os.getenv("STORE_ID")
AI_BEHAVIOR_URL = f"{BASE_URL}/api/v1/stores/{ID}/ai-behavior"
CALL_LOG_API_URL = f"{BASE_URL}/api/v1/call/details/"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
STORE_NAME = os.getenv("STORE_NAME")
PUBLIC_URL = os.getenv("PUBLIC_URL")
AUDIO_URL = os.getenv("AUDIO_URL")
TOKEN = get_auth_token()
app = FastAPI()

client = OpenAI(api_key=OPENAI_API_KEY)

CALL_SESSIONS = {}

LOG_FILE = "calllog.json"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RECORDINGS_DIR = os.path.join(BASE_DIR, "recordings")  # folder where MP3s are

app.mount(
    "/recordings",
    StaticFiles(directory=RECORDINGS_DIR),
    name="recordings",
)

async def send_call_log(call_sid: str):
    try:
        if call_sid not in CALL_SESSIONS:
            return

        session = CALL_SESSIONS[call_sid]

        url = f"{AUDIO_URL}/{call_sid}.mp3"

        payload = {
            "phone_number": session.get("phone_number"),
            "issue": session.get("issue"),
            "store": session.get("store_id"),
            "call_type": session.get("call_type", "AI_RESOLVED"),
            "outcome": session.get("outcome", "QUOTE_PROVIDED"),
            "duration": str(
                int((datetime.utcnow() - session["started_at"]).total_seconds())
            ),
            "started_at": session.get("started_at").isoformat(),
            "ended_at": datetime.utcnow().isoformat(),
            "audio_url": url,
            "transcripts": session.get("transcripts")
        }

        # =====================================
        # 1️⃣ SAVE LOCALLY TO JSON FILE
        # =====================================
        data = [] 

        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r") as f:
                try:
                    loaded = json.load(f)
                    if isinstance(loaded, dict): data = [loaded]
                    elif isinstance(loaded, list): data = loaded
                    else:
                        data = []
 
                except:
                    data = []
        else:
            data = []

        data.append(payload)

        with open(LOG_FILE, "w") as f:
            json.dump(data, f, indent=4)
        print(data)
        print("💾 Saved locally to JSON")

        # =====================================
        # 2️⃣ SEND TO /save-call-log ENDPOINT
        # =====================================

        async with httpx.AsyncClient() as client:
             response = await client.post(
              CALL_LOG_API_URL,
              json=payload,
              headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json"
        }
    )

        print("Call log status:", response.status_code)

        print("✅ Call log sent to API")

        # =====================================
        # CLEANUP MEMORY
        # =====================================

        CALL_SESSIONS.pop(call_sid, None)

    except Exception as e:
        print("❌ Failed sending call log:", e)

# ==========================================
# LOAD AI BEHAVIOR
# ==========================================


def load_ai_behavior():

    TOKEN = get_auth_token()

    try:
        response = requests.get(
            AI_BEHAVIOR_URL,
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": "application/json"
            }
        )

        response.raise_for_status()
        data = response.json()

        print("RAW AI BEHAVIOR TYPE:", type(data))
        print("RAW AI BEHAVIOR:", json.dumps(data, indent=2))

        # 🔥 Keep unwrapping until dict
        while isinstance(data, list):
            if not data:
                return {}
            data = data[0]

        if not isinstance(data, dict):
            return {}

        return data

    except Exception as e:
        print("❌ Failed to fetch AI behavior:", e)
        return {}


# ==========================================
# BUSINESS HOURS CHECK (Timezone Aware)
# ==========================================

def is_business_open(behavior_data):

    # Current time (change timezone if needed)
    tz = pytz.timezone("America/New_York")
    now = datetime.now(tz)

    current_day = now.weekday()  # Monday = 0
    current_time = now.time()

    business_hours = behavior_data.get("business_hours", [])

    for day_config in business_hours:

        day_number = day_config.get("day")


        if day_number == current_day:

            if not day_config.get("is_open"):
                return True

            open_time = datetime.strptime(
                day_config["open_time"], "%H:%M:%S"
            ).time()

            close_time = datetime.strptime(
                day_config["close_time"], "%H:%M:%S"
            ).time()

            #return open_time <= current_time <= close_time

    return True




def get_dynamic_hours(behavior_data):

    business_hours = behavior_data.get("business_hours", [])

    day_map = {
        0: "Monday",
        1: "Tuesday",
        2: "Wednesday",
        3: "Thursday",
        4: "Friday",
        5: "Saturday",
        6: "Sunday"
    }

    formatted_hours = []

    for day_config in business_hours:
        day_number = day_config.get("day")
        day_name = day_map.get(day_number, f"Day {day_number}")

        if not day_config.get("is_open"):
            formatted_hours.append(f"{day_name}: Closed")
        else:
            open_time = day_config.get("open_time", "")[:5]
            close_time = day_config.get("close_time", "")[:5]
            formatted_hours.append(f"{day_name}: {open_time} - {close_time}")

    return "\n".join(formatted_hours)


def detect_issue(user_input: str) -> str:
    text = user_input.lower()

    REPAIR_TYPES = [
    {"id": 1, "name": "Battery"},
    {"id": 2, "name": "LCD"},
    {"id": 3, "name": "Software"},
    {"id": 4, "name": "OLED"},
    {"id": 5, "name": "OEM"},
    {"id": 6, "name": "Back Camera"},
    {"id": 7, "name": "Charge Port"},
    {"id": 8, "name": "Back Glass"},
    {"id": 9, "name": "Camera Glass"},
    {"id": 10, "name": "UB Screen"},
    {"id": 11, "name": "Dock"},
    {"id": 12, "name": "Octa / UB"},
    {"id": 13, "name": "Housing"},
    {"id": 14, "name": "Front Cam"},
    {"id": 15, "name": "Glass"},
    {"id": 16, "name": "HDMI / RETIMER"},
    {"id": 17, "name": "HDD 500GB"},
    {"id": 18, "name": "HDD 1TB"},
    {"id": 19, "name": "SSD 500GB"},
    {"id": 20, "name": "SSD 1TB"},
    {"id": 21, "name": "DISK DRIVE"},
    {"id": 22, "name": "POWER SUPPLY"},
    {"id": 23, "name": "REFLASH"},
    {"id": 24, "name": "Device Cleaning"},
    {"id": 25, "name": "Digi Only"},
    {"id": 26, "name": "LCD Only"},
    {"id": 27, "name": "Charging Repair"},
    {"id": 28, "name": "Head Jack"},
    {"id": 29, "name": "SD Card Reader"},
    {"id": 30, "name": "Card Reader"},
    {"id": 31, "name": "Cooling Fan"},
    {"id": 32, "name": "Joycon Stick/Rail"},
    {"id": 33, "name": "CPU"},
]
    # First try direct name match
    for repair in REPAIR_TYPES:
        if repair["name"].lower() in text:
            return repair["name"]

    # Synonym mapping (important for real conversations)
    SYNONYMS = {
        "screen": ["LCD", "OLED", "Glass", "UB Screen"],
        "battery": ["Battery"],
        "charge": ["Charge Port", "Charging Repair"],
        "camera": ["Back Camera", "Front Cam", "Camera Glass"],
        "software": ["Software", "REFLASH"],
        "storage": ["HDD 500GB", "HDD 1TB", "SSD 500GB", "SSD 1TB"],
        "hdmi": ["HDMI / RETIMER"],
        "fan": ["Cooling Fan"],
        "cpu": ["CPU"],
        "power": ["POWER SUPPLY"],
        "clean": ["Device Cleaning"],
        "dock": ["Dock"],
        "housing": ["Housing"],
        "glass": ["Glass", "Back Glass"],
    }

    for keyword, mapped_repairs in SYNONYMS.items():
        if keyword in text:
            return mapped_repairs[0]  # return first match

    return "UNKNOWN"

def send_appointment_link(to_number: str):
    try:
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        twilio_number = os.getenv("TWILIO_PHONE_NUMBER")
        appointment_link = os.getenv("APPOINTMENT_LINK")  # fixed typo

        if not all([account_sid, auth_token, twilio_number, appointment_link]):
            raise ValueError("Missing one or more required environment variables.")

        client = Client(account_sid, auth_token)

        message = client.messages.create(
            body=f"Thank you for calling! You can book your appointment here: {appointment_link}",
            from_=twilio_number,
            to=to_number
        )

        return message.sid

    except TwilioRestException as e:
        print(f"Twilio API error: {e}")
        return None

    except Exception as e:
        print(f"Unexpected error: {e}")
        return None

async def start_call_recording(call_sid: str):
    """Start full-call recording safely in a background thread."""
    twilio_client = Client(
        os.getenv("TWILIO_ACCOUNT_SID"),
        os.getenv("TWILIO_AUTH_TOKEN")
    )

    try:
        # Wrap sync Twilio API call in asyncio.to_thread
        await asyncio.to_thread(
            lambda: twilio_client.calls(call_sid).recordings.create(
                recording_channels="dual",  # records both sides
                recording_status_callback=f"{PUBLIC_URL}/recording-status",
                recording_status_callback_method="POST"
            )
        )
        print(f"[RECORDING] Started recording for call {call_sid}")
    except Exception as e:
        print(f"[RECORDING] Failed to start recording for {call_sid}: {e}")

# ==========================================
# ROUTES
# ==========================================

@app.on_event("startup")
async def startup_event():
    global retriever, behavior_data

    print("🚀 Server starting...")
    behavior_data = load_ai_behavior()
    print("AI Behavior Loaded")
    retriever = build_vectorstore()
    print("✅ RAG ready")
    

@app.post("/")
async def root(request: Request, background_tasks: BackgroundTasks):
    return await voice(request, background_tasks)

@app.post("/voice")
async def voice(request: Request, background_tasks: BackgroundTasks):
    try:
        form = await request.form()
        form_data = dict(form)

        speech = form_data.get("SpeechResult")
        call_sid = form_data.get("CallSid")
        from_number = form_data.get("From")

        response = VoiceResponse()

        # Initialize call session
        if call_sid not in CALL_SESSIONS:
            CALL_SESSIONS[call_sid] = {
                "messages": [],
                "phone_number": from_number,
                "issue": None,
                "call_type": "AI_RESOLVED",
                "outcome": "QUOTE_PROVIDED",
                "store_id": ID,
                "started_at": datetime.utcnow(),
                "audio_url": None,
                "transcripts": []
            }

            # --------- START FULL CALL RECORDING IN BACKGROUND ---------
            background_tasks.add_task(start_call_recording, call_sid)

        call_memory = CALL_SESSIONS[call_sid]

        # ----------------- Handle first greeting and speech -----------------
        if not speech:
            open_status = is_business_open(behavior_data)
            greetings = behavior_data.get("greetings", {})

            if open_status:
                greeting = greetings.get("opening_hours_greeting", "")
                greeting = greeting.replace("{store_name}", STORE_NAME)
                response.say(greeting, voice="alice", language="en-US")

                gather = Gather(
                    input="speech",
                    action=f"{PUBLIC_URL}/voice",
                    method="POST",
                    timeout=15,
                    speechTimeout="auto",
                    language="en-US",
                    speechModel="phone_call"
                )
                response.append(gather)
                return Response(response.to_xml(), media_type="application/xml")
            else:
                closed_msg = greetings.get("closed_hours_message", "We are closed.")
                call_memory["call_type"] = "DROPPED"
                call_memory["outcome"] = "CALL_DROPPED"
                await send_call_log(call_sid)
                response.say(closed_msg, voice="alice")
                response.hangup()
                return Response(response.to_xml(), media_type="application/xml")

        # ----------------- Process speech and AI response -----------------
        if not call_memory["issue"]:
            call_memory["issue"] = detect_issue(speech)

        lower_speech = speech.lower()

        # Manager transfer
        for item in behavior_data.get("auto_transfer_keywords", []):
            keyword = item.get("keyword", "").lower()
            if keyword and keyword in lower_speech:
                manager_number = os.getenv("MANAGER_NUMBER")
                if manager_number:
                    call_memory["call_type"] = "WARM_TRANSFER"
                    call_memory["outcome"] = "ESCALATED"
                    response.say("Connecting you to a human agent.", voice="alice")
                    response.dial(manager_number, timeout=20)
                    await send_call_log(call_sid)
                    return Response(response.to_xml(), media_type="application/xml")

        # Appointment booking
        if any(word in lower_speech for word in ["appointment", "book", "schedule"]):
            call_memory["call_type"] = "APPOINTMENT"
            call_memory["outcome"] = "APPOINTMENT_BOOKED"
            try:
                send_appointment_link(call_memory["phone_number"])
                await send_call_log(call_sid)
            except Exception as e:
                print("❌ Failed sending appointment link:", e)

        # RAG + AI
        if retriever is None:
            call_memory["call_type"] = "DROPPED"
            call_memory["outcome"] = "CALL_DROPPED"
            await send_call_log(call_sid)
            response.say("System is initializing. Please try again shortly.", voice="alice")
            response.hangup()
            return Response(response.to_xml(), media_type="application/xml")

        try:
            docs = retriever.invoke(speech)
        except Exception as e:
            print("❌ RAG ERROR:", e)
            docs = []

        context = "\n\n".join([doc.page_content for doc in docs if hasattr(doc, "page_content")]) if docs else ""

        tone = behavior_data.get("tone", "professional")
        system_behavior = f"""
You are a retail call assistant for {STORE_NAME}.
VOICE STYLE:
- Tone: {tone}
RULES:
- Answer ONLY from retrieved knowledge.
- Keep responses short and voice-friendly.
- If unsure, divert the call to manager.
- Never mention internal documents.
"""
        messages = [{"role": "system", "content": system_behavior}]
        if call_memory["messages"]:
            messages += call_memory["messages"]

        messages.append({
            "role": "user",
            "content": f"Retrieved Knowledge:\n{context}\n\nUser Question:\n{speech}"
        })

        ai_response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.3
        )
        reply = ai_response.choices[0].message.content.strip()

        # Save transcript
        call_memory["transcripts"].append({"speaker": "CUSTOMER", "message": speech})
        call_memory["transcripts"].append({"speaker": "AI", "message": reply})
        call_memory["messages"].append({"role": "user", "content": speech})
        call_memory["messages"].append({"role": "assistant", "content": reply})

        print("🤖 AI reply:", reply)

        response.say(reply, voice="alice", language="en-US")

        # Continue recording + listening for next input
        gather = Gather(
            input="speech",
            action=f"{PUBLIC_URL}/voice",
            method="POST",
            timeout=15,
            speechTimeout="auto",
            language="en-US",
            speechModel="phone_call"
        )
        response.append(gather)

        return Response(response.to_xml(), media_type="application/xml")

    except Exception as e:
        print("❌ ERROR:", e)
        if call_sid in CALL_SESSIONS:
            CALL_SESSIONS[call_sid]["call_type"] = "DROPPED"
            CALL_SESSIONS[call_sid]["outcome"] = "CALL_DROPPED"
            await send_call_log(call_sid)
        response = VoiceResponse()
        response.say("Sorry. There was a server error.", voice="alice")
        response.hangup()
        return Response(response.to_xml(), media_type="application/xml")
# ==========================================
# SYSTEM UPDATE ENDPOINT
# ==========================================

@app.post("/update-system")
async def update_system(): 
    global behavior_data

    try:
        behavior_data = load_ai_behavior()
        print("System update endpoint got hit.")

        return {
            "status": "success",
            "message": "System updated successfully"
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"RAG update failed: {str(e)}"
        )


# ==========================================
# RAG UPDATE ENDPOINT
# ==========================================


@app.post("/update-rag")
async def update_rag():
    try:
        global retriever
        retriever = rebuild_vectorstore()
        print("RAG update endpoint got hit.")

        return {
            "status": "success",
            "message": "RAG updated successfully"
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"RAG update failed: {str(e)}"
        )


# ------------------------------------------
# RECORDING STATUS - handle segments
# ------------------------------------------
@app.post("/recording-status")
async def recording_status(request: Request):
    form = await request.form()
    call_sid = form.get("CallSid")
    recording_url = form.get("RecordingUrl")

    print(f"Recording URL for {call_sid}: {recording_url}")

    # Ensure session exists
    if call_sid not in CALL_SESSIONS:
        CALL_SESSIONS[call_sid] = {"recordings": []}

    CALL_SESSIONS[call_sid].setdefault("recordings", []).append(recording_url)

    # Download segment
    twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
    twilio_token = os.getenv("TWILIO_AUTH_TOKEN")

    try:
        r = requests.get(recording_url + ".mp3", auth=(twilio_sid, twilio_token), timeout=30)
        r.raise_for_status()

        segment_index = len(CALL_SESSIONS[call_sid]["recordings"])
        file_path = os.path.join(RECORDINGS_DIR, f"{call_sid}_{segment_index}.mp3")
        with open(file_path, "wb") as f:
            f.write(r.content)

        print(f"Segment saved to {file_path}")

    except requests.RequestException as e:
        print(f"Failed to download recording segment for {call_sid}: {e}")

    return "OK"


# ------------------------------------------
# RECORDING COMPLETE - merge segments
# ------------------------------------------
@app.post("/recording-complete")
async def recording_complete(request: Request):
    form = await request.form()
    call_sid = form.get("CallSid")
    print(f"[COMPLETE] Recording complete for {call_sid}")

    if call_sid in CALL_SESSIONS:
        segments = CALL_SESSIONS[call_sid].get("recordings", [])
        if segments:
            combined = AudioSegment.empty()
            for idx, url in enumerate(segments, start=1):
                file_path = os.path.join(RECORDINGS_DIR, f"{call_sid}_{idx}.mp3")
                combined += AudioSegment.from_mp3(file_path)

            # Export full call
            full_file = os.path.join(RECORDINGS_DIR, f"{call_sid}_full.mp3")
            combined.export(full_file, format="mp3")
            CALL_SESSIONS[call_sid]["audio_url"] = full_file

            print(f"✅ Full call recording saved as {full_file}")


    return "", 200



