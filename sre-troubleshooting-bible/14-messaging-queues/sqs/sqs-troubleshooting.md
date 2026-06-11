# SQS Troubleshooting
> **Category:** Messaging | AWS | SQS
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#aws` `#sqs` `#messaging` `#oncall`

---

## SQS Architecture Overview

```
┌───────────┐     ┌──────────────┐     ┌──────────────┐     ┌───────────┐
│ Producer  │────▶│  SQS Queue   │────▶│  Consumer    │────▶│  DLQ      │
│ (EC2/     │     │  (Standard   │     │  (Lambda/    │     │  (another │
│  Lambda/  │     │   or FIFO)   │     │   EC2/ECS)   │     │   SQS Q)  │
│  on-prem) │     │              │     │              │     │           │
└───────────┘     └──────────────┘     └──────────────┘     └───────────┘
                        │                                         │
                        │  Visibility Timeout                     │
                        │  (message hidden                        │
                        │   during processing)                    │
                        │                                         │
                   Redrive Policy                                 │
                   (after N failures)                             │
```

---

## Message Visibility Timeout

### The Problem

When a consumer receives a message, it becomes invisible to other consumers for the duration of the **visibility timeout**. The consumer must process and delete the message before this timeout expires. If the timeout expires before deletion, the message becomes visible again and another consumer will receive it — causing **duplicate processing**.

### How to Tune Visibility Timeout

```
Visibility Timeout = Processing Time (p99) + Buffer

Processing Time (p99):
  - Fast API call: 200ms
  - Database write: 500ms
  - Complex workflow: 45 seconds
  - PDF generation: 120 seconds

Buffer:
  - Network latency variance: +20%
  - GC pause overhead (Java): +30%
  - Cold start (Lambda): +5-10 seconds for first invocation

Example:
  p99 processing time: 45s
  Buffer: 20s
  Recommended visibility timeout: 65s — round up to 120s for safety
```

### Scenario: Customer Charged Twice

**Symptoms:**
- Order processing takes 45 seconds on average (p99 = 55s)
- Visibility timeout configured: 30 seconds
- At T+30s: message reappears in queue (original consumer still processing)
- Second consumer picks up the message, processes the same order
- Customer's credit card charged twice

**Fix:**
```bash
# Increase visibility timeout to at least 2x p99 processing time
aws sqs set-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/123456789/orders \
  --attributes VisibilityTimeout=120

# Better: extend visibility dynamically during long processing
# See Python/Java code examples below for ChangeMessageVisibility
```

### Extend Visibility Mid-Processing

When processing takes longer than expected, the consumer can call `ChangeMessageVisibility` to extend the timeout for that specific message:

```python
# Python boto3: extend visibility during processing
import boto3
import time

sqs = boto3.client('sqs')
queue_url = 'https://sqs.us-east-1.amazonaws.com/123456789/orders'

# Receive message
response = sqs.receive_message(
    QueueUrl=queue_url,
    MaxNumberOfMessages=1,
    VisibilityTimeout=60,  # Initial 60 seconds
    WaitTimeSeconds=20      # Long polling
)

message = response['Messages'][0]
receipt_handle = message['ReceiptHandle']

# Processing takes longer than expected...
time.sleep(30)

# Extend visibility by another 60 seconds (total: 90s from receipt)
sqs.change_message_visibility(
    QueueUrl=queue_url,
    ReceiptHandle=receipt_handle,
    VisibilityTimeout=60  # Resets to 60s from NOW
)
# Now the message won't reappear for another 60 seconds

# Complete processing
time.sleep(20)  # More work...

# Delete the message
sqs.delete_message(
    QueueUrl=queue_url,
    ReceiptHandle=receipt_handle
)
```

---

## Dead Letter Queue (DLQ)

### Configuration

