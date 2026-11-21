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
const visitTypeLabel = value => value === "general_health" ? "General health visit" : value;
const language = () => $("language").value;
let currentUser = null, accountPage = "login", resetCodeTimer, lastAccountCheck = 0;
let route, records = [], config, online = false;
let reviewInfo = {};
const measurementEditors = {
  weight_kg: "edit-weight", temperature_c: "edit-temperature",
  bp_systolic: "edit-systolic", bp_diastolic: "edit-diastolic",
};
let visitLocation = null, pendingLocation = null, locationRequest = 0, locating = false;
let locationStatus = "Start a visit to request location.";
let locationMap, locationPin, mappedLocation;
let reviewing = false, settling = false, saving = false, turnBusy = false, settleTimer, draftRevision;
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
  const worker = currentUser?.role === "worker";
  route = !currentUser ? "account" : !worker ? "administration" :
    Object.prototype.hasOwnProperty.call(routes, hash) ? hash : (localStorage.getItem("field-language") ? "dashboard" : "welcome");
  for (const name of [...Object.keys(routes), "account", "administration"]) $(name).hidden = name !== route;
  $("worker-navigation").hidden = !worker;
  $("logout").hidden = !currentUser;
  for (const link of document.querySelectorAll("[data-route]")) {
    if (link.dataset.route === route) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  $("page-label").textContent = routes[route] ?? (currentUser ? "Administration" : "ASHA accounts");
  renderRecords();
}

function closeLocationMap() {
  locationMap?.remove();
  locationMap = locationPin = mappedLocation = null;
  $("location-map").hidden = true;
  $("location-map-message").hidden = true;
}

function renderLocation() {
  const point = pendingLocation?.location ?? visitLocation;
  $("location-status").textContent = locationStatus;
  $("location-details").hidden = !point;
  $("map-privacy").hidden = !point;
  $("show-location-map").hidden = !point;
  $("show-location-map").textContent = locationMap ? "Hide map" : "Show map";
  $("refresh-location").disabled = locating || !!pendingLocation || saving || draftState === "SAVED" || (!active && !starting);
  $("skip-location").hidden = !locating || !!point;
  const canReview = ready && draftState !== "SAVED" && (config?.mode !== "real" || reviewing);
  $("save-visit").disabled = !canReview || settling || saving || turnBusy || locating || !!pendingLocation;
  if (!point) {
    closeLocationMap();
    return;
  }
  $("location-latitude").textContent = point.latitude.toFixed(6);
  $("location-longitude").textContent = point.longitude.toFixed(6);
  $("location-accuracy").textContent = `±${Math.round(point.accuracy_m)} m`;
  $("location-captured").textContent = new Date(point.captured_at).toLocaleString();
  if (locationMap && mappedLocation !== point) {
    mappedLocation = point;
    const coordinates = [point.latitude, point.longitude];
    locationPin.setLatLng(coordinates);
    locationMap.setView(coordinates, locationMap.getZoom());
  }
}

function toggleLocationMap() {
  if (locationMap) {
    closeLocationMap();
    renderLocation();
    return;
  }
  const point = pendingLocation?.location ?? visitLocation;
  if (!point || !window.L) return;
  $("location-map-message").hidden = true;
  $("location-map").hidden = false;
  const coordinates = [point.latitude, point.longitude];
  locationMap = L.map("location-map").setView(coordinates, 15);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, referrerPolicy: "origin",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).on("tileerror", () => {
    $("location-map-message").textContent = "Map tiles are unavailable. Your captured coordinates are still available.";
    $("location-map-message").hidden = false;
  }).addTo(locationMap);
  locationPin = L.marker(coordinates, {title: "Captured visit location", alt: "Captured visit location"}).addTo(locationMap);
  mappedLocation = point;
  renderLocation();
}

