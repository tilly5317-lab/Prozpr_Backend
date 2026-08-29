# Database backup, restore, and erasure

Prozpr's Postgres runs on **AWS RDS with automated snapshots**, configured in the
console — there is no backup code in this repo, and there should not be. This
document is the part that cannot live in the console: what the backups mean for
personal data, and what has to happen when one is restored.

A snapshot is a **complete copy of every identifier in the system**. None of the
application's protections apply to it: not the log scrubber, not response
masking, not the auth layer. Whatever the database holds in the clear, the
snapshot holds in the clear, for the whole retention window.

---

## 1. Verify before anything else: is the instance encrypted?

```bash
aws rds describe-db-instances \
  --db-instance-identifier <instance-id> \
  --query 'DBInstances[0].[StorageEncrypted,KmsKeyId,DBInstanceIdentifier]'
```

`StorageEncrypted` must be `true`.

**If it is `false`, this cannot be fixed in place.** RDS encryption is set at
creation, and snapshots inherit it — so every existing snapshot is unencrypted
and no setting will change that. The fix is a migration:

1. Take a manual snapshot.
2. `aws rds copy-db-snapshot` with `--kms-key-id` → an encrypted copy.
3. Restore that copy to a new instance.
4. Cut over (DNS/`DATABASE_URL` in `.env`, then `pm2 reload`).
5. Delete the old instance **and its unencrypted snapshots**.

This is the single longest-lead item in the DPDP work. Check it first.

## 2. Retention window

```bash
aws rds modify-db-instance \
  --db-instance-identifier <instance-id> \
  --backup-retention-period 14 --apply-immediately
```

Pick a number and write it down; "whatever the default was" is not a retention
policy. 14 days balances recovery against the fact that every extra day is
another day an erased user's data still exists somewhere.

Keep snapshots in **ap-south-1**. Cross-border transfer is permitted under the
DPDP Act unless a country is notified as restricted, but keeping the copies in
India removes the question and matches where the primary already sits.

### Superseded CAS statements

A CAMS re-upload no longer deletes what came before it: the previous statement's
`cas_uploads` row is marked `superseded` and everything derived from it stays in
place (`app/core/cas_scope.py`). That is a deliberate increase in how much
personal financial data the live database holds, so the position on it:

- **Superseded snapshots are kept indefinitely.** They exist precisely to be
  compared against — allocation drift, net-worth history, whether a user acted
  on a plan. A time-based expiry would delete the earlier half of every
  comparison, which is the problem this replaced.
- **The identity columns inside them still expire on schedule.** The 90-day
  `mf_aa_imports_identity` minimisation (`app/core/retention.py`) keys off
  `normalized_at`, so it nulls the name, address, email, mobile and PAN carried
  in a superseded statement's header exactly as it does for the live one. What
  a superseded snapshot keeps long-term is holdings and transactions, not
  contact identity.
- **Erasure takes all of them.** `cas_uploads` hangs off `users` with
  `ON DELETE CASCADE` and the purge walks the live FK graph
  (`app/domains/privacy/services/user_graph.py`), so every snapshot a user has
  ever uploaded — active, superseded or failed — is deleted with the account. No
  list needs maintaining for this to stay true.
- If growth ever forces a prune, drop the *derived rows* of snapshots older than
  the newest N and keep the `cas_uploads` header, which carries the headline
  figures. Nothing exists for this yet, and it should not be added before real
  upload cadence is known.

## 3. Access

Snapshot restore is a full data exfiltration path. Confirm who holds
`rds:RestoreDBInstanceFromDBSnapshot` and `rds:CopyDBSnapshot`, and keep that
list as short as the deploy credentials.

Note that the application's own EC2 instance role should **not** carry snapshot
permissions — the app never needs them, and the box is internet-facing.

## 4. Erasure vs. backups — the position

This is the part auditors ask about, so it is stated plainly:

- A deletion request is fulfilled **in the live database immediately**. Identity
  columns are destroyed at request time and the account stops authenticating on
  the next request (`app/core/dependencies.py`).
- The rows are purged after a **30-day grace window**
  (`app/domains/privacy/services/erasure_service.py`).
- **Backups are not edited.** Editing a snapshot is not possible, and rewriting
  history would destroy the backup's integrity. Instead, an erased user's data
  ages out of the snapshot set within the retention window above.
- **Snapshots are never used to restore individual records.** They exist for
  whole-instance disaster recovery only. Restoring one row for one user out of a
  snapshot would resurrect data that person asked us to delete.
- Any full restore **must re-apply erasures before the app is reopened** — see
  below.

## 5. Restore runbook

```bash
# 1. List available snapshots
aws rds describe-db-snapshots \
  --db-instance-identifier <instance-id> \
  --query 'sort_by(DBSnapshots,&SnapshotCreateTime)[-5:].[DBSnapshotIdentifier,SnapshotCreateTime,Status]'

# 2. Restore to a NEW instance — never over the live one
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier <instance-id>-restore-$(date +%Y%m%d) \
  --db-snapshot-identifier <snapshot-id>

# 3. Wait for it
aws rds wait db-instance-available \
  --db-instance-identifier <instance-id>-restore-$(date +%Y%m%d)
```

**4. Re-apply erasures — do this before any traffic reaches the restored data.**

A snapshot from before a deletion contains the deleted account. Restoring it
brings that person back, which turns a recovery into a fresh DPDP violation.
`deleted_user_tombstones` is deliberately excluded from the purge so it survives
to drive this:

```bash
cd ~/Prozpr_Backend
DATABASE_URL='postgresql+asyncpg://…restored-host…' venv/bin/python - <<'PY'
import asyncio
from app.core.database import _get_session_factory
from app.domains.privacy.services.erasure_service import reapply_tombstones

async def main():
    async with _get_session_factory()() as db:
        # Dry run first — it prints who would be re-purged.
        found = await reapply_tombstones(db, dry_run=True)
        print("would re-purge:", found)

asyncio.run(main())
PY
```

Re-run with `dry_run=False` and commit once the list looks right.

**5. Cut over** — point `DATABASE_URL` at the restored host and `pm2 reload
ecosystem.config.cjs --only prozpr_backend`.

**6. Delete the restored instance** once the incident is closed. A forgotten
restore instance is a second unmonitored copy of every user's data.

## 6. Test it

A backup nobody has restored is a hypothesis. Run steps 1–3 into a throwaway
instance at least once, confirm the app boots against it, run the tombstone
re-apply, and delete the instance. Note the date here when you do.

| Restore tested | By | Notes |
|---|---|---|
| _(not yet)_ | | |
