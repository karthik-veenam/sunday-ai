"""
Tuya Cloud client for geyser (Wipro 16A smart plug).
Uses tinytuya Cloud API — no local key needed.
"""
import asyncio
from functools import partial


class GeyserClient:
    def __init__(self, api_key: str, api_secret: str, device_id: str, region: str = "in"):
        self._api_key = api_key
        self._api_secret = api_secret
        self._device_id = device_id
        self._region = region
        self._cloud = None

    def _get_cloud(self):
        if self._cloud is None:
            import tinytuya
            self._cloud = tinytuya.Cloud(
                apiRegion=self._region,
                apiKey=self._api_key,
                apiSecret=self._api_secret,
                apiDeviceID=self._device_id,
            )
        return self._cloud

    async def get_state(self) -> dict:
        """Returns {'on': bool, 'power_w': float, 'current_ma': float, 'voltage_v': float}"""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, partial(self._get_cloud().getstatus, self._device_id))
        state = {}
        for item in result.get("result", []):
            code = item.get("code")
            value = item.get("value")
            if code == "switch_1":
                state["on"] = bool(value)
            elif code == "cur_power":
                state["power_w"] = round(value / 10, 1)
            elif code == "cur_current":
                state["current_ma"] = value
            elif code == "cur_voltage":
                state["voltage_v"] = round(value / 10, 1)
        return state

    async def set_state(self, on: bool) -> bool:
        """Turn geyser on or off. Returns True on success."""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            partial(self._get_cloud().sendcommand, self._device_id, [{"code": "switch_1", "value": on}])
        )
        return result.get("success", False)
