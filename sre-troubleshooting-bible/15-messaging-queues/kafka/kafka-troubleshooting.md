# Kafka Troubleshooting
> **Category:** Messaging | Kafka
> **Difficulty:** Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#kafka` `#messaging` `#streaming` `#oncall`

---

## Consumer Lag

Consumer lag is the #1 Kafka operational metric. It tells you how far behind consumers are from producers. Lag = (latest offset) - (consumer group committed offset). Lag growing = consumers can't keep up = messages will eventually exceed retention and be lost.

### Check Consumer Lag

```bash
# Show lag for all partitions in a consumer group
kafka-consumer-groups.sh \
  --bootstrap-server kafka-broker-1:9092 \
  --group order-processor \
  --describe

# Output columns:
# GROUP           TOPIC      PARTITION  CURRENT-OFFSET  LOG-END-OFFSET  LAG
# order-processor orders     0          15423000        15423500        500
# order-processor orders     1          15200000        15250000        50000  ← PROBLEM
# order-processor orders     2          15100000        15100000        0

# The LAG column: LOG-END-OFFSET - CURRENT-OFFSET
# Partition 1 has 50,000 lag — consumer for this partition is struggling
```

### Lag Investigation Decision Tree

```
LAG > 0 on partition X
  ├─ Is there a consumer assigned to this partition?
  │   └─ NO → Consumer group has fewer consumers than partitions → ADD consumers
  │
  ├─ Is the consumer processing slowly?
  │   └─ Check consumer logs for:
  │       ├─ Long GC pauses → tune JVM heap
  │       ├─ Slow external calls (DB, API) → add caching, timeouts
  │       ├─ Large batch processing → reduce max.poll.records
  │       └─ Deserialization errors → check schema compatibility
  │
  ├─ Is the topic throughput abnormally high?
  │   └─ Check producer metrics → spike in messages? Backpressure upstream.
  │
  └─ Is the consumer constantly rebalancing?
      └─ Check group coordinator logs → fix rebalance storm (see below)
```

### Scenario: Consumer Lag Growing at 1000 msg/sec

**Symptoms:**
```bash
# Every 5 seconds, lag increases by ~5000
watch -n 5 'kafka-consumer-groups.sh --bootstrap-server kafka:9092 --group order-processor --describe | grep PARTITION'
# Partition 0: LAG=15000
# Partition 1: LAG=16000
# Partition 2: LAG=0       ← Idle!
# Partition 3: LAG=0       ← Idle!
# Partition 4: LAG=0       ← Idle!

# 5 consumers for 5 partitions — should be fine
# But consumers 3, 4, 5 have 0 LAG while 1, 2 are drowning
```

**Diagnosis:** Check which consumers are assigned to which partitions.
```bash
kafka-consumer-groups.sh --bootstrap-server kafka:9092 --group order-processor --describe --members
# Shows: CONSUMER-ID, HOST, CLIENT-ID, #PARTITIONS
# consumer-1: host-a, partitions=0,1     ← 2 partitions
# consumer-2: host-b, partitions=2       ← 1 partition
# consumer-3: host-c, partitions=3       ← 1 partition
# consumer-4: host-d, partitions=4       ← 1 partition

# consumer-1 is assigned 2 partitions while others have 1.
# consumer-1 is on an undersized instance (2 CPU vs others' 4 CPU).

# Fix 1: Increase consumers to match partition count (5 consumers for 5 partitions)
# Fix 2: Ensure all consumer instances are the same size
# Fix 3: If single partition is hot (partition 1 has 10x traffic) → repartition topic
```

### Fix Consumer Lag

