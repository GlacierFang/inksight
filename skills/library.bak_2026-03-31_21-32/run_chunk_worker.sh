#!/bin/bash
cd /home/open/.openclaw/workspace/skills/library
python3 chunk_worker.py >> /tmp/chunk_worker.log 2>&1
