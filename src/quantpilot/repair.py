"""Targeted public-source recovery. Never interpolate or replace valid observations."""
import hashlib
import json
import sys
import uuid
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
from filelock import FileLock

from .data import (BASE, KLINES, VALUES, audit_cached, audit_frames, dataset_hashes,
                   fetch, load_frames, manifest, parse, period)


def targets(frames, as_of=None):
    _, end, warmup = period(as_of)
    grid = pd.date_range(warmup, end, freq="5min", inclusive="left")
    result = {}
    for family in ("mark", "index", "positioning"):
        frame = frames.get(family, pd.DataFrame())
        if frame.empty:
            raise ValueError("Run initial acquisition before targeted repair")
        invalid = ~np.isfinite(frame[VALUES[family]].to_numpy(dtype=float)).all(axis=1)
        times = grid.difference(pd.DatetimeIndex(frame.event_time))
        times = times.union(pd.DatetimeIndex(frame.loc[invalid, "event_time"]))
        times = times[(times >= warmup) & (times < end)]
        if len(times): result[family] = times
    return result


def merge_verified(old, incoming, family, wanted):
    """Insert exact missing rows or replace invalid rows only; reject conflicting history."""
    columns = VALUES[family]
    old = old.copy().set_index("event_time", drop=False)
    if old.index.duplicated().any() or incoming.event_time.duplicated().any():
        raise ValueError("duplicate repair timestamps")
    fixed, conflicts = 0, []
    for _, row in incoming[incoming.event_time.isin(wanted)].iterrows():
        t = row.event_time
        values = row[columns].to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values < 0).any(): continue
        if family in ("mark", "index") and (values <= 0).any(): continue
        if row.available_at != t + pd.Timedelta(minutes=5):
            raise ValueError("unexpected repair availability time")
        if t in old.index:
            previous = old.loc[t, columns].to_numpy(dtype=float)
            valid = np.isfinite(previous)
            if not np.allclose(previous[valid], values[valid], rtol=1e-10, atol=1e-10):
                conflicts.append(t.isoformat())
                continue
            if valid.all(): continue
        old.loc[t] = row
        fixed += 1
    return old.reset_index(drop=True).sort_values("event_time").reset_index(drop=True), fixed, conflicts


def archive_candidates(frames, as_of=None):
    needed = targets(frames, as_of)
    original = manifest(as_of)
    items = []
    for family, times in needed.items():
        if family in ("mark", "index"):
            months = set(times.strftime("%Y-%m"))
            items.extend(item for item in original if item["family"] == family and
                         any(month in item["filename"] for month in months))
            directory = "markPriceKlines" if family == "mark" else "indexPriceKlines"
            for day in times.normalize().unique():
                name = f"BTCUSDT-5m-{day:%Y-%m-%d}.zip"
                items.append(dict(family=family, filename=f"{family}-{name}",
                                  url=f"{BASE}/daily/{directory}/BTCUSDT/5m/{name}"))
        else:
            days = set(times.strftime("%Y-%m-%d"))
            items.extend(item for item in original if item["family"] == family and
                         any(day in item["filename"] for day in days))
    return items


