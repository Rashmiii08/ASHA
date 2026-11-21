const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const code = fs.readFileSync(path.join(__dirname, '../frontend/audio-worklet.js'), 'utf8');

for (const sampleRate of [16000, 44100, 48000]) {
  const chunks = [];
  let Processor;
  const AudioWorkletProcessor = class {
    port = {postMessage(chunk) { chunks.push(chunk); }};
  };
  vm.runInNewContext(code, {
    sampleRate,
    AudioWorkletProcessor,
    registerProcessor(name, object) {
      assert.equal(name, 'field-microphone');
      Processor = object;
    },
  });
  const microphone = new Processor();
  for (let offset = 0; offset < sampleRate; offset += 128) {
    const length = Math.min(128, sampleRate - offset);
    microphone.process([[new Float32Array(length).fill(0.25)]]);
  }
  assert.equal(chunks.length, Math.floor(16000 / 512));
  for (const {pcm, rms} of chunks) {
    const samples = new DataView(pcm);
    assert.equal(samples.byteLength, 1024);
    for (let i = 0; i < samples.byteLength; i += 2) assert.equal(samples.getInt16(i, true), 8192);
    assert.equal(rms, 0.25);
  }
}
console.log('AudioWorklet PCM conversion passed at 16, 44.1 and 48 kHz.');
