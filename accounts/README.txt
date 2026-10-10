One JSON file per account. Tokens are stored in PLAIN TEXT - keep this folder
private, do not sync it to git or any cloud drive, and never share it.
Plain text means anyone with read access to this folder can use the accounts.

Files here are written with 0600 permissions (owner read/write only) and
atomically (tmp file + rename), so a crash cannot leave a half-written file:
    *.json                     account credentials (tokens, plan, credits, campaign codes)
    settings.json              panel password hash, API keys, guardrails
    machine_identity.json      cached device identity (per physical machine - do NOT share across machines)
    scheduler/state.json       scheduler state and startup-claim marker

This folder is machine-specific: machine_identity.json caches the identity of
the official fingerprint component of THIS host. Do not copy the whole folder
to another machine. Do not edit these files by hand while the gateway is
running; use the web panel instead.
