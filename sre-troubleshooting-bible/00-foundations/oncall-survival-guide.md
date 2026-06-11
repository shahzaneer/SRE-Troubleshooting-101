# On-Call Survival Guide

> **Category:** Foundations | On-Call
> **Difficulty:** Basic
> **Last Reviewed:** 2026-06
> **Tags:** `#oncall` `#mental-health` `#process`

---

## Table of Contents

1. [Mental Model](#mental-model)
2. [Preparation (Before Your Shift)](#preparation-before-your-shift)
3. [During an Incident](#during-an-incident)
4. [Escalation Paths](#escalation-paths)
5. [Handover](#handover)
6. [Burnout Prevention](#burnout-prevention)
7. [Real-World Scenarios](#real-world-scenarios)
8. [On-Call Checklist](#on-call-checklist)

---

## Mental Model

### You Are Not a Hero

```text
YOUR JOB: Respond to alerts. Investigate. Mitigate. Escalate.
           Document. Hand off.

NOT YOUR JOB: Fix everything yourself. Never sleep. Never ask for help.
              Be the sole keeper of production knowledge.
```

### Core Principles

1. **Your health > the incident.** A sleep-deprived engineer is a liability. You will make worse decisions, miss obvious clues, and extend the outage. It is ALWAYS acceptable to escalate and rest.

2. **It's okay to escalate.** The on-call rotation EXISTS so you have backup. Waking someone up at 3 AM feels terrible — but the person who set up the rotation INTENDED for you to do it when needed. That's why there's a secondary, a tertiary, and an escalation policy.

3. **You don't need to know everything.** You need to know how to find the right dashboard, the right runbook, and the right person to call.

4. **Silence is worse than wrong information.** "Investigating, no ETA yet" posted every 10 minutes is infinitely better than radio silence.

---

## Preparation (Before Your Shift)

### The Day Before

```text
□ Verify laptop is working and can connect to VPN
  - Actually test the VPN connection. Don't assume it works.
  - Ensure you have the latest VPN client version.

□ Verify access to all critical systems:
  - PagerDuty/Opsgenie/VictorOps (acknowledge a test page)
  - AWS/GCP/Azure console (can you log in? MFA device working?)
  - Kubernetes clusters (kubectl cluster-info, are your certs valid?)
  - Grafana/Datadog/New Relic dashboards
  - Log aggregation (Loki/ELK/Splunk)
  - CI/CD pipeline (can you trigger a rollback?)
  - SSH keys (are they loaded in your ssh-agent?)
  - Slack/Teams/Discord (are you in #oncall and #incidents?)

□ Charge your laptop (100%)
□ Charge your phone (100%)
□ Have a backup internet connection (phone hotspot) available
□ Download or save offline copies of critical runbooks:
  - How to rollback each service
  - How to restart each service
  - How to scale up/down
  - How to check DB status
  - Escalation contacts (not just in Slack — in your phone contacts)

□ Set up your environment:
  - Open ALL dashboard tabs you might need (don't wait until an incident)
  - Pre-authenticate to kubectl, AWS CLI, SSH, etc.
  - Configure your terminal with the right contexts
```

### The Shift Handover (Incoming)

```text
FROM THE OUTGOING ON-CALL, GET:
  - Any ongoing or recently mitigated incidents?
  - Any flaky services to watch?
  - Any known issues with monitoring false positives?
  - Any scheduled maintenance or deploys during your shift?
  - Who is the secondary on-call? (Confirm their phone number)
  - Who is the escalation manager? (Confirm their phone number)
  - Any known gaps: "If X alerts, it's a false positive unless Y also alerts"

BEFORE THE OUTGOING ON-CALL LOGS OFF:
  - Verify you can receive pages (send a test notification)
  - Walk through the current state of dashboards together
```

---

## During an Incident

### The First 5 Minutes

```text
□ STAY CALM. Breathe. You've trained for this.

□ Acknowledge the page. Immediately. Even if you can't start
  investigating right away. "Ack" means "I've seen this and
  I'm taking ownership."

□ Open your pre-configured dashboard tabs. Don't spend 3 minutes
  finding the right Grafana URL.

□ DECLARE THE INCIDENT:
  /incident declare severity=[P0|P1|P2|P3] service=<name>

□ If P0 or P1: Page secondary on-call and comms lead.
  Don't wait. They can always stand down if it's a false alarm.
```

### Communication Cadence

```text
STAKEHOLDER UPDATES:
  - Every 15 minutes minimum for P0
  - Every 30 minutes for P1
  - Template: "Still investigating. Impact: [X]. Mitigation status: [Y]. ETA: [Z]."
  - "No update yet" IS an update. Post it.

INCIDENT CHANNEL UPDATES:
  - Every new finding: post immediately
  - Every action taken: post immediately ("Rolling back deploy #3847 now")
  - Every hypothesis: post before testing ("Checking if it's the DB lock")
  - Helps others join without asking "what's happening?"
  - Creates the post-mortem timeline for free

STATUS PAGE:
  - Update within 15 minutes of incident start for P0/P1
  - Update when mitigated
  - Update when resolved
  - Template: https://statuspage.io (or similar)
```

### Decision-Making Under Stress

```text
BAD DECISIONS COME FROM:
  - Panic ("Just restart everything!")
  - Fatigue (working 3+ hours straight on an incident)
  - Tunnel vision ("I'm SURE it's the cache")
  - Ego ("I don't need help, I've got this")

GOOD DECISIONS COME FROM:
  - Process ("I declare incident, page secondary, open dashboards")
  - Evidence ("The dashboard says X, log says Y. What do these point to?")
  - Collaboration ("Bob, what do you think about the DB hypothesis?")
  - Delegation ("Alice, check the network. Bob, check recent deploys.")

THE 15-MINUTE RULE:
  If you've been working on an incident for 15 minutes without progress:
    1. Escalate — bring in secondary or SME
    2. OR change approach — try a different hypothesis
    3. OR take a 2-minute break — step away, drink water, breathe
```

### The Oxygen Mask Principle

```text
"Put on your own oxygen mask before helping others."

TRANSLATED FOR ON-CALL:
  - If you haven't eaten in 6 hours: EAT
  - If you haven't slept in 20 hours: SLEEP (hand over first)
  - If you're shaking from adrenaline: step away for 5 minutes
  - If you feel sick: you are not helping. Hand over.

A P0 incident with a fresh engineer is better than
a P0 incident with an exhausted engineer.
```

---

## Escalation Paths

### Know These BEFORE an Incident

```text
ESCALATION LADDER (example):

Level 1: PRIMARY ON-CALL (you)
  - First responder
  - Typical: You handle 90% of alerts yourself

Level 2: SECONDARY ON-CALL
  - Backup. Gets paged if you don't ack in 5 minutes
  - You can also page them immediately if:
    - Incident is P0
    - You need a second pair of eyes
    - You're not sure about severity

Level 3: TEAM LEAD / TECH LEAD
  - Gets paged if neither primary nor secondary ack in 10 min
  - You should page them if:
    - You need to make a risky decision (rollback? failover?)
    - Incident has lasted > 30 min without progress
    - You need authority (e.g., "can we take down non-critical services?")

Level 4: ENGINEERING MANAGER
  - Gets paged if incident > 1 hour, or P0 > 30 min
  - Handles: stakeholder comms, resource allocation, "get Bob's team involved"

Level 5: DIRECTOR / VP
  - Gets paged if incident > 2 hours, or significant revenue at risk
  - Makes the call: "Do we wake up the entire org?"
```

### When to Escalate — Decision Matrix

```text
SITUATION                                   | ACTION
--------------------------------------------|-----------------------
You've been on it for 15 min, no progress   | Page secondary
The problem is in a service you don't own   | Page that team's on-call
You need to roll back but don't know how    | Page secondary / tech lead
You're considering a risky action (DB failover, DNS change) | Page tech lead
Incident > 1 hour                           | Page EM
Revenue being lost right now                | Page EM immediately
You've been awake 20+ hours                 | Hand over to secondary
You feel overwhelmed or panicked            | Page secondary (this is OKAY)
```

### Escalation Scripts

```text
PAGING SECONDARY (at 3 AM):
  "Hey {name}, sorry to wake you. We have a P0 on payment-api.
   Error rate is 100%. I've been on it for 15 minutes and could
   use another pair of eyes. Can you join?"

PAGING TECH LEAD:
  "Hey {name}, we have a P0 on payment-api, 30 min in.
   I've identified the issue as a DB migration lock, but I need
   a decision: should I kill the migration query or roll back the
   deployment? I need your call on this."

PAGING ANOTHER TEAM'S ON-CALL:
  "Hey {name}, this is {your_name} from the SRE team.
   We have a P0 on payment-api. We're seeing DB connection pool
   exhaustion and think the issue might be in the database layer.
   Can you take a look at the RDS cluster?"
```

---

## Handover

### Outgoing: What to Communicate

```text
AT THE END OF YOUR SHIFT, provide to the incoming on-call:

1. INCIDENT SUMMARY (if any):
   - What happened
   - Current status (resolved? mitigated? ongoing?)
   - Any monitoring needed

2. PENDING INVESTIGATIONS:
   - "We noticed a 10% increase in DB latency starting at 4 AM.
     I opened a ticket but didn't find the cause. Watch this."

3. KNOWN ISSUES:
   - "Alert X is known to false-positive during the 8 AM traffic ramp."
   - "Service Y has been flaky on Tuesdays (scheduled backup overlap)."

4. SCHEDULED CHANGES:
   - "Deploy of service Z scheduled for your shift at 10 AM."
   - "DB maintenance window 2-4 AM tomorrow."

5. YOUR CONTACT INFO (if you're the backup):
   - "I'll be reachable on my phone until noon, then off-grid."
```

### Handover Template

```markdown
# On-Call Handover: 2026-06-11 (Alice → Bob)

## Active Alerts
- None. All dashboards green as of 08:00.

## Recent Incidents
- 2026-06-11 03:15 UTC: P2 — api-gateway p99 latency spiked to 2s for 10 min.
  Self-resolved. Ticket filed: ENG-9100. No action needed but watch if it recurs.

## Pending Investigations
- [ENG-9100] api-gateway latency spike at 03:15 — check during next occurrence:
  - DB slow query log
  - Upstream service latency
  - K8s node CPU throttle

## Scheduled Changes
- 10:00 UTC: Deploy api-gateway v2.7.1 (bugfix). PR: #4521.
  Deployment runbook: https://wiki.internal/runbooks/api-gateway-deploy

## Known Fragility
- payment-service tends to OOM during the 12:00 UTC traffic peak.
  If it happens: scale to 10 replicas, then investigate.
  Permanent fix in progress: ENG-8900.

## Escalation Contacts
- Secondary: Carol Chen, +1-555-0123
- EM: Dave Kim, +1-555-0456
- DB team on-call: PagerDuty schedule "db-team"
```

---

## Burnout Prevention

### The Problem

```text
On-call burnout is the #1 reason SREs leave their jobs.

SYMPTOMS:
  - Dreading your phone buzzing
  - Anxiety the night before your shift starts
  - Inability to focus during the day after a night page
  - Irritability with teammates
  - The thought: "If I get paged one more time, I'm quitting"

STATISTICS (from SRE surveys):
  - 62% of SREs report burnout symptoms
  - Average tenure for SRE at a high-churn company: 18 months
  - #1 factor: excessive on-call load (> 1 week/month)
  - #2 factor: being paged for non-actionable alerts
```

### Organizational Guardrails

```text
ON-CALL FREQUENCY:
  - Maximum: 1 week per month (25%)
  - Ideal: 1 week per 6-8 weeks (12-15%)
  - Minimum team size to sustain 24/7: 8 engineers (so each does 1 week/month)

COMPENSATION:
  - On-call pay: additional stipend per week on-call
  - Time-in-lieu: for every hour of off-hours incident work, 1.5 hours off
  - "Do not disturb" morning: if paged after midnight, no expectation to
    be at morning standup

POST-INCIDENT RECOVERY:
  - After a P0 that lasts > 2 hours: take the next morning off
  - After a P0 that happens overnight: take the next day off
  - After a brutal on-call week: take Friday off (or the following Monday)

PSYCHOLOGICAL SAFETY:
  - Post-mortems are blameless. Always.
  - If you cause an incident, you are NOT in trouble.
    The system that let you cause it is what needs fixing.
  - "Thank you for finding this bug the hard way" is the correct response.
```

### Personal Survival Tactics

```text
DURING A P0:
  - Set a timer. Every 30 min, stand up and look out a window for 1 min.
  - Drink water. Dehydration amplifies stress.
  - If you're stuck: tell someone. "I'm stuck. Can you look at this?"
  - Remember: this IS going to end. All incidents get resolved eventually.

AFTER A P0:
  - The adrenaline crash is real. Don't drive immediately after.
  - Eat something. Protein, not sugar.
  - Do NOT immediately open your laptop and start coding the fix.
    Rest first. Fix later.

BETWEEN SHIFTS:
  - Exercise. Even a 15-minute walk clears stress hormones.
  - Have a non-screen hobby. Reading (paper), cooking, running,
    playing an instrument — anything that doesn't involve a screen.
  - Sleep hygiene: no screens 1 hour before bed. Especially not
    Slack or PagerDuty on your phone.

SIGNALING YOU NEED HELP:
  - Tell your manager: "My on-call load is unsustainable."
  - Be specific: "I was paged 12 times last week between 2-4 AM."
  - Propose: "Can we reduce false-alert pages by fixing X?"
  - If your manager doesn't act: escalate to skip-level.
  - If nothing changes: this is a valid reason to switch teams or jobs.
```

---

## Real-World Scenarios

### Scenario 1: The 3 AM Marathon

```text
SITUATION:
  Engineer is paged at 3:00 AM about database latency.
  Spends 2 hours debugging alone. Can't fix it.
  Works until 7:00 AM. Misses morning standup.
  Makes a mistake the next day due to sleep deprivation.
  Takes 3 days to recover from sleep debt.

WHAT SHOULD HAVE HAPPENED:
  T+15 min: No progress → page secondary.
  T+30 min: Still no progress → page DB team on-call.
  T+45 min: DB team on-call joins, identifies issue in 10 minutes.
  T+60 min: Issue resolved. Engineer goes back to sleep at 4:00 AM.
  Next day: Engineer attends standup, no mistakes.

LESSON: 60 minutes of outage with escalation > 120 minutes of
        heroic solo debugging + 3 days of recovery.
```

### Scenario 2: The Cascading Failure and the Calm Commander

```text
SITUATION:
  Saturday, 11:00 PM. P0 alert fires.
  Primary on-call (junior SRE, first month on rotation) is panicked.
  "Oh my god, everything is down. What do I do? I'm not qualified for this."

WHAT THEY DID RIGHT:
  1. Acknowledged immediately.
  2. Declared P0 in the incident channel.
  3. Paged secondary ("I need help, I'm new to this").
  4. Paged tech lead ("It's P0, I want oversight on my decisions").
  5. Posted in channel: "I'm primary on-call (junior). Secondary and tech lead
     are joining. I'm opening dashboards now."
  6. When secondary joined: "Here's what I see. Can you confirm?"
  7. When tech lead joined: "I think we need to roll back. Do you agree?"
  8. Tech lead confirmed, secondary executed rollback. Incident resolved
     in 22 minutes.

POST-INCIDENT:
  Manager: "That was the calmest junior on-call response I've seen."
  Junior: "I was terrified. But I knew the process. Acknowledge, declare,
          escalate, communicate."

LESSON: Process replaces experience. A junior with good process beats
        a senior who tries to solo it.
```

### Scenario 3: The "No, I've Got This" Trap

```text
SITUATION:
  Senior engineer on-call. P0 fire. 90 minutes in.
  Secondary pages: "Do you need help?"
  Senior: "No, I've got this. I'm close to fixing it."
  120 minutes: Same status.
  180 minutes: Finally accepts help. Secondary joins.
  Problem identified and fixed in 15 minutes by secondary.
  Total outage: 195 minutes. Could have been 30.

POST-MORTEM ACTION ITEM:
  "Implement a hard 30-minute timer on P0 incidents.
   If not mitigated within 30 minutes, secondary MUST join
   regardless of primary's preference."

LESSON: Ego is the enemy of fast resolution. The 30-minute rule
        removes the burden of asking for help. It just happens.
```

### Scenario 4: The False Alarm That Wasn't

```text
SITUATION:
  2:00 AM. Alert fires: "High CPU on api-gateway."
  On-call checks dashboard: CPU at 78%. Drops to 40% after 3 minutes.
  "That's a false alarm," they think. Goes back to sleep.
  Doesn't acknowledge. Doesn't investigate.
  2:10 AM: Alert fires again. CPU at 82%. Drops again.
  "Flapping alert." Snoozes PagerDuty for 1 hour.
  2:45 AM: api-gateway crashes. CPU at 100% for 15 straight minutes.
  PagerDuty is silenced. No one knows.
  3:30 AM: User reports come in. On-call wakes up to 47 Slack messages.

WHAT SHOULD HAVE HAPPENED:
  - NEVER silence PagerDuty without an active investigation.
  - A "flapping" alert IS a signal. Something is going wrong.
  - At minimum: acknowledge, check dashboards, note the pattern,
    file a ticket to investigate during business hours.
  - If you silence, set a short timer (15 min max) as a reminder
    to re-check.

LESSON: Two spikes at 2 AM on a service that's normally flat
        IS NOT NORMAL. "Flapping" = "I don't want to investigate."
```

---

## On-Call Checklist

```text
===== START OF SHIFT =====

□ Acknowledge shift in PagerDuty/Opsgenie
□ Test: can I receive a page? (Use test notification)
□ Confirm secondary on-call name and phone number
□ Confirm escalation manager name and phone number
□ Open critical dashboards in browser tabs:
  □ RED metrics for all owned services
  □ USE metrics for all infrastructure
  □ Log explorer (Loki/ELK/Splunk) — pre-loaded with {service=~".*"}
  □ Kubernetes cluster overview
  □ CI/CD pipeline status
  □ Cloud provider status page
□ Verify kubectl context is production
□ Verify AWS/GCP/Azure CLI session is active
□ Read outgoing handover notes
□ Check #incidents Slack channel for recent activity
□ Note any scheduled maintenance during your shift

===== DURING INCIDENT =====

□ Acknowledge page immediately
□ Open incident channel or use /incident declare
□ Determine severity (P0/P1/P2/P3)
□ If P0 or P1: page secondary ON-CALL and comms lead
□ Start investigation — check recent deploys first
□ Post updates to incident channel every 10-15 min
□ Mitigate before investigating root cause
□ Escalate if no progress in 15 min

===== END OF SHIFT =====

□ Write handover notes (use template above)
□ Send to incoming on-call
□ Verify incoming on-call can receive pages
□ Close dashboards
□ Acknowledge shift end in PagerDuty/Opsgenie
□ Take a walk. You've earned it.

===== BETWEEN SHIFTS =====

□ Review false-positive alerts from last shift: file tickets to fix
□ Review unresolved issues: do they need escalation?
□ Review your mental state: do you need a break? TELL YOUR MANAGER.
```
