"""
Factory scaffold generator — Phase 6a of the MSE build/deploy pipeline.

Turns a READY_TO_BUILD opportunity_pipeline row into a complete, deployable
product repo: FastAPI backend with the retention scaffold (ADR-003), MCP
manifest (ADR-005), Stripe webhook handler, RLS-ready migration, and a
minimal Next.js 15 frontend skeleton (auth + UsageTracker wiring only —
the actual product UI/UX is a separate, ICP-driven design pass per
CLAUDE.md's design workflow, not something this generator should fake).

Files proven generic in this repo (MSE itself) are copied verbatim rather
than regenerated — they already satisfy every ADR/non-negotiable and
reinventing them per product would just be a source of drift.

This module only writes files to disk. It does NOT provision any live
infrastructure (no Supabase project, no Stripe account, no Vercel deploy,
no git push) — those are separate, cost-bearing steps (6b/6c/6d) gated on
an explicit human decision per CLAUDE.md's HITL non-negotiable, not
something a scaffold step should trigger as a side effect.
"""
import re
import shutil
from pathlib import Path
from typing import Any, Optional

from core.supabase_client import get_supabase

AGENT_ID = "factory-scaffold"

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# (source relative to REPO_ROOT, dest relative to scaffold output) — files
# already fully generic and proven in production; copied byte-for-byte.
_COPY_FILES = [
    ("core/supabase_client.py", "core/supabase_client.py"),
    ("core/sanitization.py", "core/sanitization.py"),
    ("core/llm_router.py", "core/llm_router.py"),
    ("core/retention/digest_generator.py", "core/retention/digest_generator.py"),
    ("core/retention/milestone_detector.py", "core/retention/milestone_detector.py"),
    ("core/retention/reengagement_trigger.py", "core/retention/reengagement_trigger.py"),
    ("api/middleware/auth.py", "api/middleware/auth.py"),
    ("api/routers/events.py", "api/routers/events.py"),
    ("api/routers/milestones.py", "api/routers/milestones.py"),
    ("api/routers/digest.py", "api/routers/digest.py"),
    ("api/routers/reengagement.py", "api/routers/reengagement.py"),
    ("api/routers/stripe.py", "api/routers/stripe.py"),
    ("requirements.txt", "requirements.txt"),
    ("requirements-dev.txt", "requirements-dev.txt"),
    ("runtime.txt", "runtime.txt"),
    ("railpack.json", "railpack.json"),
    ("pytest.ini", "pytest.ini"),
    (".gitignore", ".gitignore"),
    # Generic Next.js config — no MSE branding/colors in these, safe to copy.
    ("frontend/tsconfig.json", "frontend/tsconfig.json"),
    ("frontend/next.config.ts", "frontend/next.config.ts"),
    ("frontend/postcss.config.js", "frontend/postcss.config.js"),
]

_INIT_DIRS = ["api", "api/middleware", "api/routers", "core", "core/retention", "tests"]


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "product"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _load_opportunity(db, product_id: str) -> dict:
    # maybe_single().execute() returns bare None (not a Response with
    # .data=None) when zero rows match — a nonexistent product_id is
    # exactly that case, guard against it explicitly.
    result = db.table("opportunity_pipeline").select("*").eq("id", product_id).maybe_single().execute()
    row = result.data if result is not None else None
    if not row:
        raise ValueError(f"No opportunity_pipeline row found for id={product_id}")
    if row["status"] != "READY_TO_BUILD":
        raise ValueError(
            f"Opportunity {product_id} is status={row['status']!r}, not READY_TO_BUILD — refusing to scaffold"
        )
    return row


def _main_py(product_name: str) -> str:
    return f'''import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.middleware.tenant_context import tenant_context_middleware
from api.routers import events, milestones, digest, reengagement, stripe, mcp

app = FastAPI(title="{product_name} API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(tenant_context_middleware)

app.include_router(events.router)
app.include_router(milestones.router)
app.include_router(digest.router)
app.include_router(reengagement.router)
app.include_router(stripe.router)
app.include_router(mcp.router)


@app.get("/health")
async def health():
    return {{"status": "ok"}}
'''


