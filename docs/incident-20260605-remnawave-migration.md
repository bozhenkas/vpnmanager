# Incident 2026-06-05 — Remnawave migration / Happ routing

## Current status

Owner reported the service works now. Do not change production now.

Allowed work at this checkpoint: local repo documentation/memory only, and read-only production inspection if explicitly useful. Forbidden: restarts, DB writes, file writes on prod, nginx reloads, service changes, routing rewrites, Remnawave config pushes, cleanup scripts.

## What broke

Migration to Remnawave/new RU exposed fragile coupling between routing JSON, Remnawave squads/inbounds, node mappings, nginx ingress, and the Happ subscription format.

Earlier automated fixes caused or covered up issues:
- Direct SQL cleanup/rename of Remnawave technical tags broke node mappings for FIN/SWE/HOME.
- Shadow tests were too weak: `/` 200, WS `101`, nodes connected, and subscription JSON `200` did not prove the real Happ path.
- `SMART_REMNA` missed the foreign inbound mapping to `REMNA_VLESS_TCP_REALITY_7443`; RU WS accepted the connection, but FIN/SWE foreign nodes rejected user UUIDs.
- Telegram was changed to `DIRECT`, which was wrong. Owner later required Telegram through foreign.
- Repeated Remnawave restarts/config pushes during active debugging caused transient subscription/Happ failures; `502` was observed during active changes.
- Some reports were too confident before a client-equivalent test plus access logs proved the path.

## Do not do

- Do not direct-SQL rename Remnawave technical tags/inbounds such as `REMNA_VLESS_TCP_REALITY_7443` or `HOME_VLESS_TCP_REALITY_7443`.
- Do not run concurrent production mutations through multiple agents/subagents.
- Do not restart Remnawave or push configs while the owner is testing unless announced and approved.
- Do not change routing without a backup plus real Xray-from-current-subscription test.
- Do not rely only on `/` 200, WS `101`, nodes connected, or subscription JSON `200`.
- Do not rollback Happ from routing JSON to plain base64.
- Do not claim "fixed" without real client-equivalent tests and logs.

## Required shadow test after Remnawave/subscription/nginx changes

After any Remnawave, subscription, or nginx change:

1. Record UTC timestamps for the change and test window.
2. Avoid testing during Remnawave restarts/config pushes.
3. Fetch the exact subscription body for real test users from external nginx `/subscribe/...`.
4. Fetch the same subscription through local upstream `127.0.0.1:9090`.
5. Save the exact JSON body used for testing.
6. Build/run Xray from that exact JSON for `smart`, `fin`, `fra`, `swe`, and `direct`.
7. Verify real traffic, for example `https://www.gstatic.com/generate_204`, from each generated profile.
8. Correlate RU access logs with foreign node access logs.
9. Confirm the expected user UUID/email is accepted on the expected node/outbound.
10. Only then report the path as verified.

## Notes for future agents

The safe state after this incident is not "keep fixing"; it is "stop mutating prod unless explicitly asked." If more investigation is needed, prefer read-only evidence first and state exactly what was checked. Any new prod mutation needs a backup path, one owner-approved actor, a narrow change, and the real shadow-test checklist above.
