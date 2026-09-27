from pydantic import BaseModel
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
