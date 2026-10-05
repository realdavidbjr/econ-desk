#!/usr/bin/env python3
"""
Econ Desk data builder.

Runs on GitHub Actions a few times each weekday. It reads config.json, pulls
every series from FRED (St. Louis Fed) and every news feed, and writes:
  docs/data/data.json  - everything the dashboard shows
  docs/data/brief.md   - a plain-text snapshot Claude reads for the morning brief

Nothing here needs editing for normal use; change config.json instead.
A series or feed that fails is skipped and listed in the dashboard's status panel.
"""
import bisect
import datetime as dt
import html
import json
import os
import re
import sys
import time
from pathlib import Path

import feedparser
import requests

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
OUT_DIR = ROOT / "docs" / "data"
FRED = "https://api.stlouisfed.org/fred"
KEY = os.environ.get("FRED_API_KEY", "").strip()
TODAY = dt.date.today()
CONTACT = CONFIG.get("contact_email") or "no-email-given"
HEADERS = {"User-Agent": f"EconDesk/1.0 personal dashboard ({CONTACT})"}

problems = []          # everything that failed, shown in the status panel
_series_cache = {}     # id -> parsed series (or None if it failed)


# ---------------------------------------------------------------- FRED access
def fred_get(path, **params):
    params.update(api_key=KEY, file_type="json")
    for attempt in range(5):
        try:
            r = requests.get(f"{FRED}/{path}", params=params, timeout=30)
        except requests.RequestException as e:
            err = str(e)
        else:
            if r.status_code == 200:
                return r.json()
            err = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code == 400:      # bad series id etc; retrying won't help
                break
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(err)


def get_series(sid):
    """Fetch one FRED series (metadata + full history). Cached per run."""
    if sid in _series_cache:
        return _series_cache[sid]
    try:
        meta = fred_get("series", series_id=sid)["seriess"][0]
        raw = fred_get("series/observations", series_id=sid,
                       observation_start="1990-01-01")["observations"]
        obs = [(dt.date.fromisoformat(o["date"]), float(o["value"]))
               for o in raw if o["value"] not in (".", "")]
        if not obs:
            raise RuntimeError("no observations")
        s = {
            "id": sid,
            "title": meta.get("title", sid),
            "units": meta.get("units", ""),
            "freq": meta.get("frequency_short", ""),
            "updated": meta.get("last_updated", ""),
            "obs": obs,
        }
    except Exception as e:  # noqa: BLE001
        problems.append({"kind": "FRED series", "name": sid, "error": str(e)[:200]})
        s = None
    _series_cache[sid] = s
    time.sleep(0.25)  # stay well under FRED's 120 requests/minute
    return s


def first_working(ids):
    """Return the first series in ids that downloads, else None."""
    if isinstance(ids, str):
        ids = [ids]
    for sid in ids:
        s = get_series(sid)
        if s:
            return s
    return None


def bn_factor(units):
    """Multiplier that converts a series' units into billions of dollars."""
    u = (units or "").lower()
    if "trillion" in u:
        return 1000.0
    if "billion" in u:
        return 1.0
    if "million" in u:
        return 0.001
    return None


# ---------------------------------------------------------------- maths
def value_asof(obs, when):
    """Last observation on or before date `when` (obs sorted by date)."""
    dates = [d for d, _ in obs]
    i = bisect.bisect_right(dates, when) - 1
    return obs[i] if i >= 0 else None


def year_ago(d):
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29 February
        return d.replace(year=d.year - 1, day=28)


def transform(obs, how):
    """Return a new observation list transformed by level/yoy/mom/diff."""
    if how == "level":
        return list(obs)
    out = []
    if how == "yoy":
        dates = [d for d, _ in obs]
        for d, v in obs:
            target = year_ago(d)
            i = bisect.bisect_right(dates, target) - 1
            if i < 0:
                continue
            d0, v0 = obs[i]
            if (target - d0).days > 45 or v0 == 0:
                continue
            out.append((d, (v / v0 - 1) * 100))
    elif how == "mom":
        for (d0, v0), (d, v) in zip(obs, obs[1:]):
            if v0:
                out.append((d, (v / v0 - 1) * 100))
    elif how == "diff":
        for (d0, v0), (d, v) in zip(obs, obs[1:]):
            out.append((d, v - v0))
    return out


