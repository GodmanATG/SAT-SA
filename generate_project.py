import os
import json

base_dir = r"d:\SIH2026\SAT-SA"
folders = [
    "backend/app/routers",
    "backend/app/analytics",
    "backend/app/ingestion",
    "data/sample",
    "frontend"
]

for folder in folders:
    os.makedirs(os.path.join(base_dir, folder), exist_ok=True)

files = {}

files["schema.sql"] = """-- Alerts table
CREATE TABLE IF NOT EXISTS alerts (
    alert_id TEXT PRIMARY KEY,
    cse_id TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low', 'info')),
    source_system TEXT NOT NULL,
    target_asset_id TEXT,
    alert_category TEXT NOT NULL,
    acknowledged_at TIMESTAMP,
    closed_at TIMESTAMP,
    disposition TEXT CHECK (disposition IN ('true_positive', 'false_positive', 'benign', 'escalated', 'suppressed', 'pending')),
    assigned_analyst TEXT,
    FOREIGN KEY (cse_id) REFERENCES cse_profiles(cse_id)
);

-- Cases table
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    cse_id TEXT NOT NULL,
    linked_alert_ids TEXT[], -- array of alert IDs
    opened_at TIMESTAMP NOT NULL,
    closed_at TIMESTAMP,
    priority TEXT NOT NULL CHECK (priority IN ('P1', 'P2', 'P3', 'P4')),
    escalated BOOLEAN DEFAULT FALSE,
    escalated_to TEXT,
    investigation_notes TEXT,
    root_cause_identified BOOLEAN DEFAULT FALSE,
    remediation_actions TEXT,
    closure_reason TEXT,
    assigned_analyst TEXT,
    FOREIGN KEY (cse_id) REFERENCES cse_profiles(cse_id)
);

-- Assets table
CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY,
    cse_id TEXT NOT NULL,
    hostname TEXT,
    asset_type TEXT NOT NULL CHECK (asset_type IN ('server', 'workstation', 'network_device', 'database', 'web_application', 'iot_device', 'cloud_service')),
    criticality TEXT NOT NULL CHECK (criticality IN ('critical', 'high', 'medium', 'low')),
    monitored BOOLEAN DEFAULT TRUE,
    environment TEXT CHECK (environment IN ('production', 'staging', 'development', 'dr')),
    FOREIGN KEY (cse_id) REFERENCES cse_profiles(cse_id)
);

-- CSE Profiles table
CREATE TABLE IF NOT EXISTS cse_profiles (
    cse_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    sector TEXT NOT NULL,
    size_tier TEXT NOT NULL CHECK (size_tier IN ('large', 'medium', 'small')),
    soc_model TEXT CHECK (soc_model IN ('in_house', 'mssp', 'hybrid')),
    analyst_count INTEGER,
    submission_period_start DATE,
    submission_period_end DATE
);

-- Findings table (output of analytics engine)
CREATE TABLE IF NOT EXISTS findings (
    finding_id TEXT PRIMARY KEY,
    cse_id TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('execution_gap', 'negative_space', 'peer_anomaly')),
    rule_id TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    summary TEXT NOT NULL,
    evidence JSONB NOT NULL,
    confidence FLOAT,
    recommendation TEXT,
    peer_context TEXT,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (cse_id) REFERENCES cse_profiles(cse_id)
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_alerts_cse ON alerts(cse_id);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);
CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts(timestamp);
CREATE INDEX IF NOT EXISTS idx_alerts_target_asset ON alerts(target_asset_id);
CREATE INDEX IF NOT EXISTS idx_cases_cse ON cases(cse_id);
CREATE INDEX IF NOT EXISTS idx_assets_cse ON assets(cse_id);
CREATE INDEX IF NOT EXISTS idx_findings_cse ON findings(cse_id);
CREATE INDEX IF NOT EXISTS idx_findings_category ON findings(category);
"""

