"""Nanobot-Lite: Ultra-lightweight AI agent for Telegram on Termux."""

__version__ = "0.1.0"
__logo__ = r"""
 _   _                      _
| \ | |                    | |
|  \| | _____  __ ____   __| | ___
| . ` |/ _ \ \/ / \ \ / / _` |/ _ \
| |\  |  __/>  <   \ V / (_| | (_) |
\_| \_/\___/_/\_\   \_/ \__,_|\___/
"""

from pathlib import Path

# Config directory
CONFIG_DIR = Path.home() / ".nanobot_lite"
CONFIG_DIR.mkdir(exist_ok=True, parents=True)
