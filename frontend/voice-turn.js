// Independent push-to-talk mode: record WAV, transcribe with Gnani REST, answer with Gnani Timbre.
(() => {
  const $ = id => document.getElementById(id);
  let token = null, stream = null, context = null, source = null, processor = null;
  let recording = false, chunks = [], count = 0, rate = 16000, busy = false;
  const status = $("turn-status"), talk = $("turn-talk"), begin = $("turn-start"), save = $("turn-save");
  const say = value => { status.textContent = value; };
  const fail = e => say(e?.message || "Something went wrong. Try again.");
  const post = async (url, body, type) => {
    const r = await fetch(url, {method:"POST", headers:{"Content-Type":type}, body});
    const json = await r.json().catch(() => ({}));
    if (!r.ok) throw Error(json.detail || "Voice request failed");
    return json;
  };
  const speak = async data => {
    $("transcript").textContent = data.transcript || "Waiting for your answer…";
    $("assistant").textContent = data.assistant;
    if (data.form) {
      const details = $("details");
      details.replaceChildren();
      for (const [key,value] of Object.entries(data.form)) {
        if (value == null) continue;
        const dt = document.createElement("dt"), dd = document.createElement("dd");
        dt.textContent = key.replaceAll("_"," ");
        dd.textContent = Array.isArray(value) ? value.join(", ") : String(value);
        details.append(dt,dd);
      }
    }
    save.hidden = data.state !== "CONFIRMING";
    if (data.audio) {
      try {
        const raw = Uint8Array.from(atob(data.audio), c => c.charCodeAt(0));
        const ctx = new AudioContext({sampleRate:16000});
        const buffer = ctx.createBuffer(1, raw.length/2, 16000);
        const view = new DataView(raw.buffer);
        for(let i=0;i<raw.length/2;i++) buffer.getChannelData(0)[i] = view.getInt16(i*2,true)/32768;
        const node = ctx.createBufferSource(); node.buffer=buffer; node.connect(ctx.destination);
        node.onended=()=>ctx.close(); node.start();
      } catch { say("Reply displayed. Audio playback was unavailable."); }
    }
  };
  const wav = () => {
    const samples = new Float32Array(count);
    let offset = 0;
    for(const chunk of chunks) { samples.set(chunk,offset); offset+=chunk.length; }
    const length = Math.ceil(samples.length * 16000 / rate);
    const bytes = new ArrayBuffer(44+length*2), v=new DataView(bytes);
    const word=(p,s)=>{for(let i=0;i<s.length;i++)v.setUint8(p+i,s.charCodeAt(i));};
    word(0,"RIFF");v.setUint32(4,36+length*2,true);word(8,"WAVE");word(12,"fmt ");
    v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);
    v.setUint32(24,16000,true);v.setUint32(28,32000,true);v.setUint16(32,2,true);
    v.setUint16(34,16,true);word(36,"data");v.setUint32(40,length*2,true);
    for(let i=0;i<length;i++) {
      const s=Math.max(-1,Math.min(1,samples[Math.min(samples.length-1,Math.floor(i*rate/16000))]||0));
      v.setInt16(44+i*2,Math.round(s*(s<0?32768:32767)),true);
    }
    return bytes;
  };
  const cleanup = async () => {
    processor?.disconnect(); source?.disconnect();
    stream?.getTracks().forEach(t=>t.stop());
    if(context && context.state!=="closed") await context.close();
    processor=source=stream=context=null;
  };
  begin.onclick = async () => {
    if(busy) return; busy=true;begin.disabled=true;
    try {
      say("Connecting to Gnani…");
      const data=await post("/api/voice-turn/start",JSON.stringify({language:$("language").value}),"application/json");
      token=data.session; talk.disabled=false; await speak(data); say("Ready. Tap Speak to answer.");
    } catch(e){fail(e);} finally{busy=false;begin.disabled=false;}
  };
  talk.onclick = async () => {
    if(busy || !token) return;
    if(recording) {
      recording=false; talk.disabled=true; talk.textContent="Speak";
      const audio=wav(); await cleanup(); busy=true;
      try {
        say("Gnani is recognizing your voice…");
        const data=await post("/api/voice-turn/"+encodeURIComponent(token),audio,"audio/wav");
        await speak(data);say("Ready for your next answer.");
      } catch(e){fail(e);} finally {busy=false;talk.disabled=false;}
      return;
    }
    try {
      stream=await navigator.mediaDevices.getUserMedia({audio:true});
      context=new AudioContext();await context.resume();rate=context.sampleRate;
      source=context.createMediaStreamSource(stream);
      processor=context.createScriptProcessor(4096,1,1);
      chunks=[];count=0;
      processor.onaudioprocess=e=>{
        if(!recording || count>=rate*25) return;
        const chunk=new Float32Array(e.inputBuffer.getChannelData(0));
        chunks.push(chunk);count+=chunk.length;
        if(count>=rate*25) {say("25 seconds reached. Tap Done.");}
      };
      source.connect(processor);processor.connect(context.destination);
      recording=true;talk.textContent="Done speaking";say("Listening. Speak, then tap Done speaking.");
    } catch(e){await cleanup();fail(e);}
  };
  save.onclick=async()=>{
    if(busy||!token)return;busy=true;save.disabled=true;
    try{const data=await post("/api/voice-turn/"+encodeURIComponent(token)+"/confirm","{}","application/json");await speak(data);say("Visit confirmation processed.");}
    catch(e){fail(e);}finally{busy=false;save.disabled=false;}
  };
})();
