"""Runtime glue for the to-do list: model call, agent spawn, broadcast.

Kept apart from ``todo_triage`` so the policy stays pure/testable.  Each
function imports ``tui_gateway.server`` lazily (server imports us indirectly).
"""

from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import todo_triage as triage

logger = logging.getLogger(__name__)
_worker_lock = threading.Lock()
_worker_running = False


def broadcast(event: str, payload: Dict[str, Any]) -> None:
    from tui_gateway import server

    server._broadcast_global_event(event, payload)


def llm(messages: List[Dict[str, str]]) -> str:
    """Short classification call on the auxiliary (cheap) client; main model is the router's fallback."""
    from agent.auxiliary_client import call_llm

    resp = call_llm(task="todo_triage", messages=messages, temperature=0, max_tokens=900, timeout=45)
    return resp.choices[0].message.content or ""


def spawn(item: Dict[str, Any]) -> Optional[str]:
    """Start a normal agent session titled with the item; returns its session id."""
    from tui_gateway import server

    db = server._get_db()
    if db is None:
        return None
    sid = f"todo-{uuid.uuid4().hex[:12]}"
    db.create_session(sid, "todo")
    try:
        db.set_session_title(sid, str(item["text"])[:80])
    except Exception:
        pass
    home = server.get_clover_home()

    def run() -> None:
        try:
            agent = server._make_agent(sid, sid, session_id=sid, session_db=db)
            result = agent.run_conversation(triage.task_prompt(item))
            final = result.get("final_response") if isinstance(result, dict) else str(result or "")
            triage.finish_item(home, item["id"], final or "", broadcast)
        except Exception as exc:  # noqa: BLE001
            logger.warning("todo %s run failed: %s", item.get("id"), exc)
            triage.fail_item(home, item["id"], broadcast, str(exc))

    threading.Thread(target=run, name=f"todo-{item['id']}", daemon=True).start()
    return sid


def kick(home: Path) -> None:
    """Triage untriaged items in the background (single worker; re-runs if more arrive)."""
    global _worker_running
    with _worker_lock:
        if _worker_running:
            return
        _worker_running = True

    def work() -> None:
        global _worker_running
        try:
            triage.triage_pending(home, llm=llm, spawn=spawn, emit=broadcast)
        except Exception:  # noqa: BLE001
            logger.debug("todo triage failed", exc_info=True)
        finally:
            with _worker_lock:
                _worker_running = False

    threading.Thread(target=work, name="todo-triage", daemon=True).start()
