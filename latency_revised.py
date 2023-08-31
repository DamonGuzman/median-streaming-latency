""" 
This script will run cursor latency analysis on all wav files in the ./audio folder relative to this file

Each file will be streamed to a local instance of On Premise Deepgram in chunks of size REALTIME_RESOLUTION

When the speech_final message is received we compare the start + duration (The Transcript Cursor) with the last chunk of audio we sent (The Audio Cursor)

We collect the latencies for each speech_final message and once the file is processed we calculate the median of those latencies and print them out in csv format

Once we have all the median latencies we can calculate the P95 using an external tool like Google Sheets

You can run this and output to a log file like this

python3 -u latency.py > log.txt &
"""


import argparse
import asyncio
import base64
import json
import sys
import wave
import websockets
import time
import statistics
import os

# Location of all the wav files
directory = 'audio'

# Mimic sending a real-time stream by sending this many seconds of audio at a time.
REALTIME_RESOLUTION = 0.02 # 20ms
ENDPOINTING = 100 # 100ms of silence will trigger speech_final
MODEL = 'phonecall'
TIER = 'nova'
ENCODING = 'linear16'
MULTICHANNEL = 'false' # We are testing single channel audio
INTERIM_RESULTS = 'true' # We need this enabled for speech_final to work

results = []

async def run(file, data, channels, sample_width, sample_rate):
    # How many bytes are contained in one second of audio.
    byte_rate = sample_width * sample_rate * channels
    audio_cursor = 0.
    latencies = []
    sent_times = []
    sent_times_after = []
    received_times = []
    iterations_ran = 0
    time_differences = []

    async with websockets.connect(
        
        # Testing against local on prem instance
        f'ws://localhost:8080/v1/listen?channels={channels}&sample_rate={sample_rate}&encoding={ENCODING}&multichannel={MULTICHANNEL}&interim_results={INTERIM_RESULTS}&model={MODEL}&tier={TIER}&endpointing={ENDPOINTING}'
        
        # Testing against hosted Deepgram
        #f'wss://api.deepgram.com/v1/listen?channels={channels}&sample_rate={sample_rate}&encoding={ENCODING}&multichannel={MULTICHANNEL}&interim_results={INTERIM_RESULTS}&model={MODEL}&tier={TIER}&endpointing={ENDPOINTING}',
        #extra_headers={
        #   'Authorization': 'Token {}'.format('TOKEN')
        #}
    ) as ws:
        async def sender(ws):
            """ Sends the data, mimicking a real-time connection.
            """
            nonlocal data, audio_cursor
            
            try:
                # Keep track of when we started
                start = time.time()
                while len(data):
                    # How many bytes are in `REALTIME_RESOLUTION` seconds of audio?
                    i = int(byte_rate * REALTIME_RESOLUTION)

                    chunk, data = data[:i], data[i:]

                    # Send the data
                    sent_times.append(time.time())
                    await ws.send(chunk)
                    sent_times_after.append(time.time())

                    # Move the audio cursor
                    audio_cursor += REALTIME_RESOLUTION

                    # Since sleep is not perfect we need to adjust each sleep duration to maintain the correct speed of sending audio chunks
                    end_now = time.time()
                    duration_now = end_now - start
                    delta = duration_now - audio_cursor

                    # Mimic real-time by waiting `REALTIME_RESOLUTION` seconds before the next packet.
                    sleepTime = REALTIME_RESOLUTION - delta

                    # Need to sleep a little to give the receiver time to process incoming messages
                    if sleepTime < 0:
                      sleepTime = 0.005

                    # sleep so the next audio chunk is sent on time
                    await asyncio.sleep(0.020)

                # A CloseStream message tells Deepgram that no more audio
                # will be sent. Deepgram will close the connection once all
                # audio has finished processing.
                await ws.send(json.dumps({
                    "type": "CloseStream"
                }))
            except Exception as e:
                print(f'Error while sending: {e}')
                raise

        async def receiver(ws):
            """ Print out the messages received from the server.
            """
            try:
                nonlocal audio_cursor, latencies
                transcript_cursor = 0.
                async for msg in ws:
                    msg = json.loads(msg)

                    if 'request_id' in msg:
                        # This is the final metadata message. It gets sent as the
                        # very last message by Deepgram during a clean shutdown.
                        # There is no transcript in it.
                        continue

                    if msg['speech_final']:
                        received_times.append(time.time())
                        print(len(received_times), len(sent_times))
                        time_differences.append(received_times[-1] - sent_times_after[-1])
                        
                        transcript_cursor = msg['start'] + msg['duration']

                        # Get the current delta between the end of the last transcript and the audio cursor
                        cursor_latency = audio_cursor - transcript_cursor

                        # keep track of the latency values
                        latencies.append(cursor_latency)

                        # Debug
                        # print(f'Sent times: {sent_times}, Received times: {received_times}, sent times after: {sent_times_after}')
                        average = statistics.mean(time_differences)
                        print(f'Average time difference: {average:.4f}')
                        
                        print(f'Measuring... Audio cursor = {audio_cursor:.4f}, Transcript cursor = {transcript_cursor:.4f}, Cursor Latency: {cursor_latency:.4f}')

            except Exception as e:
                print(f'Error while recieving: {e}')
                raise

            try:
                if len(latencies) > 0:
                    median_latency = statistics.median(latencies)
                    print(f'{file}, {median_latency:.4f}')
                else:
                    print(f'{file}, No speech_final detected!')

            except Exception as e:
                print(f'Error printing stats: {e}')
                raise

        await asyncio.wait([
            asyncio.ensure_future(sender(ws)),
            asyncio.ensure_future(receiver(ws))
        ])
                
###############################################################################
def main():
    """ Entrypoint for the example."
    """
    files = os.listdir(directory)
    files.sort()
    print(f'File, Median')
    for filename in files:
        file = os.path.join(directory, filename)
        # checking if it is a file
        if os.path.isfile(file):
            # make sure its a wav file
            if file.endswith('.wav'):
                # Open the audio file.
                with wave.open(file, 'rb') as fh:
                    (channels, sample_width, sample_rate, num_samples, _, _) = fh.getparams()
                    assert sample_width == 2, 'WAV data must be 16-bit.'
                    data = fh.readframes(num_samples)
                # Debug
                print(f'Channels = {channels}, Sample Rate = {sample_rate} Hz, Sample width = {sample_width} bytes, Size = {len(data)} bytes', file=sys.stderr)

                # Run the test.
                asyncio.get_event_loop().run_until_complete(run(file, data, channels, sample_width, sample_rate))
###############################################################################
if __name__ == '__main__':
    sys.exit(main() or 0)