def context_note(obs):
    """'Highest since Mar 2023' style note for the latest value, if notable."""
    if len(obs) < 3:
        return None
    d, v = obs[-1]
    prev = obs[-2][1]
    if v == prev:
        return None
    rising = v > prev
    for j in range(len(obs) - 2, -1, -1):
        dj, vj = obs[j]
        if (rising and vj >= v) or (not rising and vj <= v):
            if len(obs) - 1 - j < 6:      # only notable if it beats a few periods
                return None
            word = "Highest" if rising else "Lowest"
            return f"{word} since {fmt_date(dj, 'M')}"
    word = "Highest" if rising else "Lowest"
    return f"{word} since at least {obs[0][0].year}"


# ---------------------------------------------------------------- formatting
def fmt_date(d, freq):
    if freq.startswith("Q"):
        return f"Q{(d.month - 1) // 3 + 1} {d.year}"
    if freq.startswith("M") or freq.startswith("A"):
        return d.strftime("%b %Y")
    return d.strftime("%b %-d, %Y")


def fmt_value(v, kind, units="", signed=False):
    sign = "+" if (signed and v > 0) else ""
    if kind == "usd_auto":
        f = bn_factor(units)
        bn = v * f if f is not None else v
        a = abs(bn)
        neg = "-" if bn < 0 else sign
        if a >= 1000:
            return f"{neg}${a / 1000:,.2f}T"
        if a >= 10:
            return f"{neg}${a:,.0f}B"
        return f"{neg}${a:,.1f}B"
    table = {
        "pct2": lambda x: f"{x:.2f}%",
        "pct1": lambda x: f"{x:.1f}%",
        "k": lambda x: f"{x:,.0f}k",
        "kdiff": lambda x: f"{x:,.0f}k",
        "count_k": lambda x: f"{x / 1000:,.0f}k",
        "k_to_m": lambda x: f"{x / 1000:,.2f}M",
        "idx1": lambda x: f"{x:,.1f}",
        "idx2": lambda x: f"{x:,.2f}",
        "usd2": lambda x: f"${x:,.2f}",
        "pts0": lambda x: f"{x:,.0f}",
        "ratio3": lambda x: f"{x:.3f}",
        "fx2": lambda x: f"{x:.2f}",
        "fx4": lambda x: f"{x:.4f}",
    }
    body = table.get(kind, lambda x: f"{x:,.2f}")(v)
    if kind == "usd2" and v < 0:
        body = "-$" + body[2:]
    if kind == "kdiff" and v > 0:
        return "+" + body
    return sign + body


def fmt_change(chg, spec, units):
    out = _fmt_change(chg, spec, units)
    # a change that rounds to zero reads better as "unch." than "+0 bp" or "-$0.00"
    if out and not re.search(r"[1-9]", out):
        return "unch."
    return out


def _fmt_change(chg, spec, units):
    kind = spec.get("fmt", "")
    if abs(chg) < 1e-9:
        return "unch."
    if kind.startswith("pct"):
        if spec.get("bp"):
            return f"{chg * 100:+.0f} bp"
        return f"{chg:+.{1 if kind == 'pct1' else 2}f} pts"
    if spec.get("main") == "diff":
        return None  # a change of a change isn't useful; the prior value is shown instead
    return fmt_value(chg, kind, units, signed=True)


def rnd(v):
    """Round for compact JSON without losing meaningful precision."""
    if v == 0:
        return 0
    return float(f"{v:.5g}")


