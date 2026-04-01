#!/bin/bash
# Dashboard 数据采集 — 每小时自动调用，积累 SQLite 历史趋势
curl -s http://localhost:18794/api/cron-reliability > /dev/null 2>&1
curl -s http://localhost:18794/api/agent-activity > /dev/null 2>&1
curl -s "http://localhost:18794/api/usage-breakdown?days=1" > /dev/null 2>&1
