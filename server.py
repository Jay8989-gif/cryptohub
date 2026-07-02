#!/usr/bin/env python3
"""
CryptoHub backend — Hyperliquid smart-money consensus API.

A tiny FastAPI service that:
  * recomputes the consensus snapshot every REFRESH_SECONDS in a background
    thread (reusing engine.build_snapshot),
  * keeps the latest snapshot in memory,
  * serves it at GET /api/data (fast, cached — every client reads the same
    precomputed result),
  * exposes GET /api/health for uptime checks.

Auth is OFF by default. Set REQUIRE_AUTH=true (+ OUTSETA_DOMAIN) later to gate
/api/data behind an Outseta subscription — no code changes needed.

Run locally:   uvicorn server:app --port 8000
Run on Render: uvicorn server:app --host 0.0.0.0 --port $PORT
"""

import os
import threading
import time
import traceback

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import engine

# ---- config (all via environment variables) ----
REFRESH_SECONDS = int(os.getenv("REFRESH_SECONDS", "120"))     # how often to recompute
TOP = int(os.getenv("TOP", "50"))
MIN_VALUE = float(os.getenv("MIN_VALUE", "10000"))
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
REQUIRE_AUTH = os.getenv("REQUIRE_AUTH", "false").lower() == "true"
OUTSETA_DOMAIN = os.getenv("OUTSETA_DOMAIN", "")                # e.g. yourco.outseta.com

# ---- in-memory snapshot cache ----
_lock = threading.Lock()
_state = {"snapshot": None, "updatedAt": 0.0, "error": None, "refreshing": False}


def _refresh_loop():
    """Background worker: recompute the snapshot forever."""
    while True:
        try:
            with _lock:
                _state["refreshing"] = True
                prev = _state["snapshot"]
            snap = engine.build_snapshot(top=TOP, min_value=MIN_VALUE, prev=prev)
            with _lock:
                _state["snapshot"] = snap
                _state["updatedAt"] = time.time()
                _state["error"] = None
                _state["refreshing"] = False
            print(f"[refresh] ok — {len(snap.get('traders', {}))} traders", flush=True)
        except Exception:
            with _lock:
                _state["error"] = traceback.format_exc().splitlines()[-1]
                _state["refreshing"] = False
            print("[refresh] FAILED:\n" + traceback.format_exc(), flush=True)
        time.sleep(REFRESH_SECONDS)


app = FastAPI(title="CryptoHub Smart-Money API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in ALLOWED_ORIGINS],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _start():
    t = threading.Thread(target=_refresh_loop, daemon=True)
    t.start()


# ---- optional Outseta auth (only enforced when REQUIRE_AUTH=true) ----
def _check_auth(authorization: str | None):
    if not REQUIRE_AUTH:
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        import jwt
        from jwt import PyJWKClient
        jwks = PyJWKClient(f"https://{OUTSETA_DOMAIN}/.well-known/jwks")
        key = jwks.get_signing_key_from_jwt(token).key
        jwt.decode(token, key, algorithms=["RS256"], options={"verify_aud": False})
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


@app.get("/api/health")
def health():
    with _lock:
        snap = _state["snapshot"]
        age = time.time() - _state["updatedAt"] if _state["updatedAt"] else None
        return {
            "status": "ok" if snap else "starting",
            "generatedAt": snap.get("generatedAt") if snap else None,
            "ageSeconds": round(age) if age is not None else None,
            "refreshing": _state["refreshing"],
            "error": _state["error"],
            "refreshSeconds": REFRESH_SECONDS,
            "authRequired": REQUIRE_AUTH,
        }


@app.get("/api/data")
def data(authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    with _lock:
        snap = _state["snapshot"]
    if not snap:
        raise HTTPException(status_code=503, detail="Snapshot not ready yet, try again shortly")
    return JSONResponse(snap)


@app.get("/")
def root():
    return {"service": "cryptohub-smart-money-api", "endpoints": ["/api/data", "/api/health"]}