```bash
# Create a DLQ (standard queue) for failed messages
aws sqs create-queue --queue-name orders-dlq

# Get the DLQ ARN
DLQ_ARN=$(aws sqs get-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/123456789/orders-dlq \
  --attribute-names QueueArn \
  --query 'Attributes.QueueArn' --output text)

# Configure the source queue to use the DLQ
# Redrive policy: after ReceiveCount exceeds maxReceiveCount, move to DLQ
aws sqs set-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/123456789/orders \
  --attributes "{
    \"RedrivePolicy\": \"{\\\"deadLetterTargetArn\\\":\\\"${DLQ_ARN}\\\",\\\"maxReceiveCount\\\":\\\"5\\\"}\"
  }"

# maxReceiveCount = 5 means:
# Message is received 5 times, not deleted → 6th receive attempt → Moved to DLQ
# Consumer gets 5 chances to process successfully before the message is quarantined
```

### Monitoring DLQ

```bash
# Check DLQ depth — if > 0, investigate immediately
aws sqs get-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/123456789/orders-dlq \
  --attribute-names ApproximateNumberOfMessagesVisible \
           ApproximateNumberOfMessagesNotVisible

# CloudWatch alarm:
# Metric: ApproximateNumberOfMessagesVisible
# Dimensions: QueueName=orders-dlq
# Alarm: > 0 for 1 evaluation period (1 min) → PagerDuty
```

### DLQ Redrive

After fixing the consumer bug, move messages from DLQ back to the source queue:

```bash
# AWS Console: SQS → orders-dlq → "Start DLQ redrive"
# Or via CLI:
aws sqs start-message-move-task \
  --source-queue-url https://sqs.us-east-1.amazonaws.com/123456789/orders-dlq \
  --destination-queue-url https://sqs.us-east-1.amazonaws.com/123456789/orders

# Monitor the redrive
aws sqs list-message-move-tasks --source-queue-url $DLQ_URL

# If using Lambda + SQS: configure DLQ redrive on the Lambda function console
# (Lambda → Function → Configuration → Asynchronous invocation → Dead-letter queue)
```

### Scenario: DLQ Has 10,000 Messages

**Diagnosis:**
```bash
# Peek at a DLQ message to understand the failure
aws sqs receive-message \
  --queue-url https://sqs.us-east-1.amazonaws.com/123456789/orders-dlq \
  --max-number-of-messages 1 \
  --visibility-timeout 60 \
  --attribute-names All

# Output shows the message body + attributes:
# Body: { "orderId": "12345", "amount": 250.00, "currency": "INVALID" }
# ApproximateReceiveCount: 6 (DLQ threshold = 5, so it was received 6 times)

# Consumer logs show: "UnsupportedCurrencyException: INVALID"
# Root cause: Producer sends currency="INVALID" for some orders
# Consumer throws exception, message retried 5 times, then DLQ

# Fix:
# 1. Fix producer to validate currency before sending
# 2. Add consumer-side validation: if currency unknown, log warning and skip
#    (don't throw exception — log and delete the message)
# 3. Redrive valid DLQ messages back to source queue
# 4. Purge invalid messages from DLQ
```

---

## Long Polling vs Short Polling

### Cost Comparison

```bash
# Short polling (default, WaitTimeSeconds=0):
# Consumer calls ReceiveMessage → immediate response (possibly empty)
# 1000 empty polls/min × 60 min × 24 hours = 1,440,000 empty requests/day
# At $0.40 per million requests = $0.58/day wasted on empty responses

# Long polling (WaitTimeSeconds=20):
# Consumer calls ReceiveMessage → waits up to 20s for a message
# 20s wait × 3 polls/min = 4,320 polls/day
# 95% fewer empty responses = significant cost savings

# Enable long polling:
aws sqs set-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/123456789/orders \
  --attributes ReceiveMessageWaitTimeSeconds=20

# Or specify per request:
sqs.receive_message(
    QueueUrl=queue_url,
    WaitTimeSeconds=20  # Overrides queue default
)
```

### Scenario: $5000/month Lambda SQS Bill

**Diagnosis:**
- Lambda SQS trigger polls the queue 1,000 times per minute
- Most polls return empty (low-traffic queue, nighttime)
- Lambda charges per invocation: 1,000/min × 60 × 24 × 30 = 43.2M invocations/month
- That's ~$5,000/month for... nothing

