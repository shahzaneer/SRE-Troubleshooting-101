# Incident Command
> **Category:** 10x SRE | Incident Command | Leadership
> **Difficulty:** Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#incident` `#command` `#leadership` `#10x`

---

## The Incident Commander (IC) Role

The IC does NOT fix the problem. The IC ensures the problem gets fixed by coordinating the people who can fix it. The most common mistake: an IC who starts debugging instead of commanding.

### IC Responsibilities

```
1. Declare incident severity immediately (don't wait for consensus)
2. Assign roles: IC, Communications Lead, Operations Lead(s), Subject Matter Experts
3. Maintain a real-time timeline (this becomes the post-mortem)
4. Decide escalation path (when to pull in VPs, when to wake up the DB team)
5. Protect the fixers from distractions (no one bothers the Ops Lead except the IC)
6. Call "all clear" when service is restored
7. Ensure post-mortem is scheduled within 48 hours
```

### What the IC Does NOT Do

```
❌ Debug
❌ Write code
❌ SSH into servers
❌ Read log files
❌ Query databases
❌ Answer stakeholder DMs (delegate to Communications Lead)

The IC's hands should be on the keyboard for:
  ✓ Typing timeline updates
  ✓ Coordinating in the incident Slack channel
  ✓ Making go/no-go decisions
```

---

## Incident Roles

### The Minimum Viable Incident Response Team

```
┌─────────────────────────────────────────────────────────────┐
│                    INCIDENT COMMANDER                       │
│  "Amir" — Coordinates, delegates, maintains timeline       │
│  Does NOT debug. Makes decisions.                          │
└──────────┬─────────────────────────┬────────────────────────┘
           │                         │
    ┌──────▼──────┐          ┌──────▼──────────┐
    │  OPS LEAD 1 │          │ COMMUNICATIONS   │
    │   "Bob"     │          │    LEAD          │
    │  Debugs DB  │          │   "Sarah"        │
    │  runbooks   │          │  Status page     │
    └─────────────┘          │  Stakeholder     │
                             │  updates         │
    ┌─────────────┐          └──────────────────┘
    │  OPS LEAD 2 │
    │  "Charlie"  │
    │  Rollback   │
    │  commands   │
    └─────────────┘

    ┌─────────────┐
    │  SME (DB)   │  ← Called in as needed
    │  "Diana"    │     Not part of initial response
    └─────────────┘
```

### Role Descriptions

| Role | Who | Responsibilities |
|------|-----|-----------------|
| **Incident Commander** | The most experienced SRE available | Declare severity, assign roles, maintain timeline, make decisions, escalate |
| **Communications Lead** | Someone with strong writing skills | Status page updates, Slack announcements, stakeholder emails, PR coordination |
| **Operations Lead** | The best debugger on the team | Investigate root cause, execute fixes, coordinate with SMEs |
| **SME** | Domain expert (DB, network, security) | Consult on specific systems. On standby until called. |

**Never have more than 1 IC.** If the current IC needs to hand off (fatigue, end of shift), explicitly announce the handoff.

---

## Communication Channels

### Incident Slack Channel

```
Channel: #inc-20260611-payment-outage
Topic: "P0 — Payment processing down. IC: Amir. Started 10:14 UTC."

Rules for this channel:
  1. IC posts major decisions and status updates in main channel
  2. ALL debugging goes in THREADS under IC's status updates
  3. NO DMs — all communication is visible to the team
  4. If someone joins late, they can scroll up and catch up instantly
  5. When incident is resolved: lock the channel (read-only). Archive after post-mortem.
```

### Status Page Updates

