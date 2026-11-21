const $ = id => document.getElementById(id);
const routes = {
  welcome: "Welcome", dashboard: "Dashboard", live: "Voice visit",
  history: "Visit history", followups: "Follow-ups", settings: "Settings",
};
const fields = {
  beneficiary_id: "Beneficiary ID", visit_type: "Visit type", weight_kg: "Weight (kg)",
  temperature_c: "Temperature (°C)", bp_systolic: "Systolic (mmHg)", bp_diastolic: "Diastolic (mmHg)",
  vaccinations_due: "Vaccinations due", next_visit_date: "Next visit",
};
const language = () => $("language").value;
let route, records = [], config, online = false;
let active = false, starting = false, socket, token, retry, attempts = 0, ready = false;
let microphoneFrames = 0, nonzeroMicrophoneFrames = 0, droppedAudioFrames = 0;
let finalDisplayed = false, displayedFinalSequence = 0, serverFinalSequence = 0;
let voiceConnection = "Idle", serverAudioChunks, prismaDiagnostics;
let phase = "Ready", draft = {}, flagged = false, draftState, heartbeat, lastReply;
let context, stream, microphone, worklet, mutedOutput, captureTimer, nextTime = 0, generation = -1, sources = new Set();
let voiceRun = 0, lastSpeech = 0, processing = null, sustained = 0;
let waveHistory = Array(32).fill(0);

function node(tag, value, className) {
  const element = document.createElement(tag);
  if (value !== undefined) element.textContent = value;
  if (className) element.className = className;
  return element;
}

function notice(message) {
  $("notice").textContent = message;
  $("notice").hidden = !message;
}

function localDate(date = new Date()) {
  return new Intl.DateTimeFormat("en-CA", {year: "numeric", month: "2-digit", day: "2-digit"}).format(date);
}

