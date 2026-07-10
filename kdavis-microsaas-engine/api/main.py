import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.middleware.tenant_context import tenant_context_middleware
from api.routers import events, milestones, digest, pipeline, mcp, reengagement, research, stripe, ceo, marketing

app = FastAPI(title="Micro SaaS Engine API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:3001").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(tenant_context_middleware)

app.include_router(events.router)
app.include_router(milestones.router)
app.include_router(digest.router)
app.include_router(pipeline.router)
app.include_router(mcp.router)
app.include_router(reengagement.router)
app.include_router(research.router)
app.include_router(stripe.router)
app.include_router(ceo.router)
app.include_router(marketing.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