**Fix:**
```bash
# Enable long polling on the SQS queue
aws sqs set-queue-attributes \
  --queue-url $QUEUE_URL \
  --attributes ReceiveMessageWaitTimeSeconds=20

# Lambda SQS event source mapping will now wait up to 20 seconds per poll
# Empty response rate drops from 90% to <5%
# Monthly Lambda invocations: 43.2M → ~2.2M
# Monthly cost: $5,000 → ~$250
```

---

## FIFO vs Standard

### Standard Queue

| Characteristic | Value |
|---------------|-------|
| Throughput | Near-unlimited (thousands/second) |
| Delivery | At-least-once (duplicates possible) |
| Ordering | Best-effort (not guaranteed) |
| Use case | Work distribution, decoupling, batch jobs |
| Key design | **Use idempotency keys** (order ID, transaction ID) |

### FIFO Queue

| Characteristic | Value |
|---------------|-------|
| Throughput | 300 msg/s without batching, 3,000 msg/s with batching |
| Delivery | Exactly-once (within 5-minute deduplication window) |
| Ordering | Guaranteed within a MessageGroupId |
| Use case | Financial transactions, sequential workflows |
| Key design | Choose the right MessageGroupId for parallelism |

### How FIFO Ordering Works

```
Messages within the SAME MessageGroupId are processed in order.
Messages with DIFFERENT MessageGroupId can be processed in parallel.

Example: Order processing
  MessageGroupId = "user-123"  ← All orders for user 123 processed in order
  MessageGroupId = "user-456"  ← All orders for user 456 processed in order
  Messages for user-123 and user-456 are processed CONCURRENTLY

Anti-pattern: Grouping all messages under a single MessageGroupId
  MessageGroupId = "all-orders"  ← ALL messages processed sequentially!
  Throughput = 1 message at a time → 300 msg/s max
```

### Scenario: FIFO Queue Bottleneck

```
FIFO queue "orders" — 10,000 msg/s required
maxReceiveCount: 5
Throughput: 300 msg/s (without batching) — WAY below requirement

Diagnosis:
  All producers use MessageGroupId = "default" (single group)
  All messages serialized → 300 msg/s max

Fix:
  Use OrderID as MessageGroupId:
    MessageGroupId = order.getId()  ← Each order is its own group
    Orders for the same customer still ordered (use customerId)
    Throughput: thousands of concurrent groups
    But: ordering is per group, not global

  Or: switch to Standard queue with idempotency:
    Consumer checks DynamoDB before processing:
      if dynamodb.get_item(Key={'order_id': order_id}) exists → skip (already processed)
      else → process → write to DynamoDB
```

---

## Message Attributes

### Usage for Routing

Message attributes are metadata attached to messages. Consumers can inspect attributes to route messages to different handlers without parsing the body.

```python
# Producer: send message with attributes
import boto3
sqs = boto3.client('sqs')

sqs.send_message(
    QueueUrl='https://sqs.us-east-1.amazonaws.com/123456789/events',
    MessageBody='{"orderId": "abc-123", "amount": 250.00}',
    MessageAttributes={
        'EventType': {
            'StringValue': 'order_created',
            'DataType': 'String'
        },
        'Priority': {
            'StringValue': 'high',
            'DataType': 'String'
        },
        'RetryCount': {
            'StringValue': '0',
            'DataType': 'Number'
        }
    }
)

# Consumer: route based on attributes
response = sqs.receive_message(
    QueueUrl='https://sqs.us-east-1.amazonaws.com/123456789/events',
    MessageAttributeNames=['EventType', 'Priority'],
    MaxNumberOfMessages=10
)

for msg in response.get('Messages', []):
    attrs = msg.get('MessageAttributes', {})
    event_type = attrs.get('EventType', {}).get('StringValue', 'unknown')

    if event_type == 'order_created':
        process_order(msg)  # Send to order consumer
    elif event_type == 'inventory_updated':
        process_inventory(msg)  # Send to inventory consumer
    else:
        log_warning(f"Unknown event type: {event_type}")
```

---

## Python: SQS Consumer with Idempotency

