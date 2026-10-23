"""Reasoning trace shared by all agents. Every entry is saved to outputs/last_run.json."""
import time


class Tracer:
    def __init__(self):
        self.entries = []
        self._t0 = time.perf_counter()

    def log(self, agent, step, reasoning, **data):
        entry = {
            "seq": len(self.entries) + 1,
            "t_ms": round((time.perf_counter() - self._t0) * 1000, 1),
            "agent": agent,
            "step": step,
            "reasoning": reasoning,
            "data": data,
        }
        self.entries.append(entry)
        print(f"[{entry['seq']:02d}] {agent:<12} {step}: {reasoning}")
        return entry
