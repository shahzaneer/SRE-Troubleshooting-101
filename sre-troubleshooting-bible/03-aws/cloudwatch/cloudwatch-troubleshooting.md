# CloudWatch Troubleshooting

> **Category:** AWS | CloudWatch | Monitoring
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#aws` `#cloudwatch` `#monitoring` `#oncall`

---

## Table of Contents

1. [Alarm Troubleshooting](#alarm-troubleshooting)
2. [Metric Math](#metric-math)
3. [Log Insights Query Language](#log-insights-query-language)
4. [CloudWatch Agent Troubleshooting](#cloudwatch-agent-troubleshooting)
5. [Missing Logs](#missing-logs)
6. [Log Group Management](#log-group-management)
7. [Cost Optimization](#cost-optimization)

---

## Alarm Troubleshooting

### Alarm Stuck in INSUFFICIENT_DATA

```text
Symptom: Alarm state shows INSUFFICIENT_DATA and never transitions
         to OK or ALARM.

Diagnostic flow:
  1. Does the metric actually have data points?
     aws cloudwatch get-metric-statistics \
       --namespace AWS/EC2 \
       --metric-name CPUUtilization \
       --dimensions Name=InstanceId,Value=i-xxx \
       --start-time "$(date -u -v-2H +%Y-%m-%dT%H:%M:%SZ)" \
       --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
       --period 300 \
       --statistics Average
     → If no datapoints: the metric itself has no data.

  2. Does the alarm period match the metric reporting interval?
     If the metric reports every 5 minutes but alarm period is 1 minute,
     the alarm sees only 1 data point every 5 periods → insufficient data.
     Match: alarm period >= metric reporting interval.

  3. Is the CloudWatch Agent running?
     sudo systemctl status amazon-cloudwatch-agent
     sudo tail -f /opt/aws/amazon-cloudwatch-agent/logs/amazon-cloudwatch-agent.log

  4. Are the dimensions correct?
     Metric: "CWAgent,mem_used_percent,InstanceId=i-xxx"
     Alarm dimension MUST match exactly (name AND value).

  5. Composite alarm with missing child metrics:
     aws cloudwatch describe-alarms --alarm-names my-composite-alarm \
       --query "CompositeAlarms[0].AlarmRule"
     → Check each child alarm individually:
       aws cloudwatch describe-alarms --alarm-names child-alarm-1

  6. IAM permissions:
     The alarm's evaluation principal needs cloudwatch:GetMetricData

  7. Metric math expressions that reference non-existent metrics
     return no data points:
     METRICS('m1') / METRICS('m2')  ← if m2 doesn't exist, result is empty
```

### Alarm State Machine

```text
       ┌──────────────┐
       │ INSUFFICIENT  │ ← No data points received for alarm period × evaluation periods
       │    _DATA      │
       └──────┬───────┘
              │  Data arrives
              ▼
       ┌──────────────┐
  ┌───→│      OK       │
  │    └──────┬───────┘
  │           │ Metric crosses threshold for N evaluation periods
  │           ▼
  │    ┌──────────────┐
  │    │    ALARM      │
  │    └──────┬───────┘
  │           │ Metric returns below threshold for M evaluation periods
  │           │
  └───────────┘
```

### Useful Alarm CLI Commands

```bash
# List all alarms in ALARM state (what's on fire RIGHT NOW?)
aws cloudwatch describe-alarms --state-value ALARM \
  --query "MetricAlarms[?StateValue=='ALARM'].[AlarmName,StateUpdatedTimestamp]" \
  --output table

# Describe a specific alarm
aws cloudwatch describe-alarms --alarm-names my-alarm \
  --query "MetricAlarms[0].{Name:AlarmName,State:StateValue,Reason:StateReason,Metric:MetricName,Dimensions:Dimensions,Threshold:Threshold,Period:Period,EvaluationPeriods:EvaluationPeriods}"

# Check alarm history (why did it change state?)
aws cloudwatch describe-alarm-history --alarm-name my-alarm \
  --history-item-type StateUpdate \
  --start-date "$(date -u -v-24H +%Y-%m-%dT%H:%M:%SZ)" \
  --end-date "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --query "AlarmHistoryItems[*].[Timestamp,HistorySummary]" \
  --output table

# Disable an alarm temporarily (maintenance window)
aws cloudwatch disable-alarm-actions --alarm-names my-alarm

# Re-enable
aws cloudwatch enable-alarm-actions --alarm-names my-alarm
```

### Scenario: "Alarm Fires But Nothing Is Wrong"

```text
Symptom: HighCPU alarm fires at 3 AM every Sunday. On-call wakes up.
         Checks the instance — CPU is at 5%. No issues. False alarm.

Investigation:
  CloudWatch metric data for the alarm period:
  → During 3:00-3:15 AM, CPUUtilization: 0.0, 0.0, 95.0, 0.0, 0.0

  What happened? The CloudWatch agent restarts at 3 AM during a
  system update. During the restart window, the agent reports
  a "0.0" metric. On recovery, the first metric after restart
  sometimes reports a spike (95.0) because the agent calculates
  the average over a very short interval instead of the full period.

  Fix:
  1. Increase evaluation periods from 1 to 3 (alarm fires only if
     condition persists for 3 consecutive data points).
  2. Set datapointsToAlarm: 3 out of 5 (majority).
  3. Use anomaly detection instead of static threshold:
     aws cloudwatch put-metric-alarm \
       --alarm-name HighCPU-Anomaly \
       --metrics '[{
         "Id": "m1",
         "ReturnData": true,
         "MetricStat": {
           "Metric": {
             "Namespace": "AWS/EC2",
             "MetricName": "CPUUtilization",
             "Dimensions": [{"Name":"InstanceId","Value":"i-xxx"}]
           },
           "Period": 300,
           "Stat": "Average"
         }
       }, {
         "Id": "e1",
         "Expression": "ANOMALY_DETECTION_BAND(m1, 2)",
         "Label": "AnomalyBand"
       }]' \
       --evaluation-periods 3 \
       --threshold-metric-id "e1" \
       --comparison-operator GreaterThanUpperThreshold
```

---

## Metric Math

CloudWatch Metric Math enables complex expressions across multiple metrics.

### Common Metric Math Expressions

```text
1. RATIO: Error rate as percentage
   METRICS('m1') / METRICS('m2') * 100
   → m1 = ErrorCount, m2 = RequestCount

2. SUM: Aggregate across dimensions
   SUM(METRICS())
   → Sum of CPUUtilization across ALL instances with matching namespace

3. DIFFERENCE: Growth rate
   METRICS('m1') - METRICS('m2')
   → m1 = current period, m2 = previous period

4. IF: Conditional alarm
   IF(METRICS('Errors') / METRICS('Requests') > 0.05, 1, 0)
   → Returns 1 if error rate > 5%, else 0. Use as alarm source.

5. FILL: Handle missing data
   FILL(METRICS('m1'), 0)
   → Treat missing data points as 0 (use for counting metrics)

6. METRICS() with search:
   METRICS('AWS/EC2', 'CPUUtilization',
          {'InstanceId': 'i-*'})
   → All CPUUtilization metrics for instances starting with "i-"

7. RATE: Per-second rate
   RATE(METRICS('m1'))
   → Convert cumulative counter to per-second rate

8. MOVING_AVERAGE: Smooth noisy metrics
   SLICE(METRICS('m1'), 5)
   → Average over last 5 data points

9. RUNNING_SUM: Cumulative
   RUNNING_SUM(METRICS('m1'))
   → Cumulative sum of data points (useful for request counters)

10. SEARCH with pattern:
    SEARCH('{AWS/Lambda,FunctionName} MetricName="Errors"',
           'Sum', 300)
    → Search all Lambda functions for Errors metric
```

### Creating a Metric Math Alarm

```bash
# Error rate alarm using metric math
aws cloudwatch put-metric-alarm \
  --alarm-name api-5xx-error-rate \
  --alarm-description "5xx error rate > 5%" \
  --metrics '[
    {"Id": "errors", "MetricStat": {
      "Metric": {
        "Namespace": "AWS/ApiGateway",
        "MetricName": "5XXError",
        "Dimensions": [{"Name":"ApiName","Value":"MyApi"}]
      },
      "Period": 300,
      "Stat": "Sum"
    }},
    {"Id": "requests", "MetricStat": {
      "Metric": {
        "Namespace": "AWS/ApiGateway",
        "MetricName": "Count",
        "Dimensions": [{"Name":"ApiName","Value":"MyApi"}]
      },
      "Period": 300,
      "Stat": "Sum"
    }},
    {"Id": "errorRate", "Expression": "IF(errors/requests > 0.05, errors/requests, 0)", "Label": "ErrorRate"}
  ]' \
  --evaluation-periods 2 \
  --threshold 0.05 \
  --comparison-operator GreaterThanThreshold \
  --treat-missing-data notBreaching

# Anomaly detection alarm
aws cloudwatch put-metric-alarm \
  --alarm-name db-connections-anomaly \
  --metrics '[
    {"Id": "m1", "MetricStat": {
      "Metric": {
        "Namespace": "AWS/RDS",
        "MetricName": "DatabaseConnections",
        "Dimensions": [{"Name":"DBInstanceIdentifier","Value":"mydb"}]
      },
      "Period": 300,
      "Stat": "Average"
    }},
    {"Id": "anomaly", "Expression": "ANOMALY_DETECTION_BAND(m1, 3)", "Label": "Band"}
  ]' \
  --evaluation-periods 3 \
  --threshold-metric-id anomaly \
  --comparison-operator GreaterThanUpperThreshold
```

---

## Log Insights Query Language

### Essential Query Patterns

```text
1. Basic search with filter:
   fields @timestamp, @message
   | filter @message like /ERROR/
   | sort @timestamp desc
   | limit 100

2. Aggregation (count by time bucket):
   stats count(*) as errorCount by bin(5m)
   | filter @message like /ERROR/
   | sort bin(5m) desc

3. Latency analysis:
   stats avg(duration), pct(duration, 50) as p50,
         pct(duration, 95) as p95, pct(duration, 99) as p99,
         max(duration)
   by path, method
   | sort p99 desc

4. Count by field value:
   fields @timestamp, status
   | filter status >= 500
   | stats count(*) as error_count by status, path
   | sort error_count desc

5. Parse and extract:
   parse @message /(?<ip>\d+\.\d+\.\d+\.\d+)/
   | stats count(*) as request_count by ip
   | filter request_count > 100
   | sort request_count desc

6. Join-like operations:
   fields @timestamp, @message, @logStream
   | filter @message like /requestId=([\w-]+)/
   | stats count(*) as hits by @logStream

7. Pattern analysis (auto-detect patterns):
   patterns @message
   | sort count(*) desc

8. Time series comparison:
   stats count(*) as current_count by bin(5m)
   | sort bin(5m) desc
   | limit 288  # 24 hours of 5-min buckets

9. Error grouping:
   fields @timestamp, @message
   | filter @message like /ERROR/
   | parse @message /(?<@error_type>\w+Exception): (?<@error_msg>.*)/
   | stats count(*) by @error_type, @error_msg
   | sort count(*) desc

10. Dedup:
    fields @timestamp, @message, @log
    | dedup @message
    | sort @timestamp desc
```

### Scenario: "Find All Failed Requests by Error Type"

```text
Query:
  fields @timestamp, status, path, @message
  | filter status >= 400
  | parse @message /"error":\s*"(?<error_type>[^"]+)/
  | stats count(*) as total, pct(duration, 95) as p95_latency
    by error_type, status
  | sort total desc

Output interpretation:
  error_type=ValidationError,  status=400, total=4523, p95=12ms
    → Request validation errors. Client sends bad data. Normal?
  error_type=AuthenticationError, status=401, total=120, p95=45ms
    → Token expiration? Check token renewal logic.
  error_type=TimeoutError,     status=504, total=89, p95=30045ms
    → Upstream service timing out after 30 seconds.
  error_type=ConnectionRefused, status=502, total=7, p95=500ms
    → Upstream service down. Check the upstream service's health.
```

### Running Log Insights from CLI

```bash
# Start a query
QUERY_ID=$(aws logs start-query \
  --log-group-name /aws/lambda/my-function \
  --start-time $(date -u -v-2H +%s) \
  --end-time $(date -u +%s) \
  --query-string "fields @timestamp, @message | filter @message like /ERROR/ | sort @timestamp desc | limit 50" \
  --query "queryId" \
  --output text)

echo "Query ID: $QUERY_ID"

# Wait and get results
sleep 5
aws logs get-query-results --query-id "$QUERY_ID" \
  --query "results[*]" --output table
```

---

## CloudWatch Agent Troubleshooting

### Agent Configuration

```text
Config file location:
  Linux:   /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json
  Windows: C:\ProgramData\Amazon\AmazonCloudWatchAgent\amazon-cloudwatch-agent.json

Config wizard (generate config interactively):
  sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-config-wizard

Key configuration sections:
  {
    "agent": {
      "metrics_collection_interval": 60,    # seconds between metric collection
      "logfile": "/opt/aws/amazon-cloudwatch-agent/logs/amazon-cloudwatch-agent.log",
      "run_as_user": "cwagent"
    },
    "metrics": {
      "namespace": "CWAgent",               # custom namespace for your metrics
      "append_dimensions": {
        "InstanceId": "${aws:InstanceId}",   # auto-added dimensions
        "ImageId": "${aws:ImageId}",
        "InstanceType": "${aws:InstanceType}"
      },
      "metrics_collected": {
        "cpu": {
          "measurement": ["cpu_usage_idle", "cpu_usage_user", "cpu_usage_system"],
          "totalcpu": true                   # aggregate across all CPUs
        },
        "mem": {
          "measurement": ["mem_used_percent", "mem_available_percent"]
        },
        "disk": {
          "measurement": ["disk_used_percent", "disk_used", "disk_total"],
          "resources": ["/", "/data"]        # which mount points
        },
        "netstat": {
          "measurement": ["tcp_established", "tcp_time_wait"]
        },
        "swap": {
          "measurement": ["swap_used_percent"]
        },
        "processes": {
          "measurement": ["processes_total", "processes_running", "processes_blocked"]
        }
      }
    },
    "logs": {
      "logs_collected": {
        "files": {
          "collect_list": [
            {
              "file_path": "/var/log/app/app.log",
              "log_group_name": "myapp-production",
              "log_stream_name": "{instance_id}",
              "timezone": "UTC",
              "multi_line_start_pattern": "{timestamp_format}"
            }
          ]
        }
      }
    }
  }
```

### Agent Diagnostic Commands

```bash
# Agent status
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a status

# Check agent logs for errors
sudo tail -100 /opt/aws/amazon-cloudwatch-agent/logs/amazon-cloudwatch-agent.log
sudo grep -i "error\|fail\|denied\|throttl" \
  /opt/aws/amazon-cloudwatch-agent/logs/amazon-cloudwatch-agent.log

# Restart agent
sudo systemctl restart amazon-cloudwatch-agent
# Or for config-managed:
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config -m ec2 \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json -s

# Test log collection by checking agent file descriptors
sudo lsof -p $(pgrep amazon-cloudwa | head -1) | grep "/var/log"

# Verify metrics are being sent
aws cloudwatch list-metrics \
  --namespace CWAgent \
  --dimensions Name=InstanceId,Value=$(curl -s http://169.254.169.254/latest/meta-data/instance-id) \
  --query "Metrics[*].MetricName" \
  --output table
```

### Common Agent Failure Modes

```text
1. Agent not running:
   systemctl is-active amazon-cloudwatch-agent → "inactive"
   Start: sudo systemctl start amazon-cloudwatch-agent

2. Wrong region in agent config:
   Agent sends metrics to us-west-2 but CloudWatch console shows us-east-1.
   Fix: Update config file with correct region OR set AWS_REGION env var.

3. IAM permissions missing:
   Agent needs these in its instance profile/role:
     - cloudwatch:PutMetricData
     - cloudwatch:GetMetricData
     - cloudwatch:ListMetrics
     - logs:CreateLogGroup
     - logs:CreateLogStream
     - logs:PutLogEvents
     - logs:DescribeLogStreams
     - ec2:DescribeTags (if using EC2 tag-based dimension injection)

4. Config file syntax error:
   Validate JSON:
     python3 -m json.tool \
       /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json > /dev/null
   → If validation fails, agent won't start or will start with default config.

5. Disk log file rotated out:
   Agent tails a log file. Logrotate removes or renames the file.
   Agent loses the inode. Must restart.
   Fix: Use copytruncate in logrotate or restart agent after rotation.
```

---

## Missing Logs

### Diagnostic Flow for Missing Logs

```bash
# 1. Log group exists?
aws logs describe-log-groups --log-group-name-prefix /aws/lambda/my-function

# 2. Log streams exist within the log group?
aws logs describe-log-streams \
  --log-group-name /aws/lambda/my-function \
  --order-by LastEventTime --descending \
  --max-items 5

# 3. Any recent log events?
aws logs get-log-events \
  --log-group-name /aws/lambda/my-function \
  --log-stream-name 2026/06/11/[$LATEST]abc123 \
  --limit 10

# 4. Lambda: check execution role permissions
aws iam get-role-policy --role-name lambda-execution-role \
  --policy-name CloudWatchLogs \
  --query "PolicyDocument.Statement[*]"

# 5. EC2: check agent config points to correct log file
cat /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json | \
  jq '.logs.logs_collected.files.collect_list[].file_path'

# 6. Check if the source file actually has data
wc -l /var/log/app/app.log
tail -5 /var/log/app/app.log
```

### Scenario: "Lambda Invocations Succeed But No Logs"

```text
Symptom: Lambda function runs without errors (invocations increase in
         CloudWatch metrics) but no log entries appear in the log group.

Investigation:
  1. Check Lambda execution role:
     aws iam get-role --role-name lambda-exec \
       --query "Role.Arn"

  2. Check role's attached policies for CloudWatch Logs:
     aws iam list-attached-role-policies --role-name lambda-exec
     → Missing AWSLambdaBasicExecutionRole (or equivalent)

  3. The role has:
     - lambda:InvokeFunction ✓
     - dynamodb:GetItem ✓
     - s3:GetObject ✓
     - logs:* ✗ (NOT PRESENT)

  ROOT CAUSE: Someone detached the AWSLambdaBasicExecutionRole policy
  during a security audit (they thought it was too permissive).
  Lambda runs fine but can't create log streams or put log events.

  Fix: Re-attach or create a scoped policy:
  {
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Action": [
          "logs:CreateLogStream",
          "logs:CreateLogGroup",
          "logs:PutLogEvents"
        ],
        "Resource": "arn:aws:logs:*:*:*"
      }
    ]
  }
```

### Agent Log File at the Host Level

```bash
# Check agent's own log for errors about specific files
sudo grep -A5 "ERROR" \
  /opt/aws/amazon-cloudwatch-agent/logs/amazon-cloudwatch-agent.log

# Common log patterns in agent logs:
# "failed to open file: permission denied"  → agent user can't read the app log
# "file does not exist"                      → path is wrong or app hasn't created it yet
# "rate exceeded"                            → throttled by CloudWatch API
# "stream not found, creating new"           → normal — auto-creates log streams
```

---

## Log Group Management

### Retention and Storage

```bash
# List all log groups and their retention settings
aws logs describe-log-groups \
  --query "logGroups[*].[logGroupName,retentionInDays,storedBytes,creationTime]" \
  --output table

# Set retention policy (in days: 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653)
aws logs put-retention-policy \
  --log-group-name /aws/lambda/my-function \
  --retention-in-days 30

# Delete a log group
aws logs delete-log-group --log-group-name /obsolete/app

# Check total stored data
aws logs describe-log-groups \
  --query "sum(logGroups[*].storedBytes)" \
  --output text | awk '{printf "%.2f GB\n", $1/1024/1024/1024}'
```

### Log Group Export

```bash
# Export to S3 (for long-term retention/analytics)
aws logs create-export-task \
  --task-name export-2026-06-11 \
  --log-group-name /prod/api \
  --from $(date -u -v-7d +%s)000 \
  --to $(date -u +%s)000 \
  --destination my-log-archive-bucket \
  --destination-prefix cloudwatch-export/2026-06-11/

# Check export task status
aws logs describe-export-tasks \
  --task-id <export-task-id> \
  --query "exportTasks[0].status.code"

# Note: Export can take HOURS for large log groups.
# Export is NOT real-time — it's for archival purposes.
```

### Scenario: "Logs Exist But Not Searchable (Log Classic vs Insights)"

```text
Symptom: "I can see log streams in the console but Log Insights
         returns no results for my query."

Debugging:
  1. Are you querying the CORRECT log group?
     → A Lambda function can write to MULTIPLE log groups
       (one defined in the function config, one by the app code via AWS SDK).

  2. Is the time range inclusive of your data?
     → Log Insights defaults to "Last 1 hour." If you're looking
       for events from 2 hours ago, extend the range.

  3. Are you using the right field names?
     → Log Insights auto-discovers fields. Check available fields:
       In console: Log Groups → select group → Log Insights →
       "Fields" dropdown shows all discovered fields.
     → If the field isn't discovered, use parse to extract it.

  4. Does the log group have enough data?
     → Log Insights has a minimum scan volume. If the log group
       has only a few lines, queries may return empty.

  5. Check for special characters in filter:
     filter @message like /ERROR/     → regex (case-sensitive)
     filter @message = 'ERROR'       → exact match
     filter @message like /error/i   → case-insensitive regex

  Fix: Always test with a simple query first:
    fields @timestamp, @message | sort @timestamp desc | limit 5
  → If this returns nothing: wrong log group or no data in time range.
```

---

## Cost Optimization

### Where CloudWatch Costs Accumulate

```text
1. Log Ingestion (PUT):
   $0.50 per GB ingested (us-east-1).
   → This is usually the biggest line item.
   → 100 GB/day = $50/day = $1,500/month.

2. Log Storage (archival after retention):
   $0.03 per GB per month.
   → If retention = never expire, storage grows forever.

3. Log Insights Queries:
   $0.005 per GB scanned.
   → Complex queries scanning 100s of GB/day add up.

4. Custom Metrics:
   $0.30 per metric per month (first 10,000 metrics cheaper).
   → Each unique dimension combination = 1 metric.
   → CWAgent injecting InstanceId creates 1 metric per instance.

5. API Requests:
   PutMetricData: $0.01 per 1,000 requests.
   GetMetricData: $0.01 per 1,000 requests.
   → Bulk API calls add up at scale.
```

### Cost-Saving Strategies

```text
1. Set retention on ALL log groups:
   aws logs describe-log-groups \
     --query "logGroups[?retentionInDays==null].logGroupName" \
     --output text | while read lg; do
       aws logs put-retention-policy --log-group-name "$lg" --retention-in-days 30
     done

2. Reduce log verbosity:
   - Production: INFO or WARN level (not DEBUG)
   - Disable Lambda request/response logging (enable only for debugging)
   - Sample high-volume debug logs (log 1 in 1000 requests)

3. Unpublish unused custom metrics:
   aws cloudwatch list-metrics --namespace CWAgent \
     --query "Metrics[*].MetricName" | sort | uniq -c

4. Use Embedded Metric Format (EMF):
   Instead of sending both logs AND custom metrics, embed metrics
   within log events. CloudWatch extracts them automatically.
   → No additional PutMetricData costs.
   → Python example:
     from aws_embedded_metrics import metric_scope

     @metric_scope
     def my_handler(metrics, event, context):
         metrics.put_dimensions({"Environment": "Production"})
         metrics.put_metric("RequestDuration", 42.5, "Milliseconds")
         metrics.set_property("userId", event.user_id)
```

---

## References

- [CloudWatch Alarms Documentation](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/AlarmThatSendsEmail.html)
- [CloudWatch Logs Insights Syntax](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_QuerySyntax.html)
- [Metric Math Documentation](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/using-metric-math.html)
- [CloudWatch Agent Setup](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Install-CloudWatch-Agent.html)
- [Embedded Metric Format](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format.html)
- [CloudWatch Pricing](https://aws.amazon.com/cloudwatch/pricing/)