```
Cadence:
  P0: Every 15 minutes
  P1: Every 30 minutes
  P2: Every 60 minutes

Even if the update is "still investigating" — the silence is worse than the repetition.

Templates (copy-paste — don't write from scratch at 3 AM):

INVESTIGATING:
  "We are investigating [SYMPTOM]. Impact: [WHAT USERS EXPERIENCE].
   [CORE FUNCTIONALITY] remains operational.
   Next update: [TIME]. [Current UTC time]"

IDENTIFIED:
  "We have identified the cause: [BRIEF ROOT CAUSE]. We are [ACTION TAKEN].
   ETA to resolution: approximately [X] minutes.
   Next update: [TIME]. [Current UTC time]"

RESOLVED:
  "[SERVICE] has been restored. [SYMPTOM] is no longer occurring.
   We are monitoring to confirm the fix.
   A post-mortem will be published within 48 hours.
   Next update: [TIME] (or 'This is the final update'). [Current time]"
```

### Stakeholder Communication

```
Internal email template (for execs, PMs, customer success):

Subject: [P0] Payment Processing Degradation — RESOLVED

Duration: 11 minutes (10:14 UTC — 10:25 UTC)

Impact:
  - Payment processing failed for 100% of users
  - Checkout and cart remained functional
  - Estimated $2,200 in lost revenue
  - No data loss. No security impact. No PII exposure.

Root Cause:
  Database connection pool exhausted after deployment v4.2.1
  changed query patterns from 1 to 3 connections per request.

Resolution:
  Service restarted (immediate fix). Pool limits increased (permanent fix).

Next Steps:
  Post-mortem scheduled for June 13. Action items will be tracked in JIRA.

Questions: Reply to this thread or contact IC (Amir) directly.
```

---

## Delegation — The IC's Primary Skill

### How to Delegate Effectively

```
BAD: "Someone should check the database."
  → Everyone thinks someone else will do it. Nothing happens.

GOOD: "Bob, check if the payment-service DB connection pool is exhausted.
       Look at RDS metrics and HikariCP metrics. Report back in 5 minutes."
  → One person. Specific task. Specific deadline.

BAD: "Let me know what you find."
  → No deadline. No format. IC has to chase.

GOOD: "Bob, update in thread by 10:20 — is pool exhausted? yes/no + metric values."
  → Clear deliverable. Threaded communication. Time-boxed.
```

### Delegation Flow for a P0 Incident

```
IC (10:15):
  "Assignments:
   @bob — Check payment-service error logs and DB metrics. What error are we seeing?
   @charlie — Check recent deployments. What changed in the last 30 minutes?
   @sarah — Prep status page update for 10:25. Draft below. Start comms timer.
   @diana (SME) — Stand by. We may need DB expertise.

   Report back by 10:20.
   I'm maintaining the timeline. All updates in this channel."

Ops Lead (Bob) at 10:18:
  "Thread: DB investigation findings
   - payment-service logs: HikariPool-1 - Connection is not available, timed out
   - RDS CPU at 92% (normally 35%)
   - Connections active: 20/20 (maxed out)
   - Connections pending: 340 (!!!!)
   → Conclusion: Connection pool exhausted."

Ops Lead (Charlie) at 10:17:
  "Thread: Deployment investigation
   - v4.2.1 deployed at 10:05 (9 minutes ago)
   - Changes: 'Optimize recommendation queries — batch user+product lookup'
   - SUSPECT: This deployment is the trigger."

IC (10:19):
  "Analysis: v4.2.1 deployed at 10:05. Connection pool exhausted at 10:14.
   Decision: ROLLBACK to v4.2.0.

   @charlie — Execute rollback. k8s rollout undo deployment/payment-service.
   @sarah — Update status page: 'Identified: recent deployment causing DB load. Rolling back. ETA 5 min.'
   @bob — Monitor DB metrics during rollback. Alert me if anything unexpected."

Charlie (10:22):
  "Thread: Rollback status
   - kubectl rollout undo deployment/payment-service -n production
   - New pods spinning up. Old pods terminating.
   - Error rate dropping: 45% → 12% → 2% → 0.01%"

IC (10:25):
  "@sarah: Post 'RESOLVED' status update. All metrics normal.
   I'm calling this incident resolved at 10:25 UTC.
   Duration: 11 minutes.

   Post-mortem scheduled for June 13. Please drop your notes in this doc: [link].

   Great work everyone. Rest if you can.

   #incident-closed"
```

---

## Timeline Keeping — The IC's Log

