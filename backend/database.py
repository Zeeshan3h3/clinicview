"""
clinicView — Database Engine (SQLite / aiosqlite with Supabase Fallback)
Manages patient records, longitudinal visit histories, and clinical summaries.
All data is indexed by the primary key (patient_id / abha_id).
"""

from __future__ import annotations
import sqlite3
import json
import os
import uuid
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "medikiosk.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initializes tables and seeds initial ABDM mock patients."""
    conn = get_connection()
    cursor = conn.cursor()

    # 1. Patients Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            patient_id TEXT PRIMARY KEY,
            abha_id TEXT,
            abha_number TEXT,
            full_name TEXT NOT NULL,
            phone_number TEXT,
            age INTEGER,
            gender TEXT,
            dob TEXT,
            address TEXT,
            vitals TEXT,
            blood_group TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 2. Patient Sessions Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS patient_sessions (
            session_id TEXT PRIMARY KEY,
            patient_id TEXT,
            token_id TEXT,
            chief_complaint TEXT,
            session_status TEXT DEFAULT 'ACTIVE',
            language TEXT DEFAULT 'en-IN',
            clinic_mode TEXT DEFAULT 'allopathic',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES patients(patient_id)
        )
    """)

    # 3. Clinical Summaries Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clinical_summaries (
            summary_id TEXT PRIMARY KEY,
            session_id TEXT UNIQUE,
            small_summary TEXT,
            full_detailed_summary TEXT,
            vernacular_summary TEXT,
            vernacular_language TEXT,
            critical_highlights TEXT,
            qr_b64 TEXT,
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES patient_sessions(session_id)
        )
    """)

    conn.commit()

    # Pre-seed verified ABDM patients if empty
    cursor.execute("SELECT COUNT(*) FROM patients")
    count = cursor.fetchone()[0]
    if count == 0:
        _seed_default_patients(conn)

    conn.close()
    logger.info("clinicView Database initialized successfully.")


def _seed_default_patients(conn: sqlite3.Connection):
    from pateint_registery import MOCK_ABHA_REGISTRY
    from abdm_utils import normalize_profile

    cursor = conn.cursor()
    for abha_key, raw in MOCK_ABHA_REGISTRY.items():
        norm = normalize_profile(abha_key, raw)
        pat_id = f"pat_{norm['mobile']}" if norm.get("mobile") else f"pat_{uuid.uuid4().hex[:6]}"
        cursor.execute("""
            INSERT OR REPLACE INTO patients 
            (patient_id, abha_id, abha_number, full_name, phone_number, age, gender, dob, address, vitals, blood_group)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pat_id,
            norm.get("health_id", abha_key),
            norm.get("abha_number_raw", ""),
            norm.get("name", "Unknown"),
            norm.get("mobile", ""),
            norm.get("age", 30),
            norm.get("gender_display", "Male"),
            norm.get("dob", "1990-01-01"),
            norm.get("address", ""),
            "BP: 120/80 mmHg, Pulse: 74 bpm, SpO2: 98%",
            norm.get("blood_group", "O+"),
        ))
    conn.commit()
    logger.info("Pre-seeded ABDM patients into clinicView DB.")


def get_patients_by_phone(phone_number: str) -> list[dict]:
    """Fetches registered patient records by mobile number."""
    conn = get_connection()
    clean_phone = phone_number.strip().replace(" ", "").replace("-", "")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM patients WHERE phone_number = ? OR phone_number LIKE ?", (clean_phone, f"%{clean_phone[-10:]}%"))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_patient_by_id(patient_id: str) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM patients WHERE patient_id = ? OR abha_id = ?", (patient_id, patient_id))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def save_patient(patient_data: dict) -> str:
    """Inserts or updates a patient and returns patient_id."""
    conn = get_connection()
    cursor = conn.cursor()
    patient_id = patient_data.get("patient_id") or f"pat_{uuid.uuid4().hex[:8]}"
    cursor.execute("""
        INSERT OR REPLACE INTO patients 
        (patient_id, abha_id, abha_number, full_name, phone_number, age, gender, dob, address, vitals, blood_group)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        patient_id,
        patient_data.get("abha_id", ""),
        patient_data.get("abha_number", ""),
        patient_data.get("full_name", "Walk-in Patient"),
        patient_data.get("phone_number", ""),
        patient_data.get("age", 30),
        patient_data.get("gender", "Male"),
        patient_data.get("dob", "1990-01-01"),
        patient_data.get("address", ""),
        patient_data.get("vitals", "BP: 120/80 mmHg, Pulse: 72 bpm"),
        patient_data.get("blood_group", "O+"),
    ))
    conn.commit()
    conn.close()
    return patient_id


