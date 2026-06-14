# Async API & Message Queue Troubleshooting

> **Category:** API | Messaging | Async
> **Difficulty:** Intermediate to Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#messaging` `#async` `#oncall`

---

## Consumer Lag

### What It Is

Consumer lag is the number of messages that have been produced but not yet consumed. It represents how far behind the consumer is from the producer.

```
Producer ──────▶ [QUEUE/KAFKA] ──────▶ Consumer

Total messages produced: 1,000,000
Total messages consumed:   800,000
Consumer Lag:               200,000
```

### How to Measure

**Kafka:**
```bash
# Check consumer group lag
kafka-consumer-groups --bootstrap-server localhost:9092 \
  --group order-processor --describe

# Output:
# GROUP            TOPIC     PARTITION  CURRENT-OFFSET  LOG-END-OFFSET  LAG
# order-processor  orders    0          1500000          1800000         300000
# order-processor  orders    1          1450000          1750000         300000
# order-processor  orders    2          1480000          1780000         300000
# Total lag: 900000 messages
```

**Kafka with Burrow (lag monitoring):**
```bash
curl http://burrow:8000/v3/kafka/local/consumer/order-processor/lag
```

**AWS SQS:**
```bash
aws sqs get-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/123456789/orders-queue \
  --attribute-names ApproximateNumberOfMessages \
                        ApproximateNumberOfMessagesNotVisible \
                        ApproximateNumberOfMessagesDelayed

# Output (JSON):
# {
#     "ApproximateNumberOfMessages": "5000000",       ← Visible (ready to be consumed)
#     "ApproximateNumberOfMessagesNotVisible": "50000", ← In-flight (being processed)
#     "ApproximateNumberOfMessagesDelayed": "0"        ← Deliberately delayed
# }
```

**AWS CloudWatch metrics to alarm on:**
- `ApproximateNumberOfMessagesVisible` > threshold → consumer lag.
- `ApproximateAgeOfOldestMessage` > threshold → messages are stale.

**RabbitMQ:**
```bash
rabbitmqctl list_queues name messages messages_ready messages_unacknowledged
# orders_queue  5000000  4950000  50000

# Or via management API:
curl -u user:pass http://rabbitmq:15672/api/queues/%2F/orders_queue | jq '.messages_ready'
```

### Lag Diagnosis Playbook

```
1. Confirm lag:
   Kafka: kafka-consumer-groups --describe → LAG column
   SQS: ApproximateNumberOfMessagesVisible
   RabbitMQ: messages_ready

2. Determine rate mismatch:
   Producer rate: 500 msg/s
   Consumer rate: 100 msg/s
   Lag growth:    +400 msg/s → queue depth grows by 400 every second

3. Is the consumer rate normal or degraded?
   - Check consumer host metrics (CPU, memory, GC pauses)
   - Check downstream dependency latency (DB, cache)
   - Check consumer thread/process count

4. Scale consumers:
   Kafka: Increase partitions (up to consumer count limit). Max consumers = partition count.
   SQS: Increase consumer instance count (no partition limit — full horizontal scaling).
   RabbitMQ: Increase consumer prefetch count or add more consumer instances.

5. If scaling doesn't help:
   - Consumer processing has a bottleneck (slow DB query)
   - Optimize consumer code before scaling further
```

### Scenario: Order Processing Backlog

**Problem:** Alert fires at 2 AM: `ApproximateNumberOfMessagesVisible` on `orders-queue` is 5,000,000. Normal is under 10,000.

**Investigation:**
```bash
# Check SQS metrics
aws sqs get-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/123456789/orders-queue \
  --attribute-names ApproximateNumberOfMessagesVisible \
                        ApproximateAgeOfOldestMessage

# ApproximateNumberOfMessagesVisible: 5,000,000
# ApproximateAgeOfOldestMessage: 7200 (seconds = 2 hours)
```

- Producer rate: 500 orders/sec (normal).
- Consumer rate: 100 orders/sec (degraded from 600/sec normal).

**Root cause:** The consumer handles each order by calling an external payment validation API. At 1:30 AM, the payment API had a partial outage and began timing out at 30 seconds per call. Each consumer thread was blocked on a 30-second timeout → effective throughput dropped from 600/sec to 100/sec.

**Recovery:**
```bash
# 1. Kill-switch the payment validation (accept all payments temporarily):
# Feature flag: PAYMENT_VALIDATION_ENABLED=false

# 2. Scale consumers temporarily:
kubectl scale deployment/order-processor --replicas=50

# 3. After backlog is cleared, re-enable payment validation with circuit breaker.
```

**Preventive measures:**
- Circuit breaker on the payment API call (fail fast instead of waiting 30s).
- Autoscaling based on queue depth (SQS → CloudWatch alarm → ASG scale-out).
- Dead Letter Queue for messages that exceed max processing attempts.

---

## Dead Letter Queue (DLQ)

### What It Is

A DLQ is a queue that holds messages that failed processing after a configured number of retry attempts. It's a safety net — messages that can't be processed are moved aside so they don't block the main queue.

```
Producer → Main Queue → Consumer (fails 3 times)
                            │
                            └──→ Dead Letter Queue (manual inspection required)
```

### When to Check the DLQ

1. Alert on DLQ depth > 0 (or > a threshold).
2. During incident response: any spike in main queue errors may indicate poison pill messages.
3. After a code deploy: check DLQ for messages that the new code can now process.

### Poison Pill Pattern

A poison pill is a message that always fails processing — every consumer attempt throws an error. If left in the main queue:
1. Consumer picks up the message.
2. Processing fails.
3. Message is returned to the queue (or retry count incremented).
4. Next consumer picks it up → fails.
5. ... repeats indefinitely.

### Scenario: DLQ Depth Grows to 50K

**Alert:**
```
DLQ depth alarm: Dead letter queue 'orders-dlq' has 50,000 messages.
Normal: 0 messages in DLQ.
```

**Investigation:**
```bash
# Check one message from the DLQ
aws sqs receive-message \
  --queue-url https://sqs.us-east-1.amazonaws.com/123456789/orders-dlq \
  --max-number-of-messages 1 \
  --visibility-timeout 60 \
  --attribute-names All

# Output:
# {
#     "Body": "{'order_id': 12345, 'user_id': 'abc123', 'total': 99.99, 'items': [{'product_id': null, 'quantity': 1}]}",
#     "Attributes": {
#         "ApproximateReceiveCount": "3",
#         "SentTimestamp": "1686451200000"
#     }
# }
```

**Problem identified:** The `Body` has `"product_id": null` — the JSON is valid, but the business logic throws a validation error (`NullPointerException` because `product_id` is missing). The message is valid JSON (so it passes the first-level validation), but missing a required field. Each of 3 retry attempts by each consumer produces a failure log. With 500 consumers, that's 1500 failures/minute for a single bad message.

**Root cause:** A bug in the product import service allowed products with null IDs to be created. The checkout service correctly rejects them, but the message never reaches a human for inspection.

**Recovery:**
```bash
# 1. Purge the main queue of the bad messages (they're already in DLQ)
aws sqs purge-queue --queue-url https://sqs.us-east-1.amazonaws.com/123456789/orders-queue

# 2. Redrive DLQ messages back to the main queue (after fixing the source)
aws sqs start-message-move-task \
  --source-arn arn:aws:sqs:us-east-1:123456789:orders-dlq \
  --destination-arn arn:aws:sqs:us-east-1:123456789:orders-queue
```

**Permanent fix:** Add message schema validation at the producer side (schema registry or JSON Schema validation before enqueueing).

---

## Message Ordering

### SQS FIFO vs Standard

| Feature | Standard Queue | FIFO Queue |
|---|---|---|
| Ordering | Best-effort (not guaranteed) | Strictly ordered within message group |
| Throughput | Nearly unlimited | 3000 msg/s per API action (with batching) |
| Duplicates | At-least-once delivery (possible duplicates) | Exactly-once processing (deduplication within 5 minutes) |
| Use case | High throughput, ordering not critical | Financial transactions, sequential workflows |

**SQS FIFO example:**
```python
sqs.send_message(
    QueueUrl=fifo_queue_url,
    MessageBody=json.dumps({"order_id": "123", "action": "create"}),
    MessageGroupId="order-123",           # All messages for order 123 go to same group
    MessageDeduplicationId=str(uuid.uuid4()),  # Auto-generated or business key
)
```

### Kafka Ordering Within Partition

Kafka guarantees ordering **within a partition**, not across partitions.

```
Topic: orders (3 partitions)
  Partition 0: [msg:order_1, msg:order_4, msg:order_7]  ← ordered
  Partition 1: [msg:order_2, msg:order_5, msg:order_8]  ← ordered
  Partition 2: [msg:order_3, msg:order_6, msg:order_9]  ← ordered
```

If consumer group has 3 consumers, each consumes one partition. Messages within a partition are ordered. **But** messages across partitions are NOT guaranteed to arrive in the order they were produced.

### Scenario: Payment Processed Before Order Created

**Problem:** "Occasionally, the payment service processes a payment before the order-created event. The payment service receives a `charge_customer` event referencing `order_id=999`, but no such order exists yet in the database."

**Investigation:**
```bash
# Check Kafka topic partition and key
kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic orders --from-beginning \
  --property print.partition=true --property print.key=true \
  --property print.timestamp=true

# Output:
# 2026-06-11T14:30:01.000 order-999  PARTITION=2  {"action":"order_created","order_id":999}
# 2026-06-11T14:30:01.010 order-999  PARTITION=0  {"action":"charge_payment","order_id":999}
```

**Root cause:** The `order_created` event was produced to Partition 2, and the `charge_payment` event to Partition 0 (because the producer didn't set a consistent partition key). Consumer 0 processed the `charge_payment` before Consumer 2 processed `order_created` → payment processed before order existed.

**Fix:** Use the same partition key for all messages related to the same order:

```java
// Producer: use order_id as the key
ProducerRecord<String, String> record = new ProducerRecord<>(
    "orders",
    "order-999",  // Key = order_id → same partition for all messages about this order
    "{'action':'charge_payment','order_id':999}"
);
producer.send(record);
```

**SQS FIFO fix:** Use `MessageGroupId=order-999` for all messages about order 999.

---

## Idempotency & Exactly-Once Processing

### The Problem: At-Least-Once Delivery

Message queues typically guarantee **at-least-once** delivery. The same message can be delivered to a consumer multiple times:

1. Consumer receives message, begins processing.
2. Processing takes 30 seconds.
3. SQS visibility timeout was 20 seconds → **message becomes visible again**.
4. Second consumer picks up the "same" message and begins processing.
5. Both consumers finish → **order processed twice, customer charged twice**.

### Idempotency Key Pattern for Message Queues

```python
def process_message(message):
    idempotency_key = f"msg:{message['message_id']}:v{message.get('version', 1)}"

    # Step 1: Check if already processed
    if redis.exists(idempotency_key):
        logger.info(f"Skipping duplicate message {idempotency_key}")
        return True  # ACK the message (already processed)

    # Step 2: Set a processing lock (prevents concurrent processing of same message)
    lock_key = f"lock:{idempotency_key}"
    if not redis.set(lock_key, "processing", nx=True, ex=60):
        logger.info(f"Another consumer is processing {idempotency_key}")
        return False  # Don't ACK — let visibility timeout handle retry

    try:
        # Step 3: Process the business logic
        order_id = message['order_id']
        if not order_exists(order_id):  # Business-level idempotency check
            create_order(message)

        # Step 4: Mark as processed (with TTL = visibility timeout * 2)
        redis.setex(idempotency_key, 3600, json.dumps({
            "status": "processed",
            "timestamp": time.time(),
            "order_id": order_id,
        }))
        return True

    except DatabaseIntegrityError as e:
        # Duplicate order insert → already processed by another consumer
        logger.warning(f"Duplicate order detected: {e}")
        redis.setex(idempotency_key, 3600, json.dumps({
            "status": "duplicate_detected",
            "timestamp": time.time(),
        }))
        return True

    except Exception as e:
        logger.error(f"Processing failed: {e}")
        return False  # Don't ACK — will be retried

    finally:
        redis.delete(lock_key)
```

### Scenario: Duplicate Orders from Visibility Timeout

**Problem:** "Customer reports being charged twice for the same order. Investigation shows two order records with identical payloads, created 15 seconds apart."

**SQS Visibility timeout: 20 seconds.** Order processing takes 35 seconds (waiting on external payment gateway).

**Timeline:**
```
t=0s:   Consumer A picks up message M (visibility timeout: 20s).
t=0s:   Consumer A begins processing order...
t=20s:  Visibility timeout expires. Message M reappears in queue.
t=20s:  Consumer B picks up message M (visibility timeout: 20s).
t=35s:  Consumer A finishes → creates order 001 → deletes message M.
t=55s:  Consumer B finishes → creates order 002 (DUPLICATE!) → tries to delete M (already deleted — harmless).
```

**Fix:**

**Option A: Increase visibility timeout.**
```bash
aws sqs set-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/123456789/orders-queue \
  --attributes VisibilityTimeout=120
```
Set visibility timeout > max processing time. Trade-off: slower recovery if consumer crashes while processing (message won't reappear for 120 seconds).

**Option B: Implement idempotency (recommended).**
Always combine appropriate timeout with idempotency. Even with proper timeout, network partitions can cause duplicate delivery.

**Option C: SQS FIFO Queue with deduplication.**
```python
sqs.send_message(
    QueueUrl=fifo_queue_url,
    MessageBody=json.dumps(order_data),
    MessageGroupId="orders",
    MessageDeduplicationId=order_data['idempotency_key'],
)
```
FIFO queues guarantee deduplication within a 5-minute window. The same `MessageDeduplicationId` won't be delivered twice within that window.

---

## Code Examples

### Python: SQS Consumer with DLQ, Idempotency, and Graceful Shutdown

```python
import asyncio
import hashlib
import json
import logging
import os
import signal
import time
import uuid
from contextlib import contextmanager
from typing import Optional

import aiobotocore.session
import redis.asyncio as redis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

QUEUE_URL = os.getenv("SQS_QUEUE_URL", "")
DLQ_URL = os.getenv("SQS_DLQ_URL", "")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
VISIBILITY_TIMEOUT = int(os.getenv("VISIBILITY_TIMEOUT", "120"))
WAIT_TIME_SECONDS = int(os.getenv("WAIT_TIME_SECONDS", "10"))
MAX_RETRIES_BEFORE_DLQ = int(os.getenv("MAX_RETRIES", "3"))

SHUTDOWN_REQUESTED = False


def handle_signal(signum, frame):
    global SHUTDOWN_REQUESTED
    logger.info(f"Received signal {signum}. Shutting down gracefully...")
    SHUTDOWN_REQUESTED = True


signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


class SqsConsumer:
    def __init__(self, queue_url: str, dlq_url: str, redis_client: redis.Redis):
        self.queue_url = queue_url
        self.dlq_url = dlq_url
        self.redis = redis_client
        self.session = aiobotocore.session.get_session()
        self._sqs_client = None
        self._message_count = 0
        self._error_count = 0

    async def _get_client(self):
        if self._sqs_client is None:
            self._sqs_client = await self.session.create_client(
                "sqs",
                region_name=os.getenv("AWS_REGION", "us-east-1"),
            ).__aenter__()
        return self._sqs_client

    async def run(self):
        client = await self._get_client()
        logger.info(f"Consumer started. Queue: {self.queue_url}")

        while not SHUTDOWN_REQUESTED:
            try:
                response = await client.receive_message(
                    QueueUrl=self.queue_url,
                    MaxNumberOfMessages=10,
                    WaitTimeSeconds=WAIT_TIME_SECONDS,
                    VisibilityTimeout=VISIBILITY_TIMEOUT,
                    AttributeNames=["ApproximateReceiveCount"],
                )

                messages = response.get("Messages", [])
                if not messages:
                    continue

                tasks = [self.process_message(client, msg) for msg in messages]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.error(f"Unhandled exception processing message: {result}")
                        self._error_count += 1

            except Exception as e:
                logger.error(f"Error polling SQS: {e}")
                await asyncio.sleep(5)

        logger.info(
            f"Consumer stopped. Processed: {self._message_count}, "
            f"Errors: {self._error_count}"
        )

    async def process_message(self, client, message):
        receipt_handle = message["ReceiptHandle"]
        message_id = message["MessageId"]
        receive_count = int(message.get("Attributes", {}).get("ApproximateReceiveCount", 1))

        logger.info(
            f"Processing message {message_id} (receive count: {receive_count})"
        )

        try:
            body = json.loads(message["Body"])
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON in message {message_id}. Sending to DLQ.")
            await self.send_to_dlq(client, message)
            await client.delete_message(
                QueueUrl=self.queue_url, ReceiptHandle=receipt_handle
            )
            return

        idempotency_key = body.get("idempotency_key", message_id)

        # Check idempotency
        if await self.redis.exists(f"processed:{idempotency_key}"):
            logger.info(f"Duplicate message {idempotency_key} — already processed")
            await client.delete_message(
                QueueUrl=self.queue_url, ReceiptHandle=receipt_handle
            )
            return

        # Check if exceeded max retries → move to DLQ
        if receive_count > MAX_RETRIES_BEFORE_DLQ:
            logger.warning(
                f"Message {message_id} exceeded max retries ({MAX_RETRIES_BEFORE_DLQ}). "
                f"Sending to DLQ."
            )
            await self.send_to_dlq(client, message)
            await client.delete_message(
                QueueUrl=self.queue_url, ReceiptHandle=receipt_handle
            )
            return

        # Process with business logic
        success = await self.business_logic(body)

        if success:
            # Mark as processed with TTL
            await self.redis.setex(
                f"processed:{idempotency_key}",
                86400,  # 24 hours TTL
                json.dumps({"status": "processed", "timestamp": time.time()}),
            )
            await client.delete_message(
                QueueUrl=self.queue_url, ReceiptHandle=receipt_handle
            )
            self._message_count += 1
            logger.info(f"Successfully processed message {message_id}")
        else:
            # Let visibility timeout expire → message will be retried
            logger.warning(
                f"Processing failed for message {message_id}. Will be retried."
            )
            self._error_count += 1

    async def business_logic(self, body: dict) -> bool:
        """Replace with actual business logic."""
        order_id = body.get("order_id")
        amount = body.get("amount")

        if not order_id or not amount:
            logger.error(f"Missing required fields in message: {body}")
            return False

        # Simulate processing
        await asyncio.sleep(0.5)

        # Example: persist order
        logger.info(f"Processing order {order_id} for ${amount}")
        return True

    async def send_to_dlq(self, client, message):
        """Send failed message to Dead Letter Queue with error context."""
        try:
            dlq_body = json.dumps({
                "original_message": json.loads(message["Body"]),
                "error": "Exceeded max processing attempts",
                "original_message_id": message["MessageId"],
                "sent_to_dlq_at": time.time(),
            })

            await client.send_message(
                QueueUrl=self.dlq_url,
                MessageBody=dlq_body,
            )
            logger.info(f"Sent message {message['MessageId']} to DLQ")
        except Exception as e:
            logger.error(f"Failed to send message {message['MessageId']} to DLQ: {e}")

    async def shutdown(self):
        if self._sqs_client:
            await self._sqs_client.__aexit__(None, None, None)


async def main():
    redis_client = redis.from_url(
        REDIS_URL, encoding="utf-8", decode_responses=True
    )
    try:
        await redis_client.ping()
    except Exception:
        logger.warning("Redis not available — running without idempotency protection")

    consumer = SqsConsumer(QUEUE_URL, DLQ_URL, redis_client)
    try:
        await consumer.run()
    finally:
        await consumer.shutdown()
        await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())


# --- Idempotency Utility (standalone) ---
def generate_idempotency_key(prefix: str = "") -> str:
    """Generate a unique idempotency key."""
    if prefix:
        return f"{prefix}_{uuid.uuid4().hex}"
    return uuid.uuid4().hex


def generate_deduplication_key(payload: dict) -> str:
    """Generate a deterministic deduplication key from payload content."""
    canonical = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()
```

### Java: Kafka Consumer with Exactly-Once Semantics

```java
package com.example.kafka;

import org.apache.kafka.clients.consumer.*;
import org.apache.kafka.clients.producer.*;
import org.apache.kafka.common.TopicPartition;
import org.apache.kafka.common.errors.WakeupException;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;

import java.time.Duration;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Kafka consumer with:
 * - Idempotent processing via Redis-backed deduplication
 * - Dead Letter topic for poison messages
 * - Graceful shutdown
 * - Lag monitoring
 * - Manual offset commit after successful processing
 */
public class IdempotentKafkaConsumer implements Runnable {

    private final String bootstrapServers;
    private final String groupId;
    private final String sourceTopic;
    private final String dlqTopic;
    private final KafkaConsumer<String, String> consumer;
    private final Producer<String, String> dlqProducer;
    private final DeduplicationStore dedupStore;
    private final AtomicBoolean running = new AtomicBoolean(true);
    private final AtomicLong processedCount = new AtomicLong(0);
    private final AtomicLong errorCount = new AtomicLong(0);
    private final AtomicLong duplicateCount = new AtomicLong(0);

    public IdempotentKafkaConsumer(
            String bootstrapServers,
            String groupId,
            String sourceTopic,
            String dlqTopic,
            DeduplicationStore dedupStore
    ) {
        this.bootstrapServers = bootstrapServers;
        this.groupId = groupId;
        this.sourceTopic = sourceTopic;
        this.dlqTopic = dlqTopic;
        this.dedupStore = dedupStore;

        Properties consumerProps = new Properties();
        consumerProps.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrapServers);
        consumerProps.put(ConsumerConfig.GROUP_ID_CONFIG, groupId);
        consumerProps.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class.getName());
        consumerProps.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class.getName());
        consumerProps.put(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, "false");
        consumerProps.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        consumerProps.put(ConsumerConfig.MAX_POLL_RECORDS_CONFIG, "100");
        // Prevent consumer from being kicked out of the group during long processing
        consumerProps.put(ConsumerConfig.MAX_POLL_INTERVAL_MS_CONFIG, "600000"); // 10 min

        this.consumer = new KafkaConsumer<>(consumerProps);

        Properties producerProps = new Properties();
        producerProps.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrapServers);
        producerProps.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class.getName());
        producerProps.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, StringSerializer.class.getName());
        producerProps.put(ProducerConfig.ACKS_CONFIG, "all");
        producerProps.put(ProducerConfig.ENABLE_IDEMPOTENCE_CONFIG, "true");

        this.dlqProducer = new KafkaProducer<>(producerProps);
    }

    @Override
    public void run() {
        consumer.subscribe(Collections.singletonList(sourceTopic));

        try {
            while (running.get()) {
                ConsumerRecords<String, String> records = consumer.poll(Duration.ofSeconds(5));

                if (records.isEmpty()) {
                    continue;
                }

                Map<Integer, Long> partitionOffsets = new HashMap<>();

                for (ConsumerRecord<String, String> record : records) {
                    try {
                        boolean success = processRecord(record);
                        if (success) {
                            partitionOffsets.put(record.partition(), record.offset());
                        }
                    } catch (WakeupException e) {
                        throw e;
                    } catch (Exception e) {
                        errorCount.incrementAndGet();
                        logError(record, e);

                        // After 3 retries, send to DLQ
                        if (shouldMoveToDlq(record)) {
                            sendToDlq(record, e.getMessage());
                            // Commit offset for poison message so we don't reprocess it
                            partitionOffsets.put(record.partition(), record.offset());
                        }
                        // Otherwise, don't commit — message will be reprocessed
                    }
                }

                // Commit offsets for successfully processed messages
                if (!partitionOffsets.isEmpty()) {
                    commitOffsets(partitionOffsets);
                }
            }
        } catch (WakeupException e) {
            // Expected during shutdown — ignore
            if (running.get()) {
                throw e;
            }
        } finally {
            consumer.close();
            dlqProducer.close();
            printStats();
        }
    }

    private boolean processRecord(ConsumerRecord<String, String> record) {
        String messageId = extractMessageId(record);
        String processingKey = "msg:" + sourceTopic + ":" + record.partition() + ":" + record.offset();

        // Step 1: Idempotency check
        if (dedupStore.isProcessed(processingKey)) {
            duplicateCount.incrementAndGet();
            logDuplicate(record, processingKey);
            return true; // Already processed — safe to commit offset
        }

        // Step 2: Acquire processing lock (distributed via Redis/Memcached)
        if (!dedupStore.tryAcquireLock(processingKey, 120)) {
            logSkip(record, processingKey); // Another consumer is processing this
            return false; // Don't commit — another consumer will handle it
        }

        try {
            // Step 3: Business logic
            OrderEvent event = parseOrderEvent(record.value());

            // Business-level idempotency check (e.g., check if order already exists)
            if (orderExists(event.getOrderId())) {
                dedupStore.markProcessed(processingKey, 86400);
                return true;
            }

            // Process the order atomically
            createOrder(event);

            // Step 4: Mark as processed (TTL = 24 hours)
            dedupStore.markProcessed(processingKey, 86400);
            processedCount.incrementAndGet();
            logSuccess(record);

            return true;

        } catch (DuplicateOrderException e) {
            // Order already exists — another consumer beat us to it
            dedupStore.markProcessed(processingKey, 86400);
            logDuplicate(record, processingKey);
            return true;

        } catch (Exception e) {
            logError(record, e);
            throw e;

        } finally {
            dedupStore.releaseLock(processingKey);
        }
    }

    private void sendToDlq(ConsumerRecord<String, String> record, String errorMessage) {
        DLQMessage dlqMessage = new DLQMessage(
            record.key(),
            record.value(),
            sourceTopic,
            record.partition(),
            record.offset(),
            errorMessage,
            System.currentTimeMillis()
        );

        ProducerRecord<String, String> dlqRecord = new ProducerRecord<>(
            dlqTopic,
            dlqMessage.toJson()
        );

        dlqProducer.send(dlqRecord, (metadata, exception) -> {
            if (exception != null) {
                System.err.println("Failed to send to DLQ: " + exception.getMessage());
            } else {
                System.out.printf("Sent to DLQ: topic=%s, partition=%d, offset=%d%n",
                    dlqTopic, metadata.partition(), metadata.offset());
            }
        });
    }

    private void commitOffsets(Map<Integer, Long> partitionOffsets) {
        for (Map.Entry<Integer, Long> entry : partitionOffsets.entrySet()) {
            consumer.commitSync(Collections.singletonMap(
                new TopicPartition(sourceTopic, entry.getKey()),
                new OffsetAndMetadata(entry.getValue() + 1)
            ));
        }
    }

    public void shutdown() {
        running.set(false);
        consumer.wakeup();
    }

    private String extractMessageId(ConsumerRecord<String, String> record) {
        return record.key() != null ? record.key() : record.topic() + ":" + record.partition() + ":" + record.offset();
    }

    private boolean shouldMoveToDlq(ConsumerRecord<String, String> record) {
        // In practice, track retry count via headers or external store
        return true; // Simplified
    }

    // Legacy — real implementations go here
    private OrderEvent parseOrderEvent(String value) { return new OrderEvent(); }
    private boolean orderExists(String orderId) { return false; }
    private void createOrder(OrderEvent event) {}

    private void logSuccess(ConsumerRecord<String, String> record) {}
    private void logDuplicate(ConsumerRecord<String, String> record, String key) {}
    private void logSkip(ConsumerRecord<String, String> record, String key) {}
    private void logError(ConsumerRecord<String, String> record, Exception e) {}
    private void printStats() {}

    static class OrderEvent { String getOrderId() { return ""; } }
    static class DuplicateOrderException extends RuntimeException {}
    static class DLQMessage {
        DLQMessage(String k, String v, String t, int p, long o, String err, long ts) {}
        String toJson() { return ""; }
    }
}

/**
 * Abstraction for idempotency storage.
 * Implementations: Redis, Memcached, DynamoDB, or local ConcurrentHashMap.
 */
interface DeduplicationStore {
    boolean isProcessed(String key);
    boolean tryAcquireLock(String key, int ttlSeconds);
    void markProcessed(String key, int ttlSeconds);
    void releaseLock(String key);
}

/**
 * In-memory implementation for single-instance use.
 * In production, use Redis with SET NX for lock and SETEX for processed markers.
 */
class InMemoryDeduplicationStore implements DeduplicationStore {
    private final ConcurrentHashMap<String, Long> processed = new ConcurrentHashMap<>();
    private final ConcurrentHashMap<String, Long> locks = new ConcurrentHashMap<>();

    @Override
    public boolean isProcessed(String key) {
        Long expiry = processed.get(key);
        if (expiry == null) return false;
        if (System.currentTimeMillis() > expiry) {
            processed.remove(key);
            return false;
        }
        return true;
    }

    @Override
    public boolean tryAcquireLock(String key, int ttlSeconds) {
        long now = System.currentTimeMillis();
        Long existing = locks.putIfAbsent(key, now + ttlSeconds * 1000L);
        if (existing != null && existing > now) {
            return false; // Lock held by another consumer
        }
        locks.put(key, now + ttlSeconds * 1000L); // Overwrite expired lock
        return true;
    }

    @Override
    public void markProcessed(String key, int ttlSeconds) {
        processed.put(key, System.currentTimeMillis() + ttlSeconds * 1000L);
    }

    @Override
    public void releaseLock(String key) {
        locks.remove(key);
    }
}
```

### JavaScript: SQS Consumer with DLQ and Idempotency

```javascript
const { SQSClient, ReceiveMessageCommand, DeleteMessageCommand, SendMessageCommand } = require('@aws-sdk/client-sqs');
const Redis = require('ioredis');

const QUEUE_URL = process.env.SQS_QUEUE_URL;
const DLQ_URL = process.env.SQS_DLQ_URL;
const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';
const MAX_RETRIES = parseInt(process.env.MAX_RETRIES || '3', 10);
const VISIBILITY_TIMEOUT = parseInt(process.env.VISIBILITY_TIMEOUT || '120', 10);

const sqs = new SQSClient({ region: process.env.AWS_REGION || 'us-east-1' });
const redis = new Redis(REDIS_URL);

let running = true;
let messageCount = 0;
let errorCount = 0;
let duplicateCount = 0;

process.on('SIGTERM', gracefulShutdown);
process.on('SIGINT', gracefulShutdown);

async function gracefulShutdown() {
  console.log('[Consumer] Shutting down gracefully...');
  running = false;
}

async function poll() {
  console.log(`[Consumer] Started. Queue: ${QUEUE_URL}`);

  while (running) {
    try {
      const command = new ReceiveMessageCommand({
        QueueUrl: QUEUE_URL,
        MaxNumberOfMessages: 10,
        WaitTimeSeconds: 10,
        VisibilityTimeout: VISIBILITY_TIMEOUT,
        AttributeNames: ['ApproximateReceiveCount'],
      });

      const response = await sqs.send(command);

      if (!response.Messages || response.Messages.length === 0) {
        continue;
      }

      const results = await Promise.allSettled(
        response.Messages.map(msg => processMessage(msg))
      );

      results.forEach((result, i) => {
        if (result.status === 'rejected') {
          console.error(`[Consumer] Unhandled error: ${result.reason.message}`);
          errorCount++;
        }
      });

      console.log(
        `[Consumer] Stats — processed: ${messageCount}, errors: ${errorCount}, duplicates: ${duplicateCount}`
      );
    } catch (err) {
      console.error(`[Consumer] Poll error: ${err.message}`);
      await sleep(5000);
    }
  }

  console.log(
    `[Consumer] Stopped. Final stats — processed: ${messageCount}, ` +
    `errors: ${errorCount}, duplicates: ${duplicateCount}`
  );
  redis.disconnect();
  sqs.destroy();
}

async function processMessage(message) {
  const receiptHandle = message.ReceiptHandle;
  const messageId = message.MessageId;
  const receiveCount = parseInt(
    message.Attributes?.ApproximateReceiveCount || '1', 10
  );

  let body;
  try {
    body = JSON.parse(message.Body);
  } catch (err) {
    console.error(`[Consumer] Invalid JSON in message ${messageId}. Moving to DLQ.`);
    await sendToDlq(message, `Invalid JSON: ${message.Body.substring(0, 200)}`);
    await deleteMessage(receiptHandle);
    return;
  }

  const idempotencyKey = body.idempotency_key || messageId;
  const processedKey = `processed:${idempotencyKey}`;
  const lockKey = `lock:${idempotencyKey}`;

  // Idempotency check
  const isProcessed = await redis.exists(processedKey);
  if (isProcessed) {
    console.log(`[Consumer] Duplicate message ${idempotencyKey} — skipping`);
    duplicateCount++;
    await deleteMessage(receiptHandle);
    return;
  }

  // Check if should move to DLQ
  if (receiveCount > MAX_RETRIES) {
    console.warn(
      `[Consumer] Message ${messageId} exceeded max retries (${receiveCount}/${MAX_RETRIES}). ` +
      `Moving to DLQ.`
    );
    await sendToDlq(message, `Exceeded max retries: ${receiveCount}`);
    await deleteMessage(receiptHandle);
    return;
  }

  // Acquire processing lock
  const lockAcquired = await redis.set(lockKey, 'processing', 'NX', 'EX', 120);
  if (!lockAcquired) {
    console.log(`[Consumer] Another consumer is processing ${idempotencyKey} — skipping`);
    return; // Don't delete message — other consumer will
  }

  try {
    const success = await businessLogic(body);

    if (success) {
      await redis.setex(
        processedKey, 86400,
        JSON.stringify({
          status: 'processed',
          timestamp: Date.now(),
          orderId: body.order_id,
        })
      );
      await deleteMessage(receiptHandle);
      messageCount++;
      console.log(`[Consumer] Successfully processed ${messageId}`);
    } else {
      console.warn(
        `[Consumer] Processing failed for ${messageId} — will retry (attempt ${receiveCount})`
      );
      errorCount++;
      // Don't delete — visibility timeout will expire
    }
  } catch (err) {
    console.error(`[Consumer] Error processing ${messageId}: ${err.message}`);
    errorCount++;
    // Don't delete — visibility timeout will expire
  } finally {
    await redis.del(lockKey);
  }
}

async function businessLogic(body) {
  const { order_id, amount } = body;

  if (!order_id || !amount) {
    console.error(`[Consumer] Missing required fields: ${JSON.stringify(body)}`);
    return false;
  }

  // Simulate processing
  await sleep(200);

  console.log(`[Consumer] Processing order ${order_id} for $${amount}`);
  return true;
}

async function sendToDlq(message, error) {
  try {
    const dlqBody = JSON.stringify({
      original_message: message.Body,
      error,
      original_message_id: message.MessageId,
      sent_to_dlq_at: Date.now(),
    });

    const command = new SendMessageCommand({
      QueueUrl: DLQ_URL,
      MessageBody: dlqBody,
    });

    await sqs.send(command);
    console.log(`[Consumer] Sent message ${message.MessageId} to DLQ`);
  } catch (err) {
    console.error(`[Consumer] Failed to send ${message.MessageId} to DLQ: ${err.message}`);
    throw err;
  }
}

async function deleteMessage(receiptHandle) {
  try {
    const command = new DeleteMessageCommand({
      QueueUrl: QUEUE_URL,
      ReceiptHandle: receiptHandle,
    });
    await sqs.send(command);
  } catch (err) {
    console.error(`[Consumer] Failed to delete message: ${err.message}`);
  }
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// --- Idempotency utilities ---
function generateIdempotencyKey(prefix = '') {
  const crypto = require('crypto');
  const id = crypto.randomUUID();
  return prefix ? `${prefix}_${id}` : id;
}

function generateDeduplicationKey(payload) {
  const crypto = require('crypto');
  const canonical = JSON.stringify(payload, Object.keys(payload).sort());
  return crypto.createHash('sha256').update(canonical).digest('hex');
}

// --- Start ---
poll().catch(err => {
  console.error(`[Consumer] Fatal error: ${err}`);
  process.exit(1);
});

module.exports = { generateIdempotencyKey, generateDeduplicationKey };
```