```bash
# Option A: Increase consumer instances (up to partition count)
# If topic has 10 partitions, max 10 consumers in the group
# Adding an 11th consumer: it sits idle — one partition = one consumer max

# Option B: Increase fetch rate
# Consumer config:
# max.poll.records=500              # Fetch more records per poll (default 500)
# fetch.max.bytes=52428800          # 50MB fetch size
# fetch.min.bytes=1048576           # 1MB minimum (reduces overhead)
# fetch.max.wait.ms=500             # Wait up to 500ms before returning data

# Option C: Optimize consumer processing
# - Batch DB writes instead of one-by-one
# - Use async I/O for external calls
# - Move heavy processing to separate topic (stream processing pattern)

# Option D: Increase topic partitions (requires producer-side changes)
kafka-topics.sh --bootstrap-server kafka:9092 --alter --topic orders --partitions 20
# ⚠️ WARNING: Increasing partitions doesn't redistribute existing data.
# Only new messages use new partitions. Existing data stays on old partitions.
# Also: message ordering is only guaranteed within a partition.
# If you rely on ordering by key, increasing partitions may break that.
```

---

## Partition Imbalance (Leader Hot-Spotting)

### Detection

```bash
# Check leader distribution across brokers
kafka-topics.sh --bootstrap-server kafka:9092 --describe --topic orders

# Output:
# Topic: orders  PartitionCount: 12  ReplicationFactor: 3
# Partition: 0   Leader: 1  Replicas: 1,2,3  Isr: 1,2,3
# Partition: 1   Leader: 2  Replicas: 2,3,1  Isr: 2,3,1
# Partition: 2   Leader: 3  Replicas: 3,1,2  Isr: 3,1,2
# ...
# Partition: 8   Leader: 3  Replicas: 3,1,2  Isr: 3,1,2
# Partition: 9   Leader: 3  Replicas: 3,1,2  Isr: 3,1,2
# Partition: 10  Leader: 3  Replicas: 3,1,2  Isr: 3,1,2
# Partition: 11  Leader: 3  Replicas: 3,1,2  Isr: 3,1,2

# Broker 3 is leader for 8 out of 12 partitions — hot spot!
# Broker 1: 2 leaders, Broker 2: 2 leaders, Broker 3: 8 leaders
```

### Fix

```bash
# Elect preferred leaders (preferred = first replica in the replicas list)
kafka-leader-election.sh --bootstrap-server kafka:9092 --election-type PREFERRED --all-topic-partitions

# Check after rebalance:
kafka-topics.sh --bootstrap-server kafka:9092 --describe --topic orders
# Leaders should now be evenly distributed: 1,2,3,1,2,3,1,2,3,1,2,3
```

### Scenario: One Broker at 100% CPU After Restart

```
Broker 3 was restarted for maintenance (OS patch). During restart, all
partitions with Broker 3 as leader failed over to replicas on Brokers 1 & 2.
After Broker 3 came back, auto.leader.rebalance.enable was FALSE (default),
so Broker 3 became follower for ALL its previous leader partitions.
Brokers 1 & 2 now have double the leader load — CPU at 100%.

Fix:
# Enable auto leader rebalancing (controlled by broker config)
# auto.leader.rebalance.enable=true
# leader.imbalance.check.interval.seconds=300 (5 min)
# leader.imbalance.per.broker.percentage=10

# Or manually trigger:
kafka-leader-election.sh --bootstrap-server kafka:9092 --election-type PREFERRED --all-topic-partitions
```

---

## Under-Replicated Partitions (URP)

A partition is "under-replicated" when one or more of its replicas is not in sync. This means you're running with reduced fault tolerance — if the leader dies, you may lose data.

### Detection

```bash
# List all under-replicated partitions cluster-wide
kafka-topics.sh --bootstrap-server kafka:9092 --describe --under-replicated-partitions

# Output (if any):
# Topic: orders           Partition: 5   Leader: 1  Replicas: 1,2,3  Isr: 1,2
#                                                                              ↑
#                                                                  Broker 3 missing!
# Topic: payments         Partition: 2   Leader: 2  Replicas: 2,3,4  Isr: 2

# JMX metric: kafka.server:type=ReplicaManager,name=UnderReplicatedPartitions
# Alert if > 0 for more than 60 seconds
```

