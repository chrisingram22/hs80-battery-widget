"""Corsair HS80 Max battery reader over the Slipstream receiver (direct HID, no iCUE needed)."""
from .state import BatteryState, Status
from .device import Hs80Device, DongleNotFound, DeviceError
from .poller import Poller

__version__ = "0.1.0"
__all__ = ["BatteryState", "Status", "Hs80Device", "DongleNotFound", "DeviceError", "Poller"]