def trim_for_chart(obs, freq):
    """Keep charts light: full detail recently, thinned-out history further back.
    Daily series: every day for 2 years, then weekly back 10 years.
    Weekly series: every week for 10 years, then monthly back to 1990."""
    if freq.startswith("D"):
        full_from, thin_from, bucket = TODAY - dt.timedelta(days=730), TODAY - dt.timedelta(days=3650), \
            (lambda d: d.isocalendar()[:2])
    elif freq.startswith("W"):
        full_from, thin_from, bucket = TODAY - dt.timedelta(days=3650), dt.date(1990, 1, 1), \
            (lambda d: (d.year, d.month))
    else:
        return [[d.isoformat(), rnd(v)] for d, v in obs if d >= dt.date(1990, 1, 1)]
    out, last_bucket = [], None
    for d, v in obs:
        if d < thin_from:
            continue
        if d >= full_from:
            out.append([d.isoformat(), rnd(v)])
            continue
        b = bucket(d)
        if b == last_bucket:
            out[-1] = [d.isoformat(), rnd(v)]   # keep the last point in each bucket
        else:
            out.append([d.isoformat(), rnd(v)])
            last_bucket = b
    return out


# ---------------------------------------------------------------- builders
def build_series(spec):
    s = first_working(spec["id"])
    if not s:
        return {"id": spec["id"] if isinstance(spec["id"], str) else spec["id"][0],
                "name": spec["name"], "ok": False}
    main = spec.get("main", "level")
    main_obs = transform(s["obs"], main)
    if len(main_obs) < 2:
        return {"id": s["id"], "name": spec["name"], "ok": False}
    (d, v), (dp, vp) = main_obs[-1], main_obs[-2]
    units = s["units"]
    # For dollar amounts in a transformed series the value is a %, not dollars
    kind = spec.get("fmt", "")
    if main in ("yoy", "mom") and kind == "usd_auto":
        kind = "pct1"
    also = []
    for how in spec.get("also", []):
        t = transform(s["obs"], how)
        if t and t[-1][0] == s["obs"][-1][0]:
            label = {"yoy": "YoY", "mom": "vs prior" if s["freq"].startswith(("D", "W")) else "MoM"}.get(how, how)
            if how == "mom" and s["freq"].startswith("Q"):
                label = "QoQ"
            if how == "mom" and s["freq"].startswith("D"):
                # a day-over-day % is noise; use one month back instead
                past = value_asof(s["obs"], d - dt.timedelta(days=30))
                if past and past[1]:
                    also.append(f"1M {(s['obs'][-1][1] / past[1] - 1) * 100:+.1f}%")
                continue
            also.append(f"{label} {t[-1][1]:+.1f}%")
    if main == "diff":
        also.insert(0, f"Prior {fmt_value(vp, kind, units)}")
    chart_how = spec.get("chart", main)
    chart_obs = main_obs if chart_how == main else transform(s["obs"], chart_how)
    chart_label = {"yoy": "% change from a year earlier", "mom": "% change from prior period",
                   "diff": "Change from prior period"}.get(chart_how, s["units"])
    factor = bn_factor(units) if kind == "usd_auto" and chart_how == "level" else None
    if factor:
        chart_obs = [(cd, cv * factor) for cd, cv in chart_obs]
        chart_label = "Billions of dollars"
    change = fmt_change(v - vp, {**spec, "fmt": kind}, units)
    direction = 0 if change == "unch." else (v > vp) - (v < vp)
    return {
        "id": s["id"],
        "name": spec["name"],
        "ok": True,
        "value": fmt_value(v, spec["fmt"] if main == "level" else kind, units),
        "change": change,
        "direction": direction,
        "also": also,
        "date": fmt_date(d, s["freq"]),
        "freq": s["freq"],
        "context": context_note(main_obs),
        "chart_label": chart_label,
        "chart": trim_for_chart(chart_obs, s["freq"]),
        "fred_title": s["title"],
        "updated": s["updated"],
    }


def build_corridor():
    c = CONFIG["corridor"]
    start = TODAY - dt.timedelta(days=365 * 3)
    out = {"lines": []}
    up, lo = get_series(c["upper"]), get_series(c["lower"])
    if up and lo:
        out["upper"] = [[d.isoformat(), v] for d, v in up["obs"] if d >= start]
        out["lower"] = [[d.isoformat(), v] for d, v in lo["obs"] if d >= start]
        out["range"] = f"{lo['obs'][-1][1]:.2f}–{up['obs'][-1][1]:.2f}%"
        # when did the target range last change?
        last_change = None
        for (d0, v0), (d1, v1) in zip(up["obs"], up["obs"][1:]):
            if v1 != v0:
                last_change = (d1, v1 - v0)
        if last_change:
            bp = round(last_change[1] * 100)
            verb = "cut" if bp < 0 else "hike"
            out["last_move"] = f"Last move: {abs(bp)} bp {verb}, {fmt_date(last_change[0], 'D')}"
    for line in c["lines"]:
        s = get_series(line["id"])
        if s:
            out["lines"].append({"name": line["name"], "id": line["id"],
                                 "data": [[d.isoformat(), v] for d, v in s["obs"] if d >= start],
                                 "latest": f"{s['obs'][-1][1]:.2f}%"})
    return out


