# 00 — Foundations

> **Core building blocks of SRE troubleshooting.**
> Read this section first if you're new to SRE or on-call. Everything else in this repository builds on these fundamentals.

---

## Quick Navigation

| # | Document | What You'll Learn | Time to Read |
|---|----------|-------------------|--------------|
| 1 | [SRE Mindset](sre-mindset.md) | Error budgets, SLO/SLI/SLA, toil reduction, worst practices | 15 min |
| 2 | [Incident Lifecycle](incident-lifecycle.md) | Detection → post-mortem: the full incident flow with timelines | 12 min |
| 3 | [Blameless Post-Mortem Template](blameless-postmortem-template.md) | How to write a post-mortem that actually prevents recurrence | 10 min |
| 4 | [On-Call Survival Guide](oncall-survival-guide.md) | Mental health, escalation paths, handoff, burnout prevention | 8 min |
| 5 | [Debugging Methodology](debugging-methodology.md) | USE method, RED method, half-split, scientific debugging | 15 min |

---

## Why This Section Matters

A firefighter doesn't grab a hose and run toward flames without training. You shouldn't troubleshoot production without a mental model either. These foundations give you:

- **A shared vocabulary**: When someone says "error budget is exhausted," you know exactly what that means and what it triggers.
- **A repeatable process**: Incidents are stressful. Process reduces cognitive load so you can focus on the problem.
- **Guardrails**: Knowing when to escalate, when to wake someone up, and when to call it quits saves your health and the company's revenue.

---

## Recommended Reading Order

1. **SRE Mindset** — Understand *why* we do things differently from traditional ops.
2. **Debugging Methodology** — Learn *how* to approach any unknown failure systematically.
3. **Incident Lifecycle** — Know *what* to do at each stage of an incident.
4. **Blameless Post-Mortem Template** — Learn how to *learn* from incidents.
5. **On-Call Survival Guide** — Prepare for the *human* side of being on-call.

---

## Key Terms Cheat Sheet

| Term | Definition |
|------|-----------|
| **SLI** | Service Level Indicator — what you measure (e.g., latency p99 < 500ms) |
| **SLO** | Service Level Objective — the internal target (e.g., 99.9% of requests meet SLI) |
| **SLA** | Service Level Agreement — the external promise with legal/financial consequences |
| **Error Budget** | 1 - SLO. The acceptable amount of unreliability you can "spend" before freezing features |
| **Toil** | Manual, repetitive, automatable, tactical work with no enduring value |
| **MTTD** | Mean Time to Detect — how long before you know something is broken |
| **MTTR** | Mean Time to Resolve/Recover — how long to fix it (or mitigate it) |
| **Blast Radius** | The scope of impact. One user? One DC? Global? |
| **P0/P1/P2/P3** | Severity levels — P0 = critical/global outage, P1 = major degradation, P2 = minor, P3 = cosmetic |
| **Runbook** | Step-by-step operational procedure for handling a known failure mode |
| **Playbook** | Higher-level incident response plan (who does what, how to communicate) |

---

## References

- [Google SRE Book — Chapter 2: The Production Environment at Google](https://sre.google/sre-book/production-environment/)
- [Google SRE Book — Chapter 4: Service Level Objectives](https://sre.google/sre-book/service-level-objectives/)
- [Google SRE Workbook — Chapter 5: Eliminating Toil](https://sre.google/workbook/eliminating-toil/)
- [Brendan Gregg — USE Method](https://www.brendangregg.com/usemethod.html)
- [Tom Wilkie — RED Method (Weaveworks blog)](https://grafana.com/blog/2018/08/02/the-red-method-how-to-instrument-your-services/)
