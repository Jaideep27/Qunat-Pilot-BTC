"""Public archive acquisition, SHA256 verification, and fail-closed coverage audit."""
from __future__ import annotations
import hashlib
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import httpx
import numpy as np
import pandas as pd

BASE = "https://data.binance.vision/data/futures/um"
KLINES = ["open_time", "open", "high", "low", "close", "volume", "close_time",
          "quote_volume", "trade_count", "taker_buy_volume", "taker_buy_quote", "ignore"]
METRICS = {"sum_open_interest": "open_interest", "count_long_short_ratio": "global_ratio",
           "count_toptrader_long_short_ratio": "top_account_ratio",
           "sum_toptrader_long_short_ratio": "top_position_ratio"}
VALUES = {"candles": ["open", "high", "low", "close", "volume", "trade_count", "taker_buy_volume"],
          "mark": ["mark_price"], "index": ["index_price"], "positioning": list(METRICS.values()),
          "funding": ["funding_rate"], "sentiment": ["fear_greed"]}
# Preserve actual settlement timestamps; tolerate sub-second archive scheduling jitter.
FUNDING_MAX_GAP = pd.Timedelta(hours=8, seconds=1)

AGES = {"candles": "0s", "mark": "10min", "index": "10min", "positioning": "15min",
        "funding": "9h", "sentiment": "2D"}

def utc(value):
    t = pd.Timestamp(value)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")

def period(as_of=None):
    end = utc(as_of or pd.Timestamp.now(tz="UTC")).normalize().replace(day=1)
    start = end - pd.DateOffset(months=24)
    return start, end, start - pd.Timedelta(days=40)

def manifest(as_of=None):
    _, end, warmup = period(as_of)
    items = []
    for month in pd.date_range(warmup.replace(day=1), end, freq="MS", inclusive="left"):
        for family, directory in [("candles", "klines"), ("mark", "markPriceKlines"), ("index", "indexPriceKlines")]:
            name = f"BTCUSDT-5m-{month:%Y-%m}.zip"
            items.append(dict(family=family, url=f"{BASE}/monthly/{directory}/BTCUSDT/5m/{name}",
                              filename=f"{family}-{name}"))
        name = f"BTCUSDT-fundingRate-{month:%Y-%m}.zip"
        items.append(dict(family="funding", url=f"{BASE}/monthly/fundingRate/BTCUSDT/{name}", filename=name))
    for day in pd.date_range(warmup, end, freq="D", inclusive="left"):
        name = f"BTCUSDT-metrics-{day:%Y-%m-%d}.zip"
        items.append(dict(family="positioning", url=f"{BASE}/daily/metrics/BTCUSDT/{name}", filename=name))
    return items

def fetch(item, root, client):
    record = dict(item)
    path = root / "raw" / item["filename"]
    try:
        r = client.get(item["url"] + ".CHECKSUM")
        r.raise_for_status()
        expected = r.text.split()[0].lower()
        if len(expected) != 64:
            raise ValueError("invalid SHA256")
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            response = client.get(item["url"])
            response.raise_for_status()
            if hashlib.sha256(response.content).hexdigest() != expected:
                raise ValueError("checksum mismatch")
            temp = path.with_suffix(".partial")
            temp.write_bytes(response.content)
            temp.replace(path)
        record.update(status="verified", sha256=expected, received_at=pd.Timestamp.now(tz="UTC").isoformat())
    except Exception as exc:
        record.update(status="missing", error=str(exc))
    return record

def parse(record, root):
    with zipfile.ZipFile(root / "raw" / record["filename"]) as z:
        names = [n for n in z.namelist() if n.endswith(".csv")]
        if len(names) != 1:
            raise ValueError("expected one CSV")
        content = z.read(names[0])
    df = pd.read_csv(io.BytesIO(content))
    family = record["family"]
    if family in ("candles", "mark", "index"):
        if df.columns[0] != "open_time":
            df = pd.read_csv(io.BytesIO(content), header=None, names=KLINES)
        else:
            df.columns = KLINES[:len(df.columns)]
        df["event_time"] = pd.to_datetime(pd.to_numeric(df.open_time), unit="ms", utc=True)
        expected_close = pd.to_numeric(df.open_time)+299999
        if not (pd.to_numeric(df.close_time) == expected_close).all():
            raise ValueError("unexpected close timestamp or five-minute bar duration")
        df["available_at"] = df.event_time + pd.Timedelta(minutes=5)
        if family != "candles":
            df = df.rename(columns={"close": f"{family}_price"})
    elif family == "positioning":
        df = df.rename(columns=METRICS)
        df["event_time"] = pd.to_datetime(df.create_time, utc=True)
        df["available_at"] = df.event_time + pd.Timedelta(minutes=5)
    else:
        df["event_time"] = pd.to_datetime(pd.to_numeric(df.calc_time), unit="ms", utc=True)
        df["available_at"] = df.event_time + pd.Timedelta(seconds=1)
        df = df.rename(columns={"last_funding_rate": "funding_rate"})
    df = df[["event_time", "available_at", *VALUES[family]]].copy()
    for c in VALUES[family]:
        df[c] = pd.to_numeric(df[c], errors="raise")
    df["source"] = record["url"]
    df["received_at"] = utc(record["received_at"])
    return df