def _tenant_context_py() -> str:
    return '''from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from api.middleware.auth import verify_jwt


async def tenant_context_middleware(request: Request, call_next):
    PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc", "/webhooks/stripe"}
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)

    # HTTPException raised inside @app.middleware("http") is NOT caught by
    # FastAPI's normal exception handlers (a Starlette BaseHTTPMiddleware
    # gotcha, found the hard way in kdavis-microsaas-engine 2026-07-16) — it
    # crashes as a raw 500 instead of the intended status code unless caught
    # and converted to a JSONResponse manually here.
    try:
        payload = verify_jwt(request)
        tenant_id = payload.get("sub")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="No tenant_id in token")
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    request.state.tenant_id = tenant_id
    # Custom app role (if any) lives in app_metadata — never user_metadata,
    # which is client-editable via auth.updateUser() and must never be
    # trusted for authorization (found the hard way in kdavis-microsaas-engine).
    request.state.role = payload.get("app_metadata", {}).get("role", "authenticated")
    return await call_next(request)
'''


def _mcp_py(product_slug: str, mcp_integration_surface: str) -> str:
    surface_note = (mcp_integration_surface or "Read/write access to this product's core data via MCP.").replace('"', '\\"')
    return f'''from fastapi import APIRouter, Request

router = APIRouter(prefix="/mcp", tags=["mcp"])


@router.get("/manifest")
async def mcp_manifest(request: Request):
    """MCP server manifest — ships from day one per ADR-005 (integration
    lock is the primary retention mechanism identified by research)."""
    return {{
        "name": "{product_slug}",
        "version": "0.1.0",
        "resources": [
            {{
                "uri": "{product_slug}://events",
                "name": "Usage Events",
                "description": "{surface_note}",
            }},
        ],
        "actions": [],
    }}
'''


def _migration_sql(product_slug: str) -> str:
    return f'''-- Migration 001: Core schema — {product_slug}
-- Retention scaffold (ADR-003 from kdavis-microsaas-engine): these 4 tables
-- must exist with RLS active before any feature work starts. Admin-access
-- RLS reads app_metadata (never user_metadata — client-editable, a real
-- privilege-escalation risk if ever used for authorization).

CREATE TABLE IF NOT EXISTS tenants (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name                TEXT,
  stripe_customer_id  TEXT,
  stripe_subscription_id TEXT,
  tier                TEXT DEFAULT 'starter',
  status              TEXT DEFAULT 'active',
  created_at          TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenants
  USING (id = auth.uid());

CREATE TABLE IF NOT EXISTS usage_events (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   UUID REFERENCES tenants(id),
  event_type  TEXT NOT NULL,
  metadata    JSONB DEFAULT '{{}}',
  created_at  TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE usage_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON usage_events
  USING (tenant_id = auth.uid());

CREATE TABLE IF NOT EXISTS milestones (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenants(id),
  milestone_key TEXT NOT NULL,
  threshold     INTEGER NOT NULL,
  achieved_at   TIMESTAMPTZ,
  notified_at   TIMESTAMPTZ,
  created_at    TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE milestones ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON milestones
  USING (tenant_id = auth.uid());

CREATE TABLE IF NOT EXISTS retention_sequences (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenants(id),
  sequence_type TEXT NOT NULL,
  status        TEXT DEFAULT 'active',
  current_step  INTEGER DEFAULT 0,
  started_at    TIMESTAMPTZ DEFAULT NOW(),
  completed_at  TIMESTAMPTZ
);
ALTER TABLE retention_sequences ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON retention_sequences
  USING (tenant_id = auth.uid());

CREATE TABLE IF NOT EXISTS weekly_digest_log (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      UUID NOT NULL REFERENCES tenants(id),
  value_metrics  JSONB DEFAULT '{{}}',
  skipped_reason TEXT,
  sent_at        TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE weekly_digest_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON weekly_digest_log
  USING (tenant_id = auth.uid());
'''