The timeline is the single most important artifact from an incident. Without it, the post-mortem becomes a debate about who remembered what.

### Real-Time Timeline Format

```
10:14 — Alert: PagerDuty #4823 — payment-service error rate > 5%
10:15 — Acknowledged by IC (Amir). Incident declared P0.
10:15 — #inc-20260611-payment-outage channel created.
10:16 — Bob assigned: investigate DB. Charlie assigned: check deployments.
10:17 — Charlie reports: v4.2.1 deployed at 10:05. Suspect deployment.
10:18 — Bob reports: DB connection pool exhausted. 340 pending connections.
10:19 — Decision: Rollback to v4.2.0. Charlie executing.
10:20 — Status page updated: "Identified: deployment causing DB load. Rolling back."
10:22 — Rollback complete. Error rate dropping.
10:23 — Metrics normalizing. CPU 35%. Error rate 0.01%.
10:25 — IC declares resolved. Status page updated: "RESOLVED."
```

### How to Keep Timeline During Chaos

```
1. Open a text editor (Notepad, TextEdit, vim — anything that autosaves).
2. Every decision, finding, or status change: write timestamp + 1 line.
3. Copy-paste into Slack channel every 10 minutes (or when asked).
4. After incident: clean up and publish. This IS the post-mortem skeleton.

PRO TIP: Record your screen. OBS or QuickTime. You can reconstruct the exact
         timeline from the recording if your notes are incomplete.
         (Don't share the recording — it's for your own reconstruction.)
```

---

## Severity Levels and Escalation

### Severity Definitions

```
P0 — CRITICAL (Page immediately)
  - Complete outage of a critical user path (login, checkout, payment)
  - Data loss or data corruption in progress
  - Security breach in progress
  - Response: All hands. IC + Comms Lead + 2 Ops Leads minimum.
  - Escalation: Page VP of Engineering if > 30 min without mitigation.

P1 — MAJOR (Page immediately)
  - Significant degradation of critical path (error rate > 5%, p99 > 5× SLO)
  - Partial outage (one region/AZ down, all others healthy)
  - Non-critical path completely down (recommendations, search)
  - Response: IC + Comms Lead + 1 Ops Lead.
  - Escalation: Page manager if > 2 hours without mitigation.

P2 — MINOR (Notify, investigate during business hours)
  - Minor degradation (error rate 1-5%, p99 2-5× SLO but self-recovering)
  - Single-instance issues (one pod crashing, others healthy)
  - Latency increase within SLO but trending wrong
  - Response: Oncall investigates. No IC needed unless it escalates.

P3 — TRIVIAL (Track, fix in next sprint)
  - Cosmetic issues
  - Non-critical metrics exceeding thresholds briefly
  - Warnings that self-resolved
  - Response: Create ticket. No immediate action.
```

### Escalation Criteria

```
P2 → P1:
  - Error rate > 5% for > 15 minutes
  - > 100 users reporting the issue
  - Issue spreading to additional services
  - Oncall engineer cannot identify cause within 30 minutes

P1 → P0:
  - Error rate > 50%
  - Complete outage of critical path
  - Data loss confirmed
  - Issue affecting multiple regions simultaneously
  - Security breach confirmed

P0 → Permanent:
  - Page VP of Engineering after 30 minutes without mitigation
  - Page CTO after 60 minutes
  - Activate disaster recovery plan after 90 minutes
```

---

## The Post-Incident Handoff

### Immediate (Within 30 Minutes of Resolution)

```
1. IC sends "all-clear" message to incident channel and any escalation channels.
2. Monitoring period declared (2 hours for P0, 1 hour for P1).
   IC or designated engineer watches dashboards for regression.
3. If no regression after monitoring period → incident truly resolved.
```

### Post-Mortem (Within 48 Hours)

```
1. IC cleans up timeline → becomes the post-mortem skeleton.
2. All participants add their observations.
3. 5 Whys analysis conducted.
4. Action items assigned with owners and deadlines.
5. Post-mortem published to the engineering org.

Post-mortem template is in: [10x Mindset → Blameless Post-Mortem Template](10x-mindset.md)
```

