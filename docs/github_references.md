# GitHub references

- Pipecat's FastAPI WebSocket transport demonstrates separate receive and send paths and explicit disconnect cleanup. Inspected commit `a8e8bf8de5da0bb0f7475fa8e7dffd8b2ffd8a16`. BSD 2-Clause. Used as a design reference. https://github.com/pipecat-ai/pipecat/blob/a8e8bf8de5da0bb0f7475fa8e7dffd8b2ffd8a16/src/pipecat/transports/websocket/fastapi.py
- GoogleChromeLabs web-audio-samples shows how to measure mono microphone RMS inside an AudioWorklet and report the result to the UI via the message port. Inspected commit `e7c0e7276a526b5394a836716ce25aa8cd26ebdb`. The repository root has an Apache License 2.0. The inspected example carries a Chromium BSD-style file notice. Used as a design reference. https://github.com/GoogleChromeLabs/web-audio-samples/blob/e7c0e7276a526b5394a836716ce25aa8cd26ebdb/src/audio-worklet/basic/volume-meter/volume-meter-processor.js

The app uses original implementations. The cloud proxy rejected GitHub's repository search API and the attempted Gnani documentation pages. The references above were inspected through Git. Neither reference specifies the Gnani protocol.