def _readme(product_name: str, vertical: str, solution_concept: str, tier_structure: dict) -> str:
    return f'''# {product_name}

**Vertical:** {vertical}
**Concept:** {solution_concept}

Generated by kdavis-microsaas-engine's factory scaffold generator
(agents/factory/scaffold_generator.py) from a READY_TO_BUILD
opportunity_pipeline row. Backend retention scaffold, auth, Stripe
webhook, and MCP manifest are wired and production-tested (copied
verbatim from kdavis-microsaas-engine, which runs this exact code live).

## Not done yet — do this before shipping

1. Provision a dedicated Supabase project (ADR-001 — never share with
   another product) and run `supabase/migrations/001_core_schema.sql`.
2. Create a dedicated Stripe account (ADR-006 — never share) and set
   `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET` in `.env`.
3. Fill in `.env` from `.env.example`.
4. Design the actual product UI — `frontend/` is a bare skeleton (auth +
   UsageTracker wiring only). Read the ICP, run the design system
   generator, apply an aesthetic matching this vertical's customer —
   per CLAUDE.md's design workflow. Never ship generic SaaS defaults.
5. Deploy backend (Railway, see `railpack.json`) and frontend (Vercel).

## Tier structure (from research)

```json
{tier_structure}
```
'''


def _env_example() -> str:
    return '''# Supabase (dedicated project — ADR-001, never shared)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-service-role-key
SUPABASE_ANON_KEY=your-anon-public-key
SUPABASE_JWT_SECRET=your-jwt-secret

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Stripe (dedicated account — ADR-006, never shared)
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...

# App
ALLOWED_ORIGINS=http://localhost:3000,https://your-product-domain.com
APP_ENV=development

# Frontend (Next.js)
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-public-key
'''


def _frontend_package_json(product_slug: str) -> str:
    return f'''{{
  "name": "{product_slug}-frontend",
  "version": "0.1.0",
  "private": true,
  "engines": {{
    "node": "22.x"
  }},
  "scripts": {{
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "next lint"
  }},
  "dependencies": {{
    "@supabase/ssr": "^0.5.2",
    "@supabase/supabase-js": "^2.45.0",
    "next": "^15.3.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0"
  }},
  "devDependencies": {{
    "@types/node": "^22.0.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "typescript": "^5.7.0",
    "tailwindcss": "^3.4.0",
    "postcss": "^8.4.0",
    "autoprefixer": "^10.4.0",
    "eslint": "^9.0.0",
    "eslint-config-next": "^15.3.0"
  }}
}}
'''


def _frontend_supabase_client() -> str:
    return '''"use client";
import { createBrowserClient } from "@supabase/ssr";

export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}
'''


def _frontend_middleware_ts() -> str:
    return '''import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

const PUBLIC_PATHS = ["/login", "/auth/callback"];

export async function middleware(request: NextRequest) {
  let supabaseResponse = NextResponse.next({ request });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() { return request.cookies.getAll(); },
        setAll(cs: { name: string; value: string; options?: Record<string, unknown> }[]) {
          cs.forEach(({ name, value }) => request.cookies.set(name, value));
          supabaseResponse = NextResponse.next({ request });
          cs.forEach(({ name, value, options }) =>
            supabaseResponse.cookies.set(name, value, options as Parameters<typeof supabaseResponse.cookies.set>[2])
          );
        },
      },
    }
  );

  const { pathname } = request.nextUrl;
  if (PUBLIC_PATHS.some((p) => pathname.startsWith(p))) return supabaseResponse;

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    return NextResponse.redirect(url);
  }

  return supabaseResponse;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)"],
};
'''


