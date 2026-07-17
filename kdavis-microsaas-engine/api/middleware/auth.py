import os
import jwt
from fastapi import Request, HTTPException

_jwk_client = None


def _get_jwk_client() -> "jwt.PyJWKClient":
    global _jwk_client
    if _jwk_client is None:
        jwks_url = f"{os.environ['SUPABASE_URL']}/auth/v1/.well-known/jwks.json"
        _jwk_client = jwt.PyJWKClient(jwks_url)
    return _jwk_client


def verify_jwt(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = auth[7:]

    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=str(e))

    try:
        if header.get("alg") == "HS256":
            # Legacy Supabase projects (shared JWT secret) and this repo's
            # own synthetic test tokens use HS256.
            payload = jwt.decode(
                token, os.environ["SUPABASE_JWT_SECRET"], algorithms=["HS256"], audience="authenticated",
            )
        else:
            # Projects migrated to Supabase's newer asymmetric JWT signing
            # keys sign real session tokens with ES256 against a public
            # JWKS endpoint, not a shared secret — confirmed live
            # 2026-07-17 via this project's own
            # /auth/v1/.well-known/jwks.json (an ES256 EC key), after a
            # real browser login was rejected with "The specified alg
            # value is not allowed" against the old HS256-only check.
            # PyJWKClient fetches + caches the JWKS and matches the
            # token's `kid` to the right key automatically. Dispatching
            # on the token's own declared alg (read from the unverified
            # header, standard practice for algorithm-agnostic
            # verification) means this never makes a network call for an
            # HS256 token — keeping the test suite's synthetic tokens
            # fast and independent of any real network access.
            signing_key = _get_jwk_client().get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token, signing_key.key, algorithms=[header["alg"]], audience="authenticated",
            )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail={"error_code": "token_expired", "message": "Token expired"})
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=str(e))
