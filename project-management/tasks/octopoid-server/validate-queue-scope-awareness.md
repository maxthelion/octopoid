# Fix validateQueue to be cluster/scope-aware

`validateQueue()` in `src/validate-queue.ts` looks up flows by name only:

```sql
SELECT states FROM flows WHERE name = ?
```

The `flows` table has a composite primary key `(name, cluster)`, but `validateQueue` ignores the cluster. If two orchestrators in different clusters register a flow with the same name (e.g. `default`) but different states, the query returns an arbitrary row.

## Fix

Add a `cluster` parameter to `validateQueue()` and filter by it:

```typescript
export async function validateQueue(
  db: D1Database,
  queue: string,
  flowName: string = 'default',
  cluster: string = 'default'
): Promise<string | null> {
  // ...
  flow = await queryOne<{ states: string }>(
    db,
    'SELECT states FROM flows WHERE name = ? AND cluster = ?',
    flowName, cluster
  )
  // ...
}
```

Update all call sites in `src/routes/tasks.ts` to pass the cluster. The cluster can come from:
- The task's `orchestrator_id` (format: `cluster-machine_id`)
- Or a `cluster` query parameter / header
- Or derived from the orchestrator registration

## Why this matters

Octopoid and boxen share the same server. Both register a `default` flow, but with different states (octopoid has `provisional`, boxen has `sanity_approved`, `human_review`). Without cluster filtering, one project's queue validation can reject the other's valid queue names.

## Acceptance criteria

- `validateQueue()` accepts a `cluster` parameter and filters the flows query by it
- All call sites in task create/update/claim pass the cluster
- When no cluster is specified, falls back to `'default'` (backwards compatible)
- If two flows exist with the same name but different clusters, each validates against its own states