```python
#!/usr/bin/env python3
"""
sqs_consumer_ops.py — Production-grade SQS consumer with idempotency (DynamoDB),
visibility timeout extension, DLQ handling, and graceful shutdown.

Usage:
    python sqs_consumer_ops.py --queue-url https://sqs...amazonaws.com/123/orders \
        --idempotency-table order-processing-locks --region us-east-1
"""

import boto3
import json
import signal
import sys
import time
import hashlib
from argparse import ArgumentParser
from datetime import datetime, timedelta
from typing import Optional


class IdempotencyGuard:
    """Prevents duplicate message processing using DynamoDB as a lock store."""

    def __init__(self, table_name: str, region: str):
        self.table = boto3.resource('dynamodb', region_name=region).Table(table_name)
        self.lock_ttl_days = 7

    def try_lock(self, message_id: str) -> bool:
        """Attempt to acquire an idempotency lock. Returns True if acquired."""
        now = datetime.utcnow()
        ttl = int((now + timedelta(days=self.lock_ttl_days)).timestamp())
        try:
            self.table.put_item(
                Item={
                    'message_id': message_id,
                    'processed_at': now.isoformat(),
                    'ttl': ttl,
                },
                ConditionExpression='attribute_not_exists(message_id)',
            )
            return True  # Lock acquired — this message is new
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False  # Already processed — skip

    def release_lock(self, message_id: str):
        """Remove the lock (for retry scenarios). Generally not needed."""
        self.table.delete_item(Key={'message_id': message_id})


class SqsConsumer:
    """SQS consumer with visibility management, idempotency, and DLQ awareness."""

    def __init__(self, queue_url: str, region: str,
                 idempotency: Optional[IdempotencyGuard] = None,
                 visibility_timeout: int = 60):
        self.client = boto3.client('sqs', region_name=region)
        self.queue_url = queue_url
        self.idempotency = idempotency
        self.default_vt = visibility_timeout
        self.running = True

        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

    def _shutdown(self, signum, frame):
        print(f"\nShutting down (signal {signum})...", file=sys.stderr)
        self.running = False

    def process_message(self, body: str, message_id: str) -> bool:
        """
        Process a message. Returns True on success.
        Override with actual business logic.
        """
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            print(f"  ✗ Invalid JSON: {body[:100]}...", file=sys.stderr)
            return False  # Malformed message — don't retry

        # Idempotency check
        order_id = data.get('order_id', message_id)
        if self.idempotency:
            if not self.idempotency.try_lock(order_id):
                print(f"  → Skipping duplicate: {order_id}")
                return True  # Already processed, consider it a success

        # Simulated processing
        amount = data.get('amount', 0)
        if amount < 0:
            raise ValueError(f"Negative amount: {amount}")

        # Simulate work
        time.sleep(0.01)
        return True

    def run(self):
        """Main consumption loop."""
        print(f"Starting SQS consumer: {self.queue_url}")

        while self.running:
            try:
                response = self.client.receive_message(
                    QueueUrl=self.queue_url,
                    MaxNumberOfMessages=10,
                    VisibilityTimeout=self.default_vt,
                    WaitTimeSeconds=20,  # Long polling
                    MessageAttributeNames=['All'],
                )
            except Exception as e:
                print(f"Receive error: {e}", file=sys.stderr)
                time.sleep(5)
                continue

            messages = response.get('Messages', [])
            if not messages:
                continue

            for msg in messages:
                receipt = msg['ReceiptHandle']
                message_id = msg['MessageId']
                body = msg['Body']
                receive_count = int(msg.get('Attributes', {})
                                    .get('ApproximateReceiveCount', '1'))

                print(f"Processing: id={message_id}, attempt={receive_count}")

                # Check if this is a poison message (retried many times already)
                if receive_count >= 4:
                    print(f"  ⚠️ Message retried {receive_count} times — skipping (DLQ will catch)",
                          file=sys.stderr)
                    self.client.delete_message(
                        QueueUrl=self.queue_url, ReceiptHandle=receipt)
                    continue

                try:
                    success = self.process_message(body, message_id)
                    if success:
                        self.client.delete_message(
                            QueueUrl=self.queue_url, ReceiptHandle=receipt)
                        print(f"  ✓ Processed: {message_id}")
                    else:
                        # Malformed — don't retry, delete
                        self.client.delete_message(
                            QueueUrl=self.queue_url, ReceiptHandle=receipt)
                        print(f"  → Deleted malformed: {message_id}")

                except Exception as e:
                    print(f"  ✗ Error: {e}", file=sys.stderr)
                    # Don't delete — message will retry after visibility timeout
                    # DLQ will catch it after maxReceiveCount retries

        print("Consumer shut down.")


def main():
    parser = ArgumentParser(description="Production SQS consumer with idempotency")
    parser.add_argument("--queue-url", required=True, help="SQS queue URL")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--idempotency-table", help="DynamoDB table for idempotency locks")
    parser.add_argument("--visibility-timeout", type=int, default=60)
    args = parser.parse_args()

    idempotency = None
    if args.idempotency_table:
        idempotency = IdempotencyGuard(args.idempotency_table, args.region)

    consumer = SqsConsumer(
        queue_url=args.queue_url,
        region=args.region,
        idempotency=idempotency,
        visibility_timeout=args.visibility_timeout,
    )
    consumer.run()


if __name__ == "__main__":
    main()
```