def build_balance_sheet():
    cfg = CONFIG["balance_sheet"]
    start = dt.date.fromisoformat(cfg["start"])
    total = get_series(cfg["total"])
    if not total:
        return None
    tf = bn_factor(total["units"]) or 0.001
    dates = [d for d, _ in total["obs"] if d >= start]
    totals = [v * tf for d, v in total["obs"] if d >= start]

    def aligned(ids):
        s = first_working(ids)
        if not s:
            return None, None
        f = bn_factor(s["units"])
        if f is None:
            problems.append({"kind": "Balance sheet", "name": s["id"], "error": f"unknown units: {s['units']}"})
            return None, None
        vals = []
        for d in dates:
            hit = value_asof(s["obs"], d)
            vals.append(hit[1] * f if hit and (d - hit[0]).days <= 10 else 0.0)
        return vals, s["id"]

    def stack(parts, whole, remainder_name):
        layers, used = [], []
        for p in parts:
            vals, sid = aligned(p["ids"])
            if vals is not None:
                layers.append({"name": p["name"], "id": sid, "data": [rnd(x) for x in vals]})
                used.append(vals)
        rest = [max(0.0, w - sum(col)) for w, col in zip(whole, zip(*used))] if used else list(whole)
        layers.append({"name": remainder_name, "id": None, "data": [rnd(x) for x in rest]})
        return layers

    out = {
        "dates": [d.isoformat() for d in dates],
        "total": [rnd(x) for x in totals],
        "assets": stack(cfg["assets"], totals, "Everything else"),
        "liabilities": stack(cfg["liabilities"], totals, "Everything else, incl. capital"),
        "events": cfg.get("events", []),
        "as_of": fmt_date(dates[-1], "D") if dates else None,
    }
    tt, _ = aligned([cfg["treasuries_total"]])
    if tt:
        out["treasuries"] = stack(cfg["treasuries"], tt, "Other Treasury holdings")
        out["treasuries_total"] = [rnd(x) for x in tt]
    return out


def build_yield_curve():
    points = []
    for p in CONFIG["yield_curve"]:
        s = get_series(p["id"])
        if s:
            points.append((p["label"], s))
    if not points:
        return None
    latest = max(s["obs"][-1][0] for _, s in points)
    snaps = []
    for label, when in (("Latest", latest), ("1 month ago", latest - dt.timedelta(days=30)),
                        ("1 year ago", year_ago(latest))):
        vals = []
        for _, s in points:
            hit = value_asof(s["obs"], when)
            vals.append(rnd(hit[1]) if hit else None)
        snaps.append({"label": label, "date": fmt_date(when, "D"), "values": vals})
    return {"maturities": [lab for lab, _ in points], "snapshots": snaps}


