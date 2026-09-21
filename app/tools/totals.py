"""Derived totals, by cause, computed in code from the engine's result, so a richer answer (a breakdown that adds up, "the other 9 come to ...") uses figures a tool returned.
Nothing here changes an amount the engine produced: it only adds and subtracts them."""
from __future__ import annotations

TOP_N = 3


def derived_totals(res: dict) -> dict:
    bill, a = res["bill"], res["amounts"]
    lines = bill["lines"]
    nm = sorted((l for l in lines if l["category"] == "non_medical"), key=lambda l: -l["billed"])
    rest = nm[TOP_N:]
    ded = a["deductions"]
    held = a["held_pending"]
    est = a["estimated_payable_if_docs_supplied"]
    out = dict(bill_total=a["gross_billed"],
               room_related_reduction=round(ded["room"] + ded["associated"], 2),          # the room and the doctor, theatre and nursing charges, reduced by the room-rent proportion
               non_medical_total=round(sum(l["billed"] for l in nm), 2), non_medical_count=len(nm),
               largest_non_medical_items=[dict(item=l["description"], amount=l["billed"]) for l in nm[:TOP_N]],
               other_non_medical_count=len(rest), other_non_medical_total=round(sum(l["billed"] for l in rest), 2),
               waiting_for_a_document_total=held, estimate_total=est,
               counted_so_far=a["payable_confirmed_now"])                                 # the estimate without what is waiting for a document (the engine's own figure, after any deductible)
    if bill["room_rule"]["type"] != "at_actuals" and bill["room_ratio"] < 1:
        out.update(room_limit_per_day=bill["room_limit_per_day"], billed_room_rate_per_day=bill["room_rate_per_day"], share_paid_percent=round(bill["room_ratio"] * 100, 1))
    return out
