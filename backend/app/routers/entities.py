from fastapi import APIRouter
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
