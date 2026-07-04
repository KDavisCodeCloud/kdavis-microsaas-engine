import os
from supabase import create_client, Client

_admin_client: Client | None = None


def get_supabase() -> Client:
    """Service-role client for admin/webhook operations. Bypasses RLS — use only where no user JWT exists."""
    global _admin_client
    if _admin_client is None:
        _admin_client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )
    return _admin_client


def get_supabase_for_request(jwt: str) -> Client:
    """Per-request client using the user's JWT. RLS enforces tenant isolation at the DB level."""
    client = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_ANON_KEY"],
    )
    client.postgrest.auth(jwt)
    return client