---

## Java: SQS Consumer with Visibility Extension

```java
package com.example.sqs;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import software.amazon.awssdk.services.sqs.SqsClient;
import software.amazon.awssdk.services.sqs.model.*;

import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Production SQS consumer with:
 * - Automatic visibility timeout extension during long processing
 * - Message attribute-based routing
 * - Graceful shutdown
 * - DLQ awareness (skips messages near maxReceiveCount)
 */
public class SqsConsumerWithVtExtension {

    private static final Logger log = LoggerFactory.getLogger(SqsConsumerWithVtExtension.class);

    private final SqsClient sqs;
    private final String queueUrl;
    private final int defaultVisibilityTimeout;
    private final int maxReceiveCount;
    private final AtomicBoolean running = new AtomicBoolean(true);

    // Tracks active receipts for visibility extension
    private final Map<String, VisibilityExtension> activeReceipts = new ConcurrentHashMap<>();

    // Extends visibility every 30 seconds for active messages
    private final ScheduledExecutorService vtExtender = Executors.newSingleThreadScheduledExecutor();

    public SqsConsumerWithVtExtension(String queueUrl, int visibilityTimeout, int maxReceiveCount) {
        this.sqs = SqsClient.builder().build();
        this.queueUrl = queueUrl;
        this.defaultVisibilityTimeout = visibilityTimeout;
        this.maxReceiveCount = maxReceiveCount;

        // Periodically extend visibility for messages still processing
        vtExtender.scheduleAtFixedRate(this::extendActiveMessages, 30, 30, TimeUnit.SECONDS);
    }

    /**
     * Extends visibility timeout for all currently processing messages.
     * Called every 30 seconds to prevent messages from reappearing during long processing.
     */
    private void extendActiveMessages() {
        for (var entry : activeReceipts.entrySet()) {
            String receipt = entry.getKey();
            VisibilityExtension ext = entry.getValue();

            // Only extend if the message is still being processed
            if (ext.isActive()) {
                try {
                    sqs.changeMessageVisibility(ChangeMessageVisibilityRequest.builder()
                            .queueUrl(queueUrl)
                            .receiptHandle(receipt)
                            .visibilityTimeout(ext.getTimeout())
                            .build());
                    log.debug("Extended visibility for receipt: {}", receipt.substring(0, 20));
                } catch (SqsException e) {
                    log.warn("Failed to extend visibility (message may already be deleted): {}",
                            e.getMessage());
                    activeReceipts.remove(receipt);
                }
            } else {
                // Message processing finished (either success or failure)
                activeReceipts.remove(receipt);
            }
        }
    }

    public void start() {
        log.info("SQS Consumer started: queue={}, visibilityTimeout={}s",
                queueUrl, defaultVisibilityTimeout);

        while (running.get()) {
            try {
                ReceiveMessageResponse response = sqs.receiveMessage(ReceiveMessageRequest.builder()
                        .queueUrl(queueUrl)
                        .maxNumberOfMessages(10)
                        .visibilityTimeout(defaultVisibilityTimeout)
                        .waitTimeSeconds(20)  // Long polling
                        .messageAttributeNames("All")
                        .attributeNamesWithStrings("ApproximateReceiveCount")
                        .build());

                List<Message> messages = response.messages();
                if (messages.isEmpty()) {
                    continue;
                }

                for (Message msg : messages) {
                    processMessageAsync(msg);
                }

            } catch (SqsException e) {
                log.error("Error receiving messages: {}", e.getMessage());
                sleep(5_000);
            }
        }

        shutdown();
    }

    private void processMessageAsync(Message msg) {
        String receipt = msg.receiptHandle();
        int receiveCount = Integer.parseInt(
                msg.attributes().getOrDefault("ApproximateReceiveCount", "1"));

        // Near DLQ threshold — skip processing, let it go to DLQ
        if (receiveCount >= maxReceiveCount - 1) {
            log.warn("Message near DLQ threshold (attempt {}/{}): skipping — {}",
                    receiveCount, maxReceiveCount, msg.messageId());
            try {
                sqs.deleteMessage(DeleteMessageRequest.builder()
                        .queueUrl(queueUrl).receiptHandle(receipt).build());
            } catch (SqsException e) {
                log.error("Failed to delete near-DLQ message: {}", e.getMessage());
            }
            return;
        }

        // Register for visibility extension
        VisibilityExtension ext = new VisibilityExtension(defaultVisibilityTimeout);
        activeReceipts.put(receipt, ext);

        // Process in a separate thread (or use a thread pool for production)
        Executors.newSingleThreadExecutor().submit(() -> {
            try {
                // Route based on message attributes
                Map<String, MessageAttributeValue> attrs = msg.messageAttributes();
                String eventType = attrs.containsKey("EventType")
                        ? attrs.get("EventType").stringValue() : "unknown";

                boolean success = switch (eventType) {
                    case "order_created" -> processOrder(msg.body());
                    case "payment_completed" -> processPayment(msg.body());
                    case "inventory_updated" -> processInventory(msg.body());
                    default -> {
                        log.warn("Unknown event type: {} — message: {}", eventType, msg.messageId());
                        yield true; // Don't retry unknown types
                    }
                };

                if (success) {
                    sqs.deleteMessage(DeleteMessageRequest.builder()
                            .queueUrl(queueUrl).receiptHandle(receipt).build());
                    log.info("Processed: id={}, type={}", msg.messageId(), eventType);
                }

            } catch (Exception e) {
                log.error("Error processing message (will retry): {} — {}",
                        msg.messageId(), e.getMessage());
                // Don't delete — message will become visible again after VT expires
                // DLQ will catch it after maxReceiveCount retries
            } finally {
                ext.markInactive();
                activeReceipts.remove(receipt);
            }
        });
    }

    private boolean processOrder(String body) {
        // Replace with actual business logic
        if (body == null || body.isBlank()) return false;
        if (body.contains("\"amount\": -")) {
            throw new RuntimeException("Negative order amount");
        }
        try {
            Thread.sleep(100);  // Simulated processing
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        }
        return true;
    }

    private boolean processPayment(String body) {
        try {
            Thread.sleep(200);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        }
        return true;
    }

    private boolean processInventory(String body) {
        return true;
    }

    private void sleep(long millis) {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    public void stop() {
        running.set(false);
    }

    private void shutdown() {
        log.info("Shutting down SQS consumer...");
        vtExtender.shutdown();
        try {
            vtExtender.awaitTermination(10, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        sqs.close();
        log.info("Consumer shut down complete.");
    }

    /**
     * Tracks visibility extension state for a single message.
     */
    private static class VisibilityExtension {
        private final int timeout;
        private final AtomicBoolean active = new AtomicBoolean(true);

        VisibilityExtension(int timeout) {
            this.timeout = timeout;
        }

        int getTimeout() { return timeout; }

        boolean isActive() { return active.get(); }

        void markInactive() { active.set(false); }
    }

    public static void main(String[] args) {
        SqsConsumerWithVtExtension consumer = new SqsConsumerWithVtExtension(
                "https://sqs.us-east-1.amazonaws.com/123456789/orders",
                120,  // 120 second visibility timeout
                5     // maxReceiveCount for DLQ
        );

        Runtime.getRuntime().addShutdownHook(new Thread(consumer::stop));
        consumer.start();
    }
}
```