def _frontend_usage_tracker() -> str:
    return '''"use client";
import { useEffect } from "react";
import { createClient } from "@/lib/supabase/client";

/** Wired into root layout per ADR-003 — usage history must exist from day
one, retrofitting retention data later is 3x the work. */
export function UsageTracker({ eventType }: { eventType: string }) {
  useEffect(() => {
    const supabase = createClient();
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (!session) return;
      fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/events`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.access_token}` },
        body: JSON.stringify({ event_type: eventType, metadata: {} }),
      }).catch(() => { /* best-effort, never block rendering on this */ });
    });
  }, [eventType]);
  return null;
}
'''


def _frontend_login_page(product_name: str) -> str:
    return f'''"use client";

import {{ useState }} from "react";
import {{ createClient }} from "@/lib/supabase/client";

export default function LoginPage() {{
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const supabase = createClient();

  async function handleSubmit(e: React.FormEvent) {{
    e.preventDefault();
    setError(null);
    const {{ error }} = await supabase.auth.signInWithOtp({{
      email,
      options: {{ emailRedirectTo: `${{window.location.origin}}/auth/callback` }},
    }});
    if (error) setError(error.message);
    else setSent(true);
  }}

  return (
    <div className="min-h-screen flex items-center justify-center" style={{{{ backgroundColor: "#070910" }}}}>
      <div className="w-full max-w-sm p-8 rounded-2xl" style={{{{ backgroundColor: "#0f1420", border: "1px solid #1c222b" }}}}>
        <h1 className="text-lg font-bold mb-6" style={{{{ color: "#eef2f5" }}}}>{product_name}</h1>
        {{sent ? (
          <p className="text-sm" style={{{{ color: "#aab4bd" }}}}>Magic link sent to {{email}}. Check your inbox.</p>
        ) : (
          <form onSubmit={{handleSubmit}} className="space-y-4">
            <input
              type="email" value={{email}} onChange={{(e) => setEmail(e.target.value)}} required
              placeholder="you@company.com"
              className="w-full px-3 py-2.5 rounded-lg text-sm outline-none"
              style={{{{ backgroundColor: "#141a22", border: "1px solid #1c222b", color: "#eef2f5" }}}}
            />
            {{error && <p className="text-sm" style={{{{ color: "#e05d5d" }}}}>{{error}}</p>}}
            <button type="submit" className="w-full py-2.5 rounded-lg text-sm font-semibold" style={{{{ backgroundColor: "#5a96ff", color: "#070910" }}}}>
              Send Magic Link
            </button>
          </form>
        )}}
      </div>
    </div>
  );
}}
'''


def _frontend_layout(product_name: str) -> str:
    return f'''import type {{ Metadata }} from "next";
import "./globals.css";

export const metadata: Metadata = {{
  title: "{product_name}",
}};

export default function RootLayout({{ children }}: {{ children: React.ReactNode }}) {{
  return (
    <html lang="en">
      <body>{{children}}</body>
    </html>
  );
}}
'''


def _frontend_globals_css() -> str:
    return '''/* Base design tokens per CLAUDE.md's non-negotiable base design system —
   product-level variation allowed on top, these core tokens are not. */
:root {
  --bg: #070910;
  --blue: #5a96ff;
  --blue-dark: #2f6fe6;
  --amber: #f5a623;
  --green: #3fd17a;
}

body {
  background-color: var(--bg);
  font-family: "IBM Plex Sans", sans-serif;
  margin: 0;
}
'''


def _tailwind_config() -> str:
    # Global base design tokens (CLAUDE.md) — Space Grotesk/IBM Plex
    # Sans/JetBrains Mono, blue/amber/green accents. Deliberately NOT a
    # copy of MSE's own frontend/tailwind.config.ts, which uses Inter —
    # a font the same global CLAUDE.md explicitly bans for shipped
    # products (MSE's dashboard is Kelvin's internal tool, not a shipped
    # product, so that rule doesn't apply to it, but it does apply here).
    return '''import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        base: "#070910",
        blue: "#5a96ff",
        "blue-dark": "#2f6fe6",
        amber: "#f5a623",
        green: "#3fd17a",
      },
      fontFamily: {
        heading: ["Space Grotesk", "sans-serif"],
        sans: ["IBM Plex Sans", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
'''


