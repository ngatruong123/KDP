---
name: operate-remakeai-image-bot
description: Operate, diagnose, test, and update the RemakeAI Google Flow image-generation bot and farm. Use when generating KDP or POD images, processing local folders, managing browser profiles and accounts, monitoring generation events, resuming jobs, or modifying code under image-bot.
---

# Operate RemakeAI image bot

Read `AGENT.md` and `image-bot/AGENT.md`.

```bash
cd image-bot
python main.py --help
python start_farm.py --help
python -m pytest
```

Inspect state and logs before restarting a job. Do not commit secrets, profiles, temporary images or debug caches. Preserve structured event output used by monitoring.
