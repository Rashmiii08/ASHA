"""HTTPS-only account endpoints with same-origin validation for mutations."""

import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from .auth import Account, COOKIE, SESSION_SECONDS

router = APIRouter(prefix="/api/auth")


class WorkerID(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$")


class Login(WorkerID):
    password: str = Field(min_length=1, max_length=128)


class Reset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    password: str = Field(min_length=12, max_length=128)
    confirm_password: str = Field(max_length=128)


class Assignment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: Literal["pending", "active", "inactive"]
    village: str = Field(min_length=2, max_length=100)


class LegacyOwner(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker: int = Field(strict=True, gt=0)


def secure(request):
    if request.url.scheme != "https":
        raise HTTPException(403, "Use the HTTPS address to access your account.")


def mutation(request):
    secure(request)
    if request.headers.get("origin") != f"https://{request.headers.get('host')}":
        raise HTTPException(403, "This request must come from the ASHA site.")


async def identity(request, role=None):
    secure(request)
    user = await run_in_threadpool(request.app.state.accounts.session, request.cookies.get(COOKIE))
    if user is None:
        raise HTTPException(401, "Please sign in. Your session may have expired.")
    if role and user["role"] != role:
        raise HTTPException(403, "This account does not have access to that action.")
    return user


async def payload(request, model, message):
    mutation(request)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 4096:
            raise HTTPException(413, "The request is too large.")
    try:
        return model.model_validate(json.loads(body))
    except (ValueError, ValidationError):
        raise HTTPException(422, message) from None


async def limit(request, operation, worker=None):
    host = request.client.host if request.client else "unknown"
    buckets = [(f"{operation}:ip:{host}", 25), (f"{operation}:global", 100)]
    if worker:
        buckets.append((f"{operation}:worker:{worker}", 6))
    if await run_in_threadpool(request.app.state.accounts.limited, *buckets):
        raise HTTPException(429, "Too many attempts. Please try again later.", headers={"Retry-After": "900"})


async def password_operation(request, function, *args):
    # Bound concurrent Argon2 allocations on small deployments.
    async with request.app.state.password_slots:
        return await run_in_threadpool(function, *args)


@router.post("/register", status_code=201)
async def register(request: Request):
    account = await payload(request, Account, "Enter a valid worker ID, Indian mobile number, area, and a password of at least 12 characters.")
    await limit(request, "register", account.worker_id.upper())
    try:
        await password_operation(request, request.app.state.accounts.create, account)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"message": "Registration received. An administrator must approve your account before you sign in."}


@router.post("/login")
async def login(request: Request, response: Response):
    form = await payload(request, Login, "Enter your worker ID and password.")
    worker = form.worker_id.upper()
    await limit(request, "login", worker)
    user, token = await password_operation(request, request.app.state.accounts.login, worker, form.password)
    if token is None:
        if user and user["status"] == "pending":
            raise HTTPException(403, "Your account is awaiting administrator approval.")
        raise HTTPException(401, "Worker ID or password is incorrect, or the account is inactive.")
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, secure=True, httponly=True, samesite="strict", path="/")
    return {"user": user}


@router.get("/me")
async def me(request: Request):
    return {"user": await identity(request)}


@router.post("/logout")
async def logout(request: Request, response: Response):
    mutation(request)
    await run_in_threadpool(request.app.state.accounts.logout, request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="strict", path="/")
    return {"message": "Signed out."}


@router.post("/forgot")
async def forgot(request: Request):
    form = await payload(request, WorkerID, "Enter a valid worker ID.")
    worker = form.worker_id.upper()
    await limit(request, "forgot", worker)
    await run_in_threadpool(request.app.state.accounts.request_reset, worker)
    return {"message": "If the account is active, an administrator can verify your identity and provide a reset code privately."}


@router.post("/reset")
async def reset(request: Request):
    form = await payload(request, Reset, "Enter the reset code and a password of at least 12 characters.")
    await limit(request, "reset")
    if not form.password.strip():
        raise HTTPException(422, "Use a password that contains more than spaces.")
    if form.password != form.confirm_password:
        raise HTTPException(422, "Passwords must match.")
    if not await password_operation(request, request.app.state.accounts.reset, form.code, form.password):
        raise HTTPException(422, "The reset code is invalid or expired.")
    return {"message": "Password reset. Sign in with your new password."}


@router.get("/workers")
async def workers(request: Request):
    await identity(request, "admin")
    return await run_in_threadpool(request.app.state.accounts.list_workers)


@router.patch("/workers/{worker}")
async def manage(worker: int, request: Request):
    await identity(request, "admin")
    form = await payload(request, Assignment, "Enter a valid area and account status.")
    if not await run_in_threadpool(request.app.state.accounts.manage_worker, worker, form.status, form.village):
        raise HTTPException(404, "Worker not found.")
    return {"message": "Worker updated."}


@router.post("/workers/{worker}/reset-code")
async def reset_code(worker: int, request: Request):
    mutation(request)
    await identity(request, "admin")
    token = await run_in_threadpool(request.app.state.accounts.issue_reset, worker)
    if not token:
        raise HTTPException(409, "An active reset request is required.")
    return {"code": token}


@router.get("/legacy-visits")
async def legacy(request: Request):
    await identity(request, "admin")
    return await run_in_threadpool(request.app.state.register.unassigned)


@router.post("/legacy-visits/{visit_id}/assign")
async def assign(visit_id: str, request: Request):
    await identity(request, "admin")
    form = await payload(request, LegacyOwner, "Select an active worker.")
    if not await run_in_threadpool(request.app.state.register.assign_unowned, visit_id, form.worker):
        raise HTTPException(409, "The visit must be unassigned and the worker must be active.")
    return {"message": "Legacy visit assigned."}