def _frontend_auth_callback() -> str:
    return '''import { NextResponse } from "next/server";
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get("code");

  if (code) {
    const cookieStore = await cookies();
    const supabase = createServerClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL!,
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
      {
        cookies: {
          getAll() { return cookieStore.getAll(); },
          setAll(cs: { name: string; value: string; options?: Record<string, unknown> }[]) {
            cs.forEach(({ name, value, options }) =>
              cookieStore.set(name, value, options as Parameters<typeof cookieStore.set>[2])
            );
          },
        },
      }
    );
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    // Redirects to "/" rather than assuming a /dashboard route exists —
    // this scaffold doesn't know the product's actual page structure yet.
    if (!error) return NextResponse.redirect(`${origin}/`);
  }

  return NextResponse.redirect(`${origin}/login?error=auth_failed`);
}
'''


def _frontend_root_page() -> str:
    return '''import { redirect } from "next/navigation";

export default function Home() {
  redirect("/login");
}
'''


def generate_scaffold(product_id: str, output_root: Path, supabase_client: Optional[Any] = None) -> Path:
    """
    Generates a complete product scaffold at output_root/{product-slug}/.
    Raises if the opportunity isn't READY_TO_BUILD, or on any write failure
    — never fails silently. Returns the path to the generated repo.
    """
    db = supabase_client if supabase_client is not None else get_supabase()
    opp = _load_opportunity(db, product_id)

    product_name = opp["solution_concept"]
    product_slug = _slugify(product_name)
    vertical = opp.get("vertical", "")
    tier_structure = opp.get("tier_structure") or {}
    mcp_integration_surface = opp.get("mcp_integration_surface") or ""

    out = Path(output_root) / product_slug
    if out.exists():
        raise FileExistsError(f"Scaffold output already exists: {out}")

    for d in _INIT_DIRS:
        _write(out / d / "__init__.py", "")

    for src_rel, dest_rel in _COPY_FILES:
        src = REPO_ROOT / src_rel
        if not src.exists():
            raise FileNotFoundError(f"Expected source file missing: {src}")
        dest = out / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dest)

    _write(out / "api" / "main.py", _main_py(product_name))
    _write(out / "api" / "middleware" / "tenant_context.py", _tenant_context_py())
    _write(out / "api" / "routers" / "mcp.py", _mcp_py(product_slug, mcp_integration_surface))
    _write(out / "supabase" / "migrations" / "001_core_schema.sql", _migration_sql(product_slug))
    _write(out / "README.md", _readme(product_name, vertical, product_name, tier_structure))
    _write(out / ".env.example", _env_example())

    _write(out / "frontend" / "package.json", _frontend_package_json(product_slug))
    _write(out / "frontend" / ".nvmrc", "22\n")
    _write(out / "frontend" / "lib" / "supabase" / "client.ts", _frontend_supabase_client())
    _write(out / "frontend" / "middleware.ts", _frontend_middleware_ts())
    _write(out / "frontend" / "components" / "UsageTracker.tsx", _frontend_usage_tracker())
    _write(out / "frontend" / "app" / "login" / "page.tsx", _frontend_login_page(product_name))
    _write(out / "frontend" / "app" / "layout.tsx", _frontend_layout(product_name))
    _write(out / "frontend" / "app" / "globals.css", _frontend_globals_css())
    _write(out / "frontend" / "tailwind.config.ts", _tailwind_config())
    _write(out / "frontend" / "app" / "auth" / "callback" / "route.ts", _frontend_auth_callback())
    _write(out / "frontend" / "app" / "page.tsx", _frontend_root_page())

    return out
