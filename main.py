import os
import time
import json
import asyncio
from typing import Optional
from dotenv import load_dotenv
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import azure.cognitiveservices.speech as speechsdk
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
load_dotenv() # Load the environmental variables from .env file

timer = None
speaker_name_dict = {}

class file_create:
    def __init__(self, speaker_name: str, text: str, time: str):
        self.speaker_name = speaker_name
        self.text = text
        self.time = time

    def __str__(self):
        # Format the entry as a string
        return f"[{self.time}] {self.speaker_name}: {self.text}"

file_container: list[file_create]

class bcolors: # Only to apply colors to the prints
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Speech key and region from your Azure Speech Recognition service
speech_key = os.getenv("SPEECH_KEY")
speech_region = os.getenv("SPEECH_REGION")

def create_diarization(loop, queue):
    # This example requires environment variables named "SPEECH_KEY" and "SPEECH_REGION"
    # Create the configuration of the recognizer from your account of Azure
    speech_config = speechsdk.SpeechConfig(
        subscription=speech_key, 
        region=speech_region
    )
    speech_config.set_property(property_id=speechsdk.PropertyId.SpeechServiceResponse_DiarizeIntermediateResults, value='true')

    # Gstreamer is used here to convert audio stream received to the format azure requires
    format = speechsdk.audio.AudioStreamFormat(compressed_stream_format=speechsdk.AudioStreamContainerFormat.ANY)
    stream = speechsdk.audio.PushAudioInputStream(format)
    audio_config = speechsdk.audio.AudioConfig(stream=stream)

    transcriber = speechsdk.transcription.ConversationTranscriber(
        speech_config=speech_config, 
        audio_config=audio_config,
        language="en-US")

    transcribing_stop = False

    # Calculate the elapsed time
    def get_elapsed_time() -> str: 
        elapsed_seconds = int(time.time() - timer)
        minutes, seconds = divmod(elapsed_seconds, 60)
        return f"{minutes:02}:{seconds:02}"

    # Make a dictionary everytime record button was pressed and check if speaker id match
    def get_speaker_name_by_voice(voice_text: str) -> Optional[str]:
        prefix_for_name = ["name is", "I am", "I'm"]

        for prefix in prefix_for_name:
            if prefix in voice_text:
                parts = voice_text.split(prefix, 1)  # Split only on the first occurrence of prefix
                if len(parts) > 1:
                    after_prefix = parts[1].strip()
                    period_index = after_prefix.find('.')
                    # if we found "." use the "." as the end point, should there be more than 2 words, we take the first 2
                    if period_index != -1:
                        name = after_prefix[:period_index].strip()
                        if len(name.split(" ")) > 2:
                            name = " ".join(name.split(" ")[:2])
                        return name
                    # else use the whole sentence after the prefix then take the first 2 words
                    else:
                        name = after_prefix.strip()
                        if len(name.split(" ")) > 2:
                            name = " ".join(name.split(" ")[:2])
                        return name
        return None
    
    # used to send words of a sentence currently being transcribed to FE so it can be displayed there
    def transcriber_transcribing_cb(evt: speechsdk.SpeechRecognitionEventArgs):
        asyncio.run_coroutine_threadsafe(queue.put(f"Transcribing|Time|{evt.result.text}|{evt.result.speaker_id}"), loop)

    # used to map name with id, and will send the full word with time to FE and also saved on .txt
    def transcriber_transcribed_cb(evt: speechsdk.SpeechRecognitionEventArgs):
        print('\nTRANSCRIBED:')
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            print('\tText={}'.format(evt.result.text))
            print('\tSpeaker ID={}\n'.format(evt.result.speaker_id))
            speaker_name = get_speaker_name_by_voice(evt.result.text)
            speaker_name_found_on_dict = speaker_name_dict.get(evt.result.speaker_id, None)
            if speaker_name != None and speaker_name_found_on_dict == None:
                speaker_name_dict[evt.result.speaker_id] = speaker_name
                speaker_name_found_on_dict = speaker_name
            if speaker_name_found_on_dict == None or speaker_name_found_on_dict == "":
                speaker_name_found_on_dict = "Unknown"

            speaker_name_found_on_dict = speaker_name_found_on_dict + " - " + str(evt.result.speaker_id).replace("Guest", "Speaker")
            elapsed_time = get_elapsed_time()
            if evt.result.text != "" and evt.result.text != None:
                entry = file_create(speaker_name=speaker_name_found_on_dict, text=evt.result.text, time=elapsed_time)
                file_container.append(entry)
            asyncio.run_coroutine_threadsafe(queue.put(f"Transcribed|{elapsed_time}|{evt.result.text}|{speaker_name_found_on_dict}"), loop)
        elif evt.result.reason == speechsdk.ResultReason.NoMatch:
            print('\tNOMATCH: Speech could not be TRANSCRIBED: {}'.format(evt.result.no_match_details))

    def transcriber_session_started_cb(evt: speechsdk.SessionEventArgs):
        print('SessionStarted event')

    def stop_cb(evt: speechsdk.SessionEventArgs):
        #"""callback that signals to stop continuous recognition upon receiving an event `evt`"""
        print('CLOSING on {}'.format(evt))
        nonlocal transcribing_stop
        transcribing_stop = True

    # Connect callbacks to the events fired by the conversation transcriber
    transcriber.transcribing.connect(transcriber_transcribing_cb)
    transcriber.transcribed.connect(transcriber_transcribed_cb)
    transcriber.session_started.connect(transcriber_session_started_cb)
    # stop transcribing on either session stopped or canceled events
    transcriber.session_stopped.connect(stop_cb)
    transcriber.canceled.connect(stop_cb)

    return transcriber, stream