def build_calendar():
    events = []
    horizon = TODAY + dt.timedelta(days=28)
    since = TODAY - dt.timedelta(days=7)
    keywords = [k.lower() for k in CONFIG.get("calendar_keywords", [])]
    try:
        res = fred_get("releases/dates", realtime_start=since.isoformat(),
                       realtime_end=horizon.isoformat(),
                       include_release_dates_with_no_data="true",
                       sort_order="asc", limit=1000, order_by="release_date")
        seen = set()
        for r in res.get("release_dates", []):
            name = r.get("release_name", "")
            d = dt.date.fromisoformat(r["date"])
            if since <= d <= horizon and any(k in name.lower() for k in keywords):
                key = (d, name)
                if key not in seen:
                    seen.add(key)
                    events.append({"date": d.isoformat(), "name": name, "kind": "data"})
    except Exception as e:  # noqa: BLE001
        problems.append({"kind": "Calendar", "name": "FRED release dates", "error": str(e)[:200]})

    for m in CONFIG.get("fomc_meetings", []):
        end = dt.date.fromisoformat(m["end"])
        label = "FOMC decision, 2:00 pm ET, and press conference"
        if m.get("sep"):
            label += ", with new projections (dot plot)"
        if since <= end <= horizon + dt.timedelta(days=365):
            events.append({"date": end.isoformat(), "name": label, "kind": "fomc"})
        minutes = end + dt.timedelta(days=21)
        if since <= minutes <= horizon + dt.timedelta(days=365):
            events.append({"date": minutes.isoformat(),
                           "name": f"FOMC minutes from the {end.strftime('%b %-d')} meeting (expected)",
                           "kind": "fomc"})
    for e in CONFIG.get("extra_events", []):
        d = dt.date.fromisoformat(e["date"])
        if since <= d <= horizon + dt.timedelta(days=365):
            events.append({"date": d.isoformat(), "name": e["name"], "kind": "other"})
    events.sort(key=lambda e: (e["date"], e["kind"] != "fomc"))
    return events


def next_fomc():
    for m in CONFIG.get("fomc_meetings", []):
        end = dt.date.fromisoformat(m["end"])
        if end >= TODAY:
            return {"start": m["start"], "end": m["end"], "sep": m.get("sep", False),
                    "days": (end - TODAY).days}
    return None


TAG_RE = re.compile(r"<[^>]+>")


def clean(text, limit=260):
    t = html.unescape(TAG_RE.sub(" ", text or ""))
    t = re.sub(r"\s+", " ", t).strip()
    return (t[: limit - 1] + "…") if len(t) > limit else t


def build_news():
    news = {}
    for cat in CONFIG.get("feeds", []):
        items = []
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=cat.get("max_age_days", 7))
        for feed in cat["feeds"]:
            try:
                r = requests.get(feed["url"], headers=HEADERS, timeout=25)
                r.raise_for_status()
                parsed = feedparser.parse(r.content)
                if not parsed.entries:
                    raise RuntimeError("feed returned no items")
            except Exception as e:  # noqa: BLE001
                problems.append({"kind": "News feed", "name": feed["name"], "error": str(e)[:160]})
                continue
            is_google = "news.google.com" in feed["url"]
            for en in parsed.entries[:25]:
                stamp = en.get("published_parsed") or en.get("updated_parsed")
                when = dt.datetime(*stamp[:6], tzinfo=dt.timezone.utc) if stamp else None
                if when and when < cutoff:
                    continue
                title = clean(en.get("title", ""), 220)
                source = feed["name"]
                if is_google:
                    src = en.get("source", {})
                    source = src.get("title") if isinstance(src, dict) and src.get("title") else source
                    if " - " in title:
                        title, _, tail = title.rpartition(" - ")
                        source = source if source != feed["name"] else tail
                summary = "" if is_google else clean(en.get("summary", ""))
                if summary.lower().startswith(title.lower()[:40]):
                    summary = ""
                items.append({"title": title, "link": en.get("link", ""), "source": source,
                              "published": when.isoformat() if when else None, "summary": summary})
        seen, unique = set(), []
        for it in sorted(items, key=lambda x: x["published"] or "", reverse=True):
            key = re.sub(r"\W+", "", it["title"].lower())[:70]
            if key and key not in seen:
                seen.add(key)
                unique.append(it)
        news[cat["category"]] = unique[:60]
    return news


def build_global():
    rows = []
    for b in CONFIG.get("global_banks", []):
        s = first_working(b["ids"])
        row = {"name": b["name"], "rate_name": b["rate_name"], "link": b.get("link")}
        if s:
            row.update(value=f"{s['obs'][-1][1]:.2f}%", date=fmt_date(s["obs"][-1][0], s["freq"]),
                       id=s["id"], chart=trim_for_chart(s["obs"], s["freq"]))
            # most recent change
            for (d0, v0), (d1, v1) in reversed(list(zip(s["obs"], s["obs"][1:]))):
                if round(v1, 3) != round(v0, 3):
                    row["last_change"] = f"{(v1 - v0) * 100:+.0f} bp on {fmt_date(d1, s['freq'])}"
                    break
        rows.append(row)
    return rows


