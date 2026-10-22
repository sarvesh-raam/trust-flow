"""Declaration chat: ownership, proposal validation, apply/dismiss flows."""
from __future__ import annotations

import pytest

from conftest import auth_headers
from test_api import _start_run, _status


@pytest.fixture
def fake_llm(monkeypatch):
    """Scripted assistant: tests set `plan` to the ChatPlan to return."""
    from routes import chat

    state = {"plan": chat.ChatPlan(reply="ok"), "calls": []}

    def fake_plan(context, editable, history, message):
        state["calls"].append({"context": context, "editable": editable, "history": history, "message": message})
        if isinstance(state["plan"], Exception):
            raise state["plan"]
        return state["plan"]

    monkeypatch.setattr(chat, "plan_chat_reply", fake_plan)
    return state


def _say(client, run_id, message, user="alice"):
    return client.post(f"/api/v1/workflow/chat/{run_id}", json={"message": message}, headers=auth_headers(user))


def _act(client, run_id, index, action, user="alice"):
    return client.post(
        f"/api/v1/workflow/chat/{run_id}/proposals/{index}", json={"action": action}, headers=auth_headers(user),
    )


def _plan(reply, *changes):
    from routes.chat import ChatPlan, ProposedChange

    return ChatPlan(reply=reply, proposed_changes=[ProposedChange(path=p, value=v) for p, v in changes])


def test_question_is_answered_and_history_is_kept(client, fake_docs, fake_llm):
    run_id, _ = _start_run(client)
    fake_llm["plan"] = _plan("The run passed with one warning.")

    r = _say(client, run_id, "Why is this a warning?")
    assert r.status_code == 200, r.text
    msgs = r.json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["proposal_status"] == "none"

    call = fake_llm["calls"][0]
    assert call["context"]["invoice"]["invoice_number"] == "INV-001"
    assert "line_items.0.hs_code" in call["editable"]
    assert not any(p.startswith("compliance") for p in call["editable"])

    history = client.get(f"/api/v1/workflow/chat/{run_id}", headers=auth_headers("alice")).json()["messages"]
    assert len(history) == 2


def test_chat_is_owner_only(client, fake_docs, fake_llm):
    run_id, _ = _start_run(client, "alice")
    assert client.get(f"/api/v1/workflow/chat/{run_id}", headers=auth_headers("mallory")).status_code == 404
    assert _say(client, run_id, "hi", user="mallory").status_code == 404
    assert not fake_llm["calls"]


def test_disallowed_or_invalid_proposals_are_dropped(client, fake_docs, fake_llm):
    run_id, _ = _start_run(client)
    fake_llm["plan"] = _plan(
        "Done.",
        ("compliance.status", "PASS"),
        ("bill_of_lading.gross_weight_kg", -5),
        ("invoice.buyer", "Globex International"),
    )
    msg = _say(client, run_id, "make it pass").json()["messages"][-1]
    assert [p["path"] for p in msg["proposals"]] == ["invoice.buyer"]
    assert msg["proposal_status"] == "pending"
    assert "left out" in msg["content"]


def test_applying_a_proposal_resolves_a_blocked_run(client, fake_docs, fake_llm):
    fake_docs.bl_weight = 1000
    run_id, _ = _start_run(client)
    assert _status(client, run_id)["status"] == "blocked"

    fake_llm["plan"] = _plan("I can set the B/L weight to 820 kg.", ("bill_of_lading.gross_weight_kg", "820"))
    _say(client, run_id, "set the BL weight to 820")

    r = _act(client, run_id, 1, "apply")
    assert r.status_code == 200, r.text
    assert r.json()["messages"][1]["proposal_status"] == "applied"
    st = _status(client, run_id)
    assert st["status"] == "completed", st["result"]
    assert st["result"]["declaration"]["bill_of_lading"]["gross_weight_kg"] == 820

    assert _act(client, run_id, 1, "apply").status_code == 404   # can't apply twice
    # The pipeline rewrote the result; the chat history is stored separately.
    history = client.get(f"/api/v1/workflow/chat/{run_id}", headers=auth_headers("alice")).json()["messages"]
    assert len(history) == 2


def test_editing_a_completed_run_revalidates_it(client, fake_docs, fake_llm):
    run_id, _ = _start_run(client)
    assert _status(client, run_id)["status"] == "completed"

    fake_llm["plan"] = _plan("Updated date.", ("invoice.date", "2024-03-15"))
    _say(client, run_id, "the invoice date is 15 March 2024")
    assert _act(client, run_id, 1, "apply").status_code == 200
    st = _status(client, run_id)
    assert st["status"] == "completed"
    assert st["result"]["declaration"]["invoice"]["date"] == "2024-03-15"

    # A change that creates a weight conflict blocks the run again.
    fake_llm["plan"] = _plan("Updated weight.", ("bill_of_lading.gross_weight_kg", 2000))
    _say(client, run_id, "BL weight is 2000")
    assert _act(client, run_id, 3, "apply").status_code == 200
    assert _status(client, run_id)["status"] == "blocked"


def test_dismissing_changes_nothing(client, fake_docs, fake_llm):
    run_id, _ = _start_run(client)
    fake_llm["plan"] = _plan("Proposed.", ("invoice.buyer", "Someone Else"))
    _say(client, run_id, "change buyer")
    r = _act(client, run_id, 1, "dismiss")
    assert r.json()["messages"][1]["proposal_status"] == "dismissed"
    assert _status(client, run_id)["result"]["declaration"]["invoice"]["buyer"] == "Globex LLC"


def test_applying_while_a_question_is_answered_is_not_lost(client, fake_docs, fake_llm, monkeypatch):
    """Saving a slow answer must not reset a proposal applied in the meantime."""
    from routes import chat

    run_id, _ = _start_run(client)
    fake_llm["plan"] = _plan("Proposed.", ("invoice.seller", "Acme Ltd"))
    _say(client, run_id, "change seller")

    def slow_answer(context, editable, history, message):
        # The user presses APPLY while the model is still answering.
        assert _act(client, run_id, 1, "apply").status_code == 200
        return chat.ChatPlan(reply="Here is the answer.")

    monkeypatch.setattr(chat, "plan_chat_reply", slow_answer)
    msgs = _say(client, run_id, "another question").json()["messages"]
    assert len(msgs) == 4
    assert msgs[1]["proposal_status"] == "applied"


def test_assistant_failure_returns_502_and_saves_nothing(client, fake_docs, fake_llm):
    run_id, _ = _start_run(client)
    fake_llm["plan"] = RuntimeError("groq down")
    assert _say(client, run_id, "hello").status_code == 502
    assert client.get(f"/api/v1/workflow/chat/{run_id}", headers=auth_headers("alice")).json()["messages"] == []