function captureLocation() {
  const requestId = ++locationRequest;
  if (location.protocol !== "https:" || !window.isSecureContext || !navigator.geolocation) {
    locationStatus = "GPS location is unavailable. Open the app over HTTPS with a supported browser. You can continue without location.";
    renderLocation();
    return;
  }
  locating = true;
  locationStatus = "Waiting for location permission and a GPS reading…";
  renderLocation();
  function failed(error) {
    if (requestId !== locationRequest) return;
    locating = false;
    const reason = error.code === 1 ? "Location permission was denied." : error.code === 3
      ? "GPS capture timed out." : "GPS location is unavailable.";
    locationStatus = `${reason} ${visitLocation || pendingLocation ? "The last captured location is retained." : "You can continue without location."}`;
    renderLocation();
  }
  try {
    navigator.geolocation.getCurrentPosition(position => {
      if (requestId !== locationRequest) return;
      const {latitude, longitude, accuracy} = position.coords;
      const timestamp = new Date(position.timestamp);
      if (![latitude, longitude, accuracy, timestamp.getTime()].every(Number.isFinite)
          || Math.abs(latitude) > 90 || Math.abs(longitude) > 180 || accuracy < 0) {
        failed({code: 2});
        return;
      }
      locating = false;
      pendingLocation = {
        type: "set_visit_location", request_id: requestId,
        location: {latitude, longitude, accuracy_m: accuracy, captured_at: timestamp.toISOString()},
      };
      locationStatus = "GPS reading captured. Waiting for the visit to receive it.";
      renderLocation();
      transmit(pendingLocation);
    }, failed, {enableHighAccuracy: true, timeout: 10000, maximumAge: 0});
  } catch { failed({code: 2}); }
}

function setPhase(value) {
  phase = value;
  $("phase").textContent = value;
  $("connection-status").textContent = `Voice connection · ${value}`;
  $("stop-voice").disabled = reviewing || (!active && !starting);
  $("start-voice").disabled = (active && !reviewing) || settling || saving || starting || !online || !!config?.voice_error;
  $("start-voice").setAttribute("aria-label", reviewing ? "Resume voice visit" : "Start voice visit");
  $("start-voice").classList.toggle("active", value === "Listening" && config?.mode === "real");
  $("interrupt").disabled = !ready;
  $("finish-utterance").hidden = config?.mode !== "real";
  $("voice-diagnostics").hidden = config?.mode !== "real";
  $("finish-utterance").disabled = !ready || reviewing;
  $("demo-send").disabled = !ready || reviewing || value === "Processing";
  const canReview = ready && draftState !== "SAVED" && (config?.mode !== "real" || reviewing);
  $("visit-save-help").textContent = draftState === "SAVED" ? "Visit saved successfully." :
    settling || turnBusy ? "Waiting for speech processing before saving." :
    !canReview ? "Stop recording to review and save the visit." :
    (reviewInfo.blockers ?? []).join(" ");
  $("edit-draft").hidden = !ready || draftState === "SAVED" || (config?.mode === "real" && !reviewing);
  $("apply-edit").disabled = settling || saving || turnBusy;
  renderLocation();
}

function vaccinationRow(plan = {}) {
  const row = node("fieldset");
  const name = node("input");
  name.placeholder = "Vaccine name (if known)";
  name.setAttribute("aria-label", "Vaccine name");
  name.maxLength = 40;
  name.value = plan.name ?? "";
  const status = node("select");
  status.setAttribute("aria-label", "Vaccination status");
  for (const value of ["due", "given"]) {
    const option = node("option", value === "due" ? "Due" : "Given");
    option.value = value;
    status.append(option);
  }
  status.value = plan.status ?? "due";
  const due = node("input");
  due.type = "date";
  due.setAttribute("aria-label", "Vaccination due date");
  due.value = plan.due_date ?? "";
  status.onchange = () => { due.disabled = status.value === "given"; };
  status.onchange();
  const remove = node("button", "Remove vaccination", "secondary");
  remove.type = "button";
  remove.onclick = () => row.remove();
  row.append(name, status, due, remove);
  return row;
}