def timestamp_ranges(index):
    ordered = pd.DatetimeIndex(index).sort_values()
    if ordered.empty:
        return []
    # Group contiguous observations in bulk; sparse/full-period gap reports stay cheap.
    boundaries = np.asarray((ordered[1:]-ordered[:-1]) != pd.Timedelta(minutes=5))
    starts = ordered[np.r_[True, boundaries]]
    ends = ordered[np.r_[boundaries, True]]
    return [[first.isoformat(), last.isoformat()] for first,last in zip(starts,ends)]


def audit_frames(frames, as_of=None, policy=None):
    start, end, warmup = period(as_of)
    grid = pd.date_range(warmup + pd.Timedelta(minutes=5), end, freq="5min")
    report = dict(start=start.isoformat(), end_exclusive=end.isoformat(), warmup_start=warmup.isoformat(),
                  release_ready=True, families={}, availability_assumptions={
                      "bars": "close time", "positioning": "event + 5 minutes",
                      "funding": "event + 1 second; settlement gap tolerance 1 second", "sentiment": "event + 24 hours"})
    for family, values in VALUES.items():
        df = frames.get(family, pd.DataFrame())
        issues, missing = [], grid
        absent_events = pd.DatetimeIndex([])
        invalid = pd.Series(False, index=df.index)
        invalid_columns = {}
        required = {"event_time", "available_at", "received_at", "source", *values}
        if not len(df):
            issues.append("no observations")
        elif required - set(df):
            issues.append(f"missing columns: {sorted(required - set(df))}")
        else:
            df = df.sort_values("available_at")
            if df.source.astype(str).str.startswith("fixture").any(): issues.append("synthetic observations cannot authorize model release")
            if df.event_time.duplicated().any(): issues.append("duplicate timestamps")
            if (df.available_at < df.event_time).any(): issues.append("availability before event")
            if df[["event_time", "available_at", "received_at", "source"]].isna().any().any():
                issues.append("missing provenance")
            finite = np.isfinite(df[values].to_numpy(dtype=float))
            invalid = pd.Series(~finite.all(axis=1), index=df.index)
            invalid_columns = {column:int((~finite[:,i]).sum()) for i,column in enumerate(values) if not finite[:,i].all()}
            if invalid.any(): issues.append("invalid numeric values")
            if family not in ("funding", "sentiment") and (df[values] < 0).any().any():
                issues.append("negative market values")
            prices = ["open", "high", "low", "close"] if family == "candles" else ([family+"_price"] if family in ("mark","index") else [])
            if prices and (df[prices] <= 0).any().any(): issues.append("non-positive prices")
            if family == "sentiment" and not df.fear_greed.between(0, 100).all():
                issues.append("sentiment out of bounds")
            if family == "candles" and ((df.high < df[["open", "close", "low"]].max(axis=1)) |
                    (df.low > df[["open", "close"]].min(axis=1)) | (df.taker_buy_volume > df.volume)).any():
                issues.append("invalid OHLC or taker volume")
            if family in ("candles", "mark", "index", "positioning"):
                native_grid = pd.date_range(warmup, end, freq="5min", inclusive="left")
                absent_events = native_grid.difference(pd.DatetimeIndex(df.event_time))
                if len(absent_events): issues.append(f"{len(absent_events)} missing native observations")
            j = pd.merge_asof(pd.DataFrame({"cutoff": grid}), df[["available_at"]].assign(valid_observation=~invalid),
                              left_on="cutoff", right_on="available_at",
                              direction="backward", tolerance=pd.Timedelta(AGES[family]))
            missing = grid[j.available_at.isna() | ~j.valid_observation.astype("boolean").fillna(False)]
            if family == "funding" and df.event_time.diff().max() > FUNDING_MAX_GAP:
                issues.append("settlement gap exceeds 8 hours plus 1 second timestamp tolerance")
        if len(missing): issues.append("missing or stale windows")
        report["families"][family] = dict(rows=len(df), missing_windows=len(missing),
            gap_ranges=timestamp_ranges(missing), native_missing_count=len(absent_events),
            native_gap_ranges=timestamp_ranges(absent_events), invalid_rows=int(invalid.sum()),
            invalid_columns=invalid_columns,
            invalid_ranges=timestamp_ranges(df.loc[invalid, "event_time"]) if invalid.any() else [], issues=issues)
        report["release_ready"] &= not issues
    report["strict_release_ready"] = bool(report["release_ready"])
    if policy is not None:
        if policy.get("mode") != "positioning_quarantine_v1":
            raise ValueError("unsupported coverage policy")
        allowed = {"invalid numeric values", "missing or stale windows"}
        position = report["families"]["positioning"]
        affected = position["native_missing_count"]+position["invalid_rows"]
        hard = [issue for issue in position["issues"] if issue not in allowed and
                issue != f'{position["native_missing_count"]} missing native observations']
        others_pass = all(not result["issues"] for name,result in report["families"].items() if name != "positioning")
        within_limit = affected <= int(len(grid)*.001) and position["rows"] > 0
        report["release_ready"] = bool(others_pass and not hard and within_limit)
        report["coverage_policy"] = dict(mode="positioning_quarantine_v1", maximum_affected_fraction=.001,
            authorized_by="User delegated coverage decision and requested autonomous completion",
            handling="No interpolation; missing/invalid positioning and 12 subsequent bars excluded")
        report["quarantine"] = dict(source_rows=affected, expected_rows=len(grid),
            source_fraction=affected/len(grid), maximum_fraction=.001,
            feature_lookback_bars=12, status="accepted" if report["release_ready"] else "blocked")
    return report

