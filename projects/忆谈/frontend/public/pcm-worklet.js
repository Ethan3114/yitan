class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(Math.round(sampleRate * 0.02));
    this.mixed = new Float32Array(this.buffer.length);
    this.used = 0;
    this.frame = 0;
    this.active = true;
    this.port.onmessage = event => {
      if (event.data === "flush") {
        this.active = false;
        this.flush();
        this.port.postMessage({ type: "flushed" });
      }
    };
  }
  flush() {
    if (!this.used) return;
    this.port.postMessage({ type: "capture", frame: this.frame, samples: this.buffer.slice(0, this.used), mixed: this.mixed.slice(0, this.used) });
    this.used = 0;
  }
  process(inputs) {
    if (!this.active) return true;
    const channel = inputs[0] && inputs[0][0];
    const playback = inputs[1] && inputs[1][0];
    if (channel) {
      for (let offset = 0; offset < channel.length;) {
        if (!this.used) this.frame = currentFrame + offset;
        const length = Math.min(this.buffer.length - this.used, channel.length - offset);
        this.buffer.set(channel.subarray(offset, offset + length), this.used);
        for (let index = 0; index < length; index++) this.mixed[this.used + index] = (channel[offset + index] + (playback?.[offset + index] || 0)) * 0.5;
        this.used += length;
        offset += length;
        if (this.used === this.buffer.length) {
          this.flush();
        }
      }
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCapture);
