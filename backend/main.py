"""
clinicView — Voice-First AI Patient Kiosk Server
Powered by Sarvam AI Full-Stack (STT + LLM + Mayura Translate + TTS) + FSM Clinical Engine.
"""

from __future__ import annotations
import os
import json
import uuid
import time
import base64
import logging
import asyncio
from typing import Dict, Any, List

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect,
    UploadFile, File, Form, HTTPException, Response, Query
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

import sarvam_client
import llm_client
import database
from dialogue_manager import DialogueManager
from pateint_registery import MOCK_ABHA_REGISTRY
from abdm_utils import normalize_profile

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("clinicView")

# In-memory sessions store
sessions: Dict[str, DialogueManager] = {}

app = FastAPI(
    title="clinicView API",
    description="Full-Stack Sarvam AI Patient Kiosk Engine",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    database.init_db()
    logger.info("clinicView startup completed.")


# ──────────────────────────────────────────────────────────────────────
# Health & Status Checks
# ──────────────────────────────────────────────────────────────────────
@app.get("/health")
@app.get("/api/health")
async def health_check():
    """Live health status of Sarvam STT, TTS, 105B LLM, Mayura, and Gemini."""
    sarvam_status = sarvam_client.check_sarvam_health()
    return {
        "status": "healthy",
        "service": "clinicView Patient Kiosk",
        "sarvam_ai": sarvam_status,
        "gemini_fallback": bool(os.getenv("GEMINI_API_KEY")),
    }


# ──────────────────────────────────────────────────────────────────────
# ABDM / Patient Auth & Registry
# ──────────────────────────────────────────────────────────────────────
class MobileInitReq(BaseModel):
    phone: str

class MobileConfirmReq(BaseModel):
    phone: str
    otp: str

@app.post("/api/abdm/auth/mobile/init")
async def abdm_mobile_init(req: MobileInitReq):
    clean = req.phone.strip().replace(" ", "").replace("-", "")
    logger.info(f"ABDM OTP requested for phone: {clean}")
    return {
        "status": "success",
        "transaction_id": f"txn_{uuid.uuid4().hex[:8]}",
        "message": "OTP sent successfully (Test Sandbox OTP is 123456)",
    }

@app.post("/api/abdm/auth/mobile/confirm")
async def abdm_mobile_confirm(req: MobileConfirmReq):
    clean = req.phone.strip().replace(" ", "").replace("-", "")
    if req.otp != "123456":
        raise HTTPException(status_code=400, detail="Invalid OTP. Use test OTP: 123456")

    # Match in DB or MOCK registry
    matched_patients = database.get_patients_by_phone(clean)
    if matched_patients:
        p = matched_patients[0]
        prev_hist = database.fetch_previous_history(p["patient_id"])
        return {
            "status": "success",
            "patient": p,
            "previous_history": prev_hist,
        }

    # Search in MOCK_ABHA_REGISTRY
    for abha_key, raw in MOCK_ABHA_REGISTRY.items():
        if raw.get("mobile", "") == clean or clean in raw.get("mobile", ""):
            norm = normalize_profile(abha_key, raw)
            pat_id = database.save_patient({
                "patient_id": f"pat_{clean}",
                "abha_id": abha_key,
                "abha_number": norm.get("abha_number_raw", ""),
                "full_name": norm.get("name", "Unknown"),
                "phone_number": clean,
                "age": norm.get("age", 30),
                "gender": norm.get("gender_display", "Male"),
                "dob": norm.get("dob", "1990-01-01"),
                "address": norm.get("address", ""),
            })
            p = database.get_patient_by_id(pat_id)
            prev_hist = database.fetch_previous_history(pat_id)
            return {
                "status": "success",
                "patient": p,
                "previous_history": prev_hist,
            }

    # Auto-register as walk-in patient if new
    pat_id = database.save_patient({
        "patient_id": f"pat_{clean}",
        "full_name": f"Citizen ({clean[-4:]})",
        "phone_number": clean,
    })
    p = database.get_patient_by_id(pat_id)
    return {"status": "success", "patient": p, "previous_history": None}


# ──────────────────────────────────────────────────────────────────────
# Speech & Audio Endpoints (Sarvam STT & TTS)
# ──────────────────────────────────────────────────────────────────────
@app.post("/api/stt")
async def api_stt(
    audio: UploadFile = File(...),
    language: str = Form("hi-IN"),
):
    """Transcribes audio using Sarvam saaras:v3."""
    audio_bytes = await audio.read()
    fmt = "wav" if "wav" in (audio.content_type or "") else "webm"
    result = sarvam_client.speech_to_text(audio_bytes, hint_language=language, audio_format=fmt)
    return result

@app.post("/api/tts")
async def api_tts(
    text: str = Form(...),
    language: str = Form("hi-IN"),
):
    """Synthesizes voice audio using Sarvam bulbul:v3."""
    audio_bytes = sarvam_client.text_to_speech(text, language_code=language)
    if not audio_bytes:
        raise HTTPException(status_code=500, detail="TTS generation failed")
    return Response(content=audio_bytes, media_type="audio/wav")


# ──────────────────────────────────────────────────────────────────────
# Session Creation & Clinical Summary
# ──────────────────────────────────────────────────────────────────────
class CreateSessionReq(BaseModel):
    patient_id: str = ""
    patient_name: str = ""
    patient_age: int | None = None
    patient_sex: str = "Male"
    patient_phone: str = ""
    patient_vitals: str = ""
    language: str = "hi-IN"
    clinic_mode: str = "allopathic"

@app.post("/api/session")
async def create_session(req: CreateSessionReq):
    dm = DialogueManager(clinic_mode=req.clinic_mode, language=req.language)
    dm.set_demographics(
        name=req.patient_name,
        age=req.patient_age,
        sex=req.patient_sex,
        phone=req.patient_phone,
        vitals=req.patient_vitals,
        patient_id=req.patient_id,
    )
    # Check for previous history
    if req.patient_id:
        prev = database.fetch_previous_history(req.patient_id)
        if prev:
            dm.set_previous_history(prev)

    ui = dm.start_session()
    session_id = dm.record.session_id
    sessions[session_id] = dm

    # Register in DB
    token_id = database.create_patient_session(
        session_id=session_id,
        patient_id=dm.record.patient_id,
        chief_complaint="",
        language=req.language,
        clinic_mode=req.clinic_mode,
    )
    return {"session_id": session_id, "token_id": token_id, "ui": ui}


@app.get("/api/summary/{session_id}")
async def get_or_generate_summary(session_id: str):
    """
    Generates the final clinical handover note via Sarvam 105B and Mayura,
    saves it to SQLite, and returns the bilingual document payload.
    """
    # Check if already generated in DB
    existing = database.get_clinical_summary(session_id)
    if existing:
        return existing

    dm = sessions.get(session_id)
    if not dm:
        raise HTTPException(status_code=404, detail="Session not found")

    filled = dm.record.filled_state
    chief_c = dm.record.chief_complaint or "General Clinical Evaluation"

    # Synthesize via Sarvam 105B + Mayura
    summary = llm_client.generate_clinical_summary(
        filled_state=filled,
        language_code=dm.language,
    )

    # Generate offline QR string
    qr_payload = f"ABDM:CLINICVIEW|SESSION:{session_id}|PATIENT:{dm.record.patient_name}|COMPLAINT:{chief_c}"
    qr_b64 = f"data:image/svg+xml;base64,{base64.b64encode(b'<svg></svg>').decode()}"

    summary_id = database.save_clinical_summary(
        session_id=session_id,
        small_summary=chief_c,
        full_detailed_summary=summary,
        vernacular_summary=summary.get("vernacular_narrative", ""),
        vernacular_language=summary.get("vernacular_language", dm.language),
        critical_highlights=summary.get("critical_highlights", []),
        qr_b64=qr_b64,
    )

    return database.get_clinical_summary(session_id)


# ──────────────────────────────────────────────────────────────────────
# Real-Time Conversational WebSocket (/ws/session)
# ──────────────────────────────────────────────────────────────────────
@app.websocket("/ws/session")
async def websocket_session(ws: WebSocket):
    await ws.accept()
    dm: DialogueManager | None = None

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            msg_type = msg.get("type", "")

            if msg_type == "start":
                session_id = msg.get("session_id")
                if session_id and session_id in sessions:
                    dm = sessions[session_id]
                else:
                    lang = msg.get("language", "hi-IN")
                    dm = DialogueManager(language=lang)
                    dm.set_demographics(
                        name=msg.get("patient_name", ""),
                        age=int(msg.get("patient_age")) if msg.get("patient_age") else None,
                        sex=msg.get("patient_sex", "Male"),
                        patient_id=msg.get("patient_id", ""),
                    )
                    sessions[dm.record.session_id] = dm

                ui = dm.start_session()
                # Synthesize TTS voice prompt
                tts_audio = sarvam_client.text_to_speech(ui["prompt"], language_code=dm.language)
                audio_b64 = base64.b64encode(tts_audio).decode("utf-8") if tts_audio else None

                await ws.send_json({
                    "type": "ui",
                    "audio_b64": audio_b64,
                    **ui,
                })

            elif msg_type == "input":
                if not dm:
                    await ws.send_json({"type": "error", "message": "No active session"})
                    continue

                input_type = msg.get("input_type", "voice")
                value = msg.get("value", "")

                await ws.send_json({"type": "orb_state", "orb_state": "processing"})

                # Execute dialogue turn
                loop = asyncio.get_event_loop()
                ui = await loop.run_in_executor(None, dm.process_patient_input, input_type, value)

                # Synthesize Sarvam TTS for the assistant prompt
                tts_audio = sarvam_client.text_to_speech(ui["prompt"], language_code=dm.language)
                audio_b64 = base64.b64encode(tts_audio).decode("utf-8") if tts_audio else None

                await ws.send_json({
                    "type": "ui",
                    "audio_b64": audio_b64,
                    **ui,
                })

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected.")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)


# Mount frontend static files
frontend_dir = None
for candidate in [
    os.path.join(os.path.dirname(__file__), "frontend"),
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend"),
    os.path.join(os.getcwd(), "frontend"),
]:
    if os.path.exists(candidate) and os.path.exists(os.path.join(candidate, "index.html")):
        frontend_dir = candidate
        break

if frontend_dir:
    logger.info(f"Mounting frontend from: {frontend_dir}")
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
else:
    logger.warning("No frontend directory found to mount")

