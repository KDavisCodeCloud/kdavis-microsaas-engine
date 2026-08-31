"""Defense-in-depth regression for surface_planner.py's wedge_type gate
(B.5 of the SPH gate-hardening pass, 2026-08-31): 'invalid' wedge_type
must be excluded from competitor-claim archetypes exactly like 'temporary'
always has been. In real operation this state is unreachable -- migration
039's approve_positioning() blocks 'invalid' from ever reaching
status='approved', no override path -- but this test constructs that state
directly anyway, since the module's own docstring calls this check
"defense in depth, not the only gate"."""
from agents.dist import surface_planner


class FakeQuery:
    def __init__(self, store, name):
        self.store = store
        self.name = name
        self._filters = []

    def select(self, *args, **kwargs):
        return self

    def eq(self, key, value):
        self._filters.append((key, value))
        return self

    def maybe_single(self):
        self._single = True
        return self

    def upsert(self, payload, **kwargs):
        self._upsert_payload = payload
        return self

    def execute(self):
        if hasattr(self, "_upsert_payload"):
            row = {**self._upsert_payload, "id": f"surface-{len(self.store.data.setdefault(self.name, [])) + 1}"}
            self.store.data[self.name].append(row)
            return type("Result", (), {"data": [row]})()
        rows = self.store.data.get(self.name, [])
        for key, value in self._filters:
            rows = [r for r in rows if r.get(key) == value]
        if getattr(self, "_single", False):
            if not rows:
                return None
            return type("Result", (), {"data": rows[0]})()
        return type("Result", (), {"data": rows})()


class FakeSupabase:
    def __init__(self, data):
        self.data = data

    def table(self, name):
        return FakeQuery(self, name)


def _db(wedge_type):
    return FakeSupabase({
        "mse_products": [{"id": "p1", "slug": "tradesdesk", "name": "TradesDesk"}],
        "mse_generator_state": [],
        "mse_positioning": [{"id": "pos1", "product_id": "p1", "status": "approved", "wedge_type": wedge_type}],
        "mse_competitors": [{"id": "c1", "name": "Jobber", "product_id": "p1"}],
        "mse_content_surfaces": [],
    })


async def test_invalid_wedge_type_plans_zero_competitor_archetypes():
    plan = await surface_planner.plan_surfaces("tradesdesk", supabase_client=_db("invalid"))
    archetypes = {item["archetype"] for item in plan}
    assert not archetypes & surface_planner._COMPETITOR_ARCHETYPES


async def test_temporary_wedge_type_still_plans_zero_competitor_archetypes():
    # Unchanged regression -- must not weaken.
    plan = await surface_planner.plan_surfaces("tradesdesk", supabase_client=_db("temporary"))
    archetypes = {item["archetype"] for item in plan}
    assert not archetypes & surface_planner._COMPETITOR_ARCHETYPES


async def test_structural_wedge_type_still_plans_competitor_archetypes():
    # Unchanged regression -- 'invalid' must not accidentally widen the pass condition either.
    plan = await surface_planner.plan_surfaces("tradesdesk", supabase_client=_db("structural"))
    archetypes = {item["archetype"] for item in plan}
    assert {"vs_competitor", "alternatives_to"} <= archetypes