files["backend/app/__init__.py"] = ""
files["backend/app/routers/__init__.py"] = ""
files["backend/app/analytics/__init__.py"] = ""
files["backend/app/ingestion/__init__.py"] = ""
files["backend/app/analytics/execution_gaps.py"] = ""
files["backend/app/analytics/negative_space.py"] = ""
files["backend/app/analytics/peer_comparison.py"] = ""
files["backend/app/analytics/risk_scorer.py"] = ""
files["backend/app/ingestion/ingest.py"] = ""
files["data/generate_data.py"] = ""

files["backend/app/models.py"] = """from datetime import date, datetime
from typing import Optional, Any
from sqlalchemy import String, Integer, Boolean, Float, Date, DateTime, JSON, ForeignKey, ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class CSEProfile(Base):
    __tablename__ = "cse_profiles"

    cse_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    sector: Mapped[str] = mapped_column(String, nullable=False)
    size_tier: Mapped[str] = mapped_column(String, nullable=False)
    soc_model: Mapped[Optional[str]] = mapped_column(String)
    analyst_count: Mapped[Optional[int]] = mapped_column(Integer)
    submission_period_start: Mapped[Optional[date]] = mapped_column(Date)
    submission_period_end: Mapped[Optional[date]] = mapped_column(Date)

class Alert(Base):
    __tablename__ = "alerts"

    alert_id: Mapped[str] = mapped_column(String, primary_key=True)
    cse_id: Mapped[str] = mapped_column(String, ForeignKey("cse_profiles.cse_id"), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    source_system: Mapped[str] = mapped_column(String, nullable=False)
    target_asset_id: Mapped[Optional[str]] = mapped_column(String)
    alert_category: Mapped[str] = mapped_column(String, nullable=False)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    disposition: Mapped[Optional[str]] = mapped_column(String)
    assigned_analyst: Mapped[Optional[str]] = mapped_column(String)

class Case(Base):
    __tablename__ = "cases"

    case_id: Mapped[str] = mapped_column(String, primary_key=True)
    cse_id: Mapped[str] = mapped_column(String, ForeignKey("cse_profiles.cse_id"), nullable=False)
    linked_alert_ids: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String))
    opened_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    priority: Mapped[str] = mapped_column(String, nullable=False)
    escalated: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)
    escalated_to: Mapped[Optional[str]] = mapped_column(String)
    investigation_notes: Mapped[Optional[str]] = mapped_column(String)
    root_cause_identified: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)
    remediation_actions: Mapped[Optional[str]] = mapped_column(String)
    closure_reason: Mapped[Optional[str]] = mapped_column(String)
    assigned_analyst: Mapped[Optional[str]] = mapped_column(String)

class Asset(Base):
    __tablename__ = "assets"

    asset_id: Mapped[str] = mapped_column(String, primary_key=True)
    cse_id: Mapped[str] = mapped_column(String, ForeignKey("cse_profiles.cse_id"), nullable=False)
    hostname: Mapped[Optional[str]] = mapped_column(String)
    asset_type: Mapped[str] = mapped_column(String, nullable=False)
    criticality: Mapped[str] = mapped_column(String, nullable=False)
    monitored: Mapped[Optional[bool]] = mapped_column(Boolean, default=True)
    environment: Mapped[Optional[str]] = mapped_column(String)

class Finding(Base):
    __tablename__ = "findings"

    finding_id: Mapped[str] = mapped_column(String, primary_key=True)
    cse_id: Mapped[str] = mapped_column(String, ForeignKey("cse_profiles.cse_id"), nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    rule_id: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str] = mapped_column(String, nullable=False)
    evidence: Mapped[Any] = mapped_column(JSON, nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    recommendation: Mapped[Optional[str]] = mapped_column(String)
    peer_context: Mapped[Optional[str]] = mapped_column(String)
    detected_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
"""

files["backend/app/database.py"] = """import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://satsa:satsa@localhost:5432/satsa")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
"""

files["backend/app/config.py"] = """from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str = "postgresql://satsa:satsa@localhost:5432/satsa"

    class Config:
        env_file = ".env"

settings = Settings()
"""