def write_brief(data):
    lines = [f"# {CONFIG.get('dashboard_title', 'Econ Desk')} snapshot",
             f"Generated {data['generated_at']} (UTC). Source: FRED unless noted.", ""]
    c = data.get("corridor", {})
    if c.get("range"):
        lines.append(f"Fed funds target range: {c['range']}. {c.get('last_move', '')}")
        for ln in c.get("lines", []):
            lines.append(f"- {ln['name']}: {ln['latest']}")
    nf = data.get("next_fomc")
    if nf:
        lines.append(f"Next FOMC decision: {nf['end']} ({nf['days']} days away)"
                     + (", with new projections." if nf["sep"] else "."))
    lines.append("")
    for sec in data["sections"]:
        lines.append(f"## {sec['title']}")
        for s in sec["series"]:
            if not s.get("ok"):
                continue
            bits = [f"{s['name']}: {s['value']}"]
            if s.get("change"):
                bits.append(f"change {s['change']}")
            bits += s.get("also", [])
            bits.append(f"as of {s['date']}")
            if s.get("context"):
                bits.append(s["context"])
            lines.append("- " + "; ".join(bits))
        lines.append("")
    bs = data.get("balance_sheet")
    if bs:
        lines.append(f"## Fed balance sheet (as of {bs['as_of']}, $ billions)")
        lines.append(f"- Total assets: {bs['total'][-1]:,.0f} (one week earlier {bs['total'][-2]:,.0f})")
        for layer in bs["assets"]:
            lines.append(f"- {layer['name']}: {layer['data'][-1]:,.0f}")
        lines.append("")
    lines.append("## Calendar, next 14 days")
    horizon = (TODAY + dt.timedelta(days=14)).isoformat()
    for e in data["calendar"]:
        if TODAY.isoformat() <= e["date"] <= horizon:
            lines.append(f"- {e['date']}: {e['name']}")
    lines.append("")
    for cat, items in data["news"].items():
        lines.append(f"## Headlines: {cat}")
        for it in items[:12]:
            lines.append(f"- {it['title']} ({it['source']}) {it['link']}")
        lines.append("")
    (OUT_DIR / "brief.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    if not KEY:
        sys.exit("FRED_API_KEY is missing. Add it under Settings > Secrets and variables > Actions.")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    started = time.time()
    data = {
        "title": CONFIG.get("dashboard_title", "Econ Desk"),
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "sections": [],
    }
    for sec in CONFIG["sections"]:
        print(f"Section: {sec['title']}")
        data["sections"].append({
            "key": sec["key"], "tab": sec["tab"], "title": sec["title"], "note": sec.get("note"),
            "series": [build_series(sp) for sp in sec["series"]],
        })
    print("Corridor, balance sheet, curve, calendar, global, news")
    data["corridor"] = build_corridor()
    data["balance_sheet"] = build_balance_sheet()
    data["yield_curve"] = build_yield_curve()
    data["calendar"] = build_calendar()
    data["next_fomc"] = next_fomc()
    data["fomc_meetings"] = CONFIG.get("fomc_meetings", [])
    data["global"] = build_global()
    data["news"] = build_news()
    data["headline_tiles"] = CONFIG.get("headline_tiles", [])
    data["useful_links"] = CONFIG.get("useful_links", [])
    data["problems"] = problems
    (OUT_DIR / "data.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    write_brief(data)
    ok = sum(1 for sec in data["sections"] for s in sec["series"] if s.get("ok"))
    total = sum(len(sec["series"]) for sec in data["sections"])
    print(f"Done in {time.time() - started:.0f}s: {ok}/{total} series OK, {len(problems)} problems.")
    for p in problems:
        print(f"  ! {p['kind']}: {p['name']}: {p['error']}")
    if ok == 0:
        sys.exit("No series downloaded. Check that the FRED_API_KEY secret is correct.")


if __name__ == "__main__":
    main()
