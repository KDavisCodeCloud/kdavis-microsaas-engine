"""
tests/test_network_guard.py

Proves the autouse no-network guard in tests/conftest.py actually bites.

Added 2026-10-01: agents/marketing/contact_discovery.py began fetching
company /team and /robots.txt pages, and the suite went from 14 seconds to
HANGING — unstubbed calls sitting on 12-second timeouts against domains like
"northwind.com". A hang is the worst failure mode available: it reads as an
infinite loop rather than a missing stub. The guard converts that into an
immediate, self-describing error.

A guard nobody tests is a guard that silently stops working, so these two
tests exist to fail if it is ever removed or narrowed by accident.
"""

import socket

import httpx
import pytest


def test_module_level_httpx_is_blocked():
    with pytest.raises(RuntimeError, match="Unstubbed network call"):
        httpx.get("https://example.com")


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete", "head", "request"])
def test_every_module_level_verb_is_blocked(method):
    with pytest.raises(RuntimeError, match="Unstubbed network call"):
        getattr(httpx, method)("https://example.com")


def test_socket_create_connection_is_blocked():
    """Catches anything bypassing httpx -- urllib, smtplib, raw sockets."""
    with pytest.raises(RuntimeError, match="Unstubbed outbound connection"):
        socket.create_connection(("example.com", 443))


def test_fastapi_testclient_still_works():
    """The guard must NOT break in-process ASGI transport. TestClient is an
    httpx.Client over ASGITransport and never opens a socket, so patching
    httpx.Client.request would break every route test while blocking no real
    network access -- which is exactly what the first version of the guard
    did."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    with TestClient(app) as client:
        assert client.get("/ping").json() == {"ok": True}


def test_opt_out_marker_is_registered():
    """@pytest.mark.allow_network must stay available as an escape hatch,
    even though nothing in this suite uses it today."""
    import configparser

    cfg = configparser.ConfigParser()
    cfg.read("pytest.ini")
    assert "allow_network" in cfg["pytest"]["markers"]
