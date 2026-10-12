/**
 * clinicView — Frontend Application Logic
 * Integrates Sarvam AI STT, TTS, 105B LLM, and Mayura Translate over WebSocket & REST.
 */

const API_BASE = window.location.origin;
const WS_BASE = `ws://${window.location.host}/ws/session`;

// App State
let currentLang = 'hi-IN';
let currentPatient = null;
let currentSessionId = null;
let currentTokenId = null;
let ws = null;
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let currentAudioPlayer = null;

// DOM Elements
const screens = {
  language: document.getElementById('screen-language'),
  auth: document.getElementById('screen-auth'),
  intake: document.getElementById('screen-intake'),
  summary: document.getElementById('screen-summary'),
};

function showScreen(name) {
  Object.values(screens).forEach(s => s.classList.remove('active'));
  if (screens[name]) screens[name].classList.add('active');
}

// ──────────────────────────────────────────────────────────────────────
// 1. Language Selection
// ──────────────────────────────────────────────────────────────────────
document.querySelectorAll('.lang-card').forEach(card => {
  card.addEventListener('click', () => {
    currentLang = card.getAttribute('data-lang');
    console.log('Language selected:', currentLang);
    showScreen('auth');
  });
});

// ──────────────────────────────────────────────────────────────────────
// 2. ABDM Auth & Patient Login
// ──────────────────────────────────────────────────────────────────────
function selectDemoProfile(phone) {
  document.getElementById('phone-input').value = phone;
  sendOtp();
}

document.getElementById('btn-send-otp').addEventListener('click', sendOtp);

async function sendOtp() {
  const phone = document.getElementById('phone-input').value.trim();
  if (!phone || phone.length < 10) {
    alert('Please enter a valid 10-digit mobile number.');
    return;
  }

  try {
    const res = await fetch(`${API_BASE}/api/abdm/auth/mobile/init`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone }),
    });
    const data = await res.json();
    if (data.status === 'success') {
      document.getElementById('otp-container').classList.remove('hidden');
    }
  } catch (err) {
    console.error('OTP Init Error:', err);
  }
}

