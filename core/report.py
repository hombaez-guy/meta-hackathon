"""Renders a Report to one printable HTML page. Technical, calm, no clutter."""

from __future__ import annotations

from jinja2 import Environment, select_autoescape

from core.digest import one_line
from core.schemas import Report

TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Foreman report {{ r.report_id }}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root{--ink:#1a1d21;--muted:#6b7280;--line:#e5e7eb;--bg:#fff;--chip:#f3f4f6;
        --ok:#15803d;--warn:#b45309;--crit:#b91c1c;--okbg:#ecfdf5;--warnbg:#fffbeb;--critbg:#fef2f2;--accent:#1d4ed8}
  @media (prefers-color-scheme:dark){:root{--ink:#e5e7eb;--muted:#9ca3af;--line:#2a2f36;--bg:#0f1115;--chip:#1a1e25;
        --okbg:#052e1a;--warnbg:#2d1d05;--critbg:#2d0a0a;--ok:#4ade80;--warn:#fbbf24;--crit:#f87171;--accent:#60a5fa}}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Inter,sans-serif}
  .page{max-width:820px;margin:0 auto;padding:28px 24px}
  header{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;border-bottom:1px solid var(--line);padding-bottom:14px}
  h1{font-size:18px;margin:0 0 2px;letter-spacing:.2px}
  .meta{color:var(--muted);font-size:12px}
  .mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
  .status{font-weight:700;font-size:12px;letter-spacing:.6px;text-transform:uppercase;padding:6px 10px;border-radius:6px;white-space:nowrap}
  .status.healthy{color:var(--ok);background:var(--okbg)} .status.warning{color:var(--warn);background:var(--warnbg)} .status.critical{color:var(--crit);background:var(--critbg)}
  .headline{font-size:16px;font-weight:600;margin:16px 0 6px}
  p{margin:0 0 8px}
  .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:14px 0}
  .tile{border:1px solid var(--line);border-radius:8px;padding:10px 12px}
  .tile .k{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
  .tile .v{font-size:22px;font-weight:600;margin-top:2px}
  .bar{height:5px;background:var(--chip);border-radius:3px;margin-top:8px;overflow:hidden}
  .bar i{display:block;height:100%;background:var(--ok)} .bar i.w{background:var(--warn)} .bar i.c{background:var(--crit)}
  .notes{display:grid;grid-template-columns:1fr 1fr;gap:10px 20px;font-size:13px;color:var(--muted);margin:4px 0 14px}
  .notes b{color:var(--ink);font-weight:600}
  h2{font-size:12px;letter-spacing:.6px;text-transform:uppercase;color:var(--muted);margin:18px 0 8px}
  .finding{border:1px solid var(--line);border-left:3px solid var(--line);border-radius:8px;padding:10px 12px;margin-bottom:8px}
  .finding.critical,.finding.high{border-left-color:var(--crit)} .finding.medium{border-left-color:var(--warn)} .finding.low,.finding.info{border-left-color:var(--ok)}
  .fh{display:flex;justify-content:space-between;gap:10px;align-items:baseline}
  .fh b{font-size:14px} .sev{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:var(--muted)}
  .ev{font-size:13px;margin-top:4px} .ev span{color:var(--muted)}
  .fix{margin-top:8px;padding:8px 10px;background:var(--chip);border-radius:6px;font-size:13px}
  .fix .t{font-weight:600} .fix ol{margin:6px 0 0 18px;padding:0} .fix li{margin:2px 0}
  .fix code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}
  .fix .sub{color:var(--muted);margin-top:4px}
  .state{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;padding:2px 6px;border-radius:4px;background:var(--bg);border:1px solid var(--line)}
  .state.applied{color:var(--ok)} .state.failed{color:var(--crit)} .state.rejected{color:var(--muted)}
  .clear{border:1px solid var(--line);background:var(--okbg);color:var(--ok);padding:12px 14px;border-radius:8px;font-weight:600}
  footer{margin-top:18px;border-top:1px solid var(--line);padding-top:8px;color:var(--muted);font-size:11px;display:flex;justify-content:space-between}
  @media print{.page{padding:0} body{font-size:12.5px}}
</style></head><body><div class="page">
<header>
  <div><h1>Foreman daily report</h1>
    <div class="meta">Server <span class="mono">{{ r.server }}</span> · {{ r.created_at[:16].replace('T',' ') }} UTC · <span class="mono">{{ r.report_id }}</span></div></div>
  <div class="status {{ v.overall_status }}">{{ v.overall_status }}</div>
</header>

<div class="headline">{{ v.headline }}</div>
<p>{{ v.summary }}</p>

<div class="grid">
  {% for k,val in [('CPU', r.chip.cpu_percent),('Memory', r.chip.memory_percent),('Disk', r.chip.disk_percent)] %}
  <div class="tile"><div class="k">{{ k }}</div><div class="v">{{ '%.0f'|format(val) }}%</div>
    <div class="bar"><i class="{{ 'c' if val>90 else 'w' if val>75 else '' }}" style="width:{{ [val,100]|min }}%"></i></div></div>
  {% endfor %}
</div>
<div class="notes">
  <div><b>Metrics (Chip)</b> · {{ v.metrics_note or r.chip.summary }}</div>
  <div><b>Logs (Chuck)</b> · {{ v.logs_note or (chuck_line if r.chuck else 'Not reviewed this run') }}</div>
  <div style="grid-column:1/-1"><b>Trend</b> · {{ v.trend_note or 'No history in the window.' }}</div>
</div>

{% if v.findings %}
<h2>Findings ({{ v.findings|length }})</h2>
{% for f in v.findings %}
<div class="finding {{ f.severity }}">
  <div class="fh"><b>{{ f.title }}</b><span class="sev">{{ f.severity }} · {{ f.source }}</span></div>
  {% if f.trend %}<div class="ev"><span>Trend</span> {{ f.trend }}</div>{% endif %}
  <div class="ev"><span>Evidence</span> {{ f.evidence }}</div>
  <div class="ev"><span>If ignored</span> {{ f.impact }}</div>
  {% if f.fix %}
  <div class="fix">
    <div class="fh"><span class="t">Proposed fix: {{ f.fix.title }}</span>
      <span><span class="state {{ f.fix.state }}">{{ f.fix.state }}</span> &nbsp;<span class="sev">risk {{ f.fix.risk }} · attempt {{ f.fix.attempt }}</span></span></div>
    <div>{{ f.fix.explanation }}</div>
    <ol>{% for s in f.fix.steps %}<li>{{ s.description }} <code>{{ s.command }}</code></li>{% endfor %}</ol>
    {% if f.fix.side_effects %}<div class="sub">Side effects: {{ f.fix.side_effects|join('; ') }}</div>{% endif %}
    {% if f.fix.rollback %}<div class="sub">Rollback: {{ f.fix.rollback }}</div>{% endif %}
    {% if f.fix.conflicts_with_previous %}<div class="sub">Versus previous proposal: {{ f.fix.conflicts_with_previous }}</div>{% endif %}
    {% if f.fix.result %}<details class="sub"><summary>Execution output</summary><div class="mono" style="white-space:pre-wrap;font-size:11px;margin-top:4px">{{ f.fix.result[-900:] }}</div></details>{% endif %}
  </div>
  {% elif f.needs_fix %}<div class="fix"><div class="sub">Fix pending.</div></div>{% endif %}
</div>
{% endfor %}
{% else %}
<h2>Findings</h2>
<div class="clear">{{ v.no_issues_statement }}</div>
{% endif %}

<footer><span>Reviewer {{ r.reviewer_model }} · Fixer {{ (v.findings|selectattr('fix')|map(attribute='fix.model')|first) or 'n/a' }} · inputs {{ 'Mongo' if (r.inputs.get('chip','')).startswith('mongo') else 'files' }}</span><span>Foreman · YOR.AI</span></footer>
</div></body></html>
"""

_env = Environment(autoescape=select_autoescape(["html"]))
_tpl = _env.from_string(TEMPLATE)


def render_report(report: Report) -> str:
    return _tpl.render(r=report, v=report.review, chuck_line=one_line(report.chuck) if report.chuck else "")