def rest_candidate(client, root, family, day):
    path = "/fapi/v1/markPriceKlines" if family == "mark" else "/fapi/v1/indexPriceKlines"
    params = dict(interval="5m", startTime=int(day.timestamp()*1000),
                  endTime=int((day+pd.Timedelta(days=1)).timestamp()*1000)-1, limit=1000)
    params["symbol" if family == "mark" else "pair"] = "BTCUSDT"
    response = client.get("https://fapi.binance.com"+path, params=params)
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list) or not rows: raise ValueError("no historical REST bars")
    frame = pd.DataFrame(rows, columns=KLINES)
    opened = pd.to_numeric(frame.open_time)
    if not (pd.to_numeric(frame.close_time) == opened+299999).all():
        raise ValueError("REST bars have unexpected duration")
    frame["event_time"] = pd.to_datetime(opened, unit="ms", utc=True)
    if not ((frame.event_time >= day) & (frame.event_time < day+pd.Timedelta(days=1))).all():
        raise ValueError("REST returned observations outside requested historical day")
    if not (opened % 300000 == 0).all(): raise ValueError("REST bar is off-grid")
    frame["available_at"] = frame.event_time+pd.Timedelta(minutes=5)
    frame[family+"_price"] = pd.to_numeric(frame.close)
    frame["source"], frame["received_at"] = str(response.url), pd.Timestamp.now(tz="UTC")
    filename = f"repair-rest-{family}-{day:%Y-%m-%d}-{uuid.uuid4().hex[:10]}.json"
    (root/"raw"/filename).write_bytes(response.content)
    record = dict(family=family, filename=filename, url=str(response.url), status="verified",
                  sha256=hashlib.sha256(response.content).hexdigest(),
                  verification="HTTPS response retained with local SHA256; no provider checksum", kind="rest",
                  received_at=frame.received_at.iloc[0].isoformat())
    return frame[["event_time", "available_at", *VALUES[family], "source", "received_at"]], record


def repair(root: Path, as_of=None):
    root = Path(root)
    with FileLock(str(root/"research-data.lock"), timeout=1):
        audit_cached(root, as_of)  # Refuse changed inputs before making modifications.
        previous = json.loads((root/"coverage.json").read_text(encoding="utf-8"))
        frames = load_frames(root)
        attempts, additions, changed = [], [], set()
        def accept(family, incoming, record):
            wanted = targets(frames, as_of).get(family, pd.DatetimeIndex([]))
            updated, count, conflicts = merge_verified(frames[family], incoming, family, wanted)
            attempts.append(dict(family=family, url=record["url"], status="checked",
                                 repaired_rows=count, conflicting_timestamps=conflicts))
            if count:
                frames[family] = updated
                changed.add(family)
                additions.append(record)
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            for item in archive_candidates(frames, as_of):
                if item["family"] not in targets(frames, as_of): continue
                print(f"Checking {item['url']}", file=sys.stderr, flush=True)
                unique = dict(item, filename="repair-"+uuid.uuid4().hex[:10]+"-"+item["filename"])
                record = fetch(unique, root, client)
                if record["status"] != "verified":
                    attempts.append(record)
                    continue
                try: accept(item["family"], parse(record, root), record)
                except Exception as exc:
                    attempts.append(dict(family=item["family"], url=item["url"], status="invalid", error=str(exc)))
            for family, times in list(targets(frames, as_of).items()):
                if family == "positioning": continue  # Old ratios cannot be obtained from 30-day REST history.
                for day in times.normalize().unique():
                    print(f"Checking historical REST {family} {day.date()}", file=sys.stderr, flush=True)
                    try:
                        incoming, record = rest_candidate(client, root, family, day)
                        accept(family, incoming, record)
                    except Exception as exc:
                        attempts.append(dict(family=family, day=str(day.date()), status="missing", error=str(exc)))
        # A failed/interrupted publication cannot retain release authorization.
        previous["release_ready"] = False
        (root/"coverage.json").write_text(json.dumps(previous, indent=2), encoding="utf-8")
        for family in changed:
            temp = root/f"{family}.repair.partial"
            frames[family].to_parquet(temp, index=False)
            temp.replace(root/f"{family}.parquet")
        report = audit_frames(frames, as_of, previous.get("coverage_policy"))
        report.update(archives=previous["archives"], dataset_hashes=dataset_hashes(root),
                      repair_sources=previous.get("repair_sources", [])+additions,
                      audit_mode="targeted_public_repair", audited_at=pd.Timestamp.now(tz="UTC").isoformat())
        report["release_ready"] &= all(r["status"] == "verified" for r in report["archives"])
        outcome = dict(attempted_at=report["audited_at"], attempts=attempts,
                       changed_families=sorted(changed), release_ready=report["release_ready"],
                       unresolved=report["families"])
        (root/"repair-report.json").write_text(json.dumps(outcome, indent=2), encoding="utf-8")
        (root/"coverage.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return outcome