document.getElementById('btn-confirm-otp').addEventListener('click', async () => {
  const phone = document.getElementById('phone-input').value.trim();
  const otp = document.getElementById('otp-input').value.trim();

  try {
    const res = await fetch(`${API_BASE}/api/abdm/auth/mobile/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone, otp }),
    });
    const data = await res.json();
    if (data.status === 'success') {
      currentPatient = data.patient;
      document.getElementById('p-name').textContent = currentPatient.full_name;
      document.getElementById('p-meta').textContent = 
        `Age: ${currentPatient.age || 35} • ${currentPatient.gender || 'Male'} • ABHA: ${currentPatient.abha_number || currentPatient.abha_id || '91-1001-2001-3001'}`;
      
      if (data.previous_history) {
        document.getElementById('followup-alert').classList.remove('hidden');
      } else {
        document.getElementById('followup-alert').classList.add('hidden');
      }

      document.getElementById('verified-patient-card').classList.remove('hidden');
      document.getElementById('otp-container').classList.add('hidden');
    } else {
      alert(data.detail || 'OTP Verification failed');
    }
  } catch (err) {
    console.error('OTP Confirm Error:', err);
  }
});

function skipToInterview() {
  currentPatient = {
    patient_id: 'pat_walkin_01',
    full_name: 'Ramesh Verma',
    age: 38,
    gender: 'Male',
    abha_number: '91-1001-2001-3001'
  };
  startIntakeSession();
}

document.getElementById('btn-start-intake').addEventListener('click', startIntakeSession);

async function startIntakeSession() {
  showScreen('intake');
  initWebSocket();
}

// ──────────────────────────────────────────────────────────────────────
// 3. Conversational Intake & WebSocket
// ──────────────────────────────────────────────────────────────────────
function initWebSocket() {
  ws = new WebSocket(WS_BASE);

  ws.onopen = () => {
    console.log('Connected to clinicView WebSocket');
    ws.send(JSON.stringify({
      type: 'start',
      language: currentLang,
      patient_id: currentPatient ? currentPatient.patient_id : 'pat_walkin',
      patient_name: currentPatient ? currentPatient.full_name : 'Patient',
      patient_age: currentPatient ? currentPatient.age : 30,
      patient_sex: currentPatient ? currentPatient.gender : 'Male',
    }));
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    console.log('WS Message:', msg);

    if (msg.type === 'ui') {
      handleUiUpdate(msg);
    } else if (msg.type === 'orb_state') {
      updateOrbState(msg.orb_state);
    }
  };

  ws.onerror = (err) => {
    console.error('WebSocket Error:', err);
  };
}

function addChatBubble(role, text) {
  if (!text) return;
  const container = document.getElementById('chat-history');
  if (!container) return;

  const lastBubble = container.lastElementChild;
  if (lastBubble && lastBubble.dataset.text === text && lastBubble.classList.contains(role)) {
    return;
  }

  const bubble = document.createElement('div');
  bubble.className = `chat-bubble ${role}`;
  bubble.dataset.text = text;

  const roleTag = document.createElement('div');
  roleTag.className = 'chat-bubble-role';
  roleTag.textContent = role === 'assistant' ? '🤖 Assistant' : '👤 You';

  const body = document.createElement('div');
  body.textContent = text;

  bubble.appendChild(roleTag);
  bubble.appendChild(body);
  container.appendChild(bubble);
  container.scrollTop = container.scrollHeight;
}

function handleUiUpdate(ui) {
  currentSessionId = ui.session_id;

  if (ui.macro_state === 'DOCUMENT_SCAN' || ui.macro_state === 'SUMMARY_CONFIRMATION' || ui.macro_state === 'COMPLETE') {
    loadFinalSummary();
    return;
  }

  // Update Section & Progress
  if (ui.section_label) {
    document.getElementById('progress-section').textContent = ui.section_label;
  }
  if (ui.progress) {
    const pct = ui.progress.percent || 20;
    document.getElementById('progress-fill').style.width = `${pct}%`;
    document.getElementById('progress-percent').textContent = `${pct}%`;
  }

  // Update Prompt
  if (ui.prompt) {
    document.getElementById('prompt-text').textContent = ui.prompt;
    addChatBubble('assistant', ui.prompt);
  }

  // Render Touch Options
  const optBox = document.getElementById('options-container');
  optBox.innerHTML = '';
  if (ui.options && ui.options.length > 0) {
    ui.options.forEach(opt => {
      const btn = document.createElement('button');
      btn.className = 'option-chip';
      btn.textContent = opt.label;
      btn.onclick = () => sendUserInput('tap', opt.value || opt.label);
      optBox.appendChild(btn);
    });
  }

  // Play TTS audio if attached
  if (ui.audio_b64) {
    playBase64Audio(ui.audio_b64);
  }

  updateOrbState('listening');
}

function updateOrbState(state) {
  const orb = document.getElementById('voice-orb');
  const label = document.getElementById('orb-status');

  if (state === 'processing') {
    label.textContent = 'Processing with Sarvam 105B...';
    label.style.color = '#0ea5e9';
  } else if (state === 'speaking') {
    label.textContent = 'Speaking (Sarvam bulbul:v3)...';
    label.style.color = '#38bdf8';
  } else {
    label.textContent = 'Listening (Sarvam saaras:v3)...';
    label.style.color = '#10b981';
  }
}

function sendUserInput(type, value) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  if (value) {
    addChatBubble('patient', value);
    document.getElementById('live-transcript').textContent = `"${value}"`;
  }
  updateOrbState('processing');
  ws.send(JSON.stringify({
    type: 'input',
    input_type: type,
    value: value,
  }));
}

function submitTextInput() {
  const input = document.getElementById('text-input');
  const val = input.value.trim();
  if (val) {
    sendUserInput('voice', val);
    input.value = '';
  }
}

document.getElementById('btn-send-text')?.addEventListener('click', submitTextInput);
document.getElementById('text-input')?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    submitTextInput();
  }
});

document.getElementById('btn-next-action')?.addEventListener('click', () => {
  sendUserInput('next', '');
});

document.getElementById('btn-skip')?.addEventListener('click', () => {
  sendUserInput('skip', '');
});

// ──────────────────────────────────────────────────────────────────────
// 4. Audio Recording & Sarvam STT
// ──────────────────────────────────────────────────────────────────────
const micBtn = document.getElementById('btn-mic');
const micLabel = document.getElementById('mic-label');

micBtn.addEventListener('click', toggleRecording);

async function toggleRecording() {
  if (isRecording) {
    stopRecording();
  } else {
    await startRecording();
  }
}

async function startRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioChunks = [];
    mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.push(e.data);
    };

    mediaRecorder.onstop = async () => {
      const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
      await processRecordedAudio(audioBlob);
    };

    mediaRecorder.start();
    isRecording = true;
    micBtn.style.background = 'linear-gradient(135deg, #10b981, #059669)';
    micLabel.textContent = 'Recording... (Tap to Send)';
    updateOrbState('listening');
  } catch (err) {
    console.error('Microphone Access Error:', err);
    alert('Please enable microphone permissions in your browser.');
  }
}

function stopRecording() {
  if (mediaRecorder && isRecording) {
    mediaRecorder.stop();
    isRecording = false;
    micBtn.style.background = 'linear-gradient(135deg, #ef4444, #dc2626)';
    micLabel.textContent = 'Transcribing with Sarvam STT...';
  }
}

async function processRecordedAudio(audioBlob) {
  const formData = new FormData();
  formData.append('audio', audioBlob, 'speech.webm');
  formData.append('language', currentLang);

  document.getElementById('live-transcript').textContent = 'Transcribing audio...';

  try {
    const res = await fetch(`${API_BASE}/api/stt`, {
      method: 'POST',
      body: formData,
    });
    const data = await res.json();
    const transcript = data.transcript || '';
    if (transcript) {
      document.getElementById('live-transcript').textContent = `"${transcript}"`;
      sendUserInput('voice', transcript);
    } else {
      document.getElementById('live-transcript').textContent = 'No speech detected. Please try again.';
    }
  } catch (err) {
    console.error('STT Request Error:', err);
  } finally {
    micLabel.textContent = 'Speak (Tap Mic)';
  }
}

function playBase64Audio(b64) {
  try {
    if (currentAudioPlayer) {
      currentAudioPlayer.pause();
    }
    const snd = new Audio(`data:audio/wav;base64,${b64}`);
    currentAudioPlayer = snd;
    updateOrbState('speaking');
    snd.onended = () => updateOrbState('listening');
    snd.play().catch(e => console.log('Audio autoplay prevented:', e));
  } catch (e) {
    console.error('Audio playback error:', e);
  }
}

// ──────────────────────────────────────────────────────────────────────
// 5. Final Clinical Summary View
// ──────────────────────────────────────────────────────────────────────
async function loadFinalSummary() {
  showScreen('summary');
  if (!currentSessionId) return;

  try {
    const res = await fetch(`${API_BASE}/api/summary/${currentSessionId}`);
    const data = await res.json();

    document.getElementById('sum-token').textContent = data.token_id || 'TOKEN #2026-A1';
    document.getElementById('sum-patient-line').textContent = 
      `Patient: ${data.full_name || 'Ramesh Verma'} • ABHA: ${data.abha_number || '91-1001-2001-3001'}`;

    const detailed = data.full_detailed_summary || {};
    document.getElementById('doc-narrative').textContent = detailed.clinical_narrative || 'Summary recorded.';
    
    // Highlights
    const hlList = document.getElementById('doc-highlights');
    hlList.innerHTML = '';
    const hls = detailed.critical_highlights || [];
    hls.forEach(h => {
      const li = document.createElement('li');
      li.textContent = h;
      hlList.appendChild(li);
    });

    // Vernacular translation via Sarvam Mayura
    document.getElementById('pat-narrative').textContent = 
      data.vernacular_summary || detailed.vernacular_narrative || detailed.clinical_narrative;

  } catch (err) {
    console.error('Summary Fetch Error:', err);
  }
}

function switchTab(tab) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-doctor').classList.add('hidden');
  document.getElementById('tab-patient').classList.add('hidden');

  if (tab === 'doctor') {
    document.querySelectorAll('.tab-btn')[0].classList.add('active');
    document.getElementById('tab-doctor').classList.remove('hidden');
  } else {
    document.querySelectorAll('.tab-btn')[1].classList.add('active');
    document.getElementById('tab-patient').classList.remove('hidden');
  }
}

function restartKiosk() {
  window.location.reload();
}

// ──────────────────────────────────────────────────────────────────────
// 6. Live Sarvam Health Monitoring
// ──────────────────────────────────────────────────────────────────────
async function checkHealthStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/health`);
    const data = await res.json();
    const services = data.sarvam_ai?.services || {};

    updateBadge('badge-stt', services.stt?.status === 'ok', `STT: ${services.stt?.latency_ms || 120}ms`);
    updateBadge('badge-llm', services.llm?.status === 'ok', `LLM: ${services.llm?.latency_ms || 350}ms`);
    updateBadge('badge-trans', services.translate?.status === 'ok', `Translate: ${services.translate?.latency_ms || 90}ms`);
    updateBadge('badge-tts', services.tts?.status === 'ok', `TTS: ${services.tts?.latency_ms || 180}ms`);
  } catch (e) {
    console.log('Health check failed:', e);
  }
}

function updateBadge(id, isOk, label) {
  const el = document.getElementById(id);
  if (!el) return;
  if (isOk) {
    el.classList.remove('offline');
    el.querySelector('.badge-label').textContent = label;
  } else {
    el.classList.add('offline');
  }
}

// Initial health check on page load
checkHealthStatus();
setInterval(checkHealthStatus, 15000);
