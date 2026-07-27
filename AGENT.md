# RemakeAI Image Bot — Agent Guide

## Scope

Automate image generation through Google Flow for local folders and KDP/POD workloads. The active implementation is under `image-bot/`.

## Commands

```bash
cd image-bot
python main.py --help
python start_farm.py --help
python -m pytest
```

Use `image-bot/SETUP.md` for environment setup and `image-bot/AGENT.md` for detailed runtime behavior.

## Rules

- Never commit `image-bot/.env`, browser profiles, temporary inputs/outputs or credentials.
- Treat generated assets and state as runtime data unless explicitly requested as fixtures.
- Keep farm/account concurrency bounded by the code configuration.
- Run focused tests for changed modules before committing.
