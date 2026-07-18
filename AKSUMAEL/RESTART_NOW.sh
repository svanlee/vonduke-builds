#!/bin/bash
# Run this on Victus to restart AKSUMAEL with improved nighttime detection
pkill -9 -f aksumael_wrapper 2>/dev/null
pkill -9 -f "python.*main.py" 2>/dev/null
sleep 3
pgrep -f "main.py" && echo "STILL RUNNING - try again" || echo "clean"
nohup bash ~/vonduke-builds/AKSUMAEL/tools/aksumael_wrapper.sh > /tmp/aksumael_wrap.log 2>&1 &
echo "Started PID $!"
echo "Watch: tail -f /tmp/aksumael_live.log"
