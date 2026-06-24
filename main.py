import os
import json
import base64
import asyncio
from datetime import datetime
from fastapi import FastAPI, Response, WebSocket, Request
from fastapi.middleware.cors import CORSMiddleware
from twilio.rest import Client
from google import genai
from google.genai import types
from dotenv import load_dotenv
import wave
import audioop  # Standard library utility to decode Mu-law if saving raw inbound
import random

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PUBLIC_CODESPACE_URL = "https://expert-space-acorn-69jp4q5vwp9c4wrv-8000.app.github.dev"
OUTPUT_DIR = "call_outputs"

# Ensure the root outputs directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)

@app.post("/twiml")
async def get_twiml():
    websocket_url = PUBLIC_CODESPACE_URL.replace("https://", "wss://") + "/media-stream"
    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Connect>
            <Stream url="{websocket_url}" />
        </Connect>
    </Response>
    """
    return Response(content=twiml_response, media_type="application/xml")


@app.post("/twilio-status")
async def twilio_status(request: Request):
    data = await request.form()
    print("📣 Twilio status callback:", dict(data))
    return Response(status_code=200)


@app.websocket("/media-stream")
async def handle_media_stream(twilio_ws: WebSocket):
    requested = twilio_ws.scope.get("subprotocols", [])
    print("🎧 WebSocket subprotocols requested:", requested)
    if "twilio-media-stream" in requested:
        await twilio_ws.accept(subprotocol="twilio-media-stream")
    else:
        await twilio_ws.accept()
    print("📞 Twilio phone call connection established!")
    print("🎧 WebSocket subprotocol negotiated:", twilio_ws.scope.get("subprotocol"))
    print("🧾 WebSocket scope:", {k: twilio_ws.scope[k] for k in ['client', 'scheme', 'path', 'subprotocols', 'subprotocol'] if k in twilio_ws.scope})
    
    # 1. Create a distinct folder for this call session
    session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    call_folder = os.path.join(OUTPUT_DIR, f"call_{session_id}")
    os.makedirs(call_folder, exist_ok=True)
    
    transcript_path = os.path.join(call_folder, "transcript.txt")
    
    # Helper function to append to our transcript file safely
    def log_transcript(speaker, text):
        with open(transcript_path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%M:%S')}] {speaker}: {text}\n")
    
    # 2. Setup WAV files for recording audio streams
    # Athena (Inbound to us): 8000Hz, 1 channel (Mono), Mu-law will be converted to 16-bit PCM (2 bytes per sample)
    athena_wav = wave.open(os.path.join(call_folder, "athena_inbound.wav"), "wb")
    athena_wav.setnchannels(1)
    athena_wav.setsampwidth(2) 
    athena_wav.setframerate(8000)

    # Gemini Patient (Outbound from us): 24000Hz, 1 channel (Mono), 16-bit Linear PCM (2 bytes per sample)
    gemini_wav = wave.open(os.path.join(call_folder, "gemini_outbound.wav"), "wb")
    gemini_wav.setnchannels(1)
    gemini_wav.setsampwidth(2)
    gemini_wav.setframerate(24000)

    ai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    # Use the fully-qualified model name discovered via the SDK
    model_id = "models/gemini-3.1-flash-live-preview"

    scenarios = [
    "You want to schedule a routine checkup next week.",
    "You urgently need an appointment and must ask if you can come in this upcoming Sunday at 10:00 AM.", 
    "You need a medication refill for Lisinopril. If asked for a pharmacy, say CVS on Main Street.",
    "You need to know if they take Blue Cross Blue Shield insurance and what time they close on Fridays."
    ]

    behaviors = [
    "The Interrupter: Frequently cut off the agent mid-sentence if they take too long or read long scripts. Test their barge-in handling.",
    "The Unclear Patient: Change your mind halfway through dates/times (e.g., 'Actually wait, Monday is bad, let's do Thursday').",
    "The Distracted Patient: Occasionally drift off or talk to someone else in the room (e.g., 'Sorry, talking to my dog, what did you ask?').",
    "Standard: Cooperative, polite, and direct. Provides a baseline control test."
    ]

    traits = [
    "Severe Stutter: Simulate an anxious stutter using text formatting (e.g., 'I-I n-need to m-make an appointment').",
    "Heavy Breather: Include physical tags in your speech like '(heavy sigh)', '*wheeze*', or 'Sorry, I'm out of breath...'",
    "The Mumbler: Use excessive verbal fillers like 'uh', 'um', 'like', and 'you know'.",
    "None: Clear, standard speech pacing."
    ]

    selected_scenario = random.choice(scenarios)
    selected_behavior = random.choice(behaviors)
    selected_trait = random.choice(traits)

    system_instruction_text = (
    "You are an advanced voice-agent evaluator acting as a human patient calling a medical clinic. "
    "Your primary goal is to simulate a highly realistic, lucid, and contextually aware human conversation "
    "while naturally stress-testing the target AI agent's capabilities. Do NOT act like a static benchmark runner. "
    "Engage in natural, back-and-forth turn-taking unless an interruption behavior is triggered.\n\n"
    "--- CURRENT PATIENT PROFILE ---\n"
    f"[SCENARIO]: {selected_scenario}\n"
    f"[PRIMARY BEHAVIOR]: {selected_behavior}\n"
    f"[SPEECH TRAIT]: {selected_trait}\n\n"
    "--- CONVERSATIONAL GUIDELINES ---\n"
    "1. Active Steering: Keep the conversation moving toward your scenario goal, but let the agent lead the process.\n"
    "2. Natural Pacing: Respond with human-like latency. Use filler words naturally, especially if your profile dictates it.\n"
    "3. Realistic Responses: If the agent asks for your name or DOB, provide realistic-sounding fake information on the fly.\n"
    "4. Adhere to Traits: Embody your assigned behavior and speech trait flawlessly throughout the entire conversation. Do not break character."
)
    
    config = types.LiveConnectConfig(
        responseModalities=[types.Modality.AUDIO],
        speechConfig=types.SpeechConfig(languageCode='en-US'),
        systemInstruction=types.Content(parts=[
                types.Part.from_text(
                    text=system_instruction_text
                )
        ])
    )
    
    stream_sid = None

    gemini_session = None
    try:
        async with ai_client.aio.live.connect(model=model_id, config=config) as gemini_session:
            print(f"Connected to Gemini Live API. Saving artifacts to: {call_folder}")
            log_transcript("SYSTEM", "Call session started.")

            async def receive_from_twilio():
                """Loop A: Listens to Twilio (Athena Clinic Bot) and logs audio."""
                nonlocal stream_sid
                MEDIA_GAP_SECONDS = 0.75
                last_media_time = None
                pending_audio_sent = False

                async def maybe_end_audio_stream():
                    nonlocal pending_audio_sent
                    if pending_audio_sent and gemini_session is not None:
                        try:
                            await gemini_session.send_realtime_input(audio_stream_end=True)
                            print("🔔 Sent audio_stream_end to Gemini")
                        except Exception as e:
                            print("Error sending audio_stream_end:", e)
                        pending_audio_sent = False

                try:
                    async for message in twilio_ws.iter_text():
                        print("📩 Received WS message from Twilio:", message[:200])
                        packet = json.loads(message)

                        if packet['event'] == 'start':
                            stream_sid = packet['start']['streamSid']
                            print(f"Streaming initialized. Stream SID: {stream_sid}")

                        elif packet['event'] == 'media':
                            media_payload = packet['media']['payload']
                            raw_audio_bytes = base64.b64decode(media_payload)
                            print(f"📥 Received {len(raw_audio_bytes)} bytes from Twilio")

                            now = datetime.now().timestamp()
                            if last_media_time is not None and now - last_media_time > MEDIA_GAP_SECONDS:
                                await maybe_end_audio_stream()

                            # Write incoming audio to Athena's WAV file
                            # Note: Twilio sends Mu-law. We convert it to standard Linear PCM on-the-fly for clean playback.
                            pcm_bytes = audioop.ulaw2lin(raw_audio_bytes, 2)
                            athena_wav.writeframes(pcm_bytes)

                            # Send realtime audio to Gemini using the newer `audio` field.
                            # Twilio sends Mu-law, so use the PCM-converted bytes here.
                            try:
                                await gemini_session.send_realtime_input(
                                    audio=types.Blob(
                                        data=pcm_bytes,
                                        mime_type="audio/pcm;rate=8000"
                                    )
                                )
                                pending_audio_sent = True
                            except Exception as e:
                                print("Error sending to Gemini realtime input:", e)

                            last_media_time = now

                        elif packet['event'] == 'stop':
                            if pending_audio_sent:
                                await maybe_end_audio_stream()
                            print("Twilio call hung up.")
                            log_transcript("SYSTEM", "Twilio call disconnected by remote endpoint.")
                            break
                        else:
                            print("⚠️ Unhandled Twilio event:", packet.get('event'))
                except Exception as e:
                    print(f"Error in Twilio receipt loop: {e}")
                    import traceback
                    traceback.print_exc()

            async def send_to_twilio():
                """Loop B: Captures Gemini's voice output, saves it, and sends it to the phone."""
                try:
                    while True:  # Keep looping to handle multiple turns
                        async for response in gemini_session.receive():
                            if response.server_content and response.server_content.interrupted:
                                if stream_sid:
                                    print("⚠️ Interruption detected! Flushing Twilio audio buffer...")
                                    log_transcript("SYSTEM", "[Interruption Detected - Gemini response stopped midway]")
                                    await twilio_ws.send_text(json.dumps({
                                        "event": "clear",
                                        "streamSid": stream_sid
                                    }))
                                continue
                            
                            if response.server_content and response.server_content.model_turn:
                                if response.server_content.output_transcription and response.server_content.output_transcription.text:
                                    log_transcript("GEMINI (Patient)", response.server_content.output_transcription.text)

                                for part in response.server_content.model_turn.parts:
                                    if part.text:
                                        log_transcript("GEMINI (Patient)", part.text)

                                    audio_data = None
                                    if response.data is not None:
                                        audio_data = response.data
                                    elif part.inline_data is not None:
                                        audio_data = part.inline_data.data

                                    if not audio_data:
                                        continue

                                    print(f"📤 Sending {len(audio_data)} bytes to Twilio")

                                    # Write outgoing audio to Gemini's WAV file.
                                    gemini_wav.writeframes(audio_data)

                                    # Downsample Gemini audio to 8000Hz for Twilio.
                                    try:
                                        downsampled, _state = audioop.ratecv(audio_data, 2, 1, 24000, 8000, None)
                                    except Exception:
                                        print("⚠️ Downsample failed, sending raw audio to Twilio")
                                        downsampled = audio_data

                                    # Convert outgoing PCM to 8-bit mu-law for Twilio playback.
                                    try:
                                        mu_law_audio = audioop.lin2ulaw(downsampled, 2)
                                    except Exception:
                                        print("⚠️ PCM-to-mu-law conversion failed, sending raw PCM to Twilio")
                                        mu_law_audio = downsampled

                                    base64_audio = base64.b64encode(mu_law_audio).decode('utf-8')

                                    if stream_sid:
                                        message = {
                                            "event": "media",
                                            "streamSid": stream_sid,
                                            "media": {
                                                "payload": base64_audio
                                            }
                                        }
                                        await twilio_ws.send_text(json.dumps(message))
                except Exception as e:
                    print(f"Error in Twilio sending loop: {e}")
                    import traceback
                    traceback.print_exc()

            # Execute both streams concurrently
            await asyncio.gather(receive_from_twilio(), send_to_twilio())
    except Exception as e:
        print("⚠️ Gemini Live connect failed:", e)
        import traceback
        traceback.print_exc()
        log_transcript("SYSTEM", f"Gemini Live unavailable: {e}")

        # Fallback: keep receiving Twilio audio and save to file until the call ends
        async def fallback_receive():
            try:
                async for message in twilio_ws.iter_text():
                    print("📩 Received WS message from Twilio (fallback):", message[:200])
                    packet = json.loads(message)
                    if packet.get('event') == 'media':
                        media_payload = packet['media']['payload']
                        raw_audio_bytes = base64.b64decode(media_payload)
                        pcm_bytes = audioop.ulaw2lin(raw_audio_bytes, 2)
                        athena_wav.writeframes(pcm_bytes)
                    elif packet.get('event') == 'stop':
                        print("Twilio call hung up (fallback).")
                        log_transcript("SYSTEM", "Twilio call disconnected by remote endpoint.")
                        break
            except Exception as e2:
                print("Error in fallback receive:", e2)
                import traceback
                traceback.print_exc()

        await fallback_receive()

    # Clean close on completion to prevent corrupted WAV headers
    athena_wav.close()
    gemini_wav.close()
    print(f"💾 Call recording saved successfully in {call_folder}")


def trigger_outbound_call():
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_PHONE_NUMBER")

    missing = [name for name, value in [
        ("TWILIO_ACCOUNT_SID", sid),
        ("TWILIO_AUTH_TOKEN", token),
        ("TWILIO_PHONE_NUMBER", from_number)
    ] if not value]

    if missing:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(missing) +
            ". Make sure your .env file contains these values and restart the app."
        )

    twilio_client = Client(sid, token)
    call = twilio_client.calls.create(
        to="+1-805-439-8008",  # The AI Engine Challenge Test Number [cite: 15, 18]
        from_=from_number,
        url=f"{PUBLIC_CODESPACE_URL}/twiml",
        status_callback=f"{PUBLIC_CODESPACE_URL}/twilio-status",
        time_limit=180,
    )
    print(f"📞 Outbound Call Triggered successfully! SID: {call.sid} status={call.status}")


if __name__ == "__main__":
    import uvicorn
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "call":
        trigger_outbound_call()
    else:
        uvicorn.run("main:app", host="0.0.0.0", port=8000, log_level="info")