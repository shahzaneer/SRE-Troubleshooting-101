# SRE Troubleshooting Bible

> The definitive production incident response & debugging reference for Site Reliability Engineers

---

## Quick Reference

| # | Section | Description |
|---|---------|-------------|
| 00 | [Foundations](./00-foundations) | First principles, mental models, blame-free culture |
| 01 | [Linux Debugging](./01-linux-debugging) | CPU, memory, disk, processes, strace, perf |
| 02 | [Networking](./02-networking) | DNS, TCP/IP, TLS, packet analysis, CDNs |
| 03 | [AWS](./03-aws) | EC2, RDS, ALB, IAM, CloudWatch, billing alerts |
| 04 | [Containers](./04-containers) | Docker, containerd, exit codes, nsenter, image builds |
| 05 | [Kubernetes](./05-kubernetes) | Pods, controllers, networking, storage, security, cluster ops |
| 06 | [Databases](./06-databases) | Connection pools, replication lag, query tuning, deadlocks |
| 07 | [API Troubleshooting](./07-api-troubleshooting) | Status codes, rate limiting, timeouts, retry storms |
| 08 | [Error Codes](./08-error-codes) | HTTP 5xx/4xx, gRPC, database errors, cloud SDK codes |
| 09 | [Observability](./09-observability) | Metrics, logs, traces, dashboards, alerting philosophy |
| 10 | [Performance](./10-performance) | Profiling, load testing, caching, garbage collection |
| 11 | [On-Call Runbooks](./11-oncall-runbooks) | Play-by-play response guides for common incidents |
| 12 | [10x SRE Playbooks](./12-10x-sre-playbooks) | Advanced patterns: chaos engineering, capacity planning |
| 13 | [Security Incidents](./13-security-incidents) | Credential leaks, DDoS, CVE response, forensic capture |
| 14 | [CI/CD](./14-ci-cd) | Pipeline failures, flaky tests, canary analysis, GitOps |
| 15 | [Messaging & Queues](./15-messaging-queues) | Kafka, RabbitMQ, SQS, dead-letter queues, backpressure |
| 16 | [Scripts & Toolkit](./16-scripts-toolkit) | Diagnostic one-liners, automation scripts, aliases |
| GL | [Glossary](GLOSSARY) | Terminology, acronyms, TLAs decoded |

---

## How to Use This Repo During an Incident

- Open [**Runbooks**](./11-oncall-runbooks) first --- pick the matching playbook and follow it step-by-step.
- Use [**Scripts & Toolkit**](./16-scripts-toolkit) for rapid diagnostic one-liners when every second counts.
- Cross-reference [**Error Codes**](./08-error-codes) if you hit an unfamiliar error string.
- Deep-dive into [**Linux Debugging**](./01-linux-debugging) or [**Networking**](./02-networking) once immediate mitigation is in place.
- **Always** contribute a retro entry and update the runbook after the incident resolves.

---

## On-Call First 5 Minutes Checklist

| Minute | Action | Details |
|--------|--------|---------|
| 0  | **Detect** | Alert received, acknowledge it in PagerDuty/OpsGenie |
| 1  | **Triage** | What's the blast radius? What's the severity level? |
| 2  | **Communicate** | Declare incident channel (#incident-xxx), `@oncall` the team |
| 3  | **Investigate** | Check dashboards, recent deploys, log spikes, APM anomalies |
| 4  | **Mitigate** | Rollback, scale up, toggle feature flag, or circuit break |
| 5  | **Status Update** | Post summary to stakeholders: what we know, what we're doing |

---

## Severity Classification

| Severity | Definition | Response SLA | Examples |
|----------|-----------|--------------|----------|
| P0 - Critical | Complete outage, data loss, security breach | 5 min ack, 15 min mitigate | Payment system down, all users 503 |
| P1 - High | Major feature broken, significant degradation | 15 min ack, 1 hr mitigate | Search not working, 50% error rate |
| P2 - Medium | Partial degradation, workaround exists | 30 min ack, 4 hr mitigate | Admin panel slow, single region degraded |
| P3 - Low | Minor bug, cosmetic issue | Next business day | Typo, non-critical UI glitch |

---

## Top Runbooks by Frequency

1. [service-down](./11-oncall-runbooks/service-down.md) --- Service unreachable or returning 5xx
2. [high-error-rate](./11-oncall-runbooks/high-error-rate.md) --- Spiking error budget burn
3. [deployment-rollback](./11-oncall-runbooks/deployment-rollback.md) --- Rollback a bad deploy fast
4. [database-connection-exhaustion](./11-oncall-runbooks/database-connection-exhaustion.md) --- Connection pool drained
5. [disk-full-emergency](./11-oncall-runbooks/disk-full-emergency.md) --- Node disk at 95%+

---

> *Built by SREs, for SREs. Contribute your incident learnings.*
