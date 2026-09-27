from fastapi import APIRouter

router = APIRouter(prefix="/api/reports", tags=["Reports"])

@router.post("/{cse_id}/generate")
def generate_report(cse_id: str):
    return {"status": "generating", "cse_id": cse_id}
