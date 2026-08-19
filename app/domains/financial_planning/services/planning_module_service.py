"""The planning module — one turn of the customer's plan, whatever shape it takes.

Uniform module surface (``run(turn, ctx, prior)``), called only by
``ai_engine``'s ``flow_financial_planning``. It replaces the two module
services the old split intents each carried, and the shape of a turn is now the
same regardless of what the customer said:

    read the message  ->  stage what it asks for  ->  read it back
    ...  ->  they confirm  ->  write  ->  fire only the affected downstreams

Three invariants, and they are the reason this file is worth reading:

**Nothing is ever written directly.** A sentence in a conversation is a
proposal, not a form submission. Profile values are held on the open ask, goals
are held on a draft, and a deletion is held too — every write path goes through
a read-back and an explicit yes. That is not caution for its own sake: the
figures here drive the customer's whole projection, and a value we merely
inferred from prose is our reading of what they said until they agree it is
right.

**Downstream work is triggered by what changed, not by what was discussed.**
The commit hands its audit rows to ``downstream``, which re-scores risk only if
a risk input actually moved and retires the cached plan only if a plan input or
a goal actually moved. A turn that changed nothing runs nothing.

**Every customer-facing string goes through the shared answer formatter.** The
domain supplies facts and a body prompt; the deterministic fallbacks here are
what ships when the formatter fails, not the normal path.

Each of those three steps reports through ``planning_audit`` — the same
``chat_ai_module_runs`` surface every other AI module writes to, under
``module="financial_planning"``. Read, staged and write are separate rows on
purpose: the gap between staged and write is where a value that was understood
but never confirmed shows up, and that pair is how you tell "they declined"
from "we dropped it".
"""

from __future__ import annotations

import logging
from typing import Any

from app.domains.ai_engine.answer_formatter import (
    FormatterFailure,
    format_with_telemetry,
)
from app.domains.ai_engine.types import ModuleOutput
from app.domains.financial_planning.models.chat_goal_draft import (
    STAGE_ABANDONED,
    STAGE_COLLECTING,
    STAGE_COMMITTED,
    STAGE_CONFIRMING,
    STAGE_FOLLOW_UP,
)
from app.domains.financial_planning.models.chat_planning_ask import STATUS_CONFIRMING
from app.domains.financial_planning.services import (
    downstream,
    goal_builder,
    goal_ops,
    planning_audit as audit,
    planning_state as state,
    privacy,
    profile_ops,
)
from app.domains.financial_planning.services.plan_context import load_profile_context
from app.domains.financial_planning.services.planning_extractor import (
    PlanningRead,
    read_message,
)
from app.domains.profile.services.profile_completeness_service import (
    gaps_for_intent,
    next_field_to_ask,
)
from app.domains.profile.services.profile_field_registry import spec

logger = logging.getLogger(__name__)

MODULE_NAME = "financial_planning"

# The requirement row the projection is gated on. Named separately from the
# intent because the intent covers both CREATING a goal (which needs a cost and
# a date and nothing else) and TESTING the plan (which does need their income).
PROJECTION_REQUIREMENT = "financial_planning_projection"

# The ask row doubles as the staging area, so one has to exist before anything
# can be held — including when nothing was asked (a volunteered fact, a pending
# goal deletion). Those rows carry this instead of a registry key. Deliberately
# NOT a real field: borrowing one meant that deferring a goal-deletion
# confirmation quietly put that field into its 30-day quiet period.
STAGING_ONLY_KEY = "_staging"


# ---------------------------------------------------------------------------
# Reply composition
# ---------------------------------------------------------------------------

