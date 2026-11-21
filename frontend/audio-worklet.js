// Preserve fractional boundaries between callbacks. Average the source samples
// in each output interval before converting to signed little-endian PCM16.
class Microphone extends AudioWorkletProcessor {
  phase = 0;
  sum = 0;
  chunk = new Int16Array(1600);
  position = 0;
  energy = 0;

  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    for (const sample of input) {
      let remaining = 16000;
      while (remaining > 0) {
        const amount = Math.min(remaining, sampleRate - this.phase);
        this.sum += sample * amount;
        this.phase += amount;
        remaining -= amount;
        if (this.phase < sampleRate) continue;
        const value = Math.max(-1, Math.min(1, this.sum / sampleRate));
        this.chunk[this.position++] = Math.round(value * (value < 0 ? 32768 : 32767));
        this.energy += value * value;
        this.sum = this.phase = 0;
        if (this.position === this.chunk.length) {
          const pcm = this.chunk.buffer;
          this.port.postMessage({pcm, rms: Math.sqrt(this.energy / this.position)}, [pcm]);
          this.chunk = new Int16Array(1600);
          this.position = this.energy = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("field-microphone", Microphone);
