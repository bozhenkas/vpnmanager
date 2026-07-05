"""xhttp stream settings для Happ/Xray JSON-подписок."""

from __future__ import annotations

from typing import Any

# native Remnawave Neo — минимальный extra (Happ не переваривает тяжёлый Hydra-блок)
XHTTP_REALITY_EXTRA_MINIMAL: dict[str, Any] = {
    "mode": "stream-one",
}


def build_xhttp_settings(
    *,
    path: str,
    host: str = "",
    mode: str = "stream-one",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "path": path,
        "mode": mode,
    }
    if host:
        settings["host"] = host
    if mode == "stream-one":
        settings["extra"] = dict(extra or XHTTP_REALITY_EXTRA_MINIMAL)
        settings["extra"]["mode"] = mode
    elif extra:
        settings["extra"] = extra
    return settings