_BODY_PROMPT = """You are Pi, working on this customer's financial plan with them in conversation.

CUSTOMER_RECORD carries some of:
  - `situation`        : what is happening on this turn — read this first
  - `question`         : the exact question to put to them, or null
  - `noted`            : what we UNDERSTOOD but have NOT saved yet
  - `saved`            : what is now WRITTEN to their record
  - `removed`          : what has just been deleted from their record
  - `on_file`          : what we already hold about them
  - `never_ask_for`    : things in `on_file`. Asking for any of these is a bug, not a question
  - `their_record`     : values they asked us to read back
  - `their_goals`      : the goals on their plan
  - `still_needed`     : plain-English descriptions of what we do not know yet
  - `numbers`          : figures WE calculated for a goal
  - `assumptions`      : things we assumed on their behalf and they may correct
  - `affordability`    : what their existing savings and SIP reach by the goal date, against what the goal will cost then
  - `confirmed_unchanged` : things they told us have not changed
  - `blocked_capability`  : what they asked for that needed an input we do not have

The two words that must never be mixed up:
  - `noted` is held, pending their go-ahead. Say "I've got", "that's noted", "I have you at". NEVER say saved, stored, updated, recorded or locked in.
  - `saved` is actually written. Only then may you say saved or updated.
  If `saved` and `removed` are both empty, nothing has changed — do not imply otherwise in any form.

Rules:
  - Read back every figure in `noted` / `saved` / `numbers` / `their_record` EXACTLY as written. Never convert, re-round or re-derive; if it says a yearly figure, say the yearly figure.
  - A chip with a `basis` was worked out from something we already held ("20% increase on the ₹30,00,000 on file"). Say the basis in the same breath as the figure — it is how they catch it if we applied their change to the wrong number.
  - `question` is the ONLY thing you may ask. If `question` is null, ask NOTHING — end on the acknowledgement. Do not invent a next step, do not offer to do more, do not fish for other numbers.
  - Ask for EVERYTHING in `still_needed` as ONE flowing question, never a list and never several questions in sequence.
  - NEVER ask for anything in `never_ask_for` or present in `on_file`. Asking a customer for something we already store reads as not listening.
  - If `confirmed_unchanged` is non-empty, acknowledge it in the same breath and NEVER ask about those fields again.
  - When `numbers` is present, walk through them in the order given and then ask if they want it added. When `assumptions` is present, name the assumption plainly and invite a correction in the same breath.
  - When `affordability` is present and a loan is what you are about to ask about, LEAD with what it says. If `current_plan_covers_it` is true, tell them their existing savings and SIP already get there and ask whether they still want to finance it — plenty of people borrow by choice. Never ask a bare "will you take a loan?" when you already know whether they need one.
  - When `removed` is present, say plainly what is gone and that they can ask you to put it back.
  - Say briefly WHY something matters, tied to what they actually asked for. One clause.
  - `on_file.first_name` is their first name. Use it where it lands naturally — opening a thread, marking a decision, delivering good or awkward news — and NOT in every reply, which reads like mail merge.

Voice: warm, direct, specific. Short sentences. Two or three of them unless walking through `numbers`. No preamble, no apology, no emojis and no decorative symbols of any kind. No bullet lists unless walking through `numbers` or `their_goals`. Never say something has been added, saved or removed unless the record above says it was.
"""


async def _compose(ctx, facts: dict[str, Any], fallback: str) -> str:
    try:
        return await format_with_telemetry(
            ctx=ctx,
            facts_pack=facts,
            body_prompt=_BODY_PROMPT,
            module_name=MODULE_NAME,
            action_mode="gather",
            profile={},
            build_fallback=lambda: fallback,
        )
    except FormatterFailure:
        return fallback
    except Exception:
        logger.exception("financial_planning formatter failed; using fallback")
        return fallback


def _noted_line(chips: list[dict[str, str]]) -> str:
    if not chips:
        return "Noted."
    body = ", ".join(c["label"].lower() + " " + c["display_value"] for c in chips)
    return "So far I have " + body + "."


# ---------------------------------------------------------------------------
# Staging area — one shape for both halves of the domain
# ---------------------------------------------------------------------------
#
# Held on the open ask's ``staged_values``. Fields and pending deletions live
# under separate keys rather than side by side, so a registry key can never
# collide with a bookkeeping one.


def _fields(staged: dict[str, Any] | None) -> dict[str, Any]:
    return dict((staged or {}).get("fields") or {})


def _deletes(staged: dict[str, Any] | None) -> list[dict[str, str]]:
    return list((staged or {}).get("goal_deletes") or [])


