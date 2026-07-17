import pytest

from agents.factory.provision_stripe import (
    create_tier_products,
    create_webhook,
    provision_stripe,
)


class FakeProduct:
    def __init__(self):
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return {"id": f"prod_{len(self.created)}", **kwargs}


class FakePrice:
    def __init__(self):
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return {"id": f"price_{len(self.created)}", **kwargs}


class FakeWebhookEndpoint:
    def __init__(self):
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return {"id": "we_test123", "secret": "whsec_test_generated", **kwargs}


class FakeStripeModule:
    def __init__(self):
        self.api_key = None
        self.Product = FakeProduct()
        self.Price = FakePrice()
        self.WebhookEndpoint = FakeWebhookEndpoint()


TIER_STRUCTURE = {"starter": 29, "growth": 59, "scale": 99}


def test_create_tier_products_creates_one_product_and_price_per_tier():
    fake = FakeStripeModule()

    result = create_tier_products("Freight Audit Copilot", TIER_STRUCTURE, "sk_test_x", stripe_module=fake)

    assert set(result.keys()) == {"starter", "growth", "scale"}
    assert len(fake.Product.created) == 3
    assert len(fake.Price.created) == 3
    assert fake.api_key == "sk_test_x"


def test_create_tier_products_sets_tier_metadata_matching_resolve_tier():
    fake = FakeStripeModule()
    create_tier_products("Freight Audit Copilot", {"starter": 29}, "sk_test_x", stripe_module=fake)

    assert fake.Price.created[0]["metadata"]["tier"] == "starter"


def test_create_tier_products_converts_dollars_to_cents():
    fake = FakeStripeModule()
    create_tier_products("Freight Audit Copilot", {"growth": 59}, "sk_test_x", stripe_module=fake)

    assert fake.Price.created[0]["unit_amount"] == 5900
    assert fake.Price.created[0]["currency"] == "usd"
    assert fake.Price.created[0]["recurring"] == {"interval": "month"}


def test_create_tier_products_links_price_to_its_own_product():
    fake = FakeStripeModule()
    result = create_tier_products("X", {"starter": 29, "growth": 59}, "sk_test_x", stripe_module=fake)

    for tier, ids in result.items():
        matching_price_call = next(p for p in fake.Price.created if p["product"] == ids["product_id"])
        assert matching_price_call["metadata"]["tier"] == tier


def test_create_tier_products_refuses_empty_tier_structure():
    fake = FakeStripeModule()
    with pytest.raises(ValueError, match="no billable tiers"):
        create_tier_products("X", {}, "sk_test_x", stripe_module=fake)


def test_create_webhook_points_at_correct_path():
    fake = FakeStripeModule()
    result = create_webhook("https://freight-api.up.railway.app", "sk_test_x", stripe_module=fake)

    assert result == {"id": "we_test123", "secret": "whsec_test_generated"}
    assert fake.WebhookEndpoint.created[0]["url"] == "https://freight-api.up.railway.app/webhooks/stripe"


def test_create_webhook_strips_trailing_slash_from_backend_url():
    fake = FakeStripeModule()
    create_webhook("https://freight-api.up.railway.app/", "sk_test_x", stripe_module=fake)

    assert fake.WebhookEndpoint.created[0]["url"] == "https://freight-api.up.railway.app/webhooks/stripe"


def test_provision_stripe_full_flow():
    fake = FakeStripeModule()

    result = provision_stripe(
        "Freight Audit Copilot", TIER_STRUCTURE, "https://freight-api.up.railway.app", "sk_test_x",
        stripe_module=fake,
    )

    assert set(result["tiers"].keys()) == {"starter", "growth", "scale"}
    assert result["webhook_id"] == "we_test123"
    assert result["webhook_secret"] == "whsec_test_generated"
