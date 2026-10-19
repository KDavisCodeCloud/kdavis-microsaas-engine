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


# ── Long-running background jobs (2026-10-18) ────────────────────────────
#
# The shared admin client above holds ONE HTTP/2 connection for the whole
# process. Three lead-source scouts started together as background tasks
# failed in the same second with httpx.RemoteProtocolError("Server
# disconnected"), and a lone VC run died the same way after ~770 requests.
# A job that runs for minutes gets its own client, and a dropped connection
# replays the query on a fresh one instead of failing the run.

_TRANSIENT_NAMES = ("RemoteProtocolError", "ConnectError", "ReadError", "WriteError",
                    "ConnectionTerminated", "LocalProtocolError")


def _is_transient(exc: BaseException) -> bool:
    return type(exc).__name__ in _TRANSIENT_NAMES


class _ReplayQuery:
    """Records a postgrest builder chain so it can be rebuilt on a new client."""

    def __init__(self, owner: "ResilientClient", table: str):
        self._owner, self._table, self._chain = owner, table, []

    def __getattr__(self, name):
        def step(*args, **kwargs):
            self._chain.append((name, args, kwargs))
            return self
        return step

    def _build(self):
        q = self._owner._client.table(self._table)
        for name, args, kwargs in self._chain:
            q = getattr(q, name)(*args, **kwargs)
        return q

    def execute(self):
        import logging
        import time
        for attempt in range(self._owner.attempts):
            try:
                return self._build().execute()
            except Exception as exc:
                if not _is_transient(exc) or attempt == self._owner.attempts - 1:
                    raise
                logging.getLogger(__name__).warning(
                    "[supabase] %s on %s, retrying on a new connection", type(exc).__name__, self._table)
                self._owner._reconnect()
                time.sleep(1.5 * (attempt + 1))


class ResilientClient:
    """table(...).<chain>.execute() with reconnect-and-replay on a dropped
    connection. Only the .table() surface: that is all the scouts use."""

    def __init__(self, factory=None, attempts: int = 3):
        self._factory = factory or (lambda: create_client(os.environ["SUPABASE_URL"],
                                                          os.environ["SUPABASE_SERVICE_KEY"]))
        self.attempts = attempts
        self._client = self._factory()

    def _reconnect(self) -> None:
        self._client = self._factory()

    def table(self, name: str) -> _ReplayQuery:
        return _ReplayQuery(self, name)


def get_supabase_for_job() -> ResilientClient:
    """A dedicated, reconnecting service-role client for one background job."""
    return ResilientClient()
