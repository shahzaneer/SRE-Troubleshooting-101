# 11 — 10x SRE Playbooks

> The difference between a good SRE and a 10x SRE isn't tool knowledge — it's judgment, intuition for systems behavior, and the ability to remain calm and effective during incidents. This section captures the mental models, advanced techniques, and leadership patterns used by the best SREs in the world.

---

## Section Contents

| # | Document | Description |
|---|----------|-------------|
| 1 | [The 10x SRE Mindset](10x-mindset.md) | 5 Whys, blameless culture, incident communication, toil elimination, SLO-based decision making |
| 2 | [Advanced Debugging Tricks](advanced-debugging-tricks.md) | gdb, perf, bpftrace, tcpdump, /proc, auditd, inotifywait — low-level diagnostic weapons |
| 3 | [Chaos Engineering](chaos-engineering.md) | Netflix model, tc netem, stress-ng, toxiproxy, Kubernetes chaos monkey, experiment design |
| 4 | [Capacity Planning](capacity-planning.md) | Traffic forecasting, resource extrapolation, headroom calculation, DB IOPS planning, cost modeling |
| 5 | [Incident Command](incident-command.md) | IC role, communication channels, delegation, timeline keeping, severity escalation, post-mortem execution |

---

## What Makes a 10x SRE?

```
10x SREs don't know 10x more commands. They:
  1. Ask "why" 5 times instead of stopping at the first symptom
  2. See patterns across incidents (this crash looks like last month's DB pool exhaustion)
  3. Communicate clearly during chaos (status pages, timelines, delegation)
  4. Automate themselves out of toil (every manual task is a bug)
  5. Use error budgets to make data-driven decisions, not gut-feel
  6. Design experiments to prove system resilience (not assume it)
  7. Read flame graphs and kernel traces, not just dashboard panels
  8. Keep calm when everything is on fire (incident command is a skill, not luck)
```

---

## Prerequisites

- 2+ years of oncall experience (you've seen enough incidents to recognize patterns)
- Deep understanding of at least one OS (Linux kernel basics, networking stack, memory management)
- Comfort with production environments (you can SSH into a production box without fear)
- Basic understanding of distributed systems (CAP theorem, consensus, leader election)

---

## Learning Path

- **Beginner**: Read 10x Mindset → apply 5 Whys to your next incident
- **Intermediate**: Practice Advanced Debugging → attach perf to a running process, generate a flamegraph
- **Advanced**: Design a Chaos Experiment → test your system's response to Redis failure
- **Master**: Lead an Incident → serve as Incident Commander for a real production incident
- **Legend**: Teach this to others → the best way to master something is to train someone else

---

*Previous Section: [10 — Networking](../networking/)*