def acquire(root: Path, as_of=None, workers=6):
    root.mkdir(parents=True, exist_ok=True)
    (root / "raw").mkdir(exist_ok=True)
    chunks = {name: [] for name in VALUES}
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            records = list(pool.map(lambda item: fetch(item, root, client), manifest(as_of)))
        for record in records:
            if record["status"] == "verified":
                try:
                    chunks[record["family"]].append(parse(record, root))
                except Exception as exc:
                    record.update(status="invalid", error=str(exc))
        try:
            response = client.get("https://api.alternative.me/fng/?limit=0&format=json")
            response.raise_for_status()
            df = pd.DataFrame(response.json()["data"])
            df["event_time"] = pd.to_datetime(df.timestamp.astype(int), unit="s", utc=True)
            df["available_at"] = df.event_time + pd.Timedelta(days=1)
            df["received_at"] = pd.Timestamp.now(tz="UTC")
            df["source"] = str(response.url)
            df["fear_greed"] = df.value.astype(float)
            chunks["sentiment"].append(df[["event_time", "available_at", "received_at", "source", "fear_greed"]])
        except Exception as exc:
            records.append(dict(family="sentiment", status="missing", error=str(exc)))
    frames = {}
    for name, parts in chunks.items():
        if parts:
            frames[name] = pd.concat(parts).sort_values("event_time").reset_index(drop=True)
            frames[name].to_parquet(root / f"{name}.parquet", index=False)
    report = audit_frames(frames, as_of)
    report["archives"] = records
    report["release_ready"] &= all(r["status"] == "verified" for r in records)
    report["dataset_hashes"] = dataset_hashes(root)
    (root / "coverage.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report

def dataset_hashes(root):
    return {f"{name}.parquet": hashlib.sha256((root / f"{name}.parquet").read_bytes()).hexdigest()
            for name in VALUES if (root / f"{name}.parquet").exists()}

def load_frames(root):
    return {name: pd.read_parquet(root / f"{name}.parquet") for name in VALUES
            if (root / f"{name}.parquet").exists()}

def join_external(candles, frames):
    out = candles.sort_values("available_at").copy()
    out["cutoff"] = out.available_at
    for family in list(VALUES)[1:]:
        right = frames.get(family)
        if right is None or right.empty:
            for c in VALUES[family]: out[c] = np.nan
            continue
        right = right[["available_at", *VALUES[family]]].sort_values("available_at")
        if family == "positioning" and not right.empty:
            native = pd.date_range(right.available_at.min(), right.available_at.max(), freq="5min")
            right = right.set_index("available_at").reindex(native).rename_axis("available_at").reset_index()
        right = right.rename(columns={"available_at": f"{family}_available_at"})
        out = pd.merge_asof(out, right, left_on="cutoff", right_on=f"{family}_available_at",
                            direction="backward", tolerance=pd.Timedelta(AGES[family]))
    return out



def audit_cached(root: Path, as_of=None, policy=None):
    """Re-audit downloaded data without provider access; verify raw and Parquet hashes."""
    previous = json.loads((root / "coverage.json").read_text(encoding="utf-8-sig"))
    hashes = dataset_hashes(root)
    if hashes != previous.get("dataset_hashes"):
        raise ValueError("cached Parquet hashes changed; reacquire before certifying coverage")
    prior = {r.get("filename"):r for r in previous.get("archives", [])}
    records = []
    for item in manifest(as_of):
        record = dict(prior.get(item["filename"], item))
        path = root / "raw" / item["filename"]
        expected = record.get("sha256")
        if record.get("status") != "verified" or not expected or not path.exists():
            record.update(status="missing", error="no previously verified cached archive")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            record.update(status="invalid", error="cached archive checksum mismatch")
        records.append(record)
    supplemental = previous.get("repair_sources", [])
    for record in supplemental:
        path = root / "raw" / record["filename"]
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError("cached repair evidence checksum mismatch")
    report = audit_frames(load_frames(root), as_of, policy or previous.get("coverage_policy"))
    report["repair_sources"] = supplemental
    report.update(archives=records, dataset_hashes=hashes, audit_mode="cached_hash_verified",
                  audited_at=pd.Timestamp.now(tz="UTC").isoformat())
    report["release_ready"] &= all(r["status"] == "verified" for r in records)
    (root / "coverage.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
