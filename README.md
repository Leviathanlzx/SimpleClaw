# SimpleClaw
A simplified, easy-to-understand version of OpenClaw aim for light use and learning.

This build is derived from and inspired by the following independent projects:

* **[OpenClaw](https://github.com/OpenClaw/OpenClaw)**: The open-source AI assistant and automation platform.
* **[Nanobot](https://github.com/HKUDS/nanobot)**: The modular execution engine that serves as the technical backbone for this simplified version.

## Setup with uv

1. Install [uv](https://github.com/astral-sh/uv).
2. Install dependencies:
   ```bash
   uv sync
   ```
3. Run the agent:
   ```bash
   uv run python main.py
   ```

## Setup with pip (Standard)

If you don't have `uv`, you can use standard pip:

1. (Optional) Create a virtual environment:
   ```bash
   python -m venv venv
   # Windows
   .\venv\Scripts\activate
   # Linux/Mac
   source venv/bin/activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the agent:
   ```bash
   python main.py
   ```

## Development

- Add dependencies: `uv add <package>`
- update dependencies: `uv lock --upgrade`
- Update `requirements.txt` : `uv export --format requirements-txt -o requirements.txt`
