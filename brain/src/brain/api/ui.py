from __future__ import annotations

import secrets
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from brain.api.routes import Svc, container
from brain.container import Container

TEMPLATES = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "web" / "templates")
_basic = HTTPBasic(realm="Knowledge Brain")


def require_ui_login(
    c: Annotated[Container, Depends(container)], credentials: Annotated[HTTPBasicCredentials, Depends(_basic)]
) -> None:
    """Browser login for the UI: any user name, password = BRAIN_API_KEY.

    The expert page writes straight into memory, so it must not be open to anyone on the network.
    """
    if not secrets.compare_digest(credentials.password.encode(), c.settings.api_key.encode()):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "wrong password",
            headers={"WWW-Authenticate": 'Basic realm="Knowledge Brain"'},
        )


ui = APIRouter(include_in_schema=False, dependencies=[Depends(require_ui_login)])


def _htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


@ui.get("/", response_class=HTMLResponse)
async def chat_page(request: Request) -> Response:
    return TEMPLATES.TemplateResponse(request, "chat.html", {"question": None, "result": None})


@ui.post("/ui/ask", response_class=HTMLResponse)
async def ui_ask(request: Request, svc: Svc, question: Annotated[str, Form(min_length=1)]) -> Response:
    result = await svc.ask(question.strip())
    template = "_answer.html" if _htmx(request) else "chat.html"
    return TEMPLATES.TemplateResponse(request, template, {"question": question, "result": result})


@ui.post("/ui/feedback", response_class=HTMLResponse)
async def ui_feedback(
    svc: Svc, question: Annotated[str, Form()], answer: Annotated[str, Form()], helpful: Annotated[bool, Form()]
) -> Response:
    await svc.record_feedback(question, answer, helpful)
    return HTMLResponse('<span class="muted">Thanks for the feedback.</span>')


@ui.post("/ui/escalate", response_class=HTMLResponse)
async def ui_escalate(svc: Svc, question: Annotated[str, Form()], bot_attempt: Annotated[str, Form()] = "") -> Response:
    gap_id = await svc.create_gap(question, f"Bot answer judged insufficient: {bot_attempt}"[:4000], "chat")
    return HTMLResponse(f'<span class="muted">Sent to an expert (ticket {gap_id}).</span>')


@ui.get("/experts", response_class=HTMLResponse)
async def experts_page(request: Request, svc: Svc) -> Response:
    open_gaps = await svc.list_gaps("open")
    answered = (await svc.list_gaps("answered"))[:10]
    return TEMPLATES.TemplateResponse(request, "experts.html", {"open_gaps": open_gaps, "answered_gaps": answered})


@ui.post("/ui/gaps/{gap_id}/answer", response_class=HTMLResponse)
async def ui_answer_gap(
    request: Request,
    gap_id: str,
    svc: Svc,
    answer: Annotated[str, Form(min_length=1)],
    expert: Annotated[str, Form(min_length=1)],
) -> Response:
    await svc.answer_gap(gap_id, answer.strip(), expert.strip())
    if not _htmx(request):
        return RedirectResponse("/experts", status_code=303)
    gaps = {g.id: g for g in await svc.list_gaps("answered")}
    return TEMPLATES.TemplateResponse(request, "_gap.html", {"gap": gaps[gap_id]})
