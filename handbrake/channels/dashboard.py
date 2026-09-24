"""Localhost dashboard HTML (ADR-0010). Forms work with JS disabled. Stdlib only."""

from __future__ import annotations

import html

from handbrake.core import Handbrake


def render(hb: Handbrake, csrf: str) -> str:
    st = hb.state()
    halt = st["halt"]["level"] if st["halt"] else "-"
    rows = []
    for card in hb.pending_approvals():
        h = html.escape(card.action_hash)
        rows.append(
            "<li>"
            f"<code>{h[:12]}</code> {html.escape(card.tool)} "
            f"<form method='post' action='/dashboard/approve'>"
            f"<input type='hidden' name='csrf' value='{html.escape(csrf)}'>"
            f"<input type='hidden' name='action_hash' value='{h}'>"
            "<button type='submit'>approve</button></form> "
            f"<form method='post' action='/dashboard/deny'>"
            f"<input type='hidden' name='csrf' value='{html.escape(csrf)}'>"
            f"<input type='hidden' name='action_hash' value='{h}'>"
            "<button type='submit'>deny</button></form></li>"
        )
    tail = hb.audit_tail(8)
    audit = "".join(
        f"<li>{html.escape(r.kind)} {html.escape(str(r.payload)[:120])}</li>" for r in tail
    )
    pending = "".join(rows) or "<li>none</li>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>MITO</title></head>
<body>
<h1>MITO</h1>
<p>state={html.escape(str(st['metabolic_state']))}
 autonomy={html.escape(str(st['autonomy']))}
 halt={html.escape(str(halt))}
 atp={st['atp']['balance']:.0f}
 runway={html.escape(str(st['atp']['runway_days']))}
 unseen={st.get('mail_unseen', 0)}</p>
<h2>Halt</h2>
<form method="post" action="/dashboard/halt">
<input type="hidden" name="csrf" value="{html.escape(csrf)}">
<select name="level"><option>soft</option><option>hard</option><option>panic</option></select>
<button type="submit">halt</button>
</form>
<h2>Approvals</h2><ul>{pending}</ul>
<h2>Audit</h2><ul>{audit}</ul>
</body></html>
"""

