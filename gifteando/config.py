import json
import uuid
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "gifteando" / "config.json"


@dataclass
class ApiKeys:
    giphy: str
    klipy: str
    customer_id: str


def _empty_keys() -> ApiKeys:
    return ApiKeys(giphy="", klipy="", customer_id=str(uuid.uuid4()))


def load_keys() -> ApiKeys:
    if not CONFIG_PATH.is_file():
        return _empty_keys()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_keys()
    if not isinstance(raw, dict):
        return _empty_keys()
    customer_id = str(raw.get("customer_id") or "").strip() or str(uuid.uuid4())
    return ApiKeys(
        giphy=str(raw.get("giphy_api_key") or "").strip(),
        klipy=str(raw.get("klipy_api_key") or "").strip(),
        customer_id=customer_id,
    )


def save_keys(giphy: str, klipy: str, customer_id: str | None = None) -> ApiKeys:
    current = load_keys()
    keys = ApiKeys(
        giphy=giphy.strip(),
        klipy=klipy.strip(),
        customer_id=(customer_id or current.customer_id or str(uuid.uuid4())).strip(),
    )
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "giphy_api_key": keys.giphy,
        "klipy_api_key": keys.klipy,
        "customer_id": keys.customer_id,
    }
    CONFIG_PATH.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    CONFIG_PATH.chmod(0o600)
    return keys
