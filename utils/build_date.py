from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess


# Paths relative to this script
BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_FILE = BASE_DIR / 'app' / 'static' / 'build_date.json'


def update_build_date():
    """Write the current UTC build timestamp and stage the JSON file with Git."""
    # Get current UTC time in ISO format
    now = datetime.now(timezone.utc)
    data = {
        'updatedAt': now.isoformat(),
    }

    # Ensure static directory exists
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

    subprocess.run(['git', 'add', str(OUTPUT_FILE)], check=True)
    print(f'Updated and staged {OUTPUT_FILE.name}')


if __name__ == '__main__':
    # print(f"Updating {OUTPUT_FILE}")
    update_build_date()
