"""
ApexCore Banking Portal: a standalone, intentionally legacy-styled mock
banking back-office application.

Purpose: this is the proxy target for evaluating computer-use automation. It exists
so the computer-use agent has a *real, hostile-but-honest* UI surface to
drive: nested tables, non-semantic markup, ASP.NET-style control IDs,
server-rendered pages, no test IDs, and a couple of runtime conditions
(maintenance interstitial, slow load, not-found) that a production replay
engine must handle explicitly rather than assume away.

Run: uvicorn mock_target.app:app --port 8000 --reload
"""
from __future__ import annotations

import time

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mock_target import core_data

app = FastAPI(title="ApexCore Banking Portal (mock)")
templates = Jinja2Templates(directory="mock_target/templates")
app.mount("/static", StaticFiles(directory="mock_target/templates"), name="static")


def render(request: Request, name: str, context: dict | None = None) -> HTMLResponse:
    """Version-agnostic wrapper around Jinja2Templates.TemplateResponse.

    Starlette changed this signature across versions (request-in-context-dict
    vs. request-as-first-positional-arg). This keeps call sites simple and
    stable regardless of which Starlette is installed.
    """
    ctx = dict(context or {})
    return templates.TemplateResponse(request, name, ctx)


@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    return render(request, "home.html")


# ---------------------------------------------------------------------------
# Flow 1: Member lookup & balance inquiry
# ---------------------------------------------------------------------------

@app.get("/portal/member-lookup", response_class=HTMLResponse)
def member_lookup_form(request: Request):
    maintenance = core_data.maybe_trigger_maintenance()
    return render(request, "member_lookup.html", {"maintenance": maintenance})


@app.post("/portal/member-lookup", response_class=HTMLResponse)
def member_lookup_submit(request: Request, ctl00_MainContent_txtMemberId: str = Form(...)):
    member_id = ctl00_MainContent_txtMemberId.strip()

    # Simulated transient slow load (recoverable condition: caller should wait/retry)
    delay = core_data.consume_slow_load()
    if delay:
        time.sleep(delay)

    member = core_data.get_member(member_id)
    if member is None:
        # Expected business outcome, not an error.
        return render(request, "member_not_found.html", {"member_id": member_id})

    return render(request, "member_detail.html", {"member": member})


# ---------------------------------------------------------------------------
# Flow 2: Open sub-account (write / irreversible action) + confirmation
# ---------------------------------------------------------------------------

@app.get("/portal/sub-account/new", response_class=HTMLResponse)
def sub_account_form(request: Request, member_id: str):
    member = core_data.get_member(member_id)
    if member is None:
        return render(request, "member_not_found.html", {"member_id": member_id})
    if member.status == "FROZEN":
        return render(request, "account_frozen.html", {"member": member})
    return render(request, "sub_account_new.html", {"member": member})


@app.post("/portal/sub-account/review", response_class=HTMLResponse)
def sub_account_review(
    request: Request,
    ctl00_MainContent_hidMemberId: str = Form(...),
    ctl00_MainContent_ddlProductType: str = Form(...),
    ctl00_MainContent_txtInitialDeposit: str = Form(...),
):
    member = core_data.get_member(ctl00_MainContent_hidMemberId)
    if member is None:
        return render(request, "member_not_found.html", {"member_id": ctl00_MainContent_hidMemberId})
    try:
        deposit = float(ctl00_MainContent_txtInitialDeposit)
    except ValueError:
        return render(
            request,
            "validation_error.html",
            {"message": "Initial deposit must be a valid number.", "member": member},
        )
    if deposit < 25.00:
        return render(
            request,
            "validation_error.html",
            {"message": "Initial deposit must be at least $25.00 for this product.", "member": member},
        )

    return render(
        request,
        "sub_account_review.html",
        {"member": member, "product_type": ctl00_MainContent_ddlProductType, "deposit": deposit},
    )


@app.post("/portal/sub-account/confirm", response_class=HTMLResponse)
def sub_account_confirm(
    request: Request,
    ctl00_MainContent_hidMemberId: str = Form(...),
    ctl00_MainContent_hidProductType: str = Form(...),
    ctl00_MainContent_hidDeposit: str = Form(...),
):
    member = core_data.get_member(ctl00_MainContent_hidMemberId)
    if member is None:
        return render(request, "member_not_found.html", {"member_id": ctl00_MainContent_hidMemberId})
    receipt = core_data.open_sub_account(
        member.member_id,
        ctl00_MainContent_hidProductType,
        float(ctl00_MainContent_hidDeposit),
    )
    return render(request, "sub_account_confirmation.html", {"member": member, "receipt": receipt})


# ---------------------------------------------------------------------------
# Admin/demo controls: toggle runtime conditions for evidence generation.
# Not part of the "product" surface - these simulate conditions a real core
# system would produce on its own schedule.
# ---------------------------------------------------------------------------

@app.post("/admin/maintenance/{state}")
def toggle_maintenance(state: str):
    core_data.MAINTENANCE_MODE["enabled"] = state == "on"
    return {"maintenance_enabled": core_data.MAINTENANCE_MODE["enabled"]}


@app.post("/admin/slow-load/{count}")
def set_slow_load(count: int):
    core_data.SLOW_LOAD["remaining"] = count
    return {"slow_load_remaining": count}


@app.post("/admin/reset")
def reset():
    core_data.reset_all()
    return {"status": "reset"}
