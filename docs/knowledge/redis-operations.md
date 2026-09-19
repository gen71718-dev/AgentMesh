# Redis operations runbook

## Queue

The work queue is the stream {namespace}:runs:queue. Workers read it with
XREADGROUP against the consumer group {namespace}:workers. A message becomes
pending when a worker claims it and is removed from pending by XACK.

If a worker process dies mid-run, its messages stay in the pending entries list.
At startup each worker runs XAUTOCLAIM with a minimum idle time of
AGENTMESH_QUEUE_RECLAIM_AFTER_SECONDS, which transfers ownership of stale
entries to the new worker so the run is retried instead of being lost.

To inspect the backlog:

    redis-cli XLEN agentmesh:runs:queue
    redis-cli XPENDING agentmesh:runs:queue agentmesh:workers
    redis-cli XAUTOCLAIM agentmesh:runs:queue agentmesh:workers worker-manual 60000 0-0

## Event log

Each run has its own stream at {namespace}:run:{id}:events, capped with MAXLEN.
Clients read it with XREAD using the last event id they saw, which is how SSE
reconnection with Last-Event-ID replays missed events rather than dropping them.

Follow a run from the command line:

    redis-cli XRANGE agentmesh:run:run_abc123:events - +

## Run documents and TTL

Run documents are stored at {namespace}:run:{id} as a JSON string with an expiry
of AGENTMESH_REDIS_TTL_SECONDS, default 86400. The listing index is the sorted set
{namespace}:runs:index, scored by creation time and trimmed to
AGENTMESH_RUN_HISTORY_LIMIT entries.

## Reclaiming memory

Every key created for a run carries a TTL, so a cluster that is left running does
not grow without bound. To purge everything for the default namespace:

    redis-cli --scan --pattern 'agentmesh:*' | xargs -r redis-cli DEL