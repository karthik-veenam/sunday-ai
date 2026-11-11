# Operations and current limitations

- Keep **one Telegram poller** and **one microphone-input owner**. Do not start all historical entry points indiscriminately.
- Preserve `house.service`, `hogar-mqtt-bridge.service`, dashboard cron and unrelated infrastructure during Sunday work. This export does not stop them.
- `sunday.service` remains the live voice/transitional runtime; `sunday2.service` hosts the rebuilt brain. The audio gateway is separate.
- The body is deployed on2.7.5. Contextual display2.8.x was reverted. `sunday-glance.service` is disabled.
- Built2.8.4 fixes a Wi-Fi fallback that disables station mode and never rejoins home Wi-Fi. It is **not deployed**. A physical power restart can recover an old body stuck in its setup hotspot; USB is not a normal connectivity requirement.
- Pi-owned MQTT voice settings are independent of body availability. Body sensors must remain unavailable when the body is disconnected.
- The Hogar bridge has a known socket reconnection bug: repeated `Already connected` does not reset the client. REST polling can continue while push state is stuck. The repository records this limitation; it does not claim it is repaired.
- Broadlink/SmartIR state can be optimistic. Check network transport and the actual appliance before reporting physical success.
- The selected Auto Climate mode matters: cool-to-target stops the AC **and the Auto master**. Re-enabling while already at/below the target immediately completes again. Target comparison uses one-decimal room-temperature precision. Raising a target during cooling can intentionally cause immediate shutdown.
- Sunday-created automations are persistent HA programs. A future requested action belongs inside their sequence, not in a tool call executed now. Review trigger direction, interactions and one-shot versus recurring behavior.

## Recovery checks

```bash
systemctl status sunday sunday2 sunday-stackchan-gateway --no-pager
curl --fail http://127.0.0.1:8773/health
curl http://127.0.0.1:8090/health
journalctl -u sunday -u sunday2 -u sunday-stackchan-gateway -n 100 --no-pager
```

Health/log output can contain private operational data. Review locally; do not attach raw logs to public issues. Do not flash body firmware or replay physical actions as a diagnostic without explicit authorization.
