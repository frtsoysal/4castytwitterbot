#!/usr/bin/env python3
"""
Wrapper script to load .env and run the bot.
"""
import os
from pathlib import Path

# Load .env file if it exists
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    print("📄 Loading .env file...")
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key.strip()] = value.strip()

# Import and run the main bot
from new_market_bot import main

if __name__ == "__main__":
    main()

