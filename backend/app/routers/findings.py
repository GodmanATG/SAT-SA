from fastapi import APIRouter
from ..schemas import FindingDetail
from datetime import datetime

router = APIRouter(prefix="/api", tags=["Findings"])

@router.get("/findings/{finding_id}", response_model=FindingDetail)
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