@app.websocket("/ws") # Change to your desired websocket endpoint
async def audio_streaming(websocket: WebSocket):
    await websocket.accept() # Accept client connection
    
    loop = asyncio.get_event_loop() # Get the asyncio event loop
    message_queue = asyncio.Queue() # Create a message queue to store the results of speech recognition
    
    transcriber, stream = create_diarization(loop, message_queue) # Create the speech recognizer and the audio stream

    async def receive_audio(websocket, stream):
        audio_data = b"" # Store the audio data in bytes
        print(f"{bcolors.OKGREEN}WebSocket -> Receiving audio from client and saving into stream...{bcolors.ENDC}")
        while True: # As long as the customer is connected
            try: # Attempt to receive audio data from the client
                data = await websocket.receive_bytes()  # Receive audio data from the client
                audio_data += data  # Store audio all data chunks in a variable

                stream.write(data)  # Write audio data to the stream buffer

                # print(f"{bcolors.OKCYAN}WebSocket -> Stream data en bytes: {len(data)}{bcolors.ENDC}", end="\n")  # Data that are being sent from the client
            except WebSocketDisconnect:  # If the client is disconnected
                print(f"{bcolors.FAIL}Azure Speech Recognition -> Stream closed{bcolors.ENDC}")
                stream.close()  # Close the stream
                global timer
                timer = time.time()

                print(f"{bcolors.OKBLUE}API -> Websocket client disconnected!{bcolors.ENDC}")
                print(f"{bcolors.OKBLUE}API -> Stopping continuous recognition...{bcolors.ENDC}")
                transcriber.stop_transcribing_async() # Stop speech recognition
                print(f"{bcolors.OKBLUE}API -> Continuous recognition stopped!{bcolors.ENDC}")
                break
            except Exception as e: # If an error occurs
                print(f"Error here: {e}")
                break # Exiting the loop
        
        with open("recording.txt", "w") as file:
            for entry in file_container:
                if entry.speaker_name == None:
                    entry.speaker_name = "Unknown"
                if entry.text != None:
                    file.write(f"Start Time {str(entry.time)}" + "\n")
                    file.write(f"Voice to text: {entry.text}" + "\n")
                    file.write(f"Speaker: {entry.speaker_name}" + "\n\n")

    async def send_messages():
        """
        Allows messages recognized by the Azure service to be sent to the client via the websocket to the client
        """
        while True: # As long as the customer is connected
            message = await message_queue.get() # Get the recognized text from the queue
            await websocket.send_text(message) # Send the text to the websocket client

    try:
        global timer
        timer = time.time()
        global speaker_name_dict, file_container
        speaker_name_dict = {}
        file_container = []
        transcriber.start_transcribing_async() # Start continuous Transcribing
        print("API -> Continuous recognition running, say something to process data...")
        await asyncio.gather(receive_audio(websocket, stream), send_messages()) # Execute the functions of receiving audio and sending messages back to the client.

    except Exception as e:
        print(f"Error: {e}")

@app.get("/download/")
async def download_file():
    if os.path.exists("recording.txt"):
        return FileResponse(path="recording.txt")
    return {"error": "File not found"}