### Root Causes and Fixes

| Cause | Check | Fix |
|-------|-------|-----|
| **Broker down** | `systemctl status kafka` on each broker | Restart broker, wait for ISR catch-up |
| **Slow follower** | Disk I/O saturated on follower | Move partitions off slow disk, add bandwidth |
| **Network partition** | `ping`, `traceroute` between brokers | Fix network issue, brokers auto-heal |
| **Unclean leader election** | `unclean.leader.election.enable=true` allowed out-of-sync replica to become leader | Set to `false` (default) — avoid data loss |

### Scenario: Brokers Offline for Maintenance

```bash
# Situation: Broker 4 is being taken down for hardware replacement
# All topics with replication factor 3 and a replica on broker 4 become URP

# Before maintenance: check which topics will be impacted
kafka-topics.sh --bootstrap-server kafka:9092 --describe | grep "Replicas:.*4" | wc -l
# 47 partitions have a replica on broker 4

# Check: is min.insync.replicas satisfied during maintenance?
# If min.insync.replicas=2 and replication factor=3:
#   With Broker 4 down: 2 ISRs remain → producers can still write (acks=all OK)
#   With Broker 4 + Broker 1 down: 1 ISR → producers BLOCKED on write

# Reassign partitions away from Broker 4 before shutdown (graceful decommission):
# 1. Generate reassignment JSON
kafka-reassign-partitions.sh --bootstrap-server kafka:9092 \
  --topics-to-move-json-file topics.json \
  --broker-list "1,2,3" --generate

# 2. Execute reassignment (moves data in background)
kafka-reassign-partitions.sh --bootstrap-server kafka:9092 \
  --reassignment-json-file reassignment.json --execute

# 3. Monitor progress
kafka-reassign-partitions.sh --bootstrap-server kafka:9092 \
  --reassignment-json-file reassignment.json --verify
# "Reassignment of partition orders-5 is complete" → safe to shut down Broker 4
```

---

## Consumer Group Rebalancing Storms

### The Problem

A "rebalance storm" happens when consumers constantly join and leave the group. During each rebalance, ALL consumers in the group stop processing entirely (stop-the-world). If rebalances happen every 30 seconds, consumers spend 50%+ of time not processing — lag grows rapidly.

### Detection

```bash
# Monitor rebalance rate via JMX
# Metric: kafka.consumer:type=consumer-coordinator-metrics,client-id=X
#   join-rate, sync-rate, heartbeat-rate

# In consumer logs (DEBUG level):
# "Revoking previously assigned partitions"  ← Group rebalancing
# "Successfully joined group with generation X"  ← Joined new group

# If you see these every 30-60 seconds → rebalance storm
```

### Primary Cause: Poll Timeout

```bash
# Consumer config relationship:
# max.poll.interval.ms = 300000  # (5 min) Max time between polls before consumer is kicked out
# max.poll.records = 500         # Records fetched per poll
# session.timeout.ms = 45000     # Heartbeat timeout

# The trap: processing 500 records takes 350 seconds, but max.poll.interval.ms=300s
# Consumer misses poll → heartbeat lost → coordinator kicks consumer → rebalance
# New consumer assigned the same partition → same slow processing → kicked again → rebalance
# INFINITE LOOP — rebalance storm
```

### Scenario: Rebalance Storm

**Symptoms:**
```
Consumer logs show:
10:00:00 - Joined group (generation 14)
10:00:05 - Revoking partitions (group rebalance)
10:00:06 - Joined group (generation 15)
10:00:12 - Revoking partitions (group rebalance)
10:00:13 - Joined group (generation 16)
... repeats every 6-7 seconds forever
```

**Diagnosis:**
```bash
# Check consumer config
grep -E "max.poll|session.timeout|heartbeat" consumer.properties

# Processing time per batch:
# 500 records × 0.7s per record (slow DB write) = 350 seconds
# max.poll.interval.ms = 300000 (5 min) → TIMEOUT!

# Fix options:
# 1. Increase max.poll.interval.ms to 600000 (10 min)
# 2. Reduce max.poll.records to 100
# 3. Optimize processing to < 1 min per batch
# 4. Offload heavy processing to a separate thread pool (pause consumer, process, resume)
```

