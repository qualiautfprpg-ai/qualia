from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import smtplib
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import joblib
from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

try:
    import psycopg
except Exception:  # pragma: no cover - optional local dependency
    psycopg = None


BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "qualia_model.joblib"
DB_PATH = BASE_DIR / "qualia_app.db"
FRONTEND_DIR = BASE_DIR / "frontend"
STATIC_DIR = FRONTEND_DIR if FRONTEND_DIR.exists() else BASE_DIR
SMTP_CONFIG_PATH = BASE_DIR / "smtp_config.json"
UPLOADS_DIR = BASE_DIR / "uploads" / "turmas"
UTC = timezone.utc
SESSION_HOURS = 12
LEGACY_ADMIN_EMAIL = "admin@qualia.com"
DEFAULT_ADMIN_EMAIL = "qualiautfprpg@gmail.com"
DEFAULT_ADMIN_PASSWORD = "qualidedevida2412"
TEACHERS = ("Adriana Guimarães", "José Alves Faria")
MQTT_SHARED_KEY = os.getenv("QUALIA_MQTT_KEY", "qualia-local-key")
GOOGLE_CLIENT_ID = os.getenv("QUALIA_GOOGLE_CLIENT_ID", "")
CORS_ORIGINS = [origin.strip() for origin in os.getenv("QUALIA_CORS_ORIGINS", "*").split(",") if origin.strip()]
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
USING_POSTGRES = DATABASE_URL.startswith("postgres")
if not MODEL_PATH.exists():
    raise FileNotFoundError(
        f"Arquivo de modelo não encontrado em {MODEL_PATH}. "
        "Execute qualia_train.py antes de iniciar a API."
    )

pipeline = None
feature_cols: List[str] = []
OCR_READER = None


def ensure_model_loaded() -> Tuple[Any, List[str]]:
    global pipeline, feature_cols
    import joblib

    if pipeline is not None and feature_cols:
        return pipeline, feature_cols
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Arquivo de modelo nÃ£o encontrado em {MODEL_PATH}. "
            "Execute qualia_train.py antes de iniciar a API."
        )
    model_bundle = joblib.load(MODEL_PATH)
    pipeline = model_bundle["pipeline"]
    feature_cols = model_bundle["feature_cols"]
    return pipeline, feature_cols


class LoginInput(BaseModel):
    email: str
    password: str = Field(..., min_length=4, max_length=128)


class ForgotPasswordInput(BaseModel):
    email: str


class ResetPasswordInput(BaseModel):
    token: str = Field(..., min_length=20, max_length=256)
    password: str = Field(..., min_length=6, max_length=128)


class MessageResponse(BaseModel):
    status: str
    message: str


class AuthResponse(BaseModel):
    token: str
    role: str
    user_id: int
    name: str
    email: str


class AdminUserCreate(BaseModel):
    nome: str = Field(..., min_length=3, max_length=120)
    email: str
    cpf: str = Field(..., min_length=11, max_length=20)
    idade: int = Field(..., ge=10, le=100)
    sexo: str = Field(..., min_length=1, max_length=16)
    altura_cm: float = Field(..., ge=120, le=230)
    observacoes: Optional[str] = Field(default="")


class UserSummary(BaseModel):
    id: int
    nome: str
    email: str
    idade: Optional[int]
    sexo: Optional[str]
    altura_cm: Optional[float]
    role: str
    observacoes: Optional[str]
    created_at: str
    ultima_avaliacao_em: Optional[str] = None
    ultimo_score: Optional[float] = None


class EvaluationInput(BaseModel):
    user_id: Optional[int] = None
    email: Optional[str] = None
    roster_student_id: Optional[int] = None
    nome: Optional[str] = None
    idade: Optional[int] = Field(default=None, ge=10, le=100)
    sexo: Optional[str] = None
    altura_cm: Optional[float] = Field(default=None, ge=120, le=230)
    tipo_avaliacao: str = Field(default="completa")
    peso: float = Field(..., gt=0, le=300)
    bf: Optional[float] = Field(default=None, ge=0, le=80)
    agua: Optional[float] = Field(default=None, ge=0, le=100)
    massa_muscular: Optional[float] = Field(default=None, ge=0, le=100)
    bmr: Optional[float] = Field(default=None, ge=0, le=6000)
    idade_metabolica: Optional[float] = Field(default=None, ge=0, le=120)
    massa_ossea: Optional[float] = Field(default=None, ge=0, le=20)
    vo2: Optional[float] = Field(default=None, ge=0, le=100)
    pressao_sist: Optional[float] = Field(default=None, ge=0, le=300)
    pressao_diast: Optional[float] = Field(default=None, ge=0, le=200)
    cooper: Optional[float] = Field(default=None, ge=0, le=10)
    flexibilidade: Optional[float] = Field(default=None, ge=0, le=100)
    abd: Optional[float] = Field(default=None, ge=0, le=300)
    flexao: Optional[float] = Field(default=None, ge=0, le=300)
    fc_rep: Optional[float] = Field(default=None, ge=0, le=250)
    fc_pos: Optional[float] = Field(default=None, ge=0, le=250)
    fc_rec_5: Optional[float] = Field(default=None, ge=0, le=250)
    fonte: str = Field(default="manual")
    observacoes: Optional[str] = Field(default="")


class MqttIngestInput(EvaluationInput):
    mqtt_key: str


class EvaluationResult(BaseModel):
    score_ia: float
    imc: float
    imc_class: str
    bf_class: str
    vo2_est: Optional[float]
    vo2_class: str
    pressao_class: str
    objetivo_sugerido: str
    pontos_fortes: List[str]
    pontos_fracos: List[str]
    recomendacoes: List[str]
    nutrition_tips: List[str] = Field(default_factory=list)
    weekly_plan: List[str] = Field(default_factory=list)
    daily_water_liters: float = 0.0


class EvaluationRecord(BaseModel):
    id: int
    user_id: int
    tipo_avaliacao: str
    fonte: str
    created_at: str
    payload: Dict[str, Any]
    resultado: EvaluationResult


class DashboardResponse(BaseModel):
    usuario: UserSummary
    ultima_avaliacao: Optional[EvaluationRecord] = None
    historico: List[EvaluationRecord]


class ResultsEmailRequest(BaseModel):
    scope: str = Field(default="latest")
    recipient_email: Optional[str] = None


class ResultsEmailResponse(BaseModel):
    status: str
    recipient_email: str
    scope: str
    message: str
    error_detail: Optional[str] = None


class EmailTestInput(BaseModel):
    recipient_email: str


class EmailTestResponse(BaseModel):
    status: str
    message: str
    error_detail: Optional[str] = None


class ConfigResponse(BaseModel):
    google_login_enabled: bool
    google_client_id: str
    mqtt_ingest_enabled: bool


class AdminOverview(BaseModel):
    total_users: int
    total_evaluations: int


class WellnessNewsItem(BaseModel):
    title: str
    link: str
    source: str
    published_at: str


class AppointmentCreate(BaseModel):
    nome: str = Field(..., min_length=3, max_length=120)
    email: str
    telefone: str = Field(..., min_length=8, max_length=40)
    data_agendada: str
    horario_agendado: str
    observacoes: Optional[str] = ""


class AppointmentResponse(BaseModel):
    id: int
    nome: str
    email: str
    telefone: str
    data_agendada: str
    horario_agendado: str
    local: str
    status: str
    email_status: str


class AdminAppointmentItem(AppointmentResponse):
    created_at: str


class AppointmentCancelResponse(BaseModel):
    status: str
    email_status: str
    message: str


class TeacherOption(BaseModel):
    nome: str


class DisciplineCreate(BaseModel):
    professor_nome: str
    ano: int = Field(..., ge=2020, le=2100)
    nome: str = Field(..., min_length=3, max_length=160)
    codigo: str = Field(..., min_length=2, max_length=32)
    horario: str = Field(..., min_length=3, max_length=120)
    turma_nome: str = Field(..., min_length=2, max_length=120)
    arquivo_referencia: Optional[str] = ""


class DisciplineUpdate(DisciplineCreate):
    pass


class DisciplineSummary(BaseModel):
    id: int
    professor_nome: str
    ano: int
    nome: str
    codigo: str
    horario: str
    turma_nome: str
    arquivo_referencia: Optional[str]
    total_alunos: int
    created_at: str


class RosterImportInput(BaseModel):
    nomes_texto: str = ""
    arquivo_referencia: Optional[str] = ""


class RosterStudentItem(BaseModel):
    id: int
    disciplina_id: int
    nome: str
    matricula: Optional[str]
    linked_user_id: Optional[int]
    linked_user_email: Optional[str]
    total_testes: int
    created_at: str


class RosterImportResponse(BaseModel):
    alunos: List[RosterStudentItem]
    importados: int
    ignorados: int
    arquivo_referencia: Optional[str] = ""
    mensagem: str


class EvaluationSearchItem(BaseModel):
    evaluation_id: int
    user_id: int
    nome: str
    email: str
    tipo_avaliacao: str
    created_at: str
    score_ia: Optional[float]


class SessionUser(BaseModel):
    user_id: int
    role: str
    email: str
    nome: str


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def normalize_sex(value: Optional[str]) -> str:
    text = (value or "m").strip().lower()
    return "f" if text.startswith("f") else "m"


def password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000).hex()
    return hmac.compare_digest(check, digest)


def default_password_from_cpf(cpf: str) -> str:
    digits = "".join(ch for ch in cpf if ch.isdigit())
    return (digits[-4:] if len(digits) >= 4 else "1234") or "1234"


class RowAdapter:
    def __init__(self, columns: List[str], values: Tuple[Any, ...]):
        self._columns = list(columns)
        self._values = tuple(values)
        self._mapping = {column: values[index] for index, column in enumerate(columns)}

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._mapping[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._mapping.get(key, default)

    def keys(self) -> List[str]:
        return list(self._columns)

    def items(self):
        return self._mapping.items()

    def values(self):
        return self._mapping.values()

    def __contains__(self, key: Any) -> bool:
        return key in self._mapping


class CursorAdapter:
    def __init__(self, cursor: Any, rows: Optional[List[RowAdapter]] = None, lastrowid: Optional[int] = None):
        self._cursor = cursor
        self._rows = rows
        self._index = 0
        self.lastrowid = lastrowid

    def fetchone(self) -> Optional[RowAdapter]:
        if self._rows is None:
            return self._cursor.fetchone()
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self) -> List[RowAdapter]:
        if self._rows is None:
            return self._cursor.fetchall()
        if self._index >= len(self._rows):
            return []
        rows = self._rows[self._index :]
        self._index = len(self._rows)
        return rows


