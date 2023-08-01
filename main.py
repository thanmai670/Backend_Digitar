import json
import os

# from transformers import GPT2LMHeadModel, GPT2Tokenizer
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from typing import Dict, Callable
from scipy.signal import resample_poly
import io
from dotenv import load_dotenv
import openai
import numpy as np
import base64
import riva.client
import wave
from deepgram import Deepgram
from starlette.middleware.cors import CORSMiddleware

load_dotenv()
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


# def transcribe_streaming_audio(uri, audio_chunk_generator, streaming_config):
#     auth = riva.client.Auth(uri=uri)
#     asr_service = riva.client.ASRService(uri, auth)

#     response_generator = asr_service.streaming_response_generator(
#         audio_chunk_generator, streaming_config
#     )

#     for response in response_generator:
#         for result in response.results:
#             if result.is_final:
#                 print(result.alternatives[0].transcript)


def generate_response(input_text):
    print("method triggered")
    gpt3_api_key = os.getenv("gpt3_api_key")
    openai.api_key = gpt3_api_key

    # Call the GPT-3 API to generate a response
    response = openai.Completion.create(
        engine="text-davinci-003",
        prompt=input_text,
        max_tokens=1000,
        temperature=0.2,
    )

    # Ensure there is at least one choice in the response
    if response.choices:
        generated_text = response.choices[0].text.strip()

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


# @app.get("/", response_class=HTMLResponse)
# def get(request: Request):
#     return templates.TemplateResponse("index.html", {"request": request})


# @app.websocket("/listen")
# async def websocket_endpoint(websocket: WebSocket):
#     await websocket.accept()
#     print("WebSocket connection accepted")

#     offline_config = riva.client.RecognitionConfig(
#         encoding=riva.client.AudioEncoding.LINEAR_PCM,
#         sample_rate_hertz=16000,
#         language_code="en-US",
#     )
#     streaming_config = riva.client.StreamingRecognitionConfig(
#         config=offline_config, interim_results=True
#     )

#     audio_chunks = []
#     last_transcript = ""

#     try:
#         while True:
#             print("Waiting to receive data")
#             data = await websocket.receive_text()
#             print("Data received:", data[:50], "...")

#             audio_data = base64.b64decode(data)
#             audio_file = wave.open(io.BytesIO(audio_data), "rb")

#             audio_frames = audio_file.readframes(audio_file.getnframes())
#             audio_np = np.frombuffer(audio_frames, dtype=np.int16)
#             sample_rate = 44000
#             if sample_rate != 16000:
#                 audio_np = resample_poly(audio_np, 16000, sample_rate)
#             audio_np = (audio_np * 32767).astype(np.int16)

#             audio_chunk_generator = (
#                 audio_np.tobytes(),
#             )  # Generate a tuple with a single chunk

#             response_generator = asr_service.streaming_response_generator(
#                 audio_chunk_generator, streaming_config
#             )

#             for response in response_generator:
#                 print("top of for loop")
#                 if response.results:
#                     for result in response.results:
#                         if result.alternatives and not result.is_final:
#                             last_transcript += result.alternatives[0].transcript + " "

#                         if result.is_final:
#                             if last_transcript:
#                                 print(f"Last transcript: {last_transcript}")
#                                 response_text = generate_response(last_transcript)
#                                 print(f"response_gpt:{response_text}")

#                                 with open("transcript.json", "w") as f:
#                                     json.dump(last_transcript, f)

#                                 await websocket.send_text(
#                                     json.dumps(
#                                         {
#                                             "transcribed_text": last_transcript,
#                                             "response_text": response_text,
#                                         }
#                                     )
#                                 )

#                                 last_transcript = ""
#                             else:
#                                 print("Last transcript is None for a final result.")

#                 else:
#                     print("No results in response.")

#             audio_chunks = []

#     except Exception as e:
#         print(f"Error: {e}")
#         await websocket.close()


async def process_audio(fast_socket: WebSocket):
    async def get_transcript(data: Dict) -> None:
        if "channel" in data:
            transcript = data["channel"]["alternatives"][0]["transcript"]

            if transcript:
                response_text, response_audio = generate_response(transcript)
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

    try:
        deepgram_socket = await process_audio(websocket)

        while True:
            data = await websocket.receive_bytes()
            deepgram_socket.send(data)

    except Exception as e:
        raise Exception(f"Could not process audio: {e}")
    finally:
        await websocket.close()