function renderDetails() {
  $("details").replaceChildren(...Object.entries(fields).map(([key, label]) => {
    const row = node("div");
    const val = key === "visit_type" ? visitTypeLabel(draft[key]) : draft[key];
    row.append(node("dt", label), node("dd", Array.isArray(val) ? val.join(", ") || "None" : val ?? "—"));
    return row;
  }));
  const vaccinations = draft.vaccination_plans ?? [];
  $("vaccination-plans").replaceChildren(...vaccinations.map(plan => node("li",
    `${plan.name ?? "Unnamed vaccination"} · ${plan.status}${plan.due_date ? ` · Due ${plan.due_date} · Follow-up reminder ${plan.due_date}` : ""}`)));
  $("vaccination-heading").parentElement.hidden = !vaccinations.length;
  $("edit-vaccination-plans").replaceChildren(...vaccinations.map(vaccinationRow));
  const concerns = draft.reported_conditions ?? [];
  $("reported-empty").hidden = concerns.length > 0;
  $("reported-conditions").hidden = concerns.length === 0;
  $("reported-conditions").replaceChildren(...concerns.map(({term, source, subject}) => node("li",
    `${subject ? `${subject === "mother" ? "Mother" : "Newborn"} · ` : ""}${term} (${source === "patient_reported_previous_diagnosis" ? "patient-reported previous diagnosis" : "patient-reported"})`)));
  $("edit-beneficiary").value = draft.beneficiary_id ?? "";
  $("edit-visit-type").value = draft.visit_type ?? "";
  $("edit-concerns").hidden = concerns.length === 0;
  $("edit-concern-list").replaceChildren(...concerns.map(({term, subject}) => {
    const label = node("label");
    const input = node("input");
    input.type = "checkbox";
    input.value = subject ? `${subject}:${term}` : term;
    if (subject) input.dataset.subject = subject;
    input.checked = true;
    label.append(input, " ", subject ? `${subject}: ${term}` : term);
    const row = node("div");
    row.append(label);
    if (!subject && reviewInfo.pending_diagnoses?.includes(term)) {
      const answer = node("select");
      answer.dataset.diagnosis = term;
      answer.setAttribute("aria-label", `Previous diagnosis for ${term}`);
      for (const [value, text] of [["", "Previous diagnosis? Select an answer"],
        ["yes", "Yes, patient reports a clinician diagnosis"], ["no", "No previous diagnosis reported"]]) {
        const option = node("option", text);
        option.value = value;
        answer.append(option);
      }
      row.append(answer);
    }
    return row;
  }));
  for (const [field, id] of Object.entries(measurementEditors)) $(id).value = draft[field] ?? "";
  $("add-concern").replaceChildren(node("option", "No additional concern"));
  $("add-concern").firstElementChild.value = "";
  for (const term of reviewInfo.concern_catalog ?? []) {
    if (concerns.some(item => item.term === term)) continue;
    const option = node("option", term);
    option.value = term;
    $("add-concern").append(option);
  }
  $("edit-next-visit").value = draft.next_visit_date ?? "";
  $("edit-vaccinations").value = (draft.vaccinations_due ?? []).join(", ");
  $("edit-transcript").value = reviewInfo.transcript_correction ?? "";
  $("exclude-unclear-label").hidden = !reviewInfo.needs_clarification;
  $("exclude-unclear").checked = false;
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
    name.append(node("strong", item.beneficiary_id), node("small", visitTypeLabel(item.visit_type)));
    const date = node("div");
    date.append(node("strong", followup ? item.scheduled.date : localDate(new Date(item.created_at))));
    if (followup && item.scheduled.kind === "vaccination")
      date.append(node("small", item.scheduled.name ? `${item.scheduled.name} vaccination` : "Vaccination follow-up"));
    if (item.danger_flag) date.append(node("small", "Referral flagged", "flag"));
    if (!followup && item.next_visit_date) {
      date.append(node("small", item.followup_completed ? "Follow-up complete" : `Follow-up · ${item.next_visit_date}`));
    }
    row.append(name, date);
    if (target === $("records") && item.location) {
      const {latitude, longitude} = item.location;
      if (Number.isFinite(latitude) && Number.isFinite(longitude)) {
        const map = node("a", "Open saved location map");
        map.href = `https://www.openstreetmap.org/?mlat=${latitude}&mlon=${longitude}#map=16/${latitude}/${longitude}`;
        map.target = "_blank";
        map.rel = "noopener noreferrer";
        row.append(map);
      }
    }
    if (followup && item.scheduled.completed) row.append(node("small", "Completed"));
    if (followup && !item.scheduled.completed) {
      const button = node("button", "Mark complete", "secondary");
      button.setAttribute("aria-label", `Mark follow-up complete for ${item.beneficiary_id}`);
      button.disabled = !online;
      button.onclick = async () => {
        button.disabled = true;
        try {
          await request(`/api/visits/${item.id}/followup?followup_id=${encodeURIComponent(item.scheduled.id)}`, {method: "PATCH"});
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
  const followups = records.flatMap(record => (record.followups ?? []).map(scheduled => ({...record, scheduled})))
    .sort((a, b) => Number(a.scheduled.completed) - Number(b.scheduled.completed) || a.scheduled.date.localeCompare(b.scheduled.date));
  const query = $("search").value.trim().toLowerCase();
  const empty = online ? "No confirmed visits yet." : "Connect to load confirmed visits.";
  list($("recent"), records.slice(0, 4), empty);
  list($("records"), records.filter(r => `${r.beneficiary_id} ${visitTypeLabel(r.visit_type)}`.toLowerCase().includes(query)),
    query ? "No visits match your search." : empty);
  list($("dashboard-followups"), followups.filter(item => !item.scheduled.completed).slice(0, 4),
    online ? "No pending follow-ups." : "Connect to load follow-ups.", true);
  list($("followup-list"), followups, online ? "No follow-ups scheduled." : "Connect to load follow-ups.", true);
  const stats = {
    "Today's visits": records.filter(r => localDate(new Date(r.created_at)) === localDate()).length,
    "Completed records": records.length,
    "Pending follow-ups": followups.filter(r => !r.scheduled.completed).length,
    "Pending vaccinations": followups.filter(r => r.scheduled.kind === "vaccination" && !r.scheduled.completed).length,
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
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const error = new Error(typeof body.detail === "string" ? body.detail : "Request failed.");
      error.status = response.status;
      if (response.status === 401 && (currentUser || url === "/api/auth/me")) {
        sessionEnded("Your session expired. Sign in again to recover your draft.");
      }
      throw error;
    }
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
}

async function refreshRecords() {
  const worker = currentUser?.id;
  const fetched = await request("/api/visits");
  if (currentUser?.id !== worker) return;
  records = fetched;
  renderRecords();
}

async function checkConnection() {
  if (currentUser?.role !== "worker") return;
  const worker = currentUser.id;
  try {
    const status = await request("/api/status");
    if (currentUser?.id !== worker) return;
    config = status;
    online = true;
    $("delete-data").hidden = config.mode !== "demo";
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

// The draft ID is not an authentication credential. The backend also checks its owner.
function draftStorageKey() {
  return currentUser?.role === "worker" ? `field-draft-${currentUser.id}` : null;
}

function rememberDraft() {
  const key = draftStorageKey();
  if (!key) return;
  const pending = token && draftState !== "SAVED" ? {token, language: language(), at: Date.now()} : null;
  try {
    if (pending) sessionStorage.setItem(key, JSON.stringify(pending));
    else sessionStorage.removeItem(key);
  } catch { /* Storage may be unavailable in a private browser session. */ }
}

function restoreDraft() {
  const key = draftStorageKey();
  if (!key) return;
  try {
    const saved = JSON.parse(sessionStorage.getItem(key));
    if (saved && Date.now() - saved.at < 30 * 60 * 1000) {
      token = saved.token;
      $("language").value = $("welcome-language").value = saved.language;
      notice("An unfinished draft is available. Start the voice visit to recover it.");
    } else {
      sessionStorage.removeItem(key);
    }
  } catch { /* A fresh session is still available. */ }
}

function sessionEnded(message) {
  rememberDraft();
  notice("");
  currentUser = null;
  online = false;
  config = null;
  stop();
  resetTranscript();
  reviewInfo = {};
  records = [];
  renderDetails();
  $("worker-welcome").textContent = "Welcome, ASHA worker";
  $("worker-assignment").textContent = "";
  $("admin-workers").replaceChildren();
  $("admin-visits").replaceChildren();
  clearResetCode();
  showAccountPage("login");
  $("account-message").textContent = message;
  navigate();
}

function setUser(user) {
  currentUser = user;
  if (user.role === "worker") {
    $("worker-welcome").textContent = `Welcome, ${user.full_name}`;
    $("worker-assignment").textContent = `Worker ID ${user.worker_id} · ${user.village}`;
    restoreDraft();
    void checkConnection();
  } else {
    void loadAdministration();
  }
  navigate();
}

function showAccountPage(page) {
  accountPage = page;
  $("account-form").reset();
  $("account-message").textContent = "";
  const labels = {login: "Sign in", register: "Register as a worker", forgot: "Request password reset", reset: "Set new password"};
  $("account-title").textContent = labels[page];
  $("account-submit").textContent = labels[page];
  $("registration-fields").hidden = page !== "register";
  $("worker-id-field").hidden = page === "reset";
  $("reset-code-field").hidden = page !== "reset";
  $("password-field").hidden = page === "forgot";
  $("confirm-password-field").hidden = !["register", "reset"].includes(page);
  $("account-password").autocomplete = page === "login" ? "current-password" : "new-password";
}

function clearResetCode() {
  clearTimeout(resetCodeTimer);
  $("admin-reset-code").textContent = "";
  $("admin-reset-code").hidden = true;
}

async function loadAdministration() {
  const administrator = currentUser?.id;
  clearResetCode();
  try {
    const [workers, visits] = await Promise.all([request("/api/auth/workers"), request("/api/auth/legacy-visits")]);
    if (currentUser?.id !== administrator) return;
    $("admin-workers").replaceChildren(...workers.map(worker => {
      const row = node("div", undefined, "account-row");
      row.append(node("strong", `${worker.full_name} · ${worker.worker_id}`),
        node("p", `${worker.phone} · ${worker.village} · ${worker.status}`));
      if (worker.role === "worker") {
        const village = node("input");
        village.setAttribute("aria-label", `Area for ${worker.worker_id}`);
        village.value = worker.village;
        const status = node("select");
        status.setAttribute("aria-label", `Status for ${worker.worker_id}`);
        for (const text of ["pending", "active", "inactive"]) {
          const option = node("option", text);
          option.value = text;
          status.append(option);
        }
        status.value = worker.status;
        const apply = node("button", "Save worker assignment", "secondary");
        apply.onclick = () => adminAction(`/api/auth/workers/${worker.id}`, "PATCH", {village: village.value, status: status.value});
        row.append(village, status, apply);
      }
      if (worker.reset_requested) {
        const issue = node("button", "Issue reset code", "secondary");
        issue.onclick = async () => {
          if (!confirm("Have you independently verified this worker’s identity? Share the code only with that worker.")) return;
          try {
            const result = await request(`/api/auth/workers/${worker.id}/reset-code`, {method: "POST"});
            if (currentUser?.id !== administrator) return;
            $("admin-reset-code").textContent = `One-time code for ${worker.worker_id}: ${result.code}`;
            $("admin-reset-code").hidden = false;
            resetCodeTimer = setTimeout(clearResetCode, 60000);
            issue.disabled = true;
          } catch (error) { notice(error.message); }
        };
        row.append(issue);
      }
      return row;
    }));
    const activeWorkers = workers.filter(worker => worker.role === "worker" && worker.status === "active");
    $("admin-visits").replaceChildren(...visits.map(visit => {
      const row = node("div", undefined, "account-row");
      row.append(node("p", `${visit.id} · ${visit.created_at}`));
      const choices = node("select");
      choices.setAttribute("aria-label", `Assign worker for ${visit.id}`);
      const placeholder = node("option", "Select a worker");
      placeholder.value = "";
      choices.append(placeholder);
      for (const worker of activeWorkers) {
        const option = node("option", `${worker.worker_id} · ${worker.village}`);
        option.value = worker.id;
        choices.append(option);
      }
      const assign = node("button", "Assign legacy visit", "secondary");
      assign.onclick = () => {
        if (choices.value && confirm("Assign this visit to the selected worker?")) {
          void adminAction(`/api/auth/legacy-visits/${encodeURIComponent(visit.id)}/assign`, "POST", {worker: Number(choices.value)});
        }
      };
      row.append(choices, assign);
      return row;
    }));
  } catch (error) { notice(error.message); }
}

async function adminAction(url, method, values) {
  try {
    const result = await request(url, {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(values)});
    notice(result.message);
    await loadAdministration();
  } catch (error) { notice(error.message); }
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
  // Buffer only at the beginning or after underrun; queue subsequent PCM chunks contiguously.
  const startAt = nextTime > context.currentTime ? nextTime : context.currentTime + .06;
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
    ? `${prisma.speech_starts} started · ${prisma.processing} processing · ${prisma.partials} partial · ${prisma.finals} final · ${prisma.empty} empty · ${prisma.errors} errors · ${prisma.other} other · ${prisma.commits} finish requests`
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
    case "auth_expired":
      sessionEnded("Your session expired. Sign in again to recover your draft.");
      break;
    case "ready":
      clearTimeout(heartbeat);
      if (!message.resumed) resetTranscript();
      token = message.session;
      ready = true;
      if (pendingLocation) transmit(pendingLocation);
      voiceConnection = "Connected to Prisma";
      serverAudioChunks = prismaDiagnostics = undefined;
      if (config?.mode === "real") transmit({type: "heartbeat"});
      renderVoiceDiagnostics();
      setPhase(reviewing ? "Review draft" : "Listening");
      break;
    case "heartbeat":
      attempts = 0;
      serverAudioChunks = message.audio_chunks;
      prismaDiagnostics = message.prisma;
      serverFinalSequence = message.final_sequence ?? 0;
      renderVoiceDiagnostics();
      break;
    case "visit_location":
      if (message.request_id === pendingLocation?.request_id) {
        visitLocation = message.location;
        pendingLocation = null;
        if (!locating) locationStatus = "GPS location attached to this visit.";
        renderLocation();
      }
      break;
    case "location_error":
      pendingLocation = null;
      locationStatus = message.message;
      renderLocation();
      break;
    case "update":
      if (!pendingLocation && Object.prototype.hasOwnProperty.call(message, "location")) {
        visitLocation = message.location;
        if (!locating && visitLocation) locationStatus = "GPS location attached to this visit.";
      }
      draft = message.form;
      flagged = message.danger_flag;
      draftState = message.state;
      draftRevision = message.revision;
      reviewInfo = message.review ?? {};
      turnBusy = false;
      saving = false;
      setPhase(message.state === "SAVED" ? "Saved" : settling ? "Waiting for speech processing" : reviewing ? "Review draft" :
        message.state === "CONFIRMING" ? "Review and confirm" : "Listening");
      renderDetails();
      rememberDraft();
      if (draftState === "SAVED") {
        stop(true);
        setPhase("Saved");
        notice("Visit saved successfully. You can find it in Visit history.");
        refreshRecords().catch(() => notice("Visit saved successfully. Reconnect to refresh history."));
      }
      break;
    case "capture_paused":
      break;
    case "phase":
      turnBusy = true;
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
      saving = false;
      setPhase(phase);
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
  if (currentUser?.role !== "worker" || !active || socket) return;
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const current = new WebSocket(`${protocol}//${location.host}/ws`);
  socket = current;
  ready = false;
  generation = -1;
  setPhase(attempts ? "Reconnecting" : "Connecting");
  lastReply = performance.now();
  // Allow both the upstream upgrade and the Prisma ready acknowledgement.
  heartbeat = setTimeout(() => current.close(), 25000);
  current.onopen = () => current.send(JSON.stringify({type: "hello", language: language(), session: token || null, review_only: reviewing}));
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
  if (currentUser?.role !== "worker" || (active && !reviewing) || starting || settling || saving || !online || config?.voice_error) return;
  notice("");
  setPrismaIssue();
  microphoneFrames = nonzeroMicrophoneFrames = droppedAudioFrames = 0;
  serverAudioChunks = prismaDiagnostics = undefined;
  voiceConnection = "Opening microphone and Prisma connection…";
  renderVoiceDiagnostics();
  starting = true;
  const thisRun = ++voiceRun;
  setPhase("Starting");
  if (!token) {
    visitLocation = pendingLocation = null;
    closeLocationMap();
    captureLocation();
  } else if (!visitLocation && !pendingLocation && !locating) {
    locationStatus = "Use Refresh Location to capture a new GPS reading for this draft.";
    renderLocation();
  }
  let openedStream, openedContext;
  let startedCapture = false;
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
    if (reviewing) {
      reviewing = false;
      const previous = socket;
      socket = null;
      previous?.close();
      clearTimeout(retry);
      retry = null;
    }
    active = true;
    startedCapture = true;
    connect();
  } catch (error) {
    if (thisRun !== voiceRun) return;
    if (!token) {
      locationRequest += 1;
      locating = false;
      pendingLocation = null;
      locationStatus = "Start a visit to request location.";
    }
    $("microphone-status").textContent = "Microphone · unavailable";
    notice(error.name === "NotAllowedError" ? "Microphone permission was denied. Allow access in browser settings and try again." :
      /^(Microphone capture requires|This browser does not support)/.test(error.message) ? error.message :
      "Could not start microphone capture. Check browser and device permissions.");
    setPhase("Ready");
  } finally {
    if (!startedCapture || thisRun !== voiceRun) {
      openedStream?.getTracks().forEach(track => track.stop());
      if (openedContext?.state !== "closed") void openedContext?.close();
    }
    if (thisRun === voiceRun) {
      starting = false;
      setPhase(phase);
    }
  }
}

function releaseMicrophone() {
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
}

function pauseForReview() {
  if (!active) {
    stop(true);
    return;
  }
  voiceRun += 1;
  reviewing = true;
  releaseMicrophone();
  $("microphone-status").textContent = "Microphone · stopped";
  if (!ready) {
    turnBusy = false;
    setPhase("Review draft");
    notice("Microphone stopped while disconnected. The visible draft is preserved, but some speech may not have finished processing. Reconnect to save.");
    return;
  }
  transmit({type: "pause_capture"});
  if (config?.mode === "real" || turnBusy) {
    settling = true;
    setPhase("Waiting for speech processing");
    notice("Microphone stopped. Waiting briefly for any final speech result.");
    settleTimer = setTimeout(() => {
      settling = false;
      if (turnBusy) {
        setPhase("Processing");
        notice("Speech processing hasn’t finished. The current draft is preserved. Wait for the visit details to update before saving.");
      } else {
        setPhase("Review draft");
        notice("Microphone stopped. Review the available details. If any speech is missing, resume the visit and repeat it before saving.");
      }
    }, 5000);
  } else {
    setPhase("Review draft");
    notice("Microphone stopped. Review the draft before saving.");
  }
}

function stop(preserveDraft = false) {
  locationRequest += 1;
  locating = false;
  pendingLocation = null;
  if (!preserveDraft) {
    visitLocation = null;
    locationStatus = "Start a visit to request location.";
    closeLocationMap();
  } else if (draftState === "SAVED") {
    locationStatus = visitLocation ? "Location saved with this visit." : "This visit was saved without location.";
  }
  voiceRun += 1;
  active = ready = starting = reviewing = settling = saving = turnBusy = false;
  clearTimeout(settleTimer);
  clearTimeout(retry);
  clearTimeout(heartbeat);
  retry = null;
  attempts = generation = 0;
  const previous = socket;
  socket = null;
  previous?.close();
  releaseMicrophone();
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
$("refresh-location").onclick = captureLocation;
$("skip-location").onclick = () => {
  locationRequest += 1;
  locating = false;
  locationStatus = "Continuing without a visit location.";
  renderLocation();
};
$("show-location-map").onclick = toggleLocationMap;
$("stop-voice").onclick = pauseForReview;
$("interrupt").onclick = interrupt;
$("finish-utterance").onclick = () => transmit({type: "finish_utterance"});
$("search").oninput = renderRecords;
$("demo-form").onsubmit = event => { event.preventDefault(); sendDemo($("demo-text").value); };
$("sample").onclick = () => {
  $("demo-text").value = language() === "hi"
    ? "लाभार्थी आईडी 4821, नवजात, वजन 2.8, तापमान 37.6, बाकी टीके BCG, OPV-0. अगली विज़िट in 3 days."
    : "Beneficiary ID 4821, newborn, weight 2.8 kg, temperature 37.6. Vaccines due BCG, OPV-0. Next visit in 3 days.";
  $("demo-text").focus();
};
function submitReview(type) {
  if (!ready || settling || saving || turnBusy || (type === "confirm_visit" && (locating || pendingLocation))) return;
  if (!$("edit-visit").checkValidity()) {
    $("edit-draft").open = true;
    $("edit-visit").reportValidity();
    return;
  }
  const selected = [...$("edit-concern-list").querySelectorAll("input:checked")];
  const retained = selected.filter(input => !input.dataset.subject).map(input => input.value);
  const vaccinationPlans = [...$("edit-vaccination-plans").children].map(row => {
    const [name, status, due] = row.querySelectorAll("input, select");
    return {name: name.value.trim() || null, status: status.value, due_date: due.disabled ? null : due.value || null};
  });
  const diagnosisAnswers = Object.fromEntries([...$("edit-concern-list").querySelectorAll("select[data-diagnosis]")]
    .filter(select => retained.includes(select.dataset.diagnosis) && select.value)
    .map(select => [select.dataset.diagnosis, select.value === "yes"]));
  saving = true;
  setPhase(phase);
  transmit({
    type, revision: draftRevision,
    beneficiary_id: $("edit-beneficiary").value || null,
    visit_type: $("edit-visit-type").value || null,
    ...Object.fromEntries(Object.entries(measurementEditors).map(([field, id]) =>
      [field, $(id).value === "" ? null : Number($(id).value)])),
    next_visit_date: $("edit-next-visit").value || null,
    vaccinations_due: $("edit-vaccinations").value.split(",").map(value => value.trim()).filter(Boolean),
    transcript_correction: $("edit-transcript").value || null,
    retained_conditions: retained,
    retained_contextual_conditions: selected.filter(input => input.dataset.subject).map(input => input.value),
    vaccination_plans: vaccinationPlans,
    added_conditions: $("add-concern").value ? [$("add-concern").value] : [],
    diagnosis_answers: diagnosisAnswers,
    exclude_unclear_report: $("exclude-unclear").checked,
  });
}

$("add-vaccination-plan").onclick = () => $("edit-vaccination-plans").append(vaccinationRow());
$("edit-visit").onsubmit = event => {
  event.preventDefault();
  submitReview("edit_visit");
};
$("save-visit").onclick = () => submitReview("confirm_visit");
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
window.addEventListener("online", () => {
  if (currentUser) void checkConnection();
  else void loadSession();
});
window.addEventListener("offline", checkConnection);
setInterval(() => {
  if (currentUser && currentUser.expires_at * 1000 <= Date.now()) {
    sessionEnded("Your session expired. Sign in again to recover your draft.");
    return;
  }
  if (currentUser && Date.now() - lastAccountCheck > 60000) {
    lastAccountCheck = Date.now();
    void request("/api/auth/me").catch(() => {});
  }
  if (ready && performance.now() - lastReply > 18000) socket?.close();
  else transmit({type: "heartbeat"});
  if (!online) void checkConnection();
}, 6000);

$("language").value = $("welcome-language").value = localStorage.getItem("field-language") || "en";
try { sessionStorage.removeItem("field-draft"); } catch { /* Older drafts may have been stored by the demo. */ }
for (const button of document.querySelectorAll("[data-account-page]")) {
  button.onclick = () => showAccountPage(button.dataset.accountPage);
}
$("account-form").onsubmit = async event => {
  event.preventDefault();
  const page = accountPage;
  const values = {};
  if (page !== "reset") values.worker_id = $("account-id").value.trim();
  if (page !== "forgot") values.password = $("account-password").value;
  if (page === "register" || page === "reset") values.confirm_password = $("account-confirm").value;
  if (page === "reset") values.code = $("account-code").value.trim();
  if (page === "register") {
    values.full_name = $("account-name").value;
    values.phone = $("account-phone").value.trim();
    values.village = $("account-village").value;
  }
  $("account-submit").disabled = true;
  try {
    const result = await request(`/api/auth/${page}`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(values)});
    if (result.user) setUser(result.user);
    else {
      if (page === "reset") showAccountPage("login");
      $("account-message").textContent = result.message;
    }
  } catch (error) {
    $("account-message").textContent = error.message;
  } finally {
    $("account-password").value = $("account-confirm").value = $("account-code").value = "";
    $("account-submit").disabled = false;
  }
};
$("logout").onclick = async () => {
  try {
    await request("/api/auth/logout", {method: "POST"});
    sessionEnded("Signed out.");
  } catch (error) { notice(error.message); }
};
navigate();
renderDetails();
draw();
async function loadSession() {
  try {
    const {user} = await request("/api/auth/me");
    if (!currentUser) setUser(user);
  } catch (error) {
    if (!currentUser) $("account-message").textContent = error.status === 401
      ? "Sign in with your approved worker account." : "Connect over HTTPS to verify your account.";
  }
}
void loadSession();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