files["backend/app/schemas.py"] = """from pydantic import BaseModel
from typing import List, Optional, Any
from datetime import datetime

class EntitySummary(BaseModel):
    cse_id: str
    name: str
    sector: str
    size_tier: str
    risk_score: Optional[float] = None

class EntityDetail(EntitySummary):
    soc_model: Optional[str]
    analyst_count: Optional[int]
    total_alerts: Optional[int] = 0
    total_cases: Optional[int] = 0

class Finding(BaseModel):
    finding_id: str
    category: str
    rule_id: str
    severity: str
    summary: str
    detected_at: Optional[datetime]

class FindingDetail(Finding):
    evidence: Any
    confidence: Optional[float]
    recommendation: Optional[str]
    peer_context: Optional[str]

class PeerComparison(BaseModel):
    cse_id: str
    peer_group: str
    metrics: dict

class OverviewStats(BaseModel):
    total_cses: int
    total_findings: int
    critical_findings: int
"""

files["backend/app/routers/entities.py"] = """from fastapi import APIRouter
from typing import List
from ..schemas import EntitySummary, EntityDetail, Finding

router = APIRouter(prefix="/api", tags=["Entities"])

@router.get("/entities", response_model=List[EntitySummary])
def get_entities():
    return []

@router.get("/entities/{cse_id}", response_model=EntityDetail)
def get_entity_detail(cse_id: str):
    return EntityDetail(cse_id=cse_id, name="Placeholder", sector="finance", size_tier="large")

@router.get("/entities/{cse_id}/findings", response_model=List[Finding])
def get_entity_findings(cse_id: str):
    return []

@router.get("/overview/stats")
def get_overview_stats():
    return {"total_cses": 0, "total_findings": 0, "critical_findings": 0}

@router.get("/trends/{cse_id}")
def get_trends(cse_id: str):
    return {}
"""

files["backend/app/routers/findings.py"] = """from fastapi import APIRouter
from ..schemas import FindingDetail
from datetime import datetime

router = APIRouter(prefix="/api/findings", tags=["Findings"])

@router.get("/{finding_id}", response_model=FindingDetail)
def get_finding(finding_id: str):
    return FindingDetail(
        finding_id=finding_id,
        category="execution_gap",
        rule_id="RULE-1",
        severity="high",
        summary="Placeholder",
        detected_at=datetime.utcnow(),
        evidence={},
        confidence=0.9,
        recommendation="Fix it",
        peer_context=None
    )

@router.get("/peer-comparison/{cse_id}")
def get_peer_comparison(cse_id: str):
    return {"cse_id": cse_id, "peer_group": "finance-large", "metrics": {}}
"""

files["backend/app/routers/reports.py"] = """from fastapi import APIRouter

router = APIRouter(prefix="/api/reports", tags=["Reports"])

@router.post("/{cse_id}/generate")
def generate_report(cse_id: str):
    return {"status": "generating", "cse_id": cse_id}
"""

files["backend/app/main.py"] = """from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routers import entities, findings, reports

app = FastAPI(title="SAT-SA API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(entities.router)
app.include_router(findings.router)
app.include_router(reports.router)

@app.get("/")
def root():
    return {"message": "Welcome to SAT-SA API"}
"""

files["backend/requirements.txt"] = """fastapi
uvicorn[standard]
sqlalchemy[asyncio]
psycopg2-binary
pandas
scikit-learn
scipy
python-multipart
pydantic-settings
weasyprint
"""

files["backend/Dockerfile"] = """FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
"""

files["docker-compose.yml"] = """version: '3.8'

services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: satsa
      POSTGRES_PASSWORD: satsa
      POSTGRES_DB: satsa
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  backend:
    build: ./backend
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://satsa:satsa@db:5432/satsa
    depends_on:
      - db

volumes:
  postgres_data:
"""

files["README.md"] = """# SAT-SA (Supervisory Analytics Tool for SOC Assessment)

## Description
SAT-SA is an analytics tool for processing and assessing SOC (Security Operations Center) performance, identifying execution gaps, and providing peer comparisons.

## Setup Instructions
1. Run `docker-compose up --build` to start the PostgreSQL database and FastAPI backend.
2. Ensure you have the `.env` configuration properly set up if overriding defaults.
3. Access the API documentation at `http://localhost:8000/docs`.
"""

for filepath, content in files.items():
    full_path = os.path.join(base_dir, filepath)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)

print("Project files generated successfully.")
