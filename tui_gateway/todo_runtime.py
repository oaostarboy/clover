"""Runtime glue for the to-do list: model call, agent spawn, broadcast.

Kept apart from ``todo_triage`` so the policy stays pure/testable.  Each
function imports ``tui_gateway.server`` lazily (server imports us indirectly).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import todo_triage as triage

logger = logging.getLogger(__name__)
_worker_lock = threading.Lock()
_worker_running = False
_worker_rerun = False
_live_ids: set = set()  # item ids with an agent thread alive in THIS process
_DEBOUNCE_S = 2.0


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
            # Read-only research toolset, no approvals to hand out: the agent has
            # NO terminal, file-write, browser, send_message, cron, delegation or
            # MCP tools, so it cannot send/post/buy/delete even if told to.
            agent = server._make_agent(
                sid, sid, session_id=sid, session_db=db,
                enabled_toolsets_override=list(triage.AUTO_START_TOOLSETS),
                max_iterations_override=triage.AUTO_START_MAX_ITERATIONS,
            )
            result = agent.run_conversation(triage.task_prompt(item))
            final = result.get("final_response") if isinstance(result, dict) else str(result or "")
            triage.finish_item(home, item["id"], final or "", broadcast)
        except Exception as exc:  # noqa: BLE001
            logger.warning("todo %s run failed: %s", item.get("id"), exc)
            triage.fail_item(home, item["id"], broadcast, str(exc))
        finally:
            _live_ids.discard(item["id"])

    _live_ids.add(item["id"])
    threading.Thread(target=run, name=f"todo-{item['id']}", daemon=True).start()
    return sid


def assign(item: Dict[str, Any], agent_id: str):
    """Start ``item`` with the agent the owner picked. Returns ``(session_id, error)``.

    The gate in ``todo_triage.decide`` has already cleared the item (low risk, nothing external). The default profile is this
    gateway's own agent: same restricted web-only session as auto-start. Any other agent (peer or sibling profile) is reached
    through the crew relay (``peer.turn``); the task prompt carries the same no-send/post/buy/delete rules and a
    NEEDS_APPROVAL hand-back, and the remote agent's own approvals still apply. Unknown/offline agents become a clear error.
    """
    from . import crew as crew_mod
    from .methods_crew import crew_roster

    try:
        roster = crew_roster()
    except Exception:  # noqa: BLE001
        return None, "Could not read the agent list from the gateway"
    row = next((r for r in roster if str(r.get("id")).lower() == agent_id.lower()), None)
    if row is None:
        return None, f"Unknown agent '{agent_id}'"
    name = str(row.get("display_name") or agent_id)
    if not row.get("online"):
        return None, f"{name} is offline"
    if row.get("kind") == "profile" and agent_id.lower() in ("default", "clover"):
        sid = spawn(item)
        return (sid, "") if sid else (None, "Could not start the session")
    from tui_gateway import server

    home = server.get_clover_home()
    sid = f"todo-{uuid.uuid4().hex[:12]}"

    def run() -> None:
        try:
            out = crew_mod.relay_turn(agent_id, triage.task_prompt(item), room_id=f"todo:{item['id']}")
            triage.finish_item(home, item["id"], out.get("reply") or "", broadcast)
        except crew_mod.PeerRelayError as exc:
            triage.fail_item(home, item["id"], broadcast, exc.message)
        except Exception as exc:  # noqa: BLE001
            triage.fail_item(home, item["id"], broadcast, str(exc))
        finally:
            _live_ids.discard(item["id"])

    _live_ids.add(item["id"])
    threading.Thread(target=run, name=f"todo-{item['id']}", daemon=True).start()
    return sid, ""


def reconcile_interrupted(home: Path) -> None:
    """Items marked working/queued by auto-start whose thread died (gateway restart) become failed.

    Without this a restart would leave them counting against the 2-at-once cap forever.
    """
    from . import todo_store as store

    for it in store.load(home)["items"]:
        if it.get("auto_started") and it.get("status") in ("queued", "working") and not it.get("done") \
                and it["id"] not in _live_ids:
            triage.fail_item(home, it["id"], broadcast, "Interrupted (gateway restarted)")


def kick(home: Path) -> None:
    """Triage untriaged items in the background.

    One worker at a time; adds that arrive while it runs set a re-run flag.  The
    worker waits a short debounce so a burst of adds becomes ONE model call.
    """
    global _worker_running, _worker_rerun
    with _worker_lock:
        if _worker_running:
            _worker_rerun = True
            return
        _worker_running = True
        _worker_rerun = False

    def work() -> None:
        global _worker_running, _worker_rerun
        try:
            while True:
                time.sleep(_DEBOUNCE_S)
                try:
                    reconcile_interrupted(home)
                    triage.triage_pending(home, llm=llm, spawn=spawn, emit=broadcast, assign=assign)
                except Exception:  # noqa: BLE001
                    logger.debug("todo triage failed", exc_info=True)
                with _worker_lock:
                    if not _worker_rerun:
                        _worker_running = False
                        return
                    _worker_rerun = False
        except BaseException:
            with _worker_lock:
                _worker_running = False
            raise

    threading.Thread(target=work, name="todo-triage", daemon=True).start()
