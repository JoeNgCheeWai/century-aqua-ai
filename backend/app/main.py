import os
from dotenv import load_dotenv

load_dotenv()

# --- STARTUP CONFIG CHECK (never print secret values) ---
print("="*40)
print("API KEY:", "SET" if os.getenv("ANTHROPIC_API_KEY") else "NOT FOUND")
print("GATEKEEPER MODEL:", os.getenv("GATEKEEPER_MODEL", "NOT FOUND"))
print("SPECIALIST MODEL:", os.getenv("SPECIALIST_MODEL", "NOT FOUND"))
print("ALLOWED_ORIGINS RAW:", repr(os.getenv("ALLOWED_ORIGINS")))
print("ALLOWED_ORIGIN_REGEX RAW:", repr(os.getenv("ALLOWED_ORIGIN_REGEX")))
print("="*40)

import secrets

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.agents.gatekeeper import is_aquaculture_related
from app.agents.dr_aqua import get_aquaculture_answer
from app.api.admin.agents.reset import router as reset_router
from app.api.cao import router as cao_router
from app.api.coo import router as coo_router
from app.api.cross_check import router as cross_check_router

app = FastAPI(title="Century Aquaculture Intelligence Platform (CAIP)")

# AI_CAO (internal only, POST /api/cao) and the constitutional governance endpoints are
# gated by INTERNAL_CAO_KEY. AI_COO (internal only, POST /api/coo) has its own INTERNAL_COO_KEY.
app.include_router(cao_router)
app.include_router(coo_router)
app.include_router(cross_check_router)
app.include_router(reset_router)

# Comma-separated list of allowed frontend origins (no trailing slashes).
# Defaults keep local development working.
ALLOWED_ORIGINS = [
    origin.strip().rstrip("/")
    for origin in os.getenv(
        "ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if origin.strip()
]

# Optional regex for dynamic origins such as Vercel preview deployments,
# e.g. https://caip-frontend-.*-yourteam\.vercel\.app
ALLOWED_ORIGIN_REGEX = os.getenv("ALLOWED_ORIGIN_REGEX") or None

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- NEW: Message History Models ---
class MessageHistory(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    history: list[MessageHistory] = Field(default_factory=list)

class ChatResponse(BaseModel):
    reply: str
    in_scope: bool

@app.get("/api/health")
async def health():
    return {"status": "ok"}

@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    try:
        # 1. Gatekeeper checks if it's aquaculture
        in_scope = await is_aquaculture_related(request.message)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Gatekeeper unavailable: {exc}"
        ) from exc

    if not in_scope:
        return ChatResponse(
            reply=(
                "I'm the Century Aquaculture assistant, so I can only help with "
                "aquaculture topics such as species, water quality, feed, health "
                "and farm operations. Could you rephrase your question along "
                "those lines?"
            ),
            in_scope=False,
        )

    # 2. If YES, Dr. Aqua provides the expert answer with history!
    try:
        print("DEBUG 1: Converting history...")
        history_dicts = [{"role": msg.role, "content": msg.content} for msg in request.history]
        print(f"DEBUG 2: Calling Dr. Aqua with message: '{request.message}' and {len(history_dicts)} history items.")
        
        expert_reply = await get_aquaculture_answer(request.message, history_dicts)
        
        print("DEBUG 3: Dr. Aqua replied successfully!")
        return ChatResponse(reply=expert_reply, in_scope=True)
    except Exception as exc:
        print(f"ERROR IN DR. AQUA: {type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=502, detail=f"Dr. Aqua specialist unavailable: {exc}"
        ) from exc