function navigate() {
  const hash = location.hash.slice(1);
  route = Object.prototype.hasOwnProperty.call(routes, hash) ? hash : (localStorage.getItem("field-language") ? "dashboard" : "welcome");
  for (const name of Object.keys(routes)) $(name).hidden = name !== route;
  for (const link of document.querySelectorAll("[data-route]")) {
    if (link.dataset.route === route) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  $("page-label").textContent = routes[route];
  renderRecords();
}

function setPhase(value) {
  phase = value;
  $("phase").textContent = value;
  $("connection-status").textContent = `Voice connection · ${value}`;
  $("stop-voice").disabled = !active && !starting;
  $("start-voice").disabled = active || starting || !online || !!config?.voice_error;
  $("start-voice").classList.toggle("active", value === "Listening" && config?.mode === "real");
  $("interrupt").disabled = !ready;
  $("finish-utterance").hidden = config?.mode !== "real";
  $("voice-diagnostics").hidden = config?.mode !== "real";
  $("finish-utterance").disabled = !ready;
  $("demo-send").disabled = !ready || value === "Processing";
  $("demo-confirm").hidden = config?.mode !== "demo" || draftState !== "CONFIRMING";
  $("save-visit").disabled = !ready || value === "Processing";
}

function renderDetails() {
  $("details").replaceChildren(...Object.entries(fields).map(([key, label]) => {
    const row = node("div");
    const val = draft[key];
    row.append(node("dt", label), node("dd", Array.isArray(val) ? val.join(", ") || "None" : val ?? "—"));
    return row;
  }));
  $("danger").hidden = !flagged;
  $("danger").textContent = language() === "hi"
    ? "खतरे का संकेत मिला है। कृपया मरीज़ को तुरंत प्राथमिक स्वास्थ्य केंद्र भेजें।"
    : "Danger sign detected. Please refer to the Primary Health Centre immediately.";
  $("draft-label").textContent = draftState === "SAVED" ? "Saved" : "Draft";
  setPhase(phase);
}

function list(target, data, empty, followup = false) {
  target.replaceChildren();
  if (!data.length) {
    target.append(node("p", empty, "empty"));
    return;
  }
  for (const item of data) {
    const row = node("article", undefined, "record");
    const name = node("div");
    name.append(node("strong", item.beneficiary_id), node("small", item.visit_type));
    const date = node("div");
    date.append(node("strong", followup ? item.next_visit_date : localDate(new Date(item.created_at))));
    if (item.danger_flag) date.append(node("small", "Referral flagged", "flag"));
    if (!followup && item.next_visit_date) {
      date.append(node("small", item.followup_completed ? "Follow-up complete" : `Follow-up · ${item.next_visit_date}`));
    }
    row.append(name, date);
    if (followup && item.followup_completed) row.append(node("small", "Completed"));
    if (followup && !item.followup_completed) {
      const button = node("button", "Mark complete", "secondary");
      button.setAttribute("aria-label", `Mark follow-up complete for ${item.beneficiary_id}`);
      button.disabled = !online;
      button.onclick = async () => {
        button.disabled = true;
        try {
          await request(`/api/visits/${item.id}/followup`, {method: "PATCH"});
          await refreshRecords();
        } catch {
          notice("Could not update the follow-up. Try again once connected.");
          button.disabled = !online;
        }
      };
      row.append(button);
    }
    target.append(row);
  }
}

function renderRecords() {
  const followups = records.filter(r => r.next_visit_date).sort((a, b) =>
    Number(a.followup_completed) - Number(b.followup_completed) || a.next_visit_date.localeCompare(b.next_visit_date));
  const query = $("search").value.trim().toLowerCase();
  const empty = online ? "No confirmed visits yet." : "Connect to load confirmed visits.";
  list($("recent"), records.slice(0, 4), empty);
  list($("records"), records.filter(r => `${r.beneficiary_id} ${r.visit_type}`.toLowerCase().includes(query)),
    query ? "No visits match your search." : empty);
  list($("followup-list"), followups, online ? "No follow-ups scheduled." : "Connect to load follow-ups.", true);
  const stats = {
    "Today's visits": records.filter(r => localDate(new Date(r.created_at)) === localDate()).length,
    "Completed records": records.length,
    "Pending follow-ups": followups.filter(r => !r.followup_completed).length,
    "Flagged visits": records.filter(r => r.danger_flag).length,
  };
  $("stats").replaceChildren(...Object.entries(stats).map(([label, value]) => {
    const stat = node("div", undefined, "stat");
    stat.append(node("strong", online || records.length ? value : "—"), node("span", label));
    return stat;
  }));
}

async function request(url, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch(url, {...options, cache: "no-store", signal: controller.signal});
    if (!response.ok) throw new Error("Request failed");
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
}

async function refreshRecords() {
  records = await request("/api/visits");
  renderRecords();
}

async function checkConnection() {
  try {
    config = await request("/api/status");
    online = true;
    $("mode").hidden = config.mode !== "demo";
    $("mode").textContent = "Synthetic demo";
    $("demo-form").hidden = config.mode !== "demo";
    if (config.voice_error) notice(config.voice_error);
    if (active && !socket && !retry) connect();
    try { await refreshRecords(); }
    catch { notice("Could not refresh the visit register."); }
  } catch {
    online = false;
  }
  $("network").lastChild.textContent = online ? " Connected" : " Offline · voice needs a connection";
  $("network").classList.toggle("offline", !online);
  setPhase(phase);
  renderRecords();
}

// Store only the synthetic structured draft and the short-lived session token.
function rememberDraft() {
  const pending = token && draftState !== "SAVED" ? {token, draft, flagged, language: language(), at: Date.now()} : null;
  try {
    if (pending) sessionStorage.setItem("field-draft", JSON.stringify(pending));
    else sessionStorage.removeItem("field-draft");
  } catch { /* Storage may be unavailable in a private browser session. */ }
}

function clearPlayback() {
  for (const source of sources) {
    try { source.stop(); } catch { /* Already ended. */ }
  }
  sources.clear();
  nextTime = 0;
}

function transmit(message) {
  if (ready && socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
}

function interrupt() {
  generation += 1;
  clearPlayback();
  transmit({type: "interrupt"});
  setPhase("Interrupted");
}

function playPCM(message) {
  if (!context || message.generation !== generation) return;
  const bytes = Uint8Array.from(atob(message.data), c => c.charCodeAt(0));
  if (bytes.length === 0 || bytes.length % 2) return;
  const input = new DataView(bytes.buffer);
  const audio = context.createBuffer(1, bytes.length / 2, 16000);
  const samples = audio.getChannelData(0);
  for (let i = 0; i < samples.length; i++) samples[i] = input.getInt16(i * 2, true) / 32768;
  const source = context.createBufferSource();
  source.buffer = audio;
  source.connect(context.destination);
  const startAt = Math.max(context.currentTime + .025, nextTime);
  nextTime = startAt + audio.duration;
  source.start(startAt);
  sources.add(source);
  source.onended = () => {
    sources.delete(source);
    source.disconnect();
    if (!sources.size && phase === "Speaking") setPhase(draftState === "SAVED" ? "Saved" : "Listening");
  };
  if (message.is_first_chunk && lastSpeech) {
    const latency = performance.now() - lastSpeech + (startAt - context.currentTime) * 1000;
    $("timing").textContent = `First audio · ~${Math.round(latency)} ms · Processing · ${processing ?? "—"} ms`;
    lastSpeech = 0;
  }
  setPhase("Speaking");
}

const prismaNotices = {
  utterance_error: "Prisma couldn’t transcribe that speech segment. Try a shorter note, then pause.",
  invalid_transcript: "Prisma returned an unreadable transcript. Try speaking again.",
  empty_transcript: "Prisma returned an empty transcript. Check your microphone level and try again.",
};

function setPrismaIssue(message = "") {
  $("prisma-issue").textContent = message;
  $("prisma-issue").hidden = !message;
}

function renderVoiceDiagnostics() {
  const prisma = prismaDiagnostics;
  let status = voiceConnection;
  if (ready && microphoneFrames === 0) status = "Connected. Waiting for microphone audio.";
  else if (ready && nonzeroMicrophoneFrames === 0) status = "The microphone is sending silence. Check the selected input.";
  else if (ready && serverAudioChunks === 0) status = "The microphone has audio. Waiting for the server to receive it.";
  else if (ready && prisma?.frames && !prisma.nonzero_frames) {
    status = "Only silent frames have been sent to Prisma. Check microphone input and permissions.";
  }
  else if (ready && serverAudioChunks > 0 && prisma?.frames === 0) status = "Server received audio. Waiting for a full frame to be sent to Prisma.";
  else if (ready && prisma?.nonzero_frames && !prisma.speech_starts && !prisma.processing && !prisma.finals && !prisma.partials) {
    status = "Audio was sent to Prisma. No speech event has arrived. Speak and pause, or tap Finish speaking.";
  }
  else if (ready && (prisma?.speech_starts || prisma?.processing) && !prisma.finals && !prisma.partials) {
    status = "Prisma detected speech. Waiting for a transcript.";
  }
  else if (ready && (prisma?.partials || prisma?.finals)) status = "Prisma has returned transcript text.";
  $("voice-transcript-delivery").textContent = serverFinalSequence > displayedFinalSequence
    ? "Waiting for the latest final to reach this browser"
    : finalDisplayed ? "Nonempty final text displayed" : "No final text displayed";
  $("voice-diagnostic-status").textContent = status;
  $("voice-mic-frames").textContent = `${microphoneFrames} captured · ${nonzeroMicrophoneFrames} with signal · ${droppedAudioFrames} dropped`;
  $("voice-prisma-frames").textContent = prisma
    ? `${serverAudioChunks ?? "—"} server chunks · ${prisma.frames} sent · ${prisma.nonzero_frames} with signal · peak ${prisma.peak_dbfs ?? "—"} dBFS`
    : `Server chunks received · ${serverAudioChunks ?? "—"}`;
  $("voice-prisma-events").textContent = prisma
    ? `${prisma.speech_starts} started · ${prisma.processing} processing · ${prisma.partials} partial · ${prisma.finals} final · ${prisma.empty} empty · ${prisma.errors} errors · ${prisma.other} other · ${prisma.commits} commits`
    : "—";
}

function clearPartialTranscript() {
  $("partial-transcript").textContent = "";
  $("partial-transcript").hidden = true;
}

function resetTranscript() {
  $("transcript").textContent = "Your words will appear here.";
  clearPartialTranscript();
  finalDisplayed = false;
  displayedFinalSequence = serverFinalSequence = 0;
}

function receive(message) {
  switch (message.type) {
    case "ready":
      clearTimeout(heartbeat);
      if (!message.resumed) resetTranscript();
      token = message.session;
      ready = true;
      voiceConnection = "Connected to Prisma";
      serverAudioChunks = prismaDiagnostics = undefined;
      if (config?.mode === "real") transmit({type: "heartbeat"});
      renderVoiceDiagnostics();
      setPhase("Listening");
      break;
    case "heartbeat":
      attempts = 0;
      serverAudioChunks = message.audio_chunks;
      prismaDiagnostics = message.prisma;
      serverFinalSequence = message.final_sequence ?? 0;
      renderVoiceDiagnostics();
      break;
    case "update":
      draft = message.form;
      flagged = message.danger_flag;
      draftState = message.state;
      setPhase(message.state === "CONFIRMING" ? "Review and confirm" : message.state === "SAVED" ? "Saved" : "Listening");
      renderDetails();
      rememberDraft();
      if (draftState === "SAVED") refreshRecords().catch(() => notice("Visit saved. Reconnect to refresh history."));
      break;
    case "phase":
      setPhase("Processing");
      break;
    case "assistant":
      if (message.generation < generation) break;
      clearPlayback();
      generation = message.generation;
      processing = message.processing_ms;
      $("assistant").lang = language();
      $("assistant").textContent = message.text;
      break;
    case "prisma_notice": {
      const text = Object.prototype.hasOwnProperty.call(prismaNotices, message.code) ? prismaNotices[message.code] : "";
      if (text) {
        setPrismaIssue(text);
        notice(text);
        clearPartialTranscript();
      }
      break;
    }
    case "transcript":
      if (typeof message.text !== "string" || !message.text.trim()) break;
      if (message.final) {
        if ($("notice").textContent === $("prisma-issue").textContent) notice("");
        setPrismaIssue();
        $("transcript").textContent = message.text;
        clearPartialTranscript();
        finalDisplayed = true;
        if (Number.isSafeInteger(message.sequence) && message.sequence > 0) {
          displayedFinalSequence = message.sequence;
          transmit({type: "transcript_displayed", sequence: message.sequence});
        }
        renderVoiceDiagnostics();
      } else {
        $("partial-transcript").textContent = `Partial · ${message.text}`;
        $("partial-transcript").hidden = false;
      }
      break;
    case "audio":
      playPCM(message);
      break;
    case "interrupted":
      generation = Math.max(generation, message.generation);
      clearPlayback();
      break;
    case "error":
      notice(message.message);
      if (!ready) {
        voiceConnection = message.message;
        renderVoiceDiagnostics();
      }
      if (message.fatal) stop(true);
      else if (phase === "Processing") setPhase("Listening");
      break;
  }
}

function scheduleReconnect() {
  clearTimeout(retry);
  if (!active) return;
  setPhase("Reconnecting");
  const delay = Math.min(12000, 500 * 2 ** Math.min(attempts++, 5));
  retry = setTimeout(() => {
    retry = null;
    connect();
  }, delay);
}

function connect() {
  if (!active || socket) return;
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const current = new WebSocket(`${protocol}//${location.host}/ws`);
  socket = current;
  ready = false;
  generation = -1;
  setPhase(attempts ? "Reconnecting" : "Connecting");
  lastReply = performance.now();
  // Allow both the upstream upgrade and the Prisma ready acknowledgement.
  heartbeat = setTimeout(() => current.close(), 25000);
  current.onopen = () => current.send(JSON.stringify({type: "hello", language: language(), session: token || null}));
  current.onmessage = event => {
    if (socket !== current) return;
    lastReply = performance.now();
    receive(JSON.parse(event.data));
  };
  current.onclose = () => {
    if (socket !== current) return;
    clearTimeout(heartbeat);
    socket = null;
    ready = false;
    clearPlayback();
    clearPartialTranscript();
    if (!voiceConnection.includes("rejected")) voiceConnection = "Prisma disconnected. Reconnecting…";
    renderVoiceDiagnostics();
    scheduleReconnect();
    void checkConnection();
  };
}

function draw(level = 0) {
  const canvas = $("wave");
  const ctx = canvas.getContext("2d");
  waveHistory = [...waveHistory.slice(1), level];
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  waveHistory.forEach((value, i) => {
    const height = Math.min(45, 3 + value * 260);
    ctx.fillStyle = value > .02 ? "#267c6b" : "#b9d7cb";
    ctx.fillRect(2 + i * 10, (52 - height) / 2, 5, height);
  });
}

function capture({pcm, rms}) {
  if (captureTimer) {
    clearTimeout(captureTimer);
    captureTimer = null;
    $("microphone-status").textContent = "Microphone · capturing audio";
  }
  microphoneFrames += 1;
  if (rms > 0) nonzeroMicrophoneFrames += 1;
  if (microphoneFrames === 1 || microphoneFrames % 32 === 0) renderVoiceDiagnostics();
  draw(rms);
  const now = performance.now();
  if (rms >= .035) {
    // Retain roughly 200 ms of speech before interrupting at the new 32 ms cadence.
    const previousSamples = sustained;
    sustained += pcm.byteLength / 2;
    lastSpeech = now;
    if (sources.size && previousSamples < 3200 && sustained >= 3200) interrupt();
  } else {
    sustained = 0;
  }
  if (!ready) return;
  if (socket.bufferedAmount > 128000) {
    droppedAudioFrames += 1;
    return;
  }
  const data = btoa(String.fromCharCode(...new Uint8Array(pcm)));
  transmit({type: "audio", data});
}

async function start() {
  if (active || starting || !online || config?.voice_error) return;
  notice("");
  setPrismaIssue();
  microphoneFrames = nonzeroMicrophoneFrames = droppedAudioFrames = 0;
  serverAudioChunks = prismaDiagnostics = undefined;
  voiceConnection = "Opening microphone and Prisma connection…";
  renderVoiceDiagnostics();
  starting = true;
  const thisRun = ++voiceRun;
  setPhase("Starting");
  let openedStream, openedContext;
  try {
    if (config.mode === "real") {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.AudioContext) {
        throw new Error("Microphone capture requires a supported browser on HTTPS or localhost.");
      }
      openedContext = new AudioContext();
      await openedContext.resume();
      openedStream = await navigator.mediaDevices.getUserMedia({
        audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true},
      });
      if (!openedContext.audioWorklet || !window.AudioWorkletNode) {
        throw new Error("This browser does not support live microphone processing. Update Chrome or Safari and try again.");
      }
      await openedContext.audioWorklet.addModule("/audio-worklet.js");
      if (thisRun !== voiceRun) return;
      microphone = openedContext.createMediaStreamSource(openedStream);
      worklet = new AudioWorkletNode(openedContext, "field-microphone", {channelCount: 1, channelCountMode: "explicit"});
      mutedOutput = openedContext.createGain();
      mutedOutput.gain.value = 0;
      worklet.port.onmessage = event => {
        if (thisRun === voiceRun) capture(event.data);
      };
      // Keep the processor in the rendered graph without playing microphone audio.
      microphone.connect(worklet).connect(mutedOutput).connect(openedContext.destination);
      captureTimer = setTimeout(() => {
        $("microphone-status").textContent = "Microphone · no audio frames";
        notice("The browser opened the microphone but received no audio frames. Check the input device and restart the visit.");
      }, 4000);
      context = openedContext;
      stream = openedStream;
      $("microphone-status").textContent = "Microphone · active";
    }
    if (thisRun !== voiceRun) return;
    active = true;
    connect();
  } catch (error) {
    if (thisRun !== voiceRun) return;
    $("microphone-status").textContent = "Microphone · unavailable";
    notice(error.name === "NotAllowedError" ? "Microphone permission was denied. Allow access in browser settings and try again." :
      /^(Microphone capture requires|This browser does not support)/.test(error.message) ? error.message :
      "Could not start microphone capture. Check browser and device permissions.");
    setPhase("Ready");
  } finally {
    if (!active || thisRun !== voiceRun) {
      openedStream?.getTracks().forEach(track => track.stop());
      if (openedContext?.state !== "closed") void openedContext?.close();
    }
    if (thisRun === voiceRun) {
      starting = false;
      setPhase(phase);
    }
  }
}