---

## The IC's Mental Checklist

When you're paged at 3 AM and your brain is foggy, run through this:

```
□ 1. DECLARE: "I am IC. This is [P0/P1/P2]. Incident start: [time]."
□ 2. CHANNEL: Create #inc-[DATE]-[NAME]. All comms here. No DMs.
□ 3. ROLES: "Bob = Ops Lead. Sarah = Comms Lead. Others = SME standby."
□ 4. TIMELINE: Open text editor. Start logging every decision.
□ 5. STATUS: Post "Investigating" on status page. Set 15-min timer.
□ 6. INVESTIGATE: Assign specific hypotheses to specific people.
□ 7. DECIDE: When hypothesis confirmed → decide: rollback? restart? scale?
□ 8. COMMUNICATE: Comms Lead posts update at timer expiry. Repeat.
□ 9. ESCALATE: If >30 min without progress → escalate per severity policy.
□ 10. RESOLVE: When metrics return to baseline → declare resolved.
□ 11. MONITOR: Watch dashboards for 2 hours before truly closing.
□ 12. POSTMORTEM: Schedule within 48 hours. Publish timeline.
```

---

## Real Scenario: Perfect IC Execution

```
Incident: Payment processing completely down at 10:14 AM on a Tuesday.
          IC: Amir (experienced SRE, 3rd time as IC).
          Impact: All checkout attempts failing. Revenue loss ~$12K/hour.

10:14 — Alert fires. Amir acknowledges within 10 seconds.
10:15 — Amir declares: "I am IC. P0. Payment processing down."
         Creates #inc-20260611-payment-outage.
         Assigns: Bob (Ops Lead), Sarah (Comms Lead).
10:16 — Sarah posts status page: "Investigating — checkout failing."
         Sets 15-min timer.
10:17 — Amir assigns: "Bob, check payment-service logs for 5xx errors.
         Charlie, check deployments in last 2 hours."
10:18 — Charlie: "v4.2.1 deployed at 10:05. Changes to connection pooling."
10:19 — Bob: "HikariPool-1 exhausted. 340 pending connections.
              Deployment changed pool config from 'maximumPoolSize=50' to 'maximumPoolSize=20'."
10:20 — Amir: "Root cause confirmed. Decision: rollback to v4.2.0.
              Charlie, execute rollback. Sarah, update status: 'Identified —
              deployment issue. Rolling back. ETA 5 min.'"
10:22 — Rollback complete. Error rate dropping.
10:23 — Amir: "Bob, confirm metrics are clean." Bob: "All green. Error rate 0.01%."
10:25 — Amir: "Incident resolved. 11 minutes duration.
              Sarah, post final status update. Post-mortem Friday."

Result: 11-minute MTTR. $2,200 lost instead of $12,000+.
        Clean timeline for post-mortem. No confusion. No panic.
        Engineering VP sent: "Cleanest incident I've seen. Thank you."

Key to success:
  - Amir did NOT touch a terminal the entire time
  - Bob and Charlie were NOT distracted by status updates or stakeholder questions
  - Sarah handled ALL external communication so engineers could focus
  - Every decision logged in timeline immediately
```

---

## Common IC Mistakes

1. **IC starts debugging**: The IC who SSHes into a server has abandoned their post. The team has no coordinator.
2. **No timeline**: "I'll remember what happened." You won't. Write it down in real-time.
3. **Vague assignments**: "Someone check the database." → Nothing happens. Be specific.
4. **No escalation**: "We can fix this in 5 more minutes." → 45 minutes later, still down. Escalate early.
5. **No regular updates**: 30 minutes of silence on status page. Users and execs assume the worst.
6. **Too many people in the channel**: 25 people all speculating. Channel becomes noise. Keep it to IC + assigned roles. Others watch silently.
7. **No post-incident monitoring**: Declare resolved at 10:25, leave at 10:26. Issue returns at 10:45 and no one is watching.

---

*See also: [10x Mindset](10x-mindset.md) | [Chaos Engineering](chaos-engineering.md) | [Capacity Planning](capacity-planning.md)*
