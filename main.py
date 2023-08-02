import json
import os
from uuid import uuid4


# from transformers import GPT2LMHeadModel, GPT2Tokenizer
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from typing import Dict, Callable
from scipy.signal import resample_poly
import io

import openai
import numpy as np
import base64
import riva.client
import wave
from deepgram import Deepgram
from starlette.middleware.cors import CORSMiddleware


dg_client = Deepgram(os.getenv("DEEPGRAM_API_KEY"))
app = FastAPI()
templates = Jinja2Templates(directory="templates")
origins = [
    "http://localhost:3000",  # Assuming your React app runs on port 3000
    # "http://yourReactAppAnotherOrigin.com",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

uri = "3.84.243.225:50051"
auth = riva.client.Auth(uri=uri)
asr_service = riva.client.ASRService(auth)

language_code = "en-US"
sample_rate_hz = 16000
nchannels = 1
sampwidth = 2


conversation_histories = {}

websocket_to_client_id = {}


def generate_response(input_text, client_id):
    print("method triggered")
    gpt3_api_key = os.getenv("gpt3_api_key")
    openai.api_key = gpt3_api_key

    conversation_history = conversation_histories.get(client_id, "")
    if (
        len((conversation_history + input_text).split()) > 4090
    ):  # 4090 is an arbitrary number slightly less than 4096
        # Remove oldest messages from the history until it's short enough
        conversation_history = conversation_history.split("\n")[2:]
        while len("\n".join(conversation_history + [input_text]).split()) > 4090:
            conversation_history = conversation_history[2:]

    prompt = f"{conversation_history}\nUser: {input_text}"

    # Call the GPT-3 API to generate a response
    response = openai.Completion.create(
        engine="text-davinci-003",
        prompt=prompt,
        max_tokens=1000,
        temperature=0.2,
    )

    # Ensure there is at least one choice in the response
    if response.choices:
        generated_text = response.choices[0].text.strip()
        conversation_histories[client_id] = f"{prompt}\nAI: {generated_text}"

        # Riva TTS Service
        tts_service = riva.client.SpeechSynthesisService(auth)
        language_code = "en-US"
        sample_rate_hz = 44000

        # Generate audio from text
        responses = tts_service.synthesize_online(
            generated_text, language_code=language_code, sample_rate_hz=sample_rate_hz
        )

        streaming_audio = b""
        for resp in responses:
            streaming_audio += resp.audio
        streaming_output_file = "my_streaming_synthesized_speech.wav"
        with wave.open(streaming_output_file, "wb") as out_f:
            out_f.setnchannels(nchannels)
            out_f.setsampwidth(sampwidth)
            out_f.setframerate(sample_rate_hz)
            out_f.writeframesraw(streaming_audio)
        # Encode the audio to base64
        with open(streaming_output_file, "rb") as audio_file:
            audio_bytes = audio_file.read()

            audio_base64bytes = base64.b64encode(audio_bytes)
            audio_string = audio_base64bytes.decode("utf-8")

        # Return the response and audio url
        return generated_text, audio_string

    # Return None if there were no choices in the response
    return None, None


async def process_audio(fast_socket: WebSocket, client_id):
    async def get_transcript(data: Dict) -> None:
        if "channel" in data:
            transcript = data["channel"]["alternatives"][0]["transcript"]

            if transcript:
                response_text, response_audio = generate_response(transcript, client_id)
                print(f"response_gpt:{response_text}")

                with open("transcript.json", "w") as f:
                    json.dump(transcript, f)

                await fast_socket.send_text(
                    json.dumps(
                        {
                            "transcribed_text": transcript,
                            "response_text": response_text,
                            "response_audio": response_audio,
                        }
                    )
                )

    deepgram_socket = await connect_to_deepgram(get_transcript)

    return deepgram_socket


async def connect_to_deepgram(transcript_received_handler: Callable[[Dict], None]):
    try:
        socket = await dg_client.transcription.live(
            {"punctuate": True, "interim_results": False}
        )
        socket.registerHandler(
            socket.event.CLOSE, lambda c: print(f"Connection closed with code {c}.")
        )
        socket.registerHandler(
            socket.event.TRANSCRIPT_RECEIVED, transcript_received_handler
        )

        return socket
    except Exception as e:
        raise Exception(f"Could not open socket: {e}")


@app.get("/", response_class=HTMLResponse)
def get(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.websocket("/listen")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    client_id = str(uuid4())  # Create a new UUID for each client
    websocket_to_client_id[websocket] = client_id

    try:
        deepgram_socket = await process_audio(websocket, client_id)

        while True:
            data = await websocket.receive_bytes()
            deepgram_socket.send(data)

    except Exception as e:
        raise Exception(f"Could not process audio: {e}")
    finally:
        await websocket.close()
        del websocket_to_client_id[websocket]