function stop(preserveDraft = false) {
  voiceRun += 1;
  active = ready = starting = false;
  clearTimeout(retry);
  clearTimeout(heartbeat);
  retry = null;
  attempts = generation = 0;
  const previous = socket;
  socket = null;
  previous?.close();
  clearPlayback();
  clearTimeout(captureTimer);
  microphone?.disconnect();
  if (worklet) {
    worklet.port.onmessage = null;
    worklet.disconnect();
  }
  mutedOutput?.disconnect();
  stream?.getTracks().forEach(track => track.stop());
  if (context?.state !== "closed") void context?.close();
  context = stream = microphone = worklet = mutedOutput = null;
  token = null;
  clearPartialTranscript();
  if (!preserveDraft) voiceConnection = "Idle";
  renderVoiceDiagnostics();
  lastSpeech = sustained = 0;
  if (!preserveDraft) {
    draft = {};
    flagged = false;
    draftState = null;
    $("assistant").textContent = "Start a session to begin.";
  }
  rememberDraft();
  $("timing").textContent = "First audio · —";
  $("microphone-status").textContent = "Microphone · idle";
  waveHistory.fill(0);
  draw();
  setPhase("Ready");
  renderDetails();
}

function sendDemo(text) {
  if (!ready || phase === "Processing" || !text.trim()) return;
  transmit({type: "demo_transcript", text});
  $("demo-text").value = "";
  setPhase("Processing");
}

