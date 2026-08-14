"""Protocol-level refresh, network-switch, load-balancing and crash probe."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx


def read_events(base_url: str, key: str, after_id: int, until: str) -> list[dict]:
    events: list[dict] = []
    headers = {"Last-Event-ID": str(after_id)} if after_id else {}
    with httpx.Client(timeout=15.0, trust_env=False) as client:
        with client.stream(
            "GET", f"{base_url}/probe/runs/{key}/events", headers=headers
        ) as response:
            response.raise_for_status()
            event_type = "message"
            event_id = 0
            data = ""
            for line in response.iter_lines():
                if line.startswith("id:"):
                    event_id = int(line[3:].strip())
                elif line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data += line[5:].strip()
                elif not line and data:
                    payload = json.loads(data)
                    events.append({"id": event_id, "event": event_type, "data": payload})
                    if event_type == until:
                        return events
                    event_type, event_id, data = "message", 0, ""
    raise AssertionError(f"stream closed before {until}: {events}")


def append(base_url: str, run_id: str, event: str, data: dict) -> int:
    with httpx.Client(timeout=10.0, trust_env=False) as client:
        response = client.post(
            f"{base_url}/probe/run/{run_id}/events",
            json={"event": event, "data": data},
        )
    response.raise_for_status()
    return int(response.json()["id"])


def refresh_scenario(args) -> None:
    key = "refresh-network-failover"
    with httpx.Client(timeout=10.0, trust_env=False) as client:
        created = client.post(f"{args.api1}/probe/runs/{key}")
    created.raise_for_status()
    run_id = created.json()["run_id"]
    append(args.api1, run_id, "token", {"text": "alpha"})
    first = read_events(args.lb, key, 0, "token")
    alpha = next(event for event in first if event["event"] == "token")

    # Closing the first client simulates refresh/network loss. Reconnect through
    # the load balancer with Last-Event-ID and assert exact, non-duplicated tail.
    beta_id = append(args.api1, run_id, "token", {"text": "beta"})
    resumed = read_events(args.lb, key, alpha["id"], "token")
    tokens = [event["data"]["text"] for event in resumed if event["event"] == "token"]
    assert tokens == ["beta"], tokens
    assert resumed[-1]["id"] == beta_id

    with httpx.Client(timeout=5.0, trust_env=False) as client:
        instances = {
            client.get(
                f"{args.lb}/probe/health?i={index}",
                headers={"Connection": "close"},
            ).json()["instance_id"]
            for index in range(8)
        }
    assert instances == {"api1", "api2"}, instances
    Path(args.state).write_text(
        json.dumps({"key": key, "run_id": run_id, "last_id": beta_id}),
        encoding="utf-8",
    )
    print(json.dumps({"refresh": "ok", "network_switch": "ok", "instances": sorted(instances)}))


def failover_scenario(args) -> None:
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    gamma_id = append(args.api2, state["run_id"], "token", {"text": "gamma"})
    append(args.api2, state["run_id"], "done", {"ok": True})
    resumed = read_events(args.lb, state["key"], state["last_id"], "done")
    tokens = [event["data"]["text"] for event in resumed if event["event"] == "token"]
    assert tokens == ["gamma"], tokens
    assert next(event["id"] for event in resumed if event["event"] == "token") == gamma_id
    with httpx.Client(timeout=5.0, trust_env=False) as client:
        health = client.get(f"{args.lb}/probe/health").json()
    assert health["instance_id"] == "api2", health
    print(json.dumps({"instance_crash": "ok", "survivor": "api2"}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=["refresh", "failover"])
    parser.add_argument("--lb", default="http://127.0.0.1:18080")
    parser.add_argument("--api1", default="http://127.0.0.1:18001")
    parser.add_argument("--api2", default="http://127.0.0.1:18002")
    parser.add_argument("--state", default="../tmp/sse-ha-state.json")
    args = parser.parse_args()
    (refresh_scenario if args.scenario == "refresh" else failover_scenario)(args)


if __name__ == "__main__":
    main()
