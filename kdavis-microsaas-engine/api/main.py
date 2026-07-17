import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.middleware.tenant_context import tenant_context_middleware
from api.routers import events, milestones, digest, pipeline, mcp, reengagement, research, stripe, ceo, marketing, outreach, factory

app = FastAPI(title="Micro SaaS Engine API", version="0.1.0")

# Registration order matters: Starlette prepends each added middleware, so
# whichever is added LAST ends up OUTERMOST at runtime. CORSMiddleware must
# be added last (after tenant_context_middleware) so it wraps every
# response, including early 401/403s that tenant_context_middleware
# returns directly without calling further into the stack. With CORS
# registered first (the previous order), tenant_context_middleware was
# outermost — any request it rejected (an OPTIONS preflight, or a real
# request with a missing/invalid JWT) never reached CORSMiddleware at all,
# so the rejection response carried no Access-Control-Allow-Origin header.
# A browser can't read a cross-origin response with no CORS header
# regardless of its status code — it reports the whole thing to
# JavaScript as a generic "Failed to fetch", not the real 401/403.
# Confirmed live 2026-07-17: fixing only the OPTIONS-preflight case
# (a prior, narrower fix) left every genuine auth failure on a real
# POST/PATCH/DELETE just as broken, for the same underlying reason.
app.middleware("http")(tenant_context_middleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:3001").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
app.include_router(outreach.router)
app.include_router(factory.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
