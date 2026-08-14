from __future__ import annotations

import asyncio
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from .models import SlotRequirement
from .retrieval_cache import atomic_write_json
from .source_policy import SourcePolicyResolver
from .verification import VerificationDecision, resolve_verification_mode


NetworkProbe = Callable[[], Awaitable[bool]]
HOST_VISION_VALUES = {"available", "unavailable", "unknown"}


class CapabilityPreflight:
    """Audit search/network capabilities; vision is optional for presentation photos."""

    def __init__(
        self,
        project_path: Path | None,
        client: httpx.AsyncClient,
        adapters: list[Any],
        policy_resolver: SourcePolicyResolver,
        host_native_vision: str = "unknown",
        external_vision_available: bool = False,
        network_probe: NetworkProbe | None = None,
    ):
        self.project_path = project_path.resolve() if project_path else None
        self.client = client
        self.adapters = adapters
        self.policy_resolver = policy_resolver
        normalized = host_native_vision.casefold().strip()
        if normalized not in HOST_VISION_VALUES:
            raise ValueError("host_native_vision must be available, unavailable, or unknown")
        self.host_native_vision = normalized
        self.external_vision_available = external_vision_available
        self.network_probe = network_probe or self._probe_network

    async def run(self, slots: list[SlotRequirement], *, probe_network: bool = True) -> dict[str, Any]:
        decisions: dict[str, VerificationDecision] = {
            slot.slot_id: resolve_verification_mode(slot, self.policy_resolver.select(slot))
            for slot in slots
        }
        if probe_network:
            try:
                network_ok = await asyncio.wait_for(self.network_probe(), timeout=4.0)
            except (TimeoutError, httpx.HTTPError, OSError):
                network_ok = False
        else:
            network_ok = True

        search_available = any(
            adapter.available and adapter.capability_kind in {
                "general-web", "institutional-repository", "media-repository",
            }
            for adapter in self.adapters
        )
        host_required: list[str] = []
        blocked_slots: list[str] = []
        review_slots = sorted(decisions) if self.host_native_vision == "available" else []
        powerpoint_path = self._powerpoint_path()
        adapters = {
            adapter.name: {
                "status": "available" if adapter.available else "unavailable",
                "capability_kind": adapter.capability_kind,
                "reason": adapter.unavailable_reason,
            }
            for adapter in self.adapters
        }
        report = {
            "schema_version": 3,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "capabilities": {
                "structured_search": "available" if search_available else "unavailable",
                "search": "available" if search_available else "unavailable",
                "host_native_vision": self.host_native_vision,
                "visual_semantic_verification": (
                    "available" if self.host_native_vision == "available" or self.external_vision_available
                    else self.host_native_vision
                ),
                "external_vlm": "available-optional" if self.external_vision_available else "optional",
                "network": "available" if network_ok else "unavailable",
                "powerpoint": "available" if powerpoint_path else "unavailable",
            },
            "structured_search_backends": adapters,
            "powerpoint_path": powerpoint_path,
            "required_config": {
                "SERPER_API_KEY": "configured" if os.getenv("SERPER_API_KEY") else "optional-missing",
                "external_visual_provider": "optional-configured" if self.external_vision_available else "optional-missing",
            },
            "slots": {
                slot_id: {
                    **decision.to_dict(),
                    "status": (
                        "presentation-review-capable"
                        if self.host_native_vision == "available"
                        else "presentation-source-context-capable"
                    ),
                }
                for slot_id, decision in decisions.items()
            },
            "host_visual_required_slots": host_required,
            "host_visual_review_slots": review_slots,
            "vlm_required_slots": host_required,
            "blocked_slots": blocked_slots,
            "stage7_ready": search_available and network_ok,
            "workflow_ready": search_available and network_ok and bool(powerpoint_path),
        }
        if self.project_path:
            filename = f"capability-preflight-{slots[0].slot_id}.json" if len(slots) == 1 else "capability-preflight.json"
            output = self.project_path / "validation" / filename
            atomic_write_json(output, report)
            report["report_path"] = str(output)
        return report

    async def _probe_network(self) -> bool:
        response = await self.client.get("https://www.bing.com/robots.txt", headers={"Accept": "text/plain"})
        return response.status_code < 500

    @staticmethod
    def _powerpoint_path() -> str | None:
        configured = os.getenv("POWERPOINT_EXE")
        candidates = [
            configured,
            shutil.which("POWERPNT.EXE"), shutil.which("powerpnt"),
            r"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE",
            r"C:\Program Files (x86)\Microsoft Office\root\Office16\POWERPNT.EXE",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return str(Path(candidate).resolve())
        return None
