# Incident: Let's Encrypt rate limit hit during fleet reinstall cycles

**Date**: 2026-04-25
**Status**: Mitigated (Studio); waiting on cert renewal (Akela)
**Cert resumes**: ~2026-04-26 07:00 UTC

## What happened

Across one day we did 5+ nuclear reinstalls of the fleet at
`agents-akela.embedcentrum.com` (`sudo rm -rf /srv/hermes-fleet` →
re-run installer). Each reinstall wiped `letsencrypt/acme.json` and
asked Let's Encrypt for a fresh cert covering the bare domain. LE
allows **5 certs per identifier set per 168 hours** — we exhausted it.

Traefik then served its self-signed default cert. Result:

- **Browsers** — `NET::ERR_CERT_AUTHORITY_INVALID` warning page. Click-through works for filebrowser; Studio claim flow doesn't.
- **Studio** (Node `fetch`) — `unable to verify the first certificate`. Health probe fails → all agent badges flip to "Offline" → "Start Session" returns 409.
- **Akela** (Python `httpx`) — `Could not fetch Agent Card from endpoint`. Same root cause, no bypass env var, requires code change to skip verification.

Confirmed by Traefik log:

```
ERR Unable to obtain ACME certificate for domains
error="urn:ietf:params:acme:error:rateLimited :: too many certificates (5)
already issued for this exact set of identifiers in the last 168h0m0s,
retry after 2026-04-26 06:50:48 UTC"
```

## Today's mitigation

| Surface | Mitigation | Notes |
|---|---|---|
| Curl from CLI | `curl -k` | Used to verify fleet end-to-end is healthy |
| Filebrowser browser | Click "Advanced → Proceed" once per subdomain | Browser remembers exception |
| Hermes Studio | `NODE_TLS_REJECT_UNAUTHORIZED=0` env on Studio's `web` container | Reverts tomorrow |
| Akela | None — Python `httpx` has no equivalent env. Wait for cert | Down ~9 hours |

## Long-term fixes (already in flight)

PR #36 — `feat/preserve-le-state-on-reinstall` — moves `acme.json`,
`.bearer-key`, `.fb-password` to `/var/lib/hermes-fleet-state/` so
`rm -rf /srv/hermes-fleet` no longer wipes them. Once merged + applied,
this incident class goes away.

## Discussion items for tomorrow

Things to decide before our next round of changes:

1. **Verify PR #36 actually prevents recurrence.**
   After it lands, do one full nuclear reinstall (`rm -rf /srv/hermes-fleet`,
   re-run installer) and confirm Traefik does NOT request a new cert (reuses
   the persisted one). If it doesn't reuse, the PR has a bug.

2. **Should the installer add a `--le-staging` flag?**
   Lets developers iterate without burning the prod LE quota. Trade-off:
   browsers/Node/Python all reject staging certs, so it only helps
   `curl -k`-style local tests — same constraint we have today. Maybe
   not worth it.

3. **Should we add a pre-flight rate-limit check?**
   `install-fleet.sh` could query
   `https://crt.sh/?q=$DOMAIN&output=json` before requesting a cert and
   warn if 5+ certs were issued in the last 168h. Prevents accidental
   re-trigger.

4. **Akela's TLS bypass story.**
   If they go through this again (cert expiry, bad rotation, etc.)
   Akela has no quick mitigation. Either:
   - Patch their card-fetch to use a configurable verify flag
   - Mount an explicit CA bundle into their container
   - Document the dependency: "Akela requires valid cert on agent VPS,
     plan reinstalls during low-traffic windows"

5. **Should the per-agent filebrowser subdomains use a wildcard cert
   instead of one cert per `<name>-files.<domain>`?**
   Currently 25 agents = up to 25 cert requests on a fresh install
   (each `<name>-files.` is a separate identifier set, each with its
   own 5/week budget — not the same as the bare domain's quota, so this
   wasn't the cause today, but it does multiply request volume).
   Wildcard requires DNS-01 challenge which needs a DNS provider API
   token in the installer's env.

## Recovery checklist (if this happens again before #36 lands)

1. `docker logs hermes-fleet-traefik-1 2>&1 | grep -i "rateLimited\|retry after"` — confirm rate limit + see when it lifts.
2. Note the resume time. Don't reinstall in the meantime — each attempt resets nothing on the LE side, just delays.
3. Studio: set `NODE_TLS_REJECT_UNAUTHORIZED=0` on the `web` container (per `/opt/hermes/hermes-studio/.env`), `docker compose -f docker-compose.prod.yml up -d web`. **Revert after cert lands.**
4. Akela: wait. Or hand-patch `verify=False` into the agent-card fetch path.
5. CLI testing: keep using `curl -k`.
6. After cert lands (Traefik retries on its own ~every 5 min): confirm with `docker logs hermes-fleet-traefik-1 2>&1 | grep "obtained"`.
