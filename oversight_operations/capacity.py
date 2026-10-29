"""Synthetic-only local capacity measurement; never claims ChatGPT host concurrency."""
from __future__ import annotations

from time import perf_counter_ns
from .api import OperationsService, _quantile
from .models import CapacityReport, ResultCode, SealedIntake


def simulate_capacity(logical_workers: int = 500, events: int = 800,
                      queue_cap: int = 600) -> CapacityReport:
    if not (1 <= logical_workers <= 10000 and 1 <= events <= 100000
            and 1 <= queue_cap <= 100000):
        raise ValueError("unsupported synthetic test dimensions")
    # Mock contracts test admission and queue cost, never simulate real keys/host.
    s = OperationsService({"queue_cap": queue_cap,
                           "verify_sealed_receipt": lambda receipt, blob: True,
                           "private_key_available": lambda: False})
    latencies: list[float] = []
    accepted = 0
    backpressured = 0
    max_depth = 0
    begin = perf_counter_ns()
    for n in range(events):
        # Subject placement ranges over logical worker identities, without PII.
        subject = n % logical_workers
        intake = SealedIntake("c_" + f"{n:032x}", "ev_" + f"{n:032x}",
                              f"{n + 101:064x}", f"{n + 201:064x}",
                              "STANDARD", 0, n, "sub_" + f"{subject:032x}")
        start = perf_counter_ns()
        result = s.intake(intake, n)
        latencies.append((perf_counter_ns() - start) / 1_000_000)
        if result.code == ResultCode.BACKPRESSURE:
            backpressured += 1
        elif result.code == ResultCode.PENDING:
            accepted += 1
        else:
            raise RuntimeError("unexpected synthetic intake outcome")
        max_depth = max(max_depth, len(s.cases))
    elapsed = (perf_counter_ns() - begin) / 1_000_000
    return CapacityReport(logical_workers, events, round(elapsed, 4),
                          round(_quantile(latencies, .50) or 0, 5),
                          round(_quantile(latencies, .95) or 0, 5),
                          round(_quantile(latencies, .99) or 0, 5),
                          max_depth, backpressured, accepted, queue_cap,
                          "QUEUE_ADMISSION_BACKPRESSURE" if backpressured
                          else "LOCAL_IN_MEMORY_CPU_ONLY")