def create_patient_session(
    session_id: str,
    patient_id: str,
    chief_complaint: str = "",
    language: str = "en-IN",
    clinic_mode: str = "allopathic",
) -> str:
    conn = get_connection()
    cursor = conn.cursor()
    token_id = f"TOKEN-{uuid.uuid4().hex[:4].upper()}"
    cursor.execute("""
        INSERT INTO patient_sessions (session_id, patient_id, token_id, chief_complaint, language, clinic_mode)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (session_id, patient_id, token_id, chief_complaint, language, clinic_mode))
    conn.commit()
    conn.close()
    return token_id


def fetch_previous_history(patient_id: str) -> dict | None:
    """
    Fetches the most recent completed encounter for longitudinal clinical context.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ps.session_id, ps.chief_complaint, ps.completed_at,
               cs.full_detailed_summary, cs.small_summary, cs.vernacular_summary
        FROM patient_sessions ps
        LEFT JOIN clinical_summaries cs ON ps.session_id = cs.session_id
        WHERE ps.patient_id = ? AND ps.session_status = 'COMPLETED'
        ORDER BY ps.completed_at DESC
        LIMIT 1
    """, (patient_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    res = dict(row)
    if res.get("full_detailed_summary"):
        try:
            res["full_detailed_summary"] = json.loads(res["full_detailed_summary"])
        except Exception:
            pass
    return res


def save_clinical_summary(
    session_id: str,
    small_summary: str,
    full_detailed_summary: dict,
    vernacular_summary: str = "",
    vernacular_language: str = "hi-IN",
    critical_highlights: list = None,
    qr_b64: str = "",
) -> str:
    conn = get_connection()
    cursor = conn.cursor()
    summary_id = f"sum_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()

    cursor.execute("""
        INSERT OR REPLACE INTO clinical_summaries
        (summary_id, session_id, small_summary, full_detailed_summary, vernacular_summary, vernacular_language, critical_highlights, qr_b64, generated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        summary_id,
        session_id,
        small_summary,
        json.dumps(full_detailed_summary, ensure_ascii=False),
        vernacular_summary,
        vernacular_language,
        json.dumps(critical_highlights or [], ensure_ascii=False),
        qr_b64,
        now,
    ))

    # Mark session as completed
    cursor.execute("""
        UPDATE patient_sessions
        SET session_status = 'COMPLETED', completed_at = ?
        WHERE session_id = ?
    """, (now, session_id))

    conn.commit()
    conn.close()
    return summary_id


def get_clinical_summary(session_id: str) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT cs.*, ps.token_id, ps.chief_complaint, ps.patient_id, p.full_name, p.age, p.gender, p.vitals, p.abha_number
        FROM clinical_summaries cs
        JOIN patient_sessions ps ON cs.session_id = ps.session_id
        JOIN patients p ON ps.patient_id = p.patient_id
        WHERE cs.session_id = ?
    """, (session_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    res = dict(row)
    if res.get("full_detailed_summary"):
        try:
            res["full_detailed_summary"] = json.loads(res["full_detailed_summary"])
        except Exception:
            pass
    if res.get("critical_highlights"):
        try:
            res["critical_highlights"] = json.loads(res["critical_highlights"])
        except Exception:
            pass
    return res
