"""Persist readiness samples and estimate availability over a declared observation window."""

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


def probe(session, url, timeout):
    started = time.monotonic()
    status, error, ok = None, None, False
    try:
        response = session.get(url, timeout=timeout)
        status = response.status_code
        # A proxy/error page with HTTP 200 is not a healthy backend.
        ok = status == 200 and response.json().get("status") == "ok"
    except (requests.RequestException, ValueError, AttributeError) as exc:
        error = type(exc).__name__
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ok": ok,
        "status_code": status,
        "latency_seconds": round(time.monotonic() - started, 6),
        "error_kind": error,
    }


def summarize(samples, elapsed, *, target=95.0, required_window=86400.0, interval=30.0):
    if (
        not all(math.isfinite(v) for v in (elapsed, interval, required_window, target))
        or elapsed < 0
        or interval <= 0
        or required_window <= 0
        or not 0 <= target <= 100
    ):
        raise ValueError("Invalid monitoring window or target")
    observed_up = observed_down = 0.0
    for i, sample in enumerate(samples):
        start = max(0.0, sample["elapsed_seconds"])
        end = min(elapsed, samples[i + 1]["elapsed_seconds"] if i + 1 < len(samples) else elapsed)
        # Do not treat an unobserved long gap as continued uptime.
        duration = max(0.0, min(end - start, interval * 2))
        if sample["ok"]:
            observed_up += duration
        else:
            observed_down += duration
    known = observed_up + observed_down
    coverage = min(1.0, known / elapsed) if elapsed else 0.0
    availability = 100 * observed_up / known if known else None
    complete = elapsed >= required_window and coverage >= 0.99 and len(samples) >= 2
    return {
        "method": "sample-and-hold, gaps over 2 intervals are unknown",
        "samples": len(samples),
        "successful_samples": sum(s["ok"] for s in samples),
        "elapsed_seconds": round(elapsed, 6),
        "estimated_up_seconds": round(observed_up, 6),
        "estimated_down_seconds": round(observed_down, 6),
        "unknown_seconds": round(max(0.0, elapsed - known), 6),
        "observation_coverage": coverage,
        "estimated_availability_percent": availability,
        "target_percent": target,
        "required_window_seconds": required_window,
        "window_complete": complete,
        "meets_target": availability >= target if complete and availability is not None else None,
    }


def monitor(url, *, duration, interval, timeout, samples_path, target=95, required_window=86400):
    if (
        not all(math.isfinite(v) for v in (duration, interval, timeout, target, required_window))
        or duration <= 0
        or not 0 < interval <= 60
        or timeout <= 0
        or not 0 <= target <= 100
        or required_window <= 0
    ):
        raise ValueError("Duration/timeout/window must be positive; interval must be in (0, 60]")
    samples = []
    started = time.monotonic()
    samples_path = Path(samples_path)
    samples_path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents mixing different monitoring windows by accident.
    with samples_path.open("x", encoding="utf-8") as output, requests.Session() as session:
        session.trust_env = False
        try:
            while time.monotonic() - started < duration:
                elapsed = time.monotonic() - started
                sample = probe(session, url, min(timeout, max(0.01, duration - elapsed)))
                sample["elapsed_seconds"] = elapsed
                samples.append(sample)
                output.write(json.dumps(sample) + "\n")
                output.flush()
                remaining = duration - (time.monotonic() - started)
                if remaining > 0:
                    time.sleep(min(max(0.0, interval - sample["latency_seconds"]), remaining))
        except KeyboardInterrupt:
            pass
    return summarize(
        samples, time.monotonic() - started, target=target, required_window=required_window, interval=interval
    )


def load_samples(path):
    path = Path(path)
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Samples file exceeds 64 MiB")
    samples = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    previous = -1.0
    for sample in samples:
        if not isinstance(sample, dict):
            raise ValueError("Monitoring samples must be objects")
        elapsed = sample.get("elapsed_seconds")
        latency = sample.get("latency_seconds")
        if (
            type(elapsed) not in (int, float)
            or not math.isfinite(elapsed)
            or elapsed < 0
            or elapsed <= previous
            or type(latency) not in (int, float)
            or not math.isfinite(latency)
            or latency < 0
            or type(sample.get("ok")) is not bool
        ):
            raise ValueError("Invalid or non-monotonic monitoring samples")
        previous = elapsed
    return samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000/health")
    parser.add_argument("--duration", type=float, default=86400)
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--target", type=float, default=95)
    parser.add_argument("--required-window", type=float, default=86400)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--summarize", action="store_true", help="Read an existing samples file instead of probing")
    parser.add_argument("--elapsed", type=float, help="Total window including unobserved time; used with --summarize")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.report and args.report.resolve() == args.samples.resolve():
        parser.error("Report and samples must be different files")
    try:
        if args.summarize:
            samples = load_samples(args.samples)
            elapsed = (
                args.elapsed
                if args.elapsed is not None
                else (samples[-1]["elapsed_seconds"] + samples[-1]["latency_seconds"] if samples else 0)
            )
            if samples and elapsed < samples[-1]["elapsed_seconds"]:
                raise ValueError("Elapsed window cannot end before the last sample")
            report = summarize(
                samples, elapsed, interval=args.interval, target=args.target, required_window=args.required_window
            )
        else:
            report = monitor(
                args.url,
                duration=args.duration,
                interval=args.interval,
                timeout=args.timeout,
                target=args.target,
                required_window=args.required_window,
                samples_path=args.samples,
            )
    except (ValueError, OSError):
        parser.error("Invalid parameters or samples file already exists/is not writable")
    encoded = json.dumps(report, indent=2, allow_nan=False)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 1 if report["meets_target"] is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