function changeLanguage(value) {
  if (active || starting || token) stop();
  $("language").value = $("welcome-language").value = value;
  localStorage.setItem("field-language", value);
  renderDetails();
}

$("today").textContent = new Intl.DateTimeFormat(undefined, {weekday: "long", month: "long", day: "numeric"}).format(new Date());
$("welcome-start").onclick = () => { changeLanguage($("welcome-language").value); location.hash = "dashboard"; };
$("language").onchange = event => changeLanguage(event.target.value);
$("start-voice").onclick = start;
$("stop-voice").onclick = () => stop();
$("interrupt").onclick = interrupt;
$("finish-utterance").onclick = () => transmit({type: "finish_utterance"});
$("search").oninput = renderRecords;
$("demo-form").onsubmit = event => { event.preventDefault(); sendDemo($("demo-text").value); };
$("sample").onclick = () => {
  $("demo-text").value = language() === "hi"
    ? "लाभार्थी आईडी DEMO-4821, नवजात, वजन 2.8, तापमान 37.6, बाकी टीके BCG, OPV-0. अगली विज़िट 2026-11-25."
    : "Beneficiary ID DEMO-4821, newborn, weight 2.8 kg, temperature 37.6. Vaccines due BCG, OPV-0. Next visit 2026-11-25.";
  $("demo-text").focus();
};
$("save-visit").onclick = () => sendDemo(language() === "hi" ? "हाँ सेव करें" : "Yes, save it.");
$("delete-data").onclick = async () => {
  if (!confirm("Delete every confirmed synthetic visit from this register and discard this browser’s current draft?")) return;
  try {
    await request("/api/visits", {method: "DELETE", headers: {"X-Confirm-Delete": "synthetic-visits"}});
    stop();
    resetTranscript();
    renderVoiceDiagnostics();
    records = [];
    renderRecords();
    notice("Synthetic visits and this browser’s draft were cleared.");
  } catch { notice("Could not delete visits. Check the connection and try again."); }
};
window.addEventListener("hashchange", navigate);
window.addEventListener("online", checkConnection);
window.addEventListener("offline", checkConnection);
setInterval(() => {
  if (ready && performance.now() - lastReply > 18000) socket?.close();
  else transmit({type: "heartbeat"});
  if (!online) void checkConnection();
}, 6000);

$("language").value = $("welcome-language").value = localStorage.getItem("field-language") || "en";
try {
  const saved = JSON.parse(sessionStorage.getItem("field-draft"));
  if (saved && Date.now() - saved.at < 30 * 60 * 1000 && saved.language === language()) {
    ({token, draft, flagged} = saved);
    notice("A draft is available. Start the voice session to reconnect.");
  } else {
    sessionStorage.removeItem("field-draft");
  }
} catch { /* A fresh session is still available. */ }
navigate();
renderDetails();
draw();
void checkConnection();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
