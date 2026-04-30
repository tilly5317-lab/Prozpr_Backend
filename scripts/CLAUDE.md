# scripts/ — DEV-ONLY

Dev-only helper scripts. `reset_and_seed_dummy_data.py` is destructive — drops the DB and re-seeds from `app/data/dummy_data.json`.

## Imported by active code?

NO — scripts are run manually, not imported.

## When to touch this

When you need to update the dev-seed fixture behavior, or add a new one-off dev helper. Don't add anything that should run in production.

## Don't read unless

- You're resetting local DB state or adding a dev helper.
