# On-Call Runbooks

> **SRE Troubleshooting Bible** — Production incident response procedures.
> **Version:** 1.0 | **Last Updated:** 2026-06

---

## Runbook Quick-Reference Table

| # | Runbook | Trigger Condition | Severity | Est. Time to Mitigate |
|---|---------|-------------------|----------|----------------------|
| 1 | [High Error Rate](high-error-rate.md) | Error rate >5% for 5 min | P1 | 15-30 min |
| 2 | [High Latency](high-latency.md) | p95 latency > threshold for 5 min | P2/P1 | 15-30 min |
| 3 | [Service Down / 503](service-down.md) | Health check failing, 0% availability | P0 | <15 min |
| 4 | [DB Connection Exhaustion](database-connection-exhaustion.md) | "too many connections" errors | P1 | 10-20 min |
| 5 | [Disk Full Emergency](disk-full-emergency.md) | Disk >85% or "No space left on device" | P1 | 10-15 min |
| 6 | [Memory Leak in Production](memory-leak-in-production.md) | Memory monotonically increasing, OOM risk | P1 | 15-30 min |
| 7 | [DDoS Under Attack](ddos-under-attack.md) | 10x+ traffic spike, 503s, network sat | P0 | Ongoing |
| 8 | [SSL Certificate Expired](ssl-cert-expired.md) | NET::ERR_CERT_DATE_INVALID | P0 | 10-20 min |
| 9 | [Deployment Rollback](deployment-rollback.md) | Post-deploy error/latency spike | P1 | 5-15 min |
| 10 | [Cascading Failure](cascading-failure.md) | Multiple unrelated services failing | P0 | 30-60+ min |

---

## How to Use These Runbooks

1. **Start here.** Identify the alert that fired and navigate to the matching runbook.
2. **Follow steps sequentially.** Each runbook is ordered from fastest/cheapest to deepest/most-expensive actions.
3. **Respect abort criteria.** If you hit the timebox without progress, escalate immediately.
4. **Update the runbook.** After every incident, file a PR to improve the runbook based on what you learned.

---

## Incident Severity Definitions

| Severity | Definition | Response Time | Escalation |
|----------|-----------|---------------|------------|
| **P0** | Service completely unavailable. All users affected. Revenue impact. | 5 min acknowledge | Incident Commander + Eng Mgmt |
| **P1** | Major feature broken. Many users affected. No workaround. | 15 min acknowledge | Tech Lead + On-Call |
| **P2** | Minor degradation. Some users affected. Workaround exists. | 30 min acknowledge | On-Call only |
| **P3** | Cosmetic issue. No user impact. | Next business day | Backlog |

---

## Incident Communication Template

```
🚨 INCIDENT: [brief title]
Severity: P0/P1/P2
Start Time: HH:MM UTC
Runbook: [link to runbook]

Summary: [1-2 sentences]

Actions Taken:
- [action 1]
- [action 2]

Next Steps:
- [step 1]
- [step 2]

Earliest Mitigation ETA: HH:MM UTC
```

---

## Post-Incident Checklist

- [ ] Incident ticket filed with timeline
- [ ] Blameless post-mortem scheduled (within 5 business days for P0/P1)
- [ ] Action items created and assigned
- [ ] Runbook updated with lessons learned
- [ ] Monitoring gaps identified and tracked
- [ ] Automated test added to prevent same root cause