def _staging(
    fields: dict[str, Any] | None = None, deletes: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if fields:
        out["fields"] = fields
    if deletes:
        out["goal_deletes"] = deletes
    return out


def _has_staged(staged: dict[str, Any] | None) -> bool:
    return bool(_fields(staged) or _deletes(staged))


# ---------------------------------------------------------------------------
# Committing — the only place anything is written
# ---------------------------------------------------------------------------


async def _commit_everything(ctx, ask, draft) -> tuple[dict[str, Any], list[Any]]:
    """Write everything the customer has just agreed to, and return the facts.

    One function for the whole commit so that the audit rows from all three
    kinds of change — fields, goal deletions, and the goal being built — reach
    ``downstream`` together. Firing per-change would re-score risk twice for a
    turn that moved two risk inputs.
    """
    facts: dict[str, Any] = {}
    writes: list[Any] = []

    staged = (ask.staged_values if ask is not None else None) or {}

    fields = _fields(staged)
    if fields:
        saved, field_writes = await profile_ops.commit(
            ctx.db,
            user_id=ctx.effective_user_id,
            session_id=ctx.session_id,
            ask_id=(ask.id if ask is not None else None),
            staged=fields,
        )
        if saved:
            facts["saved"] = saved
        writes.extend(field_writes)

    removed: list[dict[str, str]] = []
    for pending in _deletes(staged):
        goals = await goal_ops.active_goals(ctx.db, ctx.effective_user_id)
        target = next((g for g in goals if str(g.id) == pending.get("goal_id")), None)
        if target is None:
            continue
        name = target.display_name
        _, write = await goal_ops.delete_goal(
            ctx.db,
            user_id=ctx.effective_user_id,
            session_id=ctx.session_id,
            goal=target,
            verbatim=pending.get("verbatim"),
        )
        writes.append(write)
        removed.append({"goal": name})
    if removed:
        facts["removed"] = removed

    if draft is not None and draft.stage == STAGE_CONFIRMING:
        goal, proj, goal_writes = await goal_builder.commit(ctx, draft)
        if goal is not None:
            writes.extend(goal_writes)
            facts["saved_goal"] = {
                "goal": goal.display_name,
                "you_need_to_save": goal_builder.money(proj.get("corpus_required")),
                "by": proj.get("target_date"),
            }
            if proj.get("financed"):
                facts["caveat"] = (
                    "the plan saves for the down payment; the EMI is not yet "
                    "folded into future surplus"
                )

    if ask is not None:
        await state.stage_values(ctx.db, ask, {})
        await state.mark_answered(ctx.db, ask)

    return facts, writes


# ---------------------------------------------------------------------------
# The turn
# ---------------------------------------------------------------------------


async def run(turn, ctx, prior: dict[str, ModuleOutput]) -> ModuleOutput:
    """Advance the customer's plan by one turn.

    ``ctx.planning_directive`` carries the gate's decision — an open question,
    an open goal draft, a field to ask for, or nothing at all.
    """
    directive = getattr(ctx, "planning_directive", None)
    ask = getattr(directive, "pending_ask", None)
    resume_intent = getattr(directive, "resume_intent", None)
    ask_field_key = getattr(directive, "field_key", None)

    draft = await state.get_open_draft(ctx.db, ctx.session_id)
    if draft is not None and draft.stage == STAGE_FOLLOW_UP:
        # The goal is saved and we asked the one follow-up. Keep the thread.
        pass

    profile = load_profile_context(getattr(ctx, "user_ctx", None))
    snapshot = await profile_ops.snapshot(ctx.db, ctx.effective_user_id)
    goals = await goal_ops.active_goals(ctx.db, ctx.effective_user_id)

    # A field we were told to ask for and have not asked yet: open the question
    # before reading the message, so this turn's answer has something to land on.
    if ask is None and ask_field_key:
        return await _open_ask(turn, ctx, ask_field_key, resume_intent)

    read = await read_message(
        utterance=turn.user_question,
        current_values=snapshot.values,
        asked_field_key=(ask.field_key if ask is not None else None),
        goal_names_on_file=goal_ops.goal_names(goals),
        draft_summary=_draft_summary(draft),
        awaiting=_awaiting(ask, draft),
        history=ctx.conversation_history,
    )

    await audit.log_read(ctx, read, ask=ask, draft=draft)

    if read.failed:
        # An LLM blip must not cost the turn. With nothing open, let the flow
        # run the projection — which is what most planning questions want.
        if ask is None and draft is None:
            return ModuleOutput(text="", side_effects={"run_projection": True})
        return await _reask(ctx, ask, draft, profile)

    return await _handle(turn, ctx, read, ask, draft, profile, snapshot, goals, resume_intent)


async def _handle(
    turn, ctx, read: PlanningRead, ask, draft, profile, snapshot, goals, resume_intent
) -> ModuleOutput:
    staged = (ask.staged_values if ask is not None else None) or {}

    # ---- they backed out --------------------------------------------------
    if read.is_cancel and draft is not None:
        await state.set_draft_stage(ctx.db, draft, STAGE_ABANDONED)
        if ask is not None:
            await state.stage_values(ctx.db, ask, {})
        return ModuleOutput(
            text=await _compose(
                ctx,
                {"situation": "They dropped the goal we were building. Nothing was saved.",
                 "question": None},
                "No problem — I've dropped that one. Tell me whenever you want to pick it back up.",
            ),
            side_effects={"planning_cancelled": True},
        )

    # ---- "later" / "why do you need that" ---------------------------------
    # Durable, not a pause. Anything staged is dropped: backing out means
    # backing out of the whole thing.
    if read.is_defer and ask is not None:
        await state.stage_values(ctx.db, ask, {})
        await state.mark_skipped(ctx.db, ask)
        return ModuleOutput(
            text=await _compose(
                ctx,
                {
                    "situation": (
                        "The customer declined. Acknowledge briefly, do not ask "
                        "again, and tell them they can add it from their profile "
                        "later. Nothing was saved."
                    ),
                    "question": None,
                    "blocked_capability": ask.resume_intent,
                },
                "No problem — I haven't saved anything. You can always add it from your profile.",
            ),
            side_effects={"planning_deferred": ask.field_key},
        )

    # ---- a yes to something we read back ----------------------------------
    waiting_on_yes = (ask is not None and ask.status == STATUS_CONFIRMING) or (
        draft is not None and draft.stage == STAGE_CONFIRMING
    )
    if read.is_confirm and waiting_on_yes:
        return await _confirm(ctx, ask, draft, resume_intent)

    # ---- a no to something we read back -----------------------------------
    if read.is_reject and waiting_on_yes and not read.operations:
        if ask is not None:
            await state.stage_values(ctx.db, ask, {})
            await state.bump_attempt(ctx.db, ask)
        if draft is not None and draft.stage == STAGE_CONFIRMING:
            await state.set_draft_stage(ctx.db, draft, STAGE_COLLECTING)
        return ModuleOutput(
            text=await _compose(
                ctx,
                {
                    "situation": "They said that was wrong. Nothing was saved. Ask what it should be.",
                    "question": "What should it be?",
                },
                "Sorry — nothing saved. What should it be?",
            ),
        )

    # ---- they changed the subject -----------------------------------------
    # The gate routes an open thread here whatever the classifier said, because
    # a bare fragment carries no topic. Now that the message has been READ, with
    # the thread in view, we can tell the difference — and a message with
    # nothing about the plan in it belongs to whoever can answer it.
    if read.kind == "unrelated" and not read.operations and not waiting_on_yes:
        return ModuleOutput(text="", side_effects={"handoff": True})

    # ---- the post-commit follow-up ----------------------------------------
    if draft is not None and draft.stage == STAGE_FOLLOW_UP:
        answered = await _follow_up(ctx, read, draft)
        if answered is not None:
            return answered

    # ---- read-only: what do you have on file? -----------------------------
    reads = [o for o in read.operations if o.verb == "read"]
    if reads and not any(o.writes for o in read.operations):
        return await _read_back(ctx, reads, snapshot, goals)

    # ---- goal deletions ---------------------------------------------------
    deletions = [o for o in read.operations if o.is_goal and o.verb == "delete"]

    # ---- profile changes --------------------------------------------------
    profile_ops_in = [
        o for o in read.operations if o.is_profile and o.verb in ("set", "adjust", "clear")
    ]
    fields, chips, rejected = profile_ops.stage(
        profile_ops_in,
        asked_key=(ask.field_key if ask is not None else None),
        already_staged=_fields(staged),
    )

    # ---- goal creation / edit ---------------------------------------------
    goal_ops_in = [
        o for o in read.operations if o.is_goal and o.verb in ("create", "update")
    ]
    if goal_ops_in or (draft is not None and draft.stage in (STAGE_COLLECTING, STAGE_CONFIRMING)):
        merged_slots: dict[str, Any] = dict(draft.slots or {}) if draft is not None else {}
        ambiguous_ref: str | None = None
        for op in goal_ops_in:
            if op.verb == "update" and draft is None:
                target = goal_ops.resolve_ref(goals, op.goal_ref)
                if target is None and goals:
                    ambiguous_ref = op.goal_ref or "that goal"
                    continue
                if target is not None:
                    draft = await state.create_draft(
                        ctx.db,
                        session_id=ctx.session_id,
                        user_id=ctx.effective_user_id,
                        origin_question=turn.user_question,
                        editing_goal_id=target.id,
                    )
                    merged_slots = goal_builder.slots_from_goal(target)
            merged_slots.update(op.slots or {})

        if ambiguous_ref and draft is None:
            return await _which_goal(ctx, goals, ambiguous_ref)

        if draft is None and goal_ops_in:
            draft = await state.create_draft(
                ctx.db,
                session_id=ctx.session_id,
                user_id=ctx.effective_user_id,
                origin_question=turn.user_question,
            )

        if draft is not None:
            return await _advance_goal(
                ctx, draft, merged_slots, profile, ask, fields, chips, read
            )

    # ---- nothing to change, and they asked about the plan -----------------
    if not fields and not deletions and not read.clarification:
        if read.wants_projection or (not read.operations and ask is None):
            return ModuleOutput(text="", side_effects={"run_projection": True})

    # ---- an ambiguous figure is a question, never a guess ------------------
    if read.clarification:
        ask = await _ensure_ask(ctx, turn, ask, read, resume_intent)
        await state.stage_values(ctx.db, ask, _staging(fields, _deletes(staged)))
        await audit.log_staged(ctx, fields=fields, deletes=_deletes(staged))
        return ModuleOutput(
            text=await _compose(
                ctx,
                {
                    "situation": (
                        "One figure is ambiguous. Nothing is saved. Note what IS "
                        "clear in one clause, then ask the clarifying question "
                        "and nothing else."
                    ),
                    "question": read.clarification,
                    "noted": profile_ops.staged_chips(fields),
                },
                read.clarification,
            ),
            side_effects={"planning_noted": profile_ops.staged_chips(fields)},
        )

    # ---- "everything else is the same" ------------------------------------
    if not fields and not deletions and read.unchanged:
        return await _advance_or_confirm(
            ctx, ask, fields, [], read.unchanged, resume_intent, rejected
        )

    # ---- a relative change with nothing to apply it to ---------------------
    no_baseline = [u for u in read.unread if u.reason == "no_baseline"]
    if no_baseline and not fields:
        return await _need_baseline(ctx, turn, ask, no_baseline[0], resume_intent)

    # ---- stage the deletion, never perform it ------------------------------
    if deletions:
        return await _stage_deletion(ctx, turn, ask, deletions, goals, fields, chips)

    # ---- nothing usable came back ------------------------------------------
    if not fields and ask is not None:
        await state.bump_attempt(ctx.db, ask)
        fs = spec(ask.field_key)
        still_open = ask.status != "cancelled"
        return ModuleOutput(
            text=await _compose(
                ctx,
                {
                    "situation": (
                        "The customer replied but did not answer the question. "
                        "Ask it once more, plainly."
                        if still_open
                        else "They have not answered after two tries. Move on "
                        "without asking again."
                    ),
                    "question": (fs.question if (fs and still_open) else None),
                },
                (fs.question if (fs and still_open) else "No problem — let's move on."),
            ),
        )

    if not fields:
        return ModuleOutput(text="", side_effects={"run_projection": True})

    ask = await _ensure_ask(ctx, turn, ask, read, resume_intent)
    await state.stage_values(ctx.db, ask, _staging(fields, _deletes(staged)))
    await audit.log_staged(ctx, fields=fields, deletes=_deletes(staged))
    return await _advance_or_confirm(
        ctx, ask, fields, chips, read.unchanged, resume_intent, rejected
    )


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


async def _confirm(ctx, ask, draft, resume_intent) -> ModuleOutput:
    """They said yes. Write, then fire only what the write actually affected."""
    facts, writes = await _commit_everything(ctx, ask, draft)

    changes = downstream.changes_from_writes(writes)
    report = await downstream.fire(ctx.db, ctx.effective_user_id, changes)

    side: dict[str, Any] = {}
    if facts.get("saved"):
        side["planning_saved"] = facts["saved"]
    if facts.get("saved_goal"):
        side["goal_saved"] = facts["saved_goal"]
    if facts.get("removed"):
        side["goal_removed"] = facts["removed"]

    # The one call we make BECAUSE something changed: a plan input or a goal
    # moved, so the verdict they were given no longer holds and we owe them the
    # new one. If nothing that feeds the projection changed, we do not run it.
    projection_queued = downstream.plan_inputs_changed(changes)
    if projection_queued:
        side["run_projection"] = True
        side["run_projection_reason"] = ", ".join(report.fired) or "plan inputs changed"

    await audit.log_write(
        ctx, writes=writes, report=report, projection_queued=projection_queued
    )

    resume = resume_intent or (ask.resume_intent if ask is not None else None)
    if resume and not side.get("run_projection"):
        gaps = gaps_for_intent(resume, await profile_ops.snapshot(ctx.db, ctx.effective_user_id))
        if not gaps.blocked:
            side["resume_intent"] = resume
            side["resume_question"] = ask.origin_question if ask is not None else None
            return ModuleOutput(text="", side_effects=side)

    facts["situation"] = (
        "They confirmed, and it is now written to their record. Say what changed "
        "and stop."
    )
    facts["question"] = None
    fallback = "Saved."
    if facts.get("saved_goal"):
        fallback = f"{facts['saved_goal']['goal']} is on your plan."
    elif facts.get("removed"):
        fallback = "Removed from your plan."

    if side.get("run_projection"):
        # The reply is written by the projection at the end of the flow, so this
        # turn produces the saved chips and nothing else.
        return ModuleOutput(text="", side_effects=side)

    return ModuleOutput(text=await _compose(ctx, facts, fallback), side_effects=side)


async def _advance_goal(
    ctx, draft, merged_slots, profile, ask, fields, chips, read
) -> ModuleOutput:
    """Move the goal conversation on, carrying any profile facts alongside it."""
    stage, facts, proj = await goal_builder.advance(ctx.db, draft, merged_slots, profile)
    if fields and ask is not None:
        await state.stage_values(
            ctx.db, ask, _staging(fields, _deletes(ask.staged_values))
        )
    if chips:
        facts["noted"] = chips
    facts["question"] = (
        "Shall I add this to your goals?"
        if stage == "confirming"
        else None  # the formatter asks for `still_needed` as one question
    )
    fallback = (
        goal_builder.fallback_summary(proj)
        if proj is not None
        else goal_builder.fallback_ask(
            goal_builder.missing_slots(merged_slots, profile), merged_slots, profile
        )
    )
    return ModuleOutput(
        text=await _compose(ctx, facts, fallback),
        side_effects={"goal_draft_id": str(draft.id)},
    )


async def _stage_deletion(ctx, turn, ask, deletions, goals, fields, chips) -> ModuleOutput:
    """Hold the deletion and read it back. A goal is never removed on one line."""
    pending: list[dict[str, str]] = []
    unmatched: list[str] = []
    for op in deletions:
        target = goal_ops.resolve_ref(goals, op.goal_ref)
        if target is None:
            unmatched.append(op.goal_ref or "that goal")
            continue
        pending.append(
            {
                "goal_id": str(target.id),
                "name": target.display_name,
                "verbatim": op.verbatim or "",
            }
        )

    if not pending:
        return await _which_goal(ctx, goals, unmatched[0] if unmatched else "that goal")

    ask = await _ensure_ask(
        ctx, turn, ask, None, None, field_key_hint=STAGING_ONLY_KEY
    )
    await state.stage_values(ctx.db, ask, _staging(fields, pending))
    await state.mark_confirming(ctx.db, ask)
    await audit.log_staged(ctx, fields=fields, deletes=pending)

    names = ", ".join(p["name"] for p in pending)
    return ModuleOutput(
        text=await _compose(
            ctx,
            {
                "situation": (
                    "They asked to remove a goal. It is NOT removed yet — name it "
                    "back to them and ask for a yes, and say in one clause what "
                    "removing it changes for the rest of the plan."
                ),
                "question": f"Shall I remove {names} from your plan?",
                "noted": chips,
                "their_goals": [goal_ops.summarise(g) for g in goals],
            },
            f"Just to check — shall I remove {names} from your plan?",
        ),
        side_effects={"planning_pending_delete": pending},
    )


async def _which_goal(ctx, goals, ref: str) -> ModuleOutput:
    """We could not tell which goal they meant. Ask; never pick."""
    return ModuleOutput(
        text=await _compose(
            ctx,
            {
                "situation": (
                    "We could not tell which goal they meant, and guessing would "
                    "change the wrong one. List what they have and ask which."
                ),
                "question": f"Which one did you mean by {ref}?",
                "their_goals": [goal_ops.summarise(g) for g in goals],
            },
            f"Which one did you mean by {ref}?",
        ),
    )


async def _read_back(ctx, reads, snapshot, goals) -> ModuleOutput:
    """Answer "what do you have on file?" — no write, no projection."""
    facts: dict[str, Any] = {
        "situation": (
            "They asked what we hold. Read it back plainly and stop. Nothing is "
            "being changed."
        ),
        "question": None,
    }
    field_keys = [o.field_key for o in reads if o.is_profile and o.field_key]
    if field_keys:
        facts["their_record"] = profile_ops.read_fields(snapshot, field_keys)
    if any(o.is_goal for o in reads):
        facts["their_goals"] = [goal_ops.summarise(g) for g in goals] or []
    fallback = "Here's what I have on file for you."
    if facts.get("their_record"):
        fallback = "I have " + ", ".join(
            f"{c['label'].lower()} {c['display_value']}" for c in facts["their_record"]
        ) + "."
    return ModuleOutput(text=await _compose(ctx, facts, fallback))


async def _follow_up(ctx, read: PlanningRead, draft) -> ModuleOutput | None:
    """After a goal lands we ask the one thing that could change the verdict."""
    changed = None
    for op in read.operations:
        if op.is_goal and (op.slots or {}).get("sip_change") is not None:
            changed = bool(op.slots["sip_change"])
            break
    if changed is None and not read.operations and read.kind in ("state", "confirm"):
        changed = False
    if changed is None:
        # They said something else entirely — close the thread and let the rest
        # of the turn handle it rather than forcing a yes/no.
        await state.set_draft_stage(ctx.db, draft, STAGE_COMMITTED)
        return None

    await state.set_draft_stage(ctx.db, draft, STAGE_COMMITTED)
    return ModuleOutput(
        text=await _compose(
            ctx,
            {
                "situation": (
                    "They answered whether anything else has changed since the "
                    "goal was added."
                ),
                "question": (
                    "What are the new numbers?" if changed else None
                ),
                "confirmed_unchanged": ([] if changed else ["their income, expenses and SIP"]),
            },
            (
                "Got it — tell me the new numbers and I'll re-run the plan."
                if changed
                else "Perfect. Your plan is up to date, so there's nothing more to do right now."
            ),
        ),
        side_effects={"planning_followup_done": True},
    )


async def _need_baseline(ctx, turn, ask, unread, resume_intent) -> ModuleOutput:
    """They gave a percentage change and we have nothing to apply it to."""
    fs = spec(unread.field_key or "")
    question = (
        "I don't have a figure on file to apply that to — what is it now?"
        if fs is None
        else f"I don't have your {profile_ops.short_label(fs).lower()} on file yet — what is it now?"
    )
    ask = await _ensure_ask(
        ctx, turn, ask, None, resume_intent, field_key_hint=unread.field_key
    )
    return ModuleOutput(
        text=await _compose(
            ctx,
            {
                "situation": (
                    "They described a change as a percentage or a difference, but "
                    "we hold no starting figure, so there is nothing to apply it "
                    "to. Say that plainly and ask for the figure itself. Do not "
                    "guess a starting point."
                ),
                "question": question,
            },
            question,
        ),
    )


async def _advance_or_confirm(
    ctx, ask, fields, chips, unchanged, resume_intent, rejected
) -> ModuleOutput:
    """Ask the next required field, or read everything back for a yes.

    The confirmation covers the WHOLE conversation, not each field in turn.
    Asking "shall I save that?" after every single answer turns a chat back into
    a form with extra steps; asking once, at the point where we have what we
    need, is the same safety with none of the friction.
    """
    resume = resume_intent or (ask.resume_intent if ask is not None else None)
    all_chips = profile_ops.staged_chips(fields)

    if resume:
        snapshot = await profile_ops.snapshot(ctx.db, ctx.effective_user_id)
        gaps = gaps_for_intent(resume, snapshot)
        outstanding = [k for k in gaps.hard_missing if k not in fields]
        quiet = await state.deferred_field_keys(ctx.db, ctx.effective_user_id)
        nxt = next_field_to_ask([k for k in outstanding if k not in quiet])
        used = await state.asks_this_session(ctx.db, ctx.session_id)
        if nxt is not None and used < state.MAX_ASKS_PER_SESSION:
            await state.mark_answered(ctx.db, ask)
            new_ask = await state.open_ask(
                ctx.db,
                session_id=ctx.session_id,
                user_id=ctx.effective_user_id,
                field_key=nxt.key,
                resume_intent=resume,
                ask_kind="hard",
                origin_question=ask.origin_question if ask is not None else None,
            )
            # Staged values belong to the CONVERSATION, not to the question that
            # happened to surface them, so they carry forward.
            await state.stage_values(ctx.db, new_ask, _staging(fields, []))
            return ModuleOutput(
                text=await _compose(
                    ctx,
                    {
                        "situation": (
                            "We have noted what they just said but NOT saved it. "
                            "Acknowledge it in one clause, then ask for the next "
                            "thing we need. Never say anything has been saved."
                        ),
                        "question": nxt.question,
                        "noted": all_chips,
                        "confirmed_unchanged": _unchanged_labels(unchanged),
                        "blocked_capability": resume,
                    },
                    _noted_line(all_chips) + " " + nxt.question,
                ),
                side_effects={"planning_noted": all_chips},
            )

    if ask is not None:
        await state.mark_confirming(ctx.db, ask)
    facts: dict[str, Any] = {
        "situation": (
            "Read back everything in `noted` as what we UNDERSTOOD, not as "
            "something stored, and ask them to confirm before we save it."
        ),
        "question": "Shall I save that to your record?",
        "noted": all_chips,
        "confirmed_unchanged": _unchanged_labels(unchanged),
    }
    if rejected:
        facts["could_not_read"] = rejected
    if resume:
        facts["blocked_capability"] = resume
    return ModuleOutput(
        text=await _compose(
            ctx, facts, _noted_line(all_chips) + " Shall I save that to your record?"
        ),
        side_effects={"planning_noted": all_chips},
    )


async def _open_ask(turn, ctx, field_key, resume_intent) -> ModuleOutput:
    """Spend this turn asking for one thing the engine cannot run without."""
    fs = spec(field_key)
    if fs is None:
        return ModuleOutput(text="", side_effects={"run_projection": True})

    await state.open_ask(
        ctx.db,
        session_id=ctx.session_id,
        user_id=ctx.effective_user_id,
        field_key=field_key,
        resume_intent=resume_intent,
        ask_kind="hard",
        origin_question=turn.user_question,
    )
    logger.info(
        "financial_planning asking %s (resume=%s) session=%s",
        field_key,
        resume_intent,
        ctx.session_id,
    )
    return ModuleOutput(
        text=await _compose(
            ctx,
            {
                "situation": (
                    "The customer asked something that needs this input before it "
                    "can be answered on their real numbers."
                ),
                "question": fs.question,
                "blocked_capability": resume_intent,
            },
            fs.question,
        ),
        side_effects={"planning_asked": fs.key},
    )


async def _reask(ctx, ask, draft, profile) -> ModuleOutput:
    """The extractor failed and something is open. Ask again rather than stall."""
    if ask is not None:
        fs = spec(ask.field_key)
        question = fs.question if fs else "Could you say that again?"
    else:
        missing = goal_builder.missing_slots(dict(draft.slots or {}), profile)
        question = goal_builder.fallback_ask(missing, dict(draft.slots or {}), profile)
    return ModuleOutput(
        text=await _compose(
            ctx,
            {
                "situation": "We could not read that message. Ask again, plainly.",
                "question": question,
            },
            question,
        )
    )


async def _ensure_ask(
    ctx, turn, ask, read, resume_intent, *, field_key_hint: str | None = None
):
    """An ask row is the staging area, so one has to exist before anything is held.

    When the customer volunteered a fact nobody asked for, the row is opened
    against whichever field they talked about — it is bookkeeping for the
    staging, not a question we are putting to them.
    """
    if ask is not None:
        return ask
    field_key = field_key_hint
    if field_key is None and read is not None:
        first = next((o for o in read.operations if o.is_profile and o.field_key), None)
        field_key = first.field_key if first else None
    return await state.open_ask(
        ctx.db,
        session_id=ctx.session_id,
        user_id=ctx.effective_user_id,
        field_key=field_key or STAGING_ONLY_KEY,
        resume_intent=resume_intent,
        ask_kind="soft",
        origin_question=privacy.redact(turn.user_question)[:1000],
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _unchanged_labels(unchanged: list[str] | None) -> list[str]:
    return [(spec(k).question if spec(k) else k) for k in (unchanged or [])]


def _draft_summary(draft) -> str | None:
    if draft is None:
        return None
    slots = draft.slots or {}
    parts = [f"{k}={v!r}" for k, v in slots.items() if v is not None]
    return f"stage={draft.stage}; " + ", ".join(parts) if parts else f"stage={draft.stage}"


def _awaiting(ask, draft) -> str | None:
    """Tell the reader what we just asked, so a bare fragment has an antecedent.

    Without this, "no, everything's the same" is unanchored and the follow-up
    silently falls through to the projection instead of being answered.
    """
    if draft is not None and draft.stage == STAGE_FOLLOW_UP:
        return (
            "whether they want to change their monthly SIP for this goal, or "
            "whether their income or expenses have changed. A plain no / "
            "'everything is the same' means sip_change=false."
        )
    if draft is not None and draft.stage == STAGE_CONFIRMING:
        return "whether the numbers we showed them are right and we should add the goal"
    if ask is not None and ask.status == STATUS_CONFIRMING:
        return "whether the figures we read back to them are right and we should save them"
    if ask is not None:
        fs = spec(ask.field_key)
        return fs.question if fs else None
    return None


__all__ = ["MODULE_NAME", "PROJECTION_REQUIREMENT", "run"]
