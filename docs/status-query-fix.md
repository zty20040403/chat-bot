# Status queries: 2026-09-06

## Evidence

Turn 1163 asked for tank's status. The model supplied `unit=systemd`, then
`unit=q` to `host_inspect`. These passed the advertised schema but failed the
executor's stricter service-name check, before reaching the control API. The
fallback fleet query succeeded, but its raw multi-host filesystem payload was
larger than the tool-result budget. Logging a Tool Call was incorrectly treated
as proof that the underlying host query had succeeded during the first diagnosis.

Direct MaxOps `GET /v1/operations` and `POST /v1/execute` with
`{"op":"host.facts","params":{"host":"tank"}}` still work. The six
existing control-service bindings passed runtime catalog compatibility checks.
This was not a breaking change in the MaxOps API.

Turns 1160 and 1161 asked whether Qwen was available but did not use the fleet
inspection/diagnostic tools. The model described an unverified failure.

## Changes

- `host_inspect(host_id)` reads system facts and fleet observations, and returns
  only the requested host. Independent query failures retain successful evidence.
- `service_inspect(host_id, unit)` handles specific services. Advertised service
  and host patterns now match execution validation.
- `fleet_overview` returns a compact projection covering every authorized host,
  root disk, observation times, failed service counts and bounded alert summaries.
  The full control/admin API remains available. Stale data is not called online;
  unavailable alert data is not reported as zero alerts.
- `model_status(profile)` uses registered model configuration, refreshes the
  local-model readiness probe and reports actual request history separately.
  `/models` success is not proof of generation success. Missing control credentials
  do not imply inference is unavailable. No key, URL or raw provider error is
  returned by this tool.
- Explicit Qwen availability questions request `model_status` before answering,
  through the existing tool-choice mechanism. This does not generate a fixed
  keyword response or issue a start/stop action.

## Verification

Focused tests cover the eight-host result budget, stale/missing evidence,
partially failed reads, valid tool parameters and Qwen readiness versus failed
generation. Live host verification must not send test messages to QQ groups.
