"""
One-time Google Home authentication setup.

If you have 2FA enabled, generate an App Password at:
  myaccount.google.com/apppasswords
and use that instead of your regular password.
"""

import json
import os
import sys


def main() -> None:
    print("=== Google Home Setup for Sunday ===\n")

    email = input("Google email: ").strip()
    password = input("App password (or Google password): ").strip()

    print("\nAuthenticating with Google...")
    try:
        from glocaltokens.client import GLocalAuthenticationTokens
        client = GLocalAuthenticationTokens(username=email, password=password)
        master_token = client.get_master_token()
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    if not master_token:
        print("ERROR: Could not get master token. Check your credentials.")
        sys.exit(1)

    print(f"Got master token: {master_token[:20]}...")

    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    config = {}
    if os.path.exists(config_path):
        with open(config_path) as f:
            config = json.load(f)

    config["google_master_token"] = master_token
    config["google_username"] = email

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    print(f"Saved to {config_path}\n")

    print("Discovering devices on your network...")
    try:
        devices = client.get_google_devices_json()
        print(f"Found {len(devices)} device(s):")
        for d in devices:
            name = d.get("device_name", "Unknown")
            ip = d.get("google_device", {}).get("local_device_info", {}).get("ip_address", "no local IP")
            print(f"  - {name}  ({ip})")
    except Exception as e:
        print(f"Could not list devices: {e}")

    print("\nSetup complete! Now run: python main.py")


if __name__ == "__main__":
    main()