class ConnectionAdapter:
    def __init__(self, raw: Any, use_postgres: bool):
        self.raw = raw
        self.use_postgres = use_postgres

    def _adapt_sql(self, sql: str) -> str:
        adapted = sql
        if not self.use_postgres:
            return adapted
        adapted = adapted.replace("?", "%s")
        adapted = adapted.replace("pendente_configuração", "pendente_configuracao")
        adapted = adapted.replace(
            "json_extract(e.result_json, '$.score_ia')",
            "CAST(e.result_json::json ->> 'score_ia' AS DOUBLE PRECISION)",
        )
        adapted = adapted.replace(
            "json_extract(e.result_json, '$.score_ia') AS score_ia",
            "CAST(e.result_json::json ->> 'score_ia' AS DOUBLE PRECISION) AS score_ia",
        )
        return adapted

    def execute(self, sql: str, params: Tuple[Any, ...] = ()) -> CursorAdapter:
        if not self.use_postgres:
            return self.raw.execute(sql, params)

        adapted = self._adapt_sql(sql)
        if "INSERT OR IGNORE INTO users" in adapted:
            adapted = adapted.replace("INSERT OR IGNORE INTO users", "INSERT INTO users", 1)
            adapted += " ON CONFLICT (email) DO NOTHING"

        needs_returning = False
        if adapted.lstrip().upper().startswith("INSERT INTO ") and " RETURNING " not in adapted.upper():
            for table in ("evaluations", "appointments", "disciplines"):
                if f"INSERT INTO {table}" in adapted:
                    adapted += " RETURNING id"
                    needs_returning = True
                    break

        cursor = self.raw.execute(adapted, params)
        if needs_returning:
            returned = cursor.fetchone()
            lastrowid = returned[0] if returned else None
            rows: List[RowAdapter] = []
            return CursorAdapter(cursor, rows=rows, lastrowid=lastrowid)

        if cursor.description is None:
            return CursorAdapter(cursor)

        columns = [desc.name for desc in cursor.description]
        rows = [RowAdapter(columns, row) for row in cursor.fetchall()]
        return CursorAdapter(cursor, rows=rows)

    def commit(self) -> None:
        self.raw.commit()

    def close(self) -> None:
        self.raw.close()


