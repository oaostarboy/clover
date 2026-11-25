#!/bin/bash
# usage: run.sh <name> <seconds> [keys...]
W=/home/starboy/Projects/clover-onboard-tui/.worktrees/subagent-sa-0-13a91dbd
rm -rf $W/.demo-user; mkdir -p $W/.demo-user
export HOME=$W/.demo-user CLOVER_HOME=$W/.demo-user/.clover PYTHONPATH=/home/starboy/Projects/clover-onboard-tui/.worktrees/subagent-sa-0-13a91dbd COLORTERM=truecolor
/home/starboy/Projects/clover-onboard-shots/.venv/bin/python -m clover_cli.main setup; sleep 60