### Java Consumer with Graceful Rebalance Handling

See Java code example at the end of this file.

---

## Message Too Large

```bash
# Error from producer: RecordTooLargeException
# org.apache.kafka.common.errors.RecordTooLargeException:
#   The message is 2097152 bytes when the maximum is 1048576 bytes.

# Kafka message size limits:
# message.max.bytes = 1048576 (1MB default, per topic, set on broker)
# max.request.size = 1048576 (1MB default, per producer)
# replica.fetch.max.bytes = 1048576 (must be >= message.max.bytes)
# fetch.max.bytes = 52428800 (50MB default, per consumer, total across partitions)

# Fix: increase all related limits
kafka-configs.sh --bootstrap-server kafka:9092 \
  --entity-type topics --entity-name large-payloads \
  --alter --add-config max.message.bytes=10485760  # 10MB

# Producer config:
# max.request.size=10485760
```

---

## Topic Retention

```bash
# Check retention configuration
kafka-topics.sh --bootstrap-server kafka:9092 --describe --topic orders

# Config output:
# retention.ms=86400000  (24 hours)
# retention.bytes=-1     (unlimited, based on time only)

# If data is missing from 2 days ago: retention deleted it

# Change retention to 7 days
kafka-configs.sh --bootstrap-server kafka:9092 \
  --entity-type topics --entity-name orders \
  --alter --add-config retention.ms=604800000

# Immediate consequences: retained data increases ~7x → disk usage!
# Monitor: kafka-log-dirs.sh --bootstrap-server kafka:9092 --describe
```

---

## Python: Resilient Kafka Consumer

