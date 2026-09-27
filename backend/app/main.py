from fastapi import FastAPI
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
