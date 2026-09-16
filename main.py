"""
Y.G.L Office System -- FastAPI application entrypoint.

Routers wired: Auth (login/MFA/refresh), KYC (webhook + manual-review,
RBAC-protected), Member (registration/login/profile, admin status-change),
Sponsor (self-service placement, admin re-sponsorship/review-queue).
Commerce/Genealogy remain unwired -- no service/router layer built yet.

Per the architecture boundary rule, this file and every module under
app/modules/ must NEVER import commission_engine directly. Only
app/services/commission_service/ may do so (enforced by
scripts/verify_import_boundary.py in CI).
"""
from fastapi import FastAPI

from app.core.config import settings
from app.modules.auth.router import router as auth_router
from app.modules.kyc.router import router as kyc_router
from app.modules.member.router import router as member_router
from app.modules.sponsor.router import router as sponsor_router

app = FastAPI(
    title=settings.app_name,
    version="0.3.0",
    description="Y.G.L (Your Global Link) — Office System API. "
                 "Phase 8.2+: Auth/MFA, RBAC, KYC, Member, Sponsor wired. Commerce pending.",
)


@app.get("/health")
async def health_check() -> dict:
    """Basic liveness check. Does not touch the database."""
    return {"status": "ok", "service": settings.app_name, "environment": settings.environment}


app.include_router(auth_router, prefix=settings.api_v1_prefix, tags=["auth"])
app.include_router(kyc_router, prefix=settings.api_v1_prefix, tags=["kyc"])
app.include_router(member_router, prefix=settings.api_v1_prefix, tags=["members"])
app.include_router(sponsor_router, prefix=settings.api_v1_prefix, tags=["sponsor"])

# --- NOT YET WIRED (remaining gap, see WORK_LOG.md) -------------------------
# Commerce (Product/Order/Payment) has no module at all yet -- not even
# DB models. Account KYC (Initial tier) also has no router yet.
