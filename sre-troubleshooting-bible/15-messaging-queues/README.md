# 14 — Messaging & Queues

> **Section Owner:** SRE Data Infrastructure On-Call
> **Last Reviewed:** 2026-06-11

Async messaging is the backbone of distributed systems. When queues back up, consumer groups lag, or messages go missing, entire workflows stall silently. This section covers diagnosing and fixing the two most common messaging platforms in production.

---

## Files in This Section

| File | Description | Difficulty |
|------|-------------|------------|
| [kafka/kafka-troubleshooting.md](kafka/kafka-troubleshooting.md) | Consumer lag, partition imbalance, under-replicated partitions, rebalancing storms, message sizing, retention config | Advanced |
| [sqs/sqs-troubleshooting.md](sqs/sqs-troubleshooting.md) | Visibility timeout, DLQ handling, long vs short polling, FIFO vs Standard, message attributes | Intermediate |

---

## When to Escalate

| Symptom | First Check | Escalate If |
|---------|------------|-------------|
| Consumer lag > 10,000 | Scale consumers to partition count | Brokers at CPU/memory limit, need cluster resize |
| DLQ messages > 0 | Check consumer logs for errors | Malformed messages need producer-side fix |
| Under-replicated partitions | Check broker status | Requires Kafka admin (partition reassignment) |
| SQS messages not being processed | Check Lambda concurrency / EC2 consumer health | Requires AWS support (service-side issue) |

---

## Quick-Reference CLI

```bash
# Kafka — consumer group lag
kafka-consumer-groups.sh --bootstrap-server localhost:9092 --group my-group --describe

# Kafka — under-replicated partitions
kafka-topics.sh --bootstrap-server localhost:9092 --describe --under-replicated-partitions

# SQS — queue attributes (including DLQ ARN, visibility timeout)
aws sqs get-queue-attributes --queue-url $QUEUE_URL --attribute-names All

# SQS — check DLQ depth
aws sqs get-queue-attributes --queue-url $DLQ_URL --attribute-names ApproximateNumberOfMessagesVisible
```

---

## Resources

- [Kafka: The Definitive Guide (O'Reilly)](https://www.confluent.io/resources/kafka-the-definitive-guide/)
- [Kafka Documentation — Operations](https://kafka.apache.org/documentation/#operations)
- [AWS SQS Best Practices](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-best-practices.html)
- [Building Event-Driven Microservices (O'Reilly)](https://www.oreilly.com/library/view/building-event-driven-microservices/9781492057888/)
