# clinicView — AI Voice Patient Intake Kiosk

A voice-first intelligent patient intake kiosk for outpatient hospital reception and OPD clinics. Built using **Sarvam AI** (Indic Voice & Language) and **Gemini Multimodal Clinical Reasoning** (Macleod's Clinical Interview Engine + Vision OCR).

---

## 🚀 How to Run

### Option 1: 1-Click Launch (Recommended on Windows)
Simply double-click the included batch script:
```text
run_kiosk.bat
```
*(Located right in the `c:\SmartIndiaHackathon\SarvamBuild\` root directory)*

---

### Option 2: Terminal / PowerShell Command
Open your terminal in `c:\SmartIndiaHackathon\SarvamBuild\backend` and run:

```bash
cd c:\SmartIndiaHackathon\SarvamBuild\backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 🌐 Opening the Application

Once the server starts, open your browser:
👉 **[http://localhost:8000](http://localhost:8000)**

---

## 🧪 Test Demo Walkthrough & Credentials

1. **Language Selection**:
   - Choose your preferred language (Hindi `hi-IN`, Bengali `bn-IN`, Tamil `ta-IN`, English `en-IN`, etc.).
2. **ABDM Verification (M1 Sandbox)**:
   - **Registered Mobile**: `9088260058` (Patient: Ramesh Verma, 38, Male)
   - **Sandbox OTP**: `123456`
   - Alternatively, use `8420744956` (Ananya Sen) or click **"Skip for Direct Intake"**.
3. **Conversational Clinical Interview**:
   - Speak into your microphone using the pulsing voice orb, or type directly into the chat input.
   - Click quick chip answers to test zero-latency fast path responses.
   - Test chief complaints like:
     - *"I have had severe chest pain and breathlessness since morning"*
     - *"पेट में 2 दिन से बहुत दर्द है"* (Hindi)
     - *"I have fever and severe headache"*
4. **Summary & Receipt Generation**:
   - When the interview concludes, view the clinical report card with extracted symptoms, red flag audit, and bilingual summary.
   - View and print the ABDM intake token.

---

## 🏗️ Architecture & Engines

| Layer | Engine / Model | Purpose |
|---|---|---|
| **Speech-to-Text (STT)** | `saaras:v3` (Sarvam AI) | Real-time Indian language speech transcription (~300ms) |
| **Text-to-Speech (TTS)** | `bulbul:v3` (Sarvam AI) + Google Native Fallback | Voice synthesis in 10 Indian languages with voice `priya` |
| **Translation** | `mayura:v1` (Sarvam AI) | Cross-lingual translation of patient history & summaries |
| **Clinical Reasoning** | `gemini-3.8-flash` (Google DeepMind) | Dynamic Macleod's Clinical Schema generation, slot filling & forking |
| **Vision OCR** | Gemini Multimodal Vision | Document digitization and red-flag extraction for prescriptions & lab reports |
| **Database** | SQLite (`medikiosk.db`) | Local persistence of encounters, patients, and clinical summaries |
| **ABDM Registry** | Sandbox M1 Mock | ABHA ID verification, OTP exchange, and token generation |
