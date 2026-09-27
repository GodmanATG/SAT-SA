from datetime import date, datetime
from typing import Optional, Any
from sqlalchemy import String, Integer, Boolean, Float, Date, DateTime, JSON, ForeignKey, ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

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