```python
#!/usr/bin/env python3
"""
kafka_consumer_ops.py — Production-grade Kafka consumer with error handling,
manual offset commit, dead-letter topic for poison messages, and graceful shutdown.

Usage:
    python kafka_consumer_ops.py --bootstrap-servers kafka:9092 \
        --topic orders --group order-processor --dlq-topic orders-dlq
"""

import json
import signal
import sys
import time
from argparse import ArgumentParser
from typing import Optional

from kafka import KafkaConsumer, KafkaProducer, TopicPartition, OffsetAndMetadata
from kafka.errors import KafkaError, CommitFailedError


class DeadLetterProducer:
    """Publishes messages that repeatedly fail processing to a DLQ topic."""

    def __init__(self, bootstrap_servers: str, dlq_topic: str):
        self.topic = dlq_topic
        self.producer = KafkaProducer(
            bootstrap_servers=bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            acks="all",
            retries=3,
            max_block_ms=10000,
        )

    def send(self, original_message: dict, error: str, topic: str, partition: int,
             offset: int, retries: int) -> None:
        """Publish failed message to DLQ with metadata."""
        dlq_message = {
            "original_topic": topic,
            "original_partition": partition,
            "original_offset": offset,
            "failed_at": time.time(),
            "retry_count": retries,
            "error": str(error),
            "payload": original_message,
        }
        future = self.producer.send(self.topic, dlq_message)
        try:
            future.get(timeout=10)
            print(f"  → Sent to DLQ: {self.topic}")
        except KafkaError as e:
            print(f"  ✗ Failed to send to DLQ: {e}", file=sys.stderr)

    def close(self):
        self.producer.close()


class SafeConsumer:
    """
    Kafka consumer wrapper with:
    - Graceful shutdown (SIGTERM/SIGINT)
    - Manual offset commit after successful processing
    - Dead letter queue for messages that fail after N retries
    - Poison pill detection (messages that crash the consumer)
    """

    def __init__(self, bootstrap_servers: str, topic: str, group_id: str,
                 dlq: Optional[DeadLetterProducer] = None, max_retries: int = 3):
        self.topic = topic
        self.dlq = dlq
        self.max_retries = max_retries
        self.running = True

        self.consumer = KafkaConsumer(
            topic,
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,
            enable_auto_commit=False,  # Manual commits for reliability
            auto_offset_reset="earliest",
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            max_poll_records=100,
            max_poll_interval_ms=600000,  # 10 min
            session_timeout_ms=30000,
            heartbeat_interval_ms=3000,
            # Isolate poison pills: move on after 1 error instead of retrying forever
        )

        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

    def _shutdown(self, signum, frame):
        print(f"\nShutting down (signal {signum})...", file=sys.stderr)
        self.running = False

    def process_message(self, message: dict) -> bool:
        """
        Process a single message. Returns True on success.
        Override this with actual business logic.
        """
        # Simulated processing — replace with actual logic
        if message.get("type") == "order":
            return self._process_order(message)
        elif message.get("type") == "payment":
            return self._process_payment(message)
        else:
            print(f"  Unknown message type: {message.get('type')}")
            return True  # Don't retry unknown types

    def _process_order(self, message: dict) -> bool:
        """Example order processing. Fails if amount is negative."""
        amount = message.get("amount", 0)
        if amount < 0:
            raise ValueError(f"Negative order amount: {amount}")
        # Simulate: write to DB, send notification, etc.
        time.sleep(0.01)  # Simulated processing time
        return True

    def _process_payment(self, message: dict) -> bool:
        """Example payment processing."""
        currency = message.get("currency", "USD")
        if currency not in ("USD", "EUR", "GBP", "JPY"):
            raise ValueError(f"Unsupported currency: {currency}")
        time.sleep(0.01)
        return True

    def run(self):
        """Main consumption loop with retry logic and DLQ fallback."""
        print(f"Starting consumer: topic={self.topic}, group={self.consumer.config['group_id']}",
              file=sys.stderr)

        retry_counts: dict = {}  # (partition, offset) → retry_count

        while self.running:
            try:
                records = self.consumer.poll(timeout_ms=1000)
            except CommitFailedError as e:
                print(f"Commit failed (rebalance?): {e}", file=sys.stderr)
                continue

            for tp, messages in records.items():
                for msg in messages:
                    retry_key = (tp.partition, msg.offset)

                    try:
                        success = self.process_message(msg.value)
                        if success:
                            # Commit offset for this message
                            meta = OffsetAndMetadata(msg.offset + 1, "")
                            self.consumer.commit({tp: meta})
                            retry_counts.pop(retry_key, None)
                            print(f"  ✓ Processed: partition={tp.partition}, offset={msg.offset}")
                    except Exception as e:
                        retries = retry_counts.get(retry_key, 0) + 1
                        retry_counts[retry_key] = retries

                        print(f"  ✗ Error (retry {retries}/{self.max_retries}): "
                              f"partition={tp.partition}, offset={msg.offset} — {e}",
                              file=sys.stderr)

                        if retries >= self.max_retries:
                            # Poison pill: move to DLQ and commit past it
                            if self.dlq:
                                self.dlq.send(
                                    original_message=msg.value,
                                    error=str(e),
                                    topic=tp.topic,
                                    partition=tp.partition,
                                    offset=msg.offset,
                                    retries=retries,
                                )
                            # Commit past the failed message so we don't reprocess it
                            meta = OffsetAndMetadata(msg.offset + 1, "")
                            self.consumer.commit({tp: meta})
                            retry_counts.pop(retry_key, None)
                            print(f"  → Poison pill moved to DLQ: offset={msg.offset}")
                        else:
                            # Don't commit — will be re-processed on next poll
                            pass

        self.consumer.close()
        if self.dlq:
            self.dlq.close()
        print("Consumer shut down cleanly.")


def main():
    parser = ArgumentParser(description="Production Kafka consumer with DLQ support")
    parser.add_argument("--bootstrap-servers", required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--group", required=True, help="Consumer group ID")
    parser.add_argument("--dlq-topic", help="Dead letter queue topic (optional)")
    parser.add_argument("--max-retries", type=int, default=3)
    args = parser.parse_args()

    dlq = None
    if args.dlq_topic:
        dlq = DeadLetterProducer(args.bootstrap_servers, args.dlq_topic)

    consumer = SafeConsumer(
        bootstrap_servers=args.bootstrap_servers,
        topic=args.topic,
        group_id=args.group,
        dlq=dlq,
        max_retries=args.max_retries,
    )
    consumer.run()


if __name__ == "__main__":
    main()
```

