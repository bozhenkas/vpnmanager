#!/usr/bin/env python3
"""заглушка dns failover watchdog.

реальную логику дописать отдельно:
- health-check ru/fin/se;
- выбор активного ip;
- обновление dns a-record;
- hysteresis, dry-run, алерты.
"""

import os


def main() -> int:
    record = os.environ.get("DNS_RECORD_NAME", "ru.goida.fun")
    candidates = os.environ.get("WATCHDOG_CANDIDATES", "ru,fin,se")
    print(f"ip-watchdog stub: record={record}, candidates={candidates}")
    print("todo: implement dns failover")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
