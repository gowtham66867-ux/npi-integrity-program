"""Verified IAP identity and explicit reviewer/supervisor permissions."""
import os
from fastapi import HTTPException, Request


def verify_iap(token: str, audience: str) -> dict:
    from google.auth.transport.requests import Request as AuthRequest
    from google.oauth2 import id_token
    claims = id_token.verify_token(token, AuthRequest(), audience=audience,
                                  certs_url="https://www.gstatic.com/iap/verify/public_key")
    if claims.get("iss") != "https://cloud.google.com/iap" or not claims.get("sub"):
        raise ValueError("invalid IAP issuer or subject")
    return claims


def authenticate(request: Request, domain: str):
    if not domain:
        if os.environ.get("K_SERVICE"):
            raise HTTPException(503, "deployed service requires verified authentication")
        request.state.actor = None
        request.state.role = "local_demo"
        return
    audience = os.environ.get("NPI_IAP_AUDIENCE", "")
    token = request.headers.get("x-goog-iap-jwt-assertion", "")
    if not audience or not token:
        raise HTTPException(403, "verified Google sign-in required")
    try:
        claims = verify_iap(token, audience)
    except Exception:
        raise HTTPException(403, "invalid Google sign-in") from None
    email = str(claims.get("email", "")).lower()
    if not email.endswith("@" + domain.lower()):
        raise HTTPException(403, "company sign-in required")
    supervisors = {s.strip().lower() for s in os.environ.get("NPI_SUPERVISORS", "").split(",") if s.strip()}
    reviewers = {s.strip().lower() for s in os.environ.get("NPI_REVIEWERS", "").split(",") if s.strip()}
    request.state.actor = email
    request.state.role = "supervisor" if email in supervisors else "reviewer" if email in reviewers else "viewer"


def actor_for(request: Request, supplied: str, supervisor: bool = False) -> str:
    actor = getattr(request.state, "actor", None)
    role = getattr(request.state, "role", "local_demo")
    if actor:
        allowed = {"supervisor"} if supervisor else {"reviewer", "supervisor"}
        if role not in allowed:
            raise HTTPException(403, "supervisor role required" if supervisor else "reviewer role required")
        return actor
    if not supplied.strip():
        raise HTTPException(400, "reviewer name required")
    return supplied.strip()


TRANSITIONS = {
    "New": {"review", "monitor", "dismiss"},
    "In review": {"monitor", "dismiss", "escalate"},
    "Monitoring": {"review", "dismiss", "escalate"},
    "Dismissed": {"reopen"},
    "Escalated": {"refer", "monitor"},
    "Referred": set(),
}


def check_transition(status: str, action: str):
    if action not in TRANSITIONS.get(status, set()):
        raise HTTPException(409, f"cannot {action} an alert in {status}")
