---
title: "Runbook: draining an intake queue backlog"
summary: "Runbook for draining a backlog on the intake queue, covering when to scale the consumer group and when to park messages by hand."
tags: [runbook, queue, order-intake]
---

# Runbook: draining an intake queue backlog

## When to use this

Use this when consumer lag on the intake queue exceeds ten minutes and is still climbing.

## Steps

1. Check the payments provider status page before scaling anything.
2. Scale the consumer group to twice its steady-state size.
3. If lag keeps climbing, park the oldest thousand messages in the dead-letter queue by hand.

## After

File an incident note even when nothing was lost.
A backlog that drains itself is still a signal about the retry policy.