---

## Java: Kafka Consumer with Error Handling

```java
package com.example.kafka;

import org.apache.kafka.clients.consumer.*;
import org.apache.kafka.clients.producer.*;
import org.apache.kafka.common.TopicPartition;
import org.apache.kafka.common.errors.WakeupException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.time.Duration;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Production-grade Kafka consumer with:
 * - Graceful shutdown via WakeupException
 * - Manual offset commit
 * - Dead letter topic for poison messages
 * - Retry tracking to prevent infinite loops
 */
public class SafeKafkaConsumer implements Runnable {

    private static final Logger log = LoggerFactory.getLogger(SafeKafkaConsumer.class);

    private final KafkaConsumer<String, String> consumer;
    private final KafkaProducer<String, String> dlqProducer;
    private final String dlqTopic;
    private final int maxRetries;
    private final AtomicBoolean running = new AtomicBoolean(true);
    private final Map<TopicPartition, Map<Long, Integer>> retryTracker = new ConcurrentHashMap<>();

    public SafeKafkaConsumer(String bootstrapServers, String groupId, String topic,
                             String dlqTopic, int maxRetries) {
        this.dlqTopic = dlqTopic;
        this.maxRetries = maxRetries;

        Properties consumerProps = new Properties();
        consumerProps.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrapServers);
        consumerProps.put(ConsumerConfig.GROUP_ID_CONFIG, groupId);
        consumerProps.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG,
                "org.apache.kafka.common.serialization.StringDeserializer");
        consumerProps.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG,
                "org.apache.kafka.common.serialization.StringDeserializer");
        consumerProps.put(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, "false");
        consumerProps.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        consumerProps.put(ConsumerConfig.MAX_POLL_RECORDS_CONFIG, "100");
        consumerProps.put(ConsumerConfig.MAX_POLL_INTERVAL_MS_CONFIG, "600000"); // 10 min
        consumerProps.put(ConsumerConfig.SESSION_TIMEOUT_MS_CONFIG, "30000");

        this.consumer = new KafkaConsumer<>(consumerProps);
        this.consumer.subscribe(Collections.singletonList(topic));

        if (dlqTopic != null && !dlqTopic.isEmpty()) {
            Properties dlqProps = new Properties();
            dlqProps.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrapServers);
            dlqProps.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG,
                    "org.apache.kafka.common.serialization.StringSerializer");
            dlqProps.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG,
                    "org.apache.kafka.common.serialization.StringSerializer");
            dlqProps.put(ProducerConfig.ACKS_CONFIG, "all");
            dlqProps.put(ProducerConfig.RETRIES_CONFIG, "3");
            this.dlqProducer = new KafkaProducer<>(dlqProps);
        } else {
            this.dlqProducer = null;
        }
    }

    @Override
    public void run() {
        try {
            log.info("Consumer started: group={}, topic={}", consumer.groupMetadata().groupId(),
                    consumer.subscription());

            while (running.get()) {
                ConsumerRecords<String, String> records = consumer.poll(Duration.ofSeconds(1));

                for (ConsumerRecord<String, String> record : records) {
                    processWithRetry(record);
                }

                // Commit offsets for successfully processed records
                consumer.commitSync();
            }
        } catch (WakeupException e) {
            log.info("Consumer wakeup called — shutting down");
        } catch (Exception e) {
            log.error("Unexpected consumer error", e);
        } finally {
            shutdown();
        }
    }

    private void processWithRetry(ConsumerRecord<String, String> record) {
        TopicPartition tp = new TopicPartition(record.topic(), record.partition());
        int retryCount = retryTracker
                .computeIfAbsent(tp, k -> new ConcurrentHashMap<>())
                .getOrDefault(record.offset(), 0);

        try {
            boolean success = processMessage(record.value());
            if (success) {
                retryTracker.get(tp).remove(record.offset());
                log.debug("Processed: topic={}, partition={}, offset={}",
                        record.topic(), record.partition(), record.offset());
            }
        } catch (Exception e) {
            retryCount++;
            retryTracker.get(tp).put(record.offset(), retryCount);

            log.error("Error processing message (retry {}/{}): topic={}, partition={}, offset={} — {}",
                    retryCount, maxRetries, record.topic(), record.partition(), record.offset(),
                    e.getMessage());

            if (retryCount >= maxRetries) {
                sendToDlq(record, e.getMessage(), retryCount);
                retryTracker.get(tp).remove(record.offset());
                log.warn("Poison pill moved to DLQ: {}", dlqTopic);
            } else {
                // Don't commit — consumer will reprocess on next poll
                // However, we must seek back to this offset since we're committing
                // manually; otherwise the poll will advance past it.
                // Actually, since we DON'T commit here, the offset stays.
                // But we need to call seek() to re-read the uncommitted message.
                consumer.seek(tp, record.offset());
            }
        }
    }

    private boolean processMessage(String value) {
        // Replace with actual business logic
        if (value == null || value.isEmpty()) {
            throw new IllegalArgumentException("Empty message body");
        }
        // Simulated: inspect for poison pattern
        if (value.contains("\"amount\": -")) {
            throw new RuntimeException("Negative amount detected");
        }
        if (value.contains("\"currency\": \"INVALID\"")) {
            throw new RuntimeException("Unsupported currency");
        }
        return true;
    }

    private void sendToDlq(ConsumerRecord<String, String> record, String error, int retries) {
        if (dlqProducer == null) {
            log.warn("No DLQ configured — dropping poison message: offset={}", record.offset());
            return;
        }

        String dlqPayload = String.format(
                "{\"original_topic\":\"%s\",\"original_partition\":%d,\"original_offset\":%d," +
                "\"retry_count\":%d,\"error\":\"%s\",\"failed_at\":%d,\"payload\":%s}",
                record.topic(), record.partition(), record.offset(),
                retries, error.replace("\"", "\\\""), System.currentTimeMillis() / 1000,
                record.value());

        ProducerRecord<String, String> dlqRecord = new ProducerRecord<>(
                dlqTopic, record.key(), dlqPayload);

        dlqProducer.send(dlqRecord, (metadata, exception) -> {
            if (exception != null) {
                log.error("Failed to send to DLQ: {}", exception.getMessage());
            } else {
                log.info("Sent to DLQ: topic={}, partition={}, offset={}",
                        metadata.topic(), metadata.partition(), metadata.offset());
            }
        });
    }

    public void stop() {
        running.set(false);
        consumer.wakeup();
    }

    private void shutdown() {
        log.info("Shutting down consumer...");
        try {
            consumer.close(Duration.ofSeconds(10));
        } catch (Exception e) {
            log.error("Error closing consumer", e);
        }
        if (dlqProducer != null) {
            dlqProducer.close(Duration.ofSeconds(5));
        }
        log.info("Consumer shut down complete.");
    }

    public static void main(String[] args) {
        SafeKafkaConsumer consumer = new SafeKafkaConsumer(
                "localhost:9092", "order-processor",
                "orders", "orders-dlq", 3);

        Thread consumerThread = new Thread(consumer);
        consumerThread.start();

        // Graceful shutdown on JVM exit
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            log.info("JVM shutdown hook triggered");
            consumer.stop();
            try {
                consumerThread.join(15000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }));
    }
}
```
