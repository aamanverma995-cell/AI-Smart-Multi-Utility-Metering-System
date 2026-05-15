"""
services/alert_service.py — Telegram alert service with cooldown
Sends messages when power, flow, or gas exceed configured thresholds.

Setup:
  1. Talk to @BotFather on Telegram → create a bot → copy the token
  2. Send /start to your bot, then:
     curl https://api.telegram.org/bot<TOKEN>/getUpdates
     Copy the chat_id from the JSON response
  3. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
"""

import logging
import time
from typing import Dict

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)


class AlertService:
    def __init__(self):
        self._bot = None
        # Track last alert time per alert type to enforce cooldown
        self._last_alert: Dict[str, float] = {}

    def init(self) -> bool:
        if not config.TELEGRAM_ENABLED:
            logger.info("Telegram alerts disabled in config")
            return True
        try:
            import telegram
            self._bot = telegram.Bot(token=config.TELEGRAM_BOT_TOKEN)
            logger.info("Telegram bot initialised")
            return True
        except Exception as exc:
            logger.error("Telegram init error: %s", exc)
            return False

    def _send(self, key: str, message: str) -> None:
        """Send a message if cooldown for 'key' has expired."""
        now = time.monotonic()
        last = self._last_alert.get(key, 0)
        if now - last < config.ALERT_COOLDOWN_SEC:
            return  # Still in cooldown
        self._last_alert[key] = now
        try:
            import asyncio
            asyncio.run(
                self._bot.send_message(
                    chat_id=config.TELEGRAM_CHAT_ID,
                    text=message,
                    parse_mode="HTML",
                )
            )
            logger.info("Telegram alert sent [%s]", key)
        except Exception as exc:
            logger.error("Telegram send error [%s]: %s", key, exc)

    def check_and_alert(self, elec, water, gas) -> None:
        """Evaluate all thresholds and send alerts as needed."""
        if not config.TELEGRAM_ENABLED or self._bot is None:
            return

        # Electricity overload
        if elec.valid and elec.power_w > config.ELECTRICITY_POWER_LIMIT_W:
            self._send(
                "elec_overload",
                f"⚡ <b>Power Overload Alert</b>\n"
                f"Current draw: <b>{elec.power_w:.0f} W</b> "
                f"(limit {config.ELECTRICITY_POWER_LIMIT_W:.0f} W)\n"
                f"Voltage: {elec.voltage_v:.1f} V | Current: {elec.current_a:.2f} A",
            )

        # Electricity alarm flag from PZEM
        if elec.valid and elec.alarm:
            self._send(
                "elec_alarm",
                "⚡ <b>PZEM-004T Alarm Active</b>\n"
                "Check your power setup — the energy meter raised an alarm.",
            )

        # Water flow overload
        if water.valid and water.flow_lpm > config.WATER_FLOW_LIMIT_LPM:
            self._send(
                "water_overload",
                f"💧 <b>High Water Flow Alert</b>\n"
                f"Flow rate: <b>{water.flow_lpm:.1f} L/min</b> "
                f"(limit {config.WATER_FLOW_LIMIT_LPM:.1f} L/min)",
            )

        # Gas alert
        if gas.valid and gas.alert:
            self._send(
                "gas_alert",
                f"🔥 <b>GAS DETECTED!</b>\n"
                f"MQ-4 sensor reading: {gas.raw_value} ({gas.pct_fsd:.1f}% FSD)\n"
                f"<b>Check for gas leaks immediately.</b>",
            )