def get_conn() -> Any:
    if USING_POSTGRES:
        if psycopg is None:
            raise RuntimeError("psycopg não está instalado para uso com PostgreSQL.")
        raw = psycopg.connect(DATABASE_URL)
        return ConnectionAdapter(raw, use_postgres=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    if USING_POSTGRES:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                idade INTEGER,
                sexo TEXT,
                altura_cm DOUBLE PRECISION,
                cpf TEXT,
                observacoes TEXT,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS evaluations (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                tipo_avaliacao TEXT NOT NULL,
                fonte TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS password_resets (
                token TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS appointments (
                id BIGSERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                email TEXT NOT NULL,
                telefone TEXT NOT NULL,
                data_agendada TEXT NOT NULL,
                horario_agendado TEXT NOT NULL,
                local TEXT NOT NULL,
                observacoes TEXT,
                status TEXT NOT NULL,
                email_status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS disciplines (
                id BIGSERIAL PRIMARY KEY,
                professor_nome TEXT NOT NULL,
                ano INTEGER NOT NULL,
                nome TEXT NOT NULL,
                codigo TEXT NOT NULL,
                horario TEXT NOT NULL,
                turma_nome TEXT NOT NULL,
                arquivo_referencia TEXT,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS roster_students (
                id BIGSERIAL PRIMARY KEY,
                disciplina_id BIGINT NOT NULL REFERENCES disciplines(id) ON DELETE CASCADE,
                nome TEXT NOT NULL,
                matricula TEXT,
                linked_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL
            )
            """,
        ]
        for statement in statements:
            conn.execute(statement)
    else:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                idade INTEGER,
                sexo TEXT,
                altura_cm REAL,
                cpf TEXT,
                observacoes TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tipo_avaliacao TEXT NOT NULL,
                fonte TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS password_resets (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                email TEXT NOT NULL,
                telefone TEXT NOT NULL,
                data_agendada TEXT NOT NULL,
                horario_agendado TEXT NOT NULL,
                local TEXT NOT NULL,
                observacoes TEXT,
                status TEXT NOT NULL,
                email_status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS disciplines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                professor_nome TEXT NOT NULL,
                ano INTEGER NOT NULL,
                nome TEXT NOT NULL,
                codigo TEXT NOT NULL,
                horario TEXT NOT NULL,
                turma_nome TEXT NOT NULL,
                arquivo_referencia TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS roster_students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                disciplina_id INTEGER NOT NULL,
                nome TEXT NOT NULL,
                matricula TEXT,
                linked_user_id INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY(disciplina_id) REFERENCES disciplines(id) ON DELETE CASCADE,
                FOREIGN KEY(linked_user_id) REFERENCES users(id) ON DELETE SET NULL
            );
            """
        )

    legacy_admin = conn.execute("SELECT id FROM users WHERE lower(email) = lower(?)", (LEGACY_ADMIN_EMAIL,)).fetchone()
    if legacy_admin:
        existing_new = conn.execute("SELECT id FROM users WHERE lower(email) = lower(?)", (DEFAULT_ADMIN_EMAIL,)).fetchone()
        if not existing_new:
            conn.execute(
                "UPDATE users SET email = ?, password_hash = ?, nome = ?, role = 'admin' WHERE id = ?",
                (
                    DEFAULT_ADMIN_EMAIL,
                    password_hash(DEFAULT_ADMIN_PASSWORD),
                    "Administrador QualIA",
                    legacy_admin["id"],
                ),
            )

    admin = conn.execute("SELECT id FROM users WHERE lower(email) = lower(?)", (DEFAULT_ADMIN_EMAIL,)).fetchone()
    if not admin:
        conn.execute(
            """
            INSERT INTO users (nome, email, password_hash, role, idade, sexo, altura_cm, cpf, observacoes, created_at)
            VALUES (?, ?, ?, 'admin', ?, ?, ?, ?, ?, ?)
            """,
            (
                "Administrador QualIA",
                DEFAULT_ADMIN_EMAIL,
                password_hash(DEFAULT_ADMIN_PASSWORD),
                30,
                "M",
                175.0,
                "00000000000",
                "Conta administrativa inicial.",
                utc_now().isoformat(),
            ),
        )
    else:
        conn.execute(
            "UPDATE users SET password_hash = ?, role = 'admin', nome = ? WHERE id = ?",
            (password_hash(DEFAULT_ADMIN_PASSWORD), "Administrador QualIA", admin["id"]),
        )
    conn.commit()
    conn.close()


def classificar_imc(imc: float) -> str:
    if imc < 20:
        return "Abaixo do ideal"
    if imc <= 24:
        return "Peso normal"
    if imc <= 29:
        return "Excesso de peso"
    if imc <= 35:
        return "Obesidade"
    return "Super obesidade"


def _bf_ranges(sexo: str, idade: float) -> List[Tuple[str, float, float]]:
    if normalize_sex(sexo) == "m":
        table = [
            ((20, 29), [("Muito Magro", 0, 5.2), ("Magro", 5.3, 9.3), ("Muito Bom", 9.4, 14.01), ("Saudável", 14.02, 17.5), ("Sobrepeso", 17.6, 22.4), ("Gordo", 22.5, 29.2), ("Muito Gordo", 29.3, 100)]),
            ((30, 39), [("Muito Magro", 0, 9.2), ("Magro", 9.3, 14.0), ("Muito Bom", 14.1, 17.5), ("Saudável", 17.6, 20.6), ("Sobrepeso", 20.7, 24.2), ("Gordo", 24.3, 30.0), ("Muito Gordo", 30.1, 100)]),
            ((40, 49), [("Muito Magro", 0, 11.5), ("Magro", 11.6, 16.3), ("Muito Bom", 16.4, 19.6), ("Saudável", 19.7, 22.5), ("Sobrepeso", 22.6, 26.2), ("Gordo", 26.3, 31.4), ("Muito Gordo", 31.5, 100)]),
            ((50, 59), [("Muito Magro", 0, 12.9), ("Magro", 13.0, 18.1), ("Muito Bom", 18.2, 21.2), ("Saudável", 21.3, 24.2), ("Sobrepeso", 24.3, 27.6), ("Gordo", 27.7, 32.4), ("Muito Gordo", 32.5, 100)]),
            ((60, 200), [("Muito Magro", 0, 13.0), ("Magro", 13.1, 18.5), ("Muito Bom", 18.6, 22.0), ("Saudável", 22.1, 25.0), ("Sobrepeso", 25.1, 28.4), ("Gordo", 28.5, 33.5), ("Muito Gordo", 33.6, 100)]),
        ]
    else:
        table = [
            ((20, 29), [("Muito Magro", 0, 10.7), ("Magro", 10.8, 17.0), ("Muito Bom", 17.1, 20.5), ("Saudável", 20.6, 23.8), ("Sobrepeso", 23.9, 27.6), ("Gordo", 27.7, 35.5), ("Muito Gordo", 35.6, 100)]),
            ((30, 39), [("Muito Magro", 0, 13.3), ("Magro", 13.4, 18.0), ("Muito Bom", 18.1, 21.8), ("Saudável", 21.9, 24.8), ("Sobrepeso", 24.9, 30.0), ("Gordo", 30.1, 35.8), ("Muito Gordo", 35.9, 100)]),
            ((40, 49), [("Muito Magro", 0, 16.1), ("Magro", 16.2, 21.4), ("Muito Bom", 21.5, 25.1), ("Saudável", 25.2, 28.3), ("Sobrepeso", 28.4, 32.1), ("Gordo", 32.2, 37.7), ("Muito Gordo", 37.8, 100)]),
            ((50, 59), [("Muito Magro", 0, 18.8), ("Magro", 18.9, 25.1), ("Muito Bom", 25.2, 28.6), ("Saudável", 28.7, 32.5), ("Sobrepeso", 32.6, 35.6), ("Gordo", 35.7, 39.6), ("Muito Gordo", 39.7, 100)]),
            ((60, 200), [("Muito Magro", 0, 19.1), ("Magro", 19.2, 25.0), ("Muito Bom", 25.1, 29.5), ("Saudável", 29.6, 32.8), ("Sobrepeso", 32.9, 36.7), ("Gordo", 36.8, 40.4), ("Muito Gordo", 40.5, 100)]),
        ]
    if idade < table[0][0][0]:
        return table[0][1]
    for (age_min, age_max), ranges in table:
        if age_min <= idade <= age_max:
            return ranges
    return table[-1][1]


def classificar_bf(bf: Optional[float], sexo: str, idade: float) -> str:
    if bf is None:
        return "Não informado"
    for label, start, end in _bf_ranges(sexo, idade):
        if start <= bf <= end:
            return label
    return "Muito Gordo"


def classificar_vo2(vo2: Optional[float], sexo: str) -> str:
    if vo2 is None:
        return "Não informado"
    if normalize_sex(sexo) == "m":
        if vo2 < 28:
            return "Baixo"
        if vo2 < 38:
            return "Moderado"
        if vo2 < 48:
            return "Bom"
        return "Muito bom"
    if vo2 < 23:
        return "Baixo"
    if vo2 < 33:
        return "Moderado"
    if vo2 < 42:
        return "Bom"
    return "Muito bom"


def classificar_pressao(sist: Optional[float], diast: Optional[float]) -> str:
    if sist is None or diast is None:
        return "Não informada"
    if sist < 120 and diast < 80:
        return "Normal"
    if 120 <= sist <= 139 or 80 <= diast <= 89:
        return "Pré-hipertensão"
    if 140 <= sist <= 159 or 90 <= diast <= 99:
        return "Hipertensão estágio 1"
    if sist >= 160 or diast >= 100:
        return "Hipertensão estágio 2"
    if sist > 180 or diast > 110:
        return "Crise hipertensiva"
    return "Hipertensão estágio 2"


def gerar_recomendacoes(imc: float, bf: Optional[float], vo2: Optional[float], score: float) -> List[str]:
    recomendacoes: List[str] = []
    if score < 60:
        recomendacoes.append("Criar um plano progressivo de treino e rotina semanal de acompanhamento.")
    if imc >= 25:
        recomendacoes.append("Priorizar controle de peso com treino aeróbico e ajuste alimentar supervisionado.")
    if bf is not None and bf >= 25:
        recomendacoes.append("Reduzir percentual de gordura com foco em constância, sono e proteína adequada.")
    if vo2 is not None and vo2 < 35:
        recomendacoes.append("Adicionar 3 a 4 sessões semanais de caminhada, corrida leve ou bicicleta.")
    if not recomendacoes:
        recomendacoes.append("Manter a rotina atual e repetir a avaliação para acompanhar evolução.")
    return recomendacoes


def gerar_pontos(imc_class: str, bf_class: str, vo2_class: str, pressao_class: str, score: float) -> Tuple[List[str], List[str]]:
    fortes: List[str] = []
    fracos: List[str] = []
    if imc_class == "Peso adequado":
        fortes.append("Peso em faixa adequada para a altura.")
    else:
        fracos.append("Peso fora da faixa ideal, pedindo atencao ao planejamento fisico e nutricional.")
    if bf_class in {"Ideal", "Adequado"}:
        fortes.append("Composicao corporal em faixa positiva.")
    elif bf_class != "Não informado":
        fracos.append("Percentual de gordura sugere necessidade de ajuste na composicao corporal.")
    if vo2_class in {"Bom", "Muito bom"}:
        fortes.append("Capacidade aeróbica favorável.")
    elif vo2_class != "Não informado":
        fracos.append("Capacidade aeróbica abaixo do desejado para melhor desempenho e saúde.")
    if pressao_class == "Normal":
        fortes.append("Pressao arterial dentro da normalidade.")
    elif pressao_class != "Não informada":
        fracos.append("Pressao arterial pede monitoramento mais proximo.")
    if score >= 80:
        fortes.append("Score global muito bom.")
    elif score < 50:
        fracos.append("Score global baixo, sugerindo intervencao estruturada.")
    if not fortes:
        fortes.append("Ainda sem pontos fortes dominantes; a tendência pode melhorar com acompanhamento.")
    if not fracos:
        fracos.append("Nenhum ponto crítico evidente no momento.")
    return fortes, fracos


def fetch_wellness_news(limit: int = 6) -> List[WellnessNewsItem]:
    query = urllib.parse.quote("qualidade de vida saúde atividade física bem-estar")
    url = f"https://news.google.com/rss/search?q={query}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            xml_data = response.read()
    except Exception:
        return []

    root = ET.fromstring(xml_data)
    news: List[WellnessNewsItem] = []
    for item in root.findall(".//item")[:limit]:
        news.append(
            WellnessNewsItem(
                title=item.findtext("title") or "Notícia",
                link=item.findtext("link") or "#",
                source=item.findtext("source") or "Google News",
                published_at=item.findtext("pubDate") or "",
            )
        )
    return news


def load_smtp_settings() -> Dict[str, Any]:
    if SMTP_CONFIG_PATH.exists():
        with SMTP_CONFIG_PATH.open("r", encoding="utf-8") as fh:
            file_settings = json.load(fh)
        return {
            "host": file_settings.get("host", ""),
            "port": int(file_settings.get("port", 587)),
            "user": file_settings.get("user", ""),
            "pass": file_settings.get("pass", ""),
            "from": file_settings.get("from", ""),
        }
    return {
        "host": os.getenv("QUALIA_SMTP_HOST", ""),
        "port": int(os.getenv("QUALIA_SMTP_PORT", "587")),
        "user": os.getenv("QUALIA_SMTP_USER", ""),
        "pass": os.getenv("QUALIA_SMTP_PASS", ""),
        "from": os.getenv("QUALIA_SMTP_FROM", ""),
    }


def load_resend_settings() -> Dict[str, str]:
    return {
        "api_key": os.getenv("RESEND_API_KEY", "").strip(),
        "from": os.getenv("RESEND_FROM", "").strip() or os.getenv("QUALIA_EMAIL_FROM", "").strip(),
    }


def send_email_message(subject: str, recipients: List[str], body: str, timeout: int = 20) -> str:
    clean_recipients = [recipient.strip() for recipient in recipients if recipient and recipient.strip()]
    if not clean_recipients:
        return "pendente_configuracao"

    resend_settings = load_resend_settings()
    if resend_settings["api_key"]:
        sender = resend_settings["from"] or "QualIA <onboarding@resend.dev>"
        payload = json.dumps(
            {
                "from": sender,
                "to": clean_recipients,
                "subject": subject,
                "text": body,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://api.resend.com/emails",
            data=payload,
            headers={
                "Authorization": f"Bearer {resend_settings['api_key']}",
                "Content-Type": "application/json",
                "User-Agent": "QualIA/1.0 (https://qualiautfprpg.com.br)",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.status >= 400:
                    raise RuntimeError(f"Erro no Resend: HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Erro no Resend: HTTP {exc.code} - {detail}") from exc
        return "enviado"

    smtp_settings = load_smtp_settings()
    smtp_host = smtp_settings["host"]
    smtp_port = smtp_settings["port"]
    smtp_user = smtp_settings["user"]
    smtp_pass = smtp_settings["pass"]
    smtp_from = smtp_settings["from"] or smtp_user or "qualia@localhost"
    if not all([smtp_host, smtp_user, smtp_pass, smtp_from]):
        return "pendente_configuracao"

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp_from
    message["To"] = ", ".join(clean_recipients)
    message.set_content(body)

    if int(smtp_port) == 465:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=timeout) as smtp:
            smtp.login(smtp_user, smtp_pass)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=timeout) as smtp:
            smtp.starttls()
            smtp.login(smtp_user, smtp_pass)
            smtp.send_message(message)
    return "enviado"


def get_schedule_slots() -> Dict[int, List[str]]:
    return {
        0: ["13:50", "14:20", "14:50", "15:20", "15:50", "16:20", "16:50", "17:20"],
        1: ["08:20", "08:50", "09:20", "09:50", "10:20", "10:50", "11:20"],
        2: ["10:20", "10:50", "11:20"],
    }


def validate_appointment_slot(date_str: str, time_str: str) -> None:
    try:
        schedule_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Data inválida para o agendamento.") from exc

    slots = get_schedule_slots()
    allowed_times = slots.get(schedule_date.weekday())
    if not allowed_times:
        raise HTTPException(status_code=400, detail="Escolha uma data de segunda, terça ou quarta no CAFIS.")
    if time_str not in allowed_times:
        raise HTTPException(status_code=400, detail="Horário inválido para o dia escolhido.")


def send_appointment_email(payload: AppointmentCreate) -> str:
    recipients = [
        "adriana@utfpr.edu.br",
        "gabriellyp@alunos.utfpr.edu.br",
        payload.email,
    ]
    body = "\n".join(
        [
            "Novo agendamento de teste físico no QualIA.",
            "",
            f"Nome: {payload.nome}",
            f"E-mail: {payload.email}",
            f"Telefone: {payload.telefone}",
            f"Data: {payload.data_agendada}",
            f"Horário: {payload.horario_agendado}",
            "Local: CAFIS - UTFPR Campus Ponta Grossa",
            f"Observações: {payload.observacoes or 'Nenhuma'}",
        ]
    )
    return send_email_message(f"Agendamento de teste físico - {payload.nome}", recipients, body, timeout=15)


def update_appointment_email_status(appointment_id: int, status: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE appointments SET email_status = ? WHERE id = ?", (status, appointment_id))
    conn.commit()
    conn.close()


def process_appointment_email(appointment_id: int, payload_data: Dict[str, Any]) -> None:
    try:
        status = send_appointment_email(AppointmentCreate(**payload_data))
    except Exception as exc:
        print(f"Falha ao enviar e-mail do agendamento {appointment_id}: {exc}", flush=True)
        status = "falha_no_envio"
    update_appointment_email_status(appointment_id, status)


def send_appointment_cancellation_email(appointment_row: sqlite3.Row) -> str:
    body = "\n".join(
        [
            f"Olá, {appointment_row['nome']}.",
            "",
            "Seu agendamento de teste físico no QualIA foi cancelado.",
            "",
            f"Data: {appointment_row['data_agendada']}",
            f"Horário: {appointment_row['horario_agendado']}",
            f"Local: {appointment_row['local']}",
            "",
            "Se precisar, faça um novo agendamento pela plataforma.",
        ]
    )
    return send_email_message(
        f"Cancelamento de agendamento - {appointment_row['nome']}",
        [appointment_row["email"]],
        body,
        timeout=15,
    )


def send_password_reset_email(user_row: Any, token: str) -> str:
    reset_url = f"{os.getenv('QUALIA_PUBLIC_URL', 'https://app.qualiautfprpg.com.br').rstrip('/')}/?reset_token={token}"
    body = "\n".join(
        [
            f"Olá, {user_row['nome']}.",
            "",
            "Recebemos uma solicitação para redefinir sua senha no QualIA.",
            "",
            "Clique no link abaixo para cadastrar uma nova senha:",
            reset_url,
            "",
            "Este link expira em 1 hora.",
            "Se você não solicitou essa alteração, ignore este e-mail.",
        ]
    )
    return send_email_message("QualIA - redefinição de senha", [user_row["email"]], body, timeout=20)


def build_dashboard_response_for_user(conn: sqlite3.Connection, user_id: int) -> DashboardResponse:
    user_row = conn.execute(
        """
        SELECT
            u.*,
            (
                SELECT e.created_at FROM evaluations e
                WHERE e.user_id = u.id
                ORDER BY e.created_at DESC
                LIMIT 1
            ) AS ultima_avaliacao_em,
            (
                SELECT json_extract(e.result_json, '$.score_ia') FROM evaluations e
                WHERE e.user_id = u.id
                ORDER BY e.created_at DESC
                LIMIT 1
            ) AS ultimo_score
        FROM users u
        WHERE u.id = ?
        """,
        (user_id,),
    ).fetchone()
    if not user_row:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")
    eval_rows = conn.execute(
        "SELECT * FROM evaluations WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    historico = [row_to_evaluation(row, user_row) for row in eval_rows]
    return DashboardResponse(
        usuario=row_to_user_summary(user_row),
        ultima_avaliacao=historico[0] if historico else None,
        historico=historico,
    )


def build_result_email_lines(record: EvaluationRecord) -> List[str]:
    payload = record.payload
    resultado = record.resultado
    lines = [
        f"Data da avaliação: {record.created_at}",
        f"Tipo da avaliação: {record.tipo_avaliacao}",
        f"Score QualIA: {resultado.score_ia:.1f}",
        f"Objetivo sugerido: {resultado.objetivo_sugerido}",
        f"IMC: {resultado.imc:.1f} ({resultado.imc_class})",
        f"Gordura corporal: {payload.get('bf') if payload.get('bf') is not None else '-'} ({resultado.bf_class})",
        f"VO2 estimado: {resultado.vo2_est:.1f}" if resultado.vo2_est is not None else "VO2 estimado: -",
        f"Classificação do VO2: {resultado.vo2_class}",
        f"Pressão arterial: {resultado.pressao_class}",
        "",
        "Pontos fortes:",
    ]
    lines.extend([f"- {item}" for item in resultado.pontos_fortes] or ["- Nenhum ponto forte registrado."])
    lines.extend(["", "Pontos de atenção:"])
    lines.extend([f"- {item}" for item in resultado.pontos_fracos] or ["- Nenhum ponto de atenção registrado."])
    lines.extend(["", "Recomendações:"])
    lines.extend([f"- {item}" for item in resultado.recomendacoes] or ["- Nenhuma recomendação registrada."])
    if resultado.nutrition_tips:
        lines.extend(["", "Recomendações de alimentação:"])
        lines.extend([f"- {item}" for item in resultado.nutrition_tips])
    if resultado.weekly_plan:
        lines.extend(["", "Plano semanal sugerido:"])
        lines.extend([f"- {item}" for item in resultado.weekly_plan])
    lines.append("")
    return lines


def send_results_email(dashboard: DashboardResponse, recipient_email: str, scope: str) -> str:
    records = dashboard.historico if scope == "all" else ([dashboard.ultima_avaliacao] if dashboard.ultima_avaliacao else [])
    if not records:
        raise HTTPException(status_code=400, detail="Este usuário ainda não possui avaliações para enviar.")

    subject = (
        f"QualIA - Histórico de resultados de {dashboard.usuario.nome}"
        if scope == "all"
        else f"QualIA - Resultado mais recente de {dashboard.usuario.nome}"
    )
    body_lines = [
        f"Olá, {dashboard.usuario.nome}.",
        "",
        "Segue abaixo o envio dos seus resultados do QualIA.",
        "",
        f"E-mail do cadastro: {dashboard.usuario.email}",
        f"Quantidade de avaliações enviadas: {len(records)}",
        "",
    ]
    for index, record in enumerate(records, start=1):
        body_lines.append(f"Resultado {index}")
        body_lines.append("-" * 24)
        body_lines.extend(build_result_email_lines(record))
    return send_email_message(subject, [recipient_email], "\n".join(body_lines), timeout=20)


def classificar_pressao(sist: Optional[float], diast: Optional[float]) -> str:
    if sist is None or diast is None:
        return "Não informada"
    if sist < 120 and diast < 80:
        return "Normal"
    if sist > 180 or diast > 110:
        return "Crise hipertensiva"
    if 120 <= sist <= 139 or 80 <= diast <= 89:
        return "Pré-hipertensão"
    if 140 <= sist <= 159 or 90 <= diast <= 99:
        return "Pressão arterial elevada (Hipertensão estágio 1)"
    if sist >= 160 or diast >= 100:
        return "Pressão arterial elevada (Hipertensão estágio 2)"
    return "Pré-hipertensão"


def gerar_pontos(imc_class: str, bf_class: str, vo2_class: str, pressao_class: str, score: float) -> Tuple[List[str], List[str]]:
    fortes: List[str] = []
    fracos: List[str] = []
    if imc_class == "Peso normal":
        fortes.append("Peso dentro da faixa esperada para a altura.")
    else:
        fracos.append("IMC fora da faixa de normalidade, pedindo atenção ao plano físico e alimentar.")
    if bf_class in {"Muito Bom", "Saudável"}:
        fortes.append("Percentual de gordura em faixa positiva para a faixa etária.")
    elif bf_class not in {"Não informado", "Magro", "Muito Magro"}:
        fracos.append("Percentual de gordura acima do ideal para sexo e idade.")
    elif bf_class == "Muito Magro":
        fracos.append("Percentual de gordura muito baixo, pedindo atenção à composição corporal.")
    if vo2_class in {"Bom", "Muito bom"}:
        fortes.append("Capacidade aeróbica favorável.")
    elif vo2_class != "Não informado":
        fracos.append("Capacidade aeróbica abaixo do desejado para saúde e desempenho.")
    if pressao_class == "Normal":
        fortes.append("Pressão arterial dentro da normalidade.")
    elif pressao_class != "Não informada":
        fracos.append("Pressão arterial pede acompanhamento mais próximo.")
    if score >= 80:
        fortes.append("Score global muito bom.")
    elif score < 50:
        fracos.append("Score global baixo, sugerindo intervenção estruturada.")
    if not fortes:
        fortes.append("Há espaço claro para evolução e o acompanhamento tende a mostrar ganho rápido no começo.")
    if not fracos:
        fracos.append("Nenhum ponto crítico evidente no momento.")
    return fortes, fracos


def gerar_recomendacoes_alimentares(
    imc_class: str,
    bf_class: str,
    pressao_class: str,
    score: float,
) -> List[str]:
    dicas: List[str] = [
        "Monte refeições com proteína magra, feijão, legumes e frutas ao longo do dia.",
        "Evite longos períodos sem comer e priorize alimentos menos ultraprocessados.",
    ]
    if imc_class in {"Excesso de peso", "Obesidade", "Super obesidade"}:
        dicas.append("Reduza bebidas açucaradas, frituras frequentes e porções muito grandes no jantar.")
    if bf_class in {"Sobrepeso", "Gordo", "Muito Gordo"}:
        dicas.append("Busque constância: mais proteína, verduras e controle de doces ao longo da semana.")
    if pressao_class not in {"Normal", "Não informada"}:
        dicas.append("Diminua o excesso de sal, embutidos e alimentos muito industrializados.")
    if score < 60:
        dicas.append("Organize uma rotina simples de café da manhã, almoço e jantar para ganhar regularidade.")
    return dicas[:5]


def gerar_plano_semanal(
    score: float,
    vo2_est: Optional[float],
    imc_class: str,
    pressao_class: str,
) -> List[str]:
    plano = [
        "Fazer 3 caminhadas de 20 a 30 minutos em ritmo confortável.",
        "Separar 2 dias para alongamento e mobilidade por 10 minutos em casa.",
        "Realizar 2 sessões curtas com agachamento na cadeira, apoio na parede e abdominal leve.",
        "Dormir em horário mais regular por pelo menos 5 noites nesta semana.",
        "Reservar 1 momento para organizar refeições e garrafa de água do dia seguinte.",
    ]
    if vo2_est is not None and vo2_est < 30:
        plano[0] = "Fazer 4 caminhadas leves de 20 minutos para melhorar a resistência cardiorrespiratória."
    if imc_class in {"Obesidade", "Super obesidade"}:
        plano[2] = "Realizar 2 sessões leves em casa com movimentos sentados, apoio na parede e pausas maiores."
    if pressao_class not in {"Normal", "Não informada"}:
        plano[3] = "Incluir 5 a 10 minutos de respiração, relaxamento ou caminhada leve em dias mais tensos."
    if score >= 80:
        plano[1] = "Manter 2 sessões semanais de mobilidade e fortalecer a rotina que já está funcionando."
    return plano


def calcular_meta_agua(peso: float) -> float:
    return round(max(1.8, peso * 0.035), 2)


def fetch_wellness_news(limit: int = 6) -> List[WellnessNewsItem]:
    query = urllib.parse.quote(
        '("Ponta Grossa" OR "Campos Gerais" OR "Paraná") (saúde OR "qualidade de vida" OR bem-estar OR "atividade física")'
    )
    url = f"https://news.google.com/rss/search?q={query}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            xml_data = response.read()
    except Exception:
        return []

    root = ET.fromstring(xml_data)
    news: List[WellnessNewsItem] = []
    for item in root.findall(".//item")[:limit]:
        news.append(
            WellnessNewsItem(
                title=item.findtext("title") or "Notícia",
                link=item.findtext("link") or "#",
                source=item.findtext("source") or "Google News",
                published_at=item.findtext("pubDate") or "",
            )
        )
    return news


def evaluate_user(user_row: sqlite3.Row, payload: EvaluationInput) -> EvaluationResult:
    altura_m = float(user_row["altura_cm"]) / 100.0
    idade = float(user_row["idade"] or 0)
    imc = float(payload.peso / (altura_m ** 2))
    vo2_est = payload.vo2
    if vo2_est is None and payload.cooper:
        dist_m = payload.cooper * 1000.0
        vo2_est = (dist_m - 504.9) / 44.73

    features = {
        "Idade": float(user_row["idade"] or 0),
        "Genero": normalize_sex(user_row["sexo"]),
        "Altura_m": altura_m,
        "Peso_kg": payload.peso,
        "Percentual_Gordura": payload.bf or 0.0,
        "Percentual_Agua": payload.agua or 0.0,
        "Percentual_Massa_Muscular": payload.massa_muscular or 0.0,
        "BMR_kcal": payload.bmr or 0.0,
        "Idade_Metabolica": payload.idade_metabolica or 0.0,
        "Massa_Ossea_kg": payload.massa_ossea or 0.0,
        "VO2max_mlkgmin": vo2_est or 0.0,
        "Pressao_Sistolica": payload.pressao_sist or 0.0,
        "Pressao_Diastolica": payload.pressao_diast or 0.0,
        "Flexibilidade_cm": payload.flexibilidade or 0.0,
        "Abdominal_rep": payload.abd or 0.0,
        "Flexao_Braco_rep": payload.flexao or 0.0,
        "FC_Repouso": payload.fc_rep or 0.0,
        "FC_Pos_Exercicio": payload.fc_pos or 0.0,
        "FC_Recuperacao_5min": payload.fc_rec_5 or 0.0,
        "Cooper_km": payload.cooper or 0.0,
        "IMC": imc,
    }
    import pandas as pd

    model_pipeline, model_feature_cols = ensure_model_loaded()
    X = pd.DataFrame([{col: features.get(col, 0.0) for col in model_feature_cols}])
    score = float(model_pipeline.predict(X)[0])
    imc_class = classificar_imc(imc)
    bf_class = classificar_bf(payload.bf, user_row["sexo"] or "m", idade)
    vo2_class = classificar_vo2(vo2_est, user_row["sexo"] or "m")
    pressao_class = classificar_pressao(payload.pressao_sist, payload.pressao_diast)
    fortes, fracos = gerar_pontos(imc_class, bf_class, vo2_class, pressao_class, score)
    recomendacoes = gerar_recomendacoes(imc, payload.bf, vo2_est, score)
    nutrition_tips = gerar_recomendacoes_alimentares(imc_class, bf_class, pressao_class, score)
    weekly_plan = gerar_plano_semanal(score, vo2_est, imc_class, pressao_class)
    daily_water_liters = calcular_meta_agua(payload.peso)
    objetivo = "Melhora global do condicionamento físico"
    if score >= 80:
        objetivo = "Manutenção da boa condição atual"
    elif imc >= 30:
        objetivo = "Redução de peso com melhora cardiorrespiratória"
    elif vo2_est is not None and vo2_est < 30:
        objetivo = "Ganhar resistência aeróbica"
    return EvaluationResult(
        score_ia=round(score, 2),
        imc=round(imc, 2),
        imc_class=imc_class,
        bf_class=bf_class,
        vo2_est=round(vo2_est, 2) if vo2_est is not None else None,
        vo2_class=vo2_class,
        pressao_class=pressao_class,
        objetivo_sugerido=objetivo,
        pontos_fortes=fortes,
        pontos_fracos=fracos,
        recomendacoes=recomendacoes,
        nutrition_tips=nutrition_tips,
        weekly_plan=weekly_plan,
        daily_water_liters=daily_water_liters,
    )


def row_to_user_summary(row: sqlite3.Row) -> UserSummary:
    return UserSummary(
        id=row["id"],
        nome=row["nome"],
        email=row["email"],
        idade=row["idade"],
        sexo=row["sexo"],
        altura_cm=row["altura_cm"],
        role=row["role"],
        observacoes=row["observacoes"],
        created_at=row["created_at"],
        ultima_avaliacao_em=row["ultima_avaliacao_em"] if "ultima_avaliacao_em" in row.keys() else None,
        ultimo_score=row["ultimo_score"] if "ultimo_score" in row.keys() and row["ultimo_score"] is not None else None,
    )


def create_session(conn: sqlite3.Connection, user_row: sqlite3.Row) -> AuthResponse:
    token = secrets.token_urlsafe(32)
    now = utc_now()
    expires = now + timedelta(hours=SESSION_HOURS)
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, user_row["id"], now.isoformat(), expires.isoformat()),
    )
    conn.commit()
    return AuthResponse(
        token=token,
        role=user_row["role"],
        user_id=user_row["id"],
        name=user_row["nome"],
        email=user_row["email"],
    )


def get_current_user(authorization: Optional[str] = Header(default=None)) -> SessionUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token nao informado.")
    token = authorization.replace("Bearer ", "", 1).strip()
    conn = get_conn()
    row = conn.execute(
        """
        SELECT s.token, s.expires_at, u.id, u.role, u.email, u.nome
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token = ?
        """,
        (token,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="Sessao invalida.")
    if datetime.fromisoformat(row["expires_at"]) < utc_now():
        raise HTTPException(status_code=401, detail="Sessao expirada.")
    return SessionUser(user_id=row["id"], role=row["role"], email=row["email"], nome=row["nome"])


def require_admin(current: SessionUser = Depends(get_current_user)) -> SessionUser:
    if current.role != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito ao admin.")
    return current


def get_user_row(conn: sqlite3.Connection, *, user_id: Optional[int] = None, email: Optional[str] = None) -> sqlite3.Row:
    if user_id is not None:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    elif email is not None:
        row = conn.execute("SELECT * FROM users WHERE lower(email) = lower(?)", (email,)).fetchone()
    else:
        row = None
    if not row:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")
    return row


def parse_roster_lines(text: str) -> List[Tuple[str, Optional[str]]]:
    items: List[Tuple[str, Optional[str]]] = []
    seen: set[str] = set()
    ignored_fragments = [
        "lista de alunos",
        "ministério da educação",
        "universidade tecnológica",
        "campus ponta grossa",
        "disciplina:",
        "modelo disc",
        "observações da turma",
        "professor",
        "total de aulas",
        "limite previsto",
        "tipo de turma",
        "turma aberta",
        "data:",
        "ensino superior",
        "avaliação por",
        "nota / conceito",
        "nível:",
        "quantidade total de alunos",
        "ativozh",
        "optb",
    ]
    for raw_line in text.splitlines():
        line = " ".join(raw_line.strip().split())
        if not line:
            continue

        lowered = line.lower()
        if any(header in lowered for header in ["cód. aluno", "cod. aluno", "nome", "chamada"]) and not any(ch.isdigit() for ch in line):
            continue
        if line in {"Nº", "No", "N°"}:
            continue
        if any(fragment in lowered for fragment in ignored_fragments):
            continue

        cleaned_line = line.replace("_______________________", "").replace("__________________", "").strip()
        cleaned_line = cleaned_line.replace("Cancelado", "").replace("CANCELADO", "").strip()

        matricula = None
        nome = cleaned_line

        tabular_match = re.match(r"^\s*\d+\s+(\d{6,})\s+(.+?)\s*$", cleaned_line)
        if tabular_match:
            matricula = tabular_match.group(1).strip()
            nome = tabular_match.group(2).strip()
        else:
            parts = [part.strip(" -\t") for part in cleaned_line.replace(";", ",").split(",") if part.strip()]
            if len(parts) >= 2 and re.search(r"\d{6,}", parts[0]):
                matricula_match = re.search(r"\d{6,}", parts[0])
                matricula = matricula_match.group(0) if matricula_match else None
                nome = parts[1]
            elif len(parts) >= 2 and re.search(r"\d{6,}", parts[-1]):
                matricula_match = re.search(r"\d{6,}", parts[-1])
                matricula = matricula_match.group(0) if matricula_match else None
                nome = parts[0]

        nome = " ".join(nome.split()).strip(" -\t")
        if not nome or len(nome) < 3:
            continue
        if sum(ch.isalpha() for ch in nome) < 3:
            continue
        if ":" in nome:
            continue
        if "parana" in nome.lower() and "federal" in nome.lower():
            continue
        if matricula is None:
            words = [word for word in re.split(r"\s+", nome) if word]
            alpha_words = [word for word in words if sum(ch.isalpha() for ch in word) >= 2]
            has_many_digits = sum(ch.isdigit() for ch in nome) >= 2
            if len(alpha_words) < 2 or has_many_digits:
                continue

        key = f"{nome.lower()}|{matricula or ''}"
        if key in seen:
            continue
        seen.add(key)
        items.append((nome, matricula))
    return items


def get_ocr_reader():
    global OCR_READER
    if OCR_READER is None:
        import easyocr

        OCR_READER = easyocr.Reader(["pt", "en"], gpu=False, verbose=False)
    return OCR_READER


def extract_roster_from_pdf_bytes(file_bytes: bytes) -> List[Tuple[str, Optional[str]]]:
    import fitz

    with tempfile.TemporaryDirectory() as tmp_dir:
        pdf_path = Path(tmp_dir) / "turma.pdf"
        pdf_path.write_bytes(file_bytes)
        document = fitz.open(pdf_path)
        reader = get_ocr_reader()
        raw_lines: List[str] = []
        for page in document:
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            image_path = Path(tmp_dir) / f"page_{page.number + 1}.png"
            pix.save(image_path)
            for text in reader.readtext(str(image_path), detail=0):
                cleaned = " ".join(str(text).split())
                if cleaned:
                    raw_lines.append(cleaned)
        document.close()

    candidates = []
    for line in raw_lines:
        has_letters = sum(ch.isalpha() for ch in line) >= 4
        enough_words = len(line.split()) >= 2
        if has_letters and enough_words:
            candidates.append(line)

    return parse_roster_lines("\n".join(candidates))


def row_to_discipline_summary(conn: sqlite3.Connection, row: sqlite3.Row) -> DisciplineSummary:
    total_alunos = conn.execute(
        "SELECT COUNT(*) FROM roster_students WHERE disciplina_id = ?",
        (row["id"],),
    ).fetchone()[0]
    return DisciplineSummary(
        id=row["id"],
        professor_nome=row["professor_nome"],
        ano=row["ano"],
        nome=row["nome"],
        codigo=row["codigo"],
        horario=row["horario"],
        turma_nome=row["turma_nome"],
        arquivo_referencia=row["arquivo_referencia"],
        total_alunos=total_alunos,
        created_at=row["created_at"],
    )


def row_to_roster_student(conn: sqlite3.Connection, row: sqlite3.Row) -> RosterStudentItem:
    linked_email = None
    if row["linked_user_id"]:
        linked = conn.execute("SELECT email FROM users WHERE id = ?", (row["linked_user_id"],)).fetchone()
        linked_email = linked["email"] if linked else None
    total_testes = 0
    if row["linked_user_id"]:
        total_testes = conn.execute(
            "SELECT COUNT(*) FROM evaluations WHERE user_id = ?",
            (row["linked_user_id"],),
        ).fetchone()[0]
    return RosterStudentItem(
        id=row["id"],
        disciplina_id=row["disciplina_id"],
        nome=row["nome"],
        matricula=row["matricula"],
        linked_user_id=row["linked_user_id"],
        linked_user_email=linked_email,
        total_testes=total_testes,
        created_at=row["created_at"],
    )


def save_roster_students(
    conn: sqlite3.Connection,
    discipline_id: int,
    parsed_students: List[Tuple[str, Optional[str]]],
) -> Tuple[List[RosterStudentItem], int, int]:
    importados = 0
    ignorados = 0
    for nome, matricula in parsed_students:
        exists = conn.execute(
            """
            SELECT id FROM roster_students
            WHERE disciplina_id = ? AND lower(nome) = lower(?) AND coalesce(matricula, '') = coalesce(?, '')
            """,
            (discipline_id, nome, matricula),
        ).fetchone()
        if exists:
            ignorados += 1
            continue
        conn.execute(
            """
            INSERT INTO roster_students (disciplina_id, nome, matricula, linked_user_id, created_at)
            VALUES (?, ?, ?, NULL, ?)
            """,
            (discipline_id, nome, matricula, utc_now().isoformat()),
        )
        importados += 1

    rows = conn.execute(
        "SELECT * FROM roster_students WHERE disciplina_id = ? ORDER BY nome ASC",
        (discipline_id,),
    ).fetchall()
    alunos = [row_to_roster_student(conn, row) for row in rows]
    return alunos, importados, ignorados


def ensure_user_for_evaluation(conn: sqlite3.Connection, payload: EvaluationInput) -> sqlite3.Row:
    if payload.user_id is not None or payload.email:
        user_row = get_user_row(conn, user_id=payload.user_id, email=payload.email)
    elif payload.roster_student_id is not None:
        roster = conn.execute(
            "SELECT * FROM roster_students WHERE id = ?",
            (payload.roster_student_id,),
        ).fetchone()
        if not roster:
            raise HTTPException(status_code=404, detail="Aluno da turma nao encontrado.")
        if roster["linked_user_id"]:
            user_row = get_user_row(conn, user_id=roster["linked_user_id"])
        else:
            if not all([payload.idade, payload.sexo, payload.altura_cm]):
                raise HTTPException(
                    status_code=400,
                    detail="Para o primeiro teste do aluno da turma, informe idade, sexo e altura.",
                )
            generated_email = f"aluno{roster['id']}@qualia.local"
            conn.execute(
                """
                INSERT OR IGNORE INTO users (
                    nome, email, password_hash, role, idade, sexo, altura_cm, cpf, observacoes, created_at
                )
                VALUES (?, ?, ?, 'user', ?, ?, ?, ?, ?, ?)
                """,
                (
                    roster["nome"],
                    generated_email,
                    password_hash("1234"),
                    payload.idade,
                    normalize_sex(payload.sexo).upper(),
                    payload.altura_cm,
                    "",
                    "Usuário criado a partir da turma/importação.",
                    utc_now().isoformat(),
                ),
            )
            user_row = conn.execute("SELECT * FROM users WHERE email = ?", (generated_email,)).fetchone()
            conn.execute(
                "UPDATE roster_students SET linked_user_id = ? WHERE id = ?",
                (user_row["id"], roster["id"]),
            )
            conn.commit()
    else:
        raise HTTPException(status_code=400, detail="Informe um usuário, e-mail ou aluno da turma.")

    updates: Dict[str, Any] = {}
    if payload.nome and payload.nome.strip() and payload.nome.strip() != (user_row["nome"] or ""):
        updates["nome"] = payload.nome.strip()
    if payload.idade and not user_row["idade"]:
        updates["idade"] = payload.idade
    if payload.sexo and not user_row["sexo"]:
        updates["sexo"] = normalize_sex(payload.sexo).upper()
    if payload.altura_cm and not user_row["altura_cm"]:
        updates["altura_cm"] = payload.altura_cm

    if updates:
        set_clause = ", ".join(f"{column} = ?" for column in updates.keys())
        conn.execute(
            f"UPDATE users SET {set_clause} WHERE id = ?",
            (*updates.values(), user_row["id"]),
        )
        conn.commit()
        user_row = conn.execute("SELECT * FROM users WHERE id = ?", (user_row["id"],)).fetchone()

    return user_row


def save_evaluation(conn: sqlite3.Connection, user_id: int, payload: EvaluationInput, result: EvaluationResult) -> int:
    cur = conn.execute(
        """
        INSERT INTO evaluations (user_id, tipo_avaliacao, fonte, payload_json, result_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            payload.tipo_avaliacao,
            payload.fonte,
            json.dumps(payload.model_dump(mode="json"), ensure_ascii=False),
            json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
            utc_now().isoformat(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def row_to_evaluation(row: sqlite3.Row, user_row: Optional[sqlite3.Row] = None) -> EvaluationRecord:
    payload_dict = json.loads(row["payload_json"])
    if user_row is not None:
        try:
            result = evaluate_user(user_row, EvaluationInput(**payload_dict))
        except Exception:
            result = EvaluationResult(**json.loads(row["result_json"]))
    else:
        result = EvaluationResult(**json.loads(row["result_json"]))
    return EvaluationRecord(
        id=row["id"],
        user_id=row["user_id"],
        tipo_avaliacao=row["tipo_avaliacao"],
        fonte=row["fonte"],
        created_at=row["created_at"],
        payload=payload_dict,
        resultado=result,
    )


app = FastAPI(title="QualIA API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS or ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)
@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/config", response_model=ConfigResponse)
def config() -> ConfigResponse:
    return ConfigResponse(
        google_login_enabled=bool(GOOGLE_CLIENT_ID),
        google_client_id=GOOGLE_CLIENT_ID,
        mqtt_ingest_enabled=True,
    )


@app.get("/wellness/news", response_model=List[WellnessNewsItem])
def wellness_news() -> List[WellnessNewsItem]:
    return fetch_wellness_news()


@app.post("/appointments", response_model=AppointmentResponse)
def create_appointment(payload: AppointmentCreate, background_tasks: BackgroundTasks) -> AppointmentResponse:
    init_db()
    validate_appointment_slot(payload.data_agendada, payload.horario_agendado)
    local = "CAFIS - UTFPR Campus Ponta Grossa"
    try:
        conn = get_conn()
        existing = conn.execute(
            """
            SELECT id FROM appointments
            WHERE data_agendada = ? AND horario_agendado = ? AND status = 'agendado'
            """,
            (payload.data_agendada, payload.horario_agendado),
        ).fetchone()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao preparar o agendamento: {exc}") from exc

    if existing:
        conn.close()
        raise HTTPException(status_code=409, detail="Esse horário já foi reservado. Escolha outro.")

    email_status = "processando_envio"

    try:
        cur = conn.execute(
            """
            INSERT INTO appointments (
                nome, email, telefone, data_agendada, horario_agendado,
                local, observacoes, status, email_status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'agendado', ?, ?)
            """,
            (
                payload.nome,
                payload.email,
                payload.telefone,
                payload.data_agendada,
                payload.horario_agendado,
                local,
                payload.observacoes or "",
                email_status,
                utc_now().isoformat(),
            ),
        )
        conn.commit()
        appointment_id = int(cur.lastrowid)
    except Exception as exc:
        conn.close()
        raise HTTPException(status_code=500, detail=f"Erro ao salvar o agendamento: {exc}") from exc
    conn.close()
    background_tasks.add_task(process_appointment_email, appointment_id, payload.model_dump(mode="json"))
    return AppointmentResponse(
        id=appointment_id,
        nome=payload.nome,
        email=payload.email,
        telefone=payload.telefone,
        data_agendada=payload.data_agendada,
        horario_agendado=payload.horario_agendado,
        local=local,
        status="agendado",
        email_status=email_status,
    )


@app.get("/")
def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/styles.css")
def styles_root_file() -> FileResponse:
    return FileResponse(STATIC_DIR / "styles.css")


@app.get("/script.js")
def script_root_file() -> FileResponse:
    return FileResponse(STATIC_DIR / "script.js")


@app.get("/config.js")
def config_root_file() -> FileResponse:
    config_path = STATIC_DIR / "config.js"
    if config_path.exists():
        return FileResponse(config_path)
    return FileResponse(BASE_DIR / "frontend" / "config.js")


@app.get("/logo.png")
def logo_root_file() -> FileResponse:
    return FileResponse(BASE_DIR / "logo.png")


@app.get("/favicon.ico")
def favicon_file() -> FileResponse:
    return FileResponse(BASE_DIR / "logo.png")


@app.get("/static/styles.css")
def styles_file() -> FileResponse:
    return FileResponse(STATIC_DIR / "styles.css")


@app.get("/static/script.js")
def script_file() -> FileResponse:
    return FileResponse(STATIC_DIR / "script.js")


@app.get("/static/logo.png")
def logo_static_file() -> FileResponse:
    return FileResponse(BASE_DIR / "logo.png")


@app.post("/auth/login", response_model=AuthResponse)
def login(payload: LoginInput) -> AuthResponse:
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE lower(email) = lower(?)", (payload.email,)).fetchone()
    if not row or not verify_password(payload.password, row["password_hash"]):
        conn.close()
        raise HTTPException(status_code=401, detail="Email ou senha invalidos.")
    response = create_session(conn, row)
    conn.close()
    return response


@app.post("/auth/logout")
def logout(current: SessionUser = Depends(get_current_user), authorization: Optional[str] = Header(default=None)) -> Dict[str, str]:
    token = authorization.replace("Bearer ", "", 1).strip()
    conn = get_conn()
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.post("/auth/forgot-password", response_model=MessageResponse)
def forgot_password(payload: ForgotPasswordInput) -> MessageResponse:
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE lower(email) = lower(?)", (payload.email,)).fetchone()
    if row:
        token = secrets.token_urlsafe(40)
        now = utc_now()
        expires = now + timedelta(hours=1)
        conn.execute("DELETE FROM password_resets WHERE user_id = ? OR expires_at < ?", (row["id"], now.isoformat()))
        conn.execute(
            "INSERT INTO password_resets (token, user_id, created_at, expires_at, used_at) VALUES (?, ?, ?, ?, NULL)",
            (token, row["id"], now.isoformat(), expires.isoformat()),
        )
        conn.commit()
        try:
            send_password_reset_email(row, token)
        except Exception as exc:
            conn.close()
            raise HTTPException(status_code=500, detail=f"Não foi possível enviar o e-mail de recuperação: {exc}") from exc
    conn.close()
    return MessageResponse(
        status="ok",
        message="Se o e-mail estiver cadastrado, enviaremos um link para redefinir a senha.",
    )


@app.post("/auth/reset-password", response_model=MessageResponse)
def reset_password(payload: ResetPasswordInput) -> MessageResponse:
    conn = get_conn()
    reset = conn.execute(
        """
        SELECT pr.*, u.email, u.nome
        FROM password_resets pr
        JOIN users u ON u.id = pr.user_id
        WHERE pr.token = ?
        """,
        (payload.token,),
    ).fetchone()
    if not reset:
        conn.close()
        raise HTTPException(status_code=400, detail="Link de recuperação inválido.")
    if reset["used_at"]:
        conn.close()
        raise HTTPException(status_code=400, detail="Este link de recuperação já foi utilizado.")
    if datetime.fromisoformat(reset["expires_at"]) < utc_now():
        conn.close()
        raise HTTPException(status_code=400, detail="Este link de recuperação expirou.")

    now = utc_now().isoformat()
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash(payload.password), reset["user_id"]))
    conn.execute("UPDATE password_resets SET used_at = ? WHERE token = ?", (now, payload.token))
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (reset["user_id"],))
    conn.commit()
    conn.close()
    return MessageResponse(status="ok", message="Senha alterada com sucesso. Entre novamente com a nova senha.")


@app.get("/admin/users", response_model=List[UserSummary])
def list_users(_: SessionUser = Depends(require_admin)) -> List[UserSummary]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            u.*,
            (
                SELECT e.created_at
                FROM evaluations e
                WHERE e.user_id = u.id
                ORDER BY e.created_at DESC
                LIMIT 1
            ) AS ultima_avaliacao_em,
            (
                SELECT json_extract(e.result_json, '$.score_ia')
                FROM evaluations e
                WHERE e.user_id = u.id
                ORDER BY e.created_at DESC
                LIMIT 1
            ) AS ultimo_score
        FROM users u
        ORDER BY u.role DESC, u.created_at DESC
        """
    ).fetchall()
    conn.close()
    return [row_to_user_summary(row) for row in rows]


@app.get("/admin/overview", response_model=AdminOverview)
def admin_overview(_: SessionUser = Depends(require_admin)) -> AdminOverview:
    conn = get_conn()
    total_users = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'user'").fetchone()[0]
    total_evaluations = conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]
    conn.close()
    return AdminOverview(total_users=total_users, total_evaluations=total_evaluations)


@app.post("/admin/email/test", response_model=EmailTestResponse)
def admin_test_email(payload: EmailTestInput, _: SessionUser = Depends(require_admin)) -> EmailTestResponse:
    try:
        status = send_email_message(
            "QualIA - teste de envio",
            [payload.recipient_email],
            "\n".join(
                [
                    "Este é um teste de envio do QualIA.",
                    "",
                    "Se você recebeu esta mensagem, a integração com o serviço de e-mail está funcionando.",
                ]
            ),
            timeout=20,
        )
    except Exception as exc:
        return EmailTestResponse(
            status="falha_no_envio",
            message=f"Houve falha no envio do teste. Detalhe: {exc}",
            error_detail=str(exc),
        )
    if status == "enviado":
        return EmailTestResponse(status=status, message="E-mail de teste enviado com sucesso.")
    return EmailTestResponse(
        status=status,
        message="O serviço de e-mail ainda precisa estar configurado para enviar mensagens.",
    )


@app.get("/admin/appointments", response_model=List[AdminAppointmentItem])
def admin_appointments(_: SessionUser = Depends(require_admin)) -> List[AdminAppointmentItem]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, nome, email, telefone, data_agendada, horario_agendado, local, status, email_status, created_at
        FROM appointments
        ORDER BY data_agendada ASC, horario_agendado ASC, created_at DESC
        """
    ).fetchall()
    conn.close()
    return [
        AdminAppointmentItem(
            id=row["id"],
            nome=row["nome"],
            email=row["email"],
            telefone=row["telefone"],
            data_agendada=row["data_agendada"],
            horario_agendado=row["horario_agendado"],
            local=row["local"],
            status=row["status"],
            email_status=row["email_status"],
            created_at=row["created_at"],
        )
        for row in rows
    ]


@app.delete("/admin/appointments/{appointment_id}", response_model=AppointmentCancelResponse)
def admin_cancel_appointment(
    appointment_id: int,
    _: SessionUser = Depends(require_admin),
) -> AppointmentCancelResponse:
    conn = get_conn()
    row = conn.execute("SELECT * FROM appointments WHERE id = ?", (appointment_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Agendamento não encontrado.")
    try:
        email_status = send_appointment_cancellation_email(row)
    except Exception:
        email_status = "falha_no_envio"
    conn.execute("DELETE FROM appointments WHERE id = ?", (appointment_id,))
    conn.commit()
    conn.close()
    message = "Agendamento desmarcado e removido da agenda."
    if email_status == "enviado":
        message = "Agendamento desmarcado, removido da agenda e e-mail enviado."
    elif email_status == "pendente_configuracao":
        message = "Agendamento removido da agenda. O aviso por e-mail depende da configuração de envio."
    elif email_status == "falha_no_envio":
        message = "Agendamento removido da agenda, mas houve falha ao enviar o e-mail."
    return AppointmentCancelResponse(status="cancelado", email_status=email_status, message=message)


@app.get("/admin/teachers", response_model=List[TeacherOption])
def admin_teachers(_: SessionUser = Depends(require_admin)) -> List[TeacherOption]:
    return [TeacherOption(nome=teacher) for teacher in TEACHERS]


@app.get("/admin/disciplines", response_model=List[DisciplineSummary])
def list_disciplines(_: SessionUser = Depends(require_admin)) -> List[DisciplineSummary]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM disciplines ORDER BY ano DESC, professor_nome ASC, turma_nome ASC, nome ASC"
    ).fetchall()
    result = [row_to_discipline_summary(conn, row) for row in rows]
    conn.close()
    return result


@app.post("/admin/disciplines", response_model=DisciplineSummary)
def create_discipline(payload: DisciplineCreate, _: SessionUser = Depends(require_admin)) -> DisciplineSummary:
    if payload.professor_nome not in TEACHERS:
        raise HTTPException(status_code=400, detail="Professor fora da lista permitida.")
    conn = get_conn()
    cur = conn.execute(
        """
        INSERT INTO disciplines (professor_nome, ano, nome, codigo, horario, turma_nome, arquivo_referencia, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.professor_nome,
            payload.ano,
            payload.nome,
            payload.codigo.upper(),
            payload.horario,
            payload.turma_nome,
            payload.arquivo_referencia or "",
            utc_now().isoformat(),
        ),
    )
    discipline_id = int(cur.lastrowid)
    conn.commit()
    row = conn.execute("SELECT * FROM disciplines WHERE id = ?", (discipline_id,)).fetchone()
    result = row_to_discipline_summary(conn, row)
    conn.close()
    return result


@app.put("/admin/disciplines/{discipline_id}", response_model=DisciplineSummary)
def update_discipline(
    discipline_id: int,
    payload: DisciplineUpdate,
    _: SessionUser = Depends(require_admin),
) -> DisciplineSummary:
    if payload.professor_nome not in TEACHERS:
        raise HTTPException(status_code=400, detail="Professor fora da lista permitida.")
    conn = get_conn()
    row = conn.execute("SELECT * FROM disciplines WHERE id = ?", (discipline_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Disciplina/turma nao encontrada.")
    conn.execute(
        """
        UPDATE disciplines
        SET professor_nome = ?, ano = ?, nome = ?, codigo = ?, horario = ?, turma_nome = ?, arquivo_referencia = ?
        WHERE id = ?
        """,
        (
            payload.professor_nome,
            payload.ano,
            payload.nome,
            payload.codigo.upper(),
            payload.horario,
            payload.turma_nome,
            payload.arquivo_referencia or "",
            discipline_id,
        ),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM disciplines WHERE id = ?", (discipline_id,)).fetchone()
    result = row_to_discipline_summary(conn, updated)
    conn.close()
    return result


@app.delete("/admin/disciplines/{discipline_id}")
def delete_discipline(discipline_id: int, _: SessionUser = Depends(require_admin)) -> Dict[str, str]:
    conn = get_conn()
    row = conn.execute("SELECT id FROM disciplines WHERE id = ?", (discipline_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Disciplina/turma nao encontrada.")
    conn.execute("DELETE FROM roster_students WHERE disciplina_id = ?", (discipline_id,))
    conn.execute("DELETE FROM disciplines WHERE id = ?", (discipline_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.get("/admin/disciplines/{discipline_id}/students", response_model=List[RosterStudentItem])
def list_discipline_students(discipline_id: int, _: SessionUser = Depends(require_admin)) -> List[RosterStudentItem]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM roster_students WHERE disciplina_id = ? ORDER BY nome ASC",
        (discipline_id,),
    ).fetchall()
    result = [row_to_roster_student(conn, row) for row in rows]
    conn.close()
    return result


@app.post("/admin/disciplines/{discipline_id}/students/import", response_model=RosterImportResponse)
def import_discipline_students(
    discipline_id: int,
    payload: RosterImportInput,
    _: SessionUser = Depends(require_admin),
) -> RosterImportResponse:
    conn = get_conn()
    discipline = conn.execute("SELECT * FROM disciplines WHERE id = ?", (discipline_id,)).fetchone()
    if not discipline:
        conn.close()
        raise HTTPException(status_code=404, detail="Disciplina/turma nao encontrada.")

    parsed_students = parse_roster_lines(payload.nomes_texto)
    if not parsed_students:
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="Cole a lista da turma no campo de texto ou selecione um PDF para importar os alunos.",
        )

    if payload.arquivo_referencia:
        conn.execute(
            "UPDATE disciplines SET arquivo_referencia = ? WHERE id = ?",
            (payload.arquivo_referencia, discipline_id),
        )

    alunos, importados, ignorados = save_roster_students(conn, discipline_id, parsed_students)
    conn.commit()
    result = RosterImportResponse(
        alunos=alunos,
        importados=importados,
        ignorados=ignorados,
        arquivo_referencia=payload.arquivo_referencia or discipline["arquivo_referencia"],
        mensagem=f"Turma importada: {importados} aluno(s) novo(s) e {ignorados} já existente(s).",
    )
    conn.close()
    return result


@app.post("/admin/disciplines/{discipline_id}/students/import-pdf", response_model=RosterImportResponse)
async def import_discipline_students_from_pdf(
    discipline_id: int,
    arquivo: UploadFile = File(...),
    _: SessionUser = Depends(require_admin),
) -> RosterImportResponse:
    conn = get_conn()
    discipline = conn.execute("SELECT * FROM disciplines WHERE id = ?", (discipline_id,)).fetchone()
    if not discipline:
        conn.close()
        raise HTTPException(status_code=404, detail="Disciplina/turma nao encontrada.")

    file_bytes = await arquivo.read()
    if not file_bytes:
        conn.close()
        raise HTTPException(status_code=400, detail="Arquivo vazio ou invalido.")

    saved_name = f"{utc_now().strftime('%Y%m%d%H%M%S')}_{arquivo.filename}"
    saved_path = UPLOADS_DIR / saved_name
    saved_path.write_bytes(file_bytes)

    parsed_students = extract_roster_from_pdf_bytes(file_bytes)
    if not parsed_students:
        conn.execute(
            "UPDATE disciplines SET arquivo_referencia = ? WHERE id = ?",
            (arquivo.filename, discipline_id),
        )
        conn.commit()
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="Nao foi possivel extrair os alunos automaticamente desse PDF. Tente outro arquivo ou use a importacao em bloco.",
        )

    conn.execute(
        "UPDATE disciplines SET arquivo_referencia = ? WHERE id = ?",
        (arquivo.filename, discipline_id),
    )

    alunos, importados, ignorados = save_roster_students(conn, discipline_id, parsed_students)
    conn.commit()
    result = RosterImportResponse(
        alunos=alunos,
        importados=importados,
        ignorados=ignorados,
        arquivo_referencia=arquivo.filename,
        mensagem=f"PDF processado: {importados} aluno(s) novo(s) e {ignorados} já existente(s).",
    )
    conn.close()
    return result


@app.get("/admin/evaluations/search", response_model=List[EvaluationSearchItem])
def search_evaluations(q: str, _: SessionUser = Depends(require_admin)) -> List[EvaluationSearchItem]:
    query = (q or "").strip()
    if len(query) < 2:
        return []
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT e.id AS evaluation_id, e.user_id, u.nome, u.email, e.tipo_avaliacao, e.created_at,
               json_extract(e.result_json, '$.score_ia') AS score_ia
        FROM evaluations e
        JOIN users u ON u.id = e.user_id
        WHERE lower(u.nome) LIKE lower(?) OR lower(u.email) LIKE lower(?)
        ORDER BY e.created_at DESC
        LIMIT 50
        """,
        (f"%{query}%", f"%{query}%"),
    ).fetchall()
    conn.close()
    return [
        EvaluationSearchItem(
            evaluation_id=row["evaluation_id"],
            user_id=row["user_id"],
            nome=row["nome"],
            email=row["email"],
            tipo_avaliacao=row["tipo_avaliacao"],
            created_at=row["created_at"],
            score_ia=row["score_ia"],
        )
        for row in rows
    ]


@app.post("/admin/users", response_model=UserSummary)
def create_user(payload: AdminUserCreate, _: SessionUser = Depends(require_admin)) -> UserSummary:
    conn = get_conn()
    existing = conn.execute("SELECT id FROM users WHERE lower(email) = lower(?)", (payload.email,)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=409, detail="Ja existe usuario com esse email.")
    conn.execute(
        """
        INSERT INTO users (nome, email, password_hash, role, idade, sexo, altura_cm, cpf, observacoes, created_at)
        VALUES (?, ?, ?, 'user', ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.nome,
            payload.email,
            password_hash(default_password_from_cpf(payload.cpf)),
            payload.idade,
            normalize_sex(payload.sexo).upper(),
            payload.altura_cm,
            payload.cpf,
            payload.observacoes or "",
            utc_now().isoformat(),
        ),
    )
    row = conn.execute("SELECT * FROM users WHERE lower(email) = lower(?)", (payload.email,)).fetchone()
    conn.commit()
    conn.close()
    return row_to_user_summary(row)


@app.delete("/admin/users/{user_id}")
def delete_user(user_id: int, _: SessionUser = Depends(require_admin)) -> Dict[str, str]:
    conn = get_conn()
    row = conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")
    if row["role"] == "admin":
        conn.close()
        raise HTTPException(status_code=400, detail="Nao e permitido remover o admin principal.")
    conn.execute("DELETE FROM evaluations WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.post("/admin/evaluations", response_model=EvaluationRecord)
def create_evaluation(payload: EvaluationInput, _: SessionUser = Depends(require_admin)) -> EvaluationRecord:
    conn = get_conn()
    user_row = ensure_user_for_evaluation(conn, payload)
    result = evaluate_user(user_row, payload)
    evaluation_id = save_evaluation(conn, user_row["id"], payload, result)
    row = conn.execute("SELECT * FROM evaluations WHERE id = ?", (evaluation_id,)).fetchone()
    conn.close()
    return row_to_evaluation(row, user_row)


@app.post("/mqtt/ingest", response_model=EvaluationRecord)
def mqtt_ingest(payload: MqttIngestInput) -> EvaluationRecord:
    if payload.mqtt_key != MQTT_SHARED_KEY:
        raise HTTPException(status_code=401, detail="mqtt_key invalida.")
    conn = get_conn()
    user_row = get_user_row(conn, user_id=payload.user_id, email=payload.email)
    as_eval = EvaluationInput(**payload.model_dump(exclude={"mqtt_key"}))
    result = evaluate_user(user_row, as_eval)
    evaluation_id = save_evaluation(conn, user_row["id"], as_eval, result)
    row = conn.execute("SELECT * FROM evaluations WHERE id = ?", (evaluation_id,)).fetchone()
    conn.close()
    return row_to_evaluation(row, user_row)


@app.get("/me/dashboard", response_model=DashboardResponse)
def my_dashboard(current: SessionUser = Depends(get_current_user)) -> DashboardResponse:
    conn = get_conn()
    dashboard = build_dashboard_response_for_user(conn, current.user_id)
    conn.close()
    return dashboard


@app.get("/users/{user_id}/dashboard", response_model=DashboardResponse)
def admin_dashboard_for_user(user_id: int, _: SessionUser = Depends(require_admin)) -> DashboardResponse:
    conn = get_conn()
    dashboard = build_dashboard_response_for_user(conn, user_id)
    conn.close()
    return dashboard


@app.post("/me/results/send-email", response_model=ResultsEmailResponse)
def email_my_results(
    payload: ResultsEmailRequest,
    current: SessionUser = Depends(get_current_user),
) -> ResultsEmailResponse:
    scope = (payload.scope or "latest").lower()
    if scope not in {"latest", "all"}:
        raise HTTPException(status_code=400, detail="Escopo inválido. Use latest ou all.")
    conn = get_conn()
    dashboard = build_dashboard_response_for_user(conn, current.user_id)
    conn.close()
    recipient_email = payload.recipient_email or dashboard.usuario.email
    error_detail = None
    try:
        status = send_results_email(dashboard, recipient_email, scope)
    except Exception as exc:
        status = "falha_no_envio"
        error_detail = str(exc)
    message = (
        "Resultado mais recente enviado por e-mail."
        if scope == "latest"
        else "Histórico completo enviado por e-mail."
    )
    if status == "pendente_configuracao":
        message = "O serviço de e-mail ainda precisa estar configurado para enviar os resultados."
    elif status == "falha_no_envio":
        message = "Houve falha no envio do e-mail com os resultados."
        if error_detail:
            message = f"{message} Detalhe: {error_detail}"
    return ResultsEmailResponse(
        status=status,
        recipient_email=recipient_email,
        scope=scope,
        message=message,
        error_detail=error_detail,
    )


@app.post("/users/{user_id}/results/send-email", response_model=ResultsEmailResponse)
def email_user_results_as_admin(
    user_id: int,
    payload: ResultsEmailRequest,
    _: SessionUser = Depends(require_admin),
) -> ResultsEmailResponse:
    scope = (payload.scope or "latest").lower()
    if scope not in {"latest", "all"}:
        raise HTTPException(status_code=400, detail="Escopo inválido. Use latest ou all.")
    conn = get_conn()
    dashboard = build_dashboard_response_for_user(conn, user_id)
    conn.close()
    recipient_email = payload.recipient_email or dashboard.usuario.email
    error_detail = None
    try:
        status = send_results_email(dashboard, recipient_email, scope)
    except Exception as exc:
        status = "falha_no_envio"
        error_detail = str(exc)
    message = (
        "Resultado mais recente enviado por e-mail."
        if scope == "latest"
        else "Histórico completo enviado por e-mail."
    )
    if status == "pendente_configuracao":
        message = "O serviço de e-mail ainda precisa estar configurado para enviar os resultados."
    elif status == "falha_no_envio":
        message = "Houve falha no envio do e-mail com os resultados."
        if error_detail:
            message = f"{message} Detalhe: {error_detail}"
    return ResultsEmailResponse(
        status=status,
        recipient_email=recipient_email,
        scope=scope,
        message=message,
        error_detail=error_detail,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("qualia_api:app", host="0.0.0.0", port=8000, reload=True)
