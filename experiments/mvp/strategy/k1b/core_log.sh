#!/bin/zsh
# D202/D203: the live core set, every 5 minutes, so a speed is quoted beside the cores it ran on.
for i in {1..300}; do print -r -- "$(date '+%H:%M:%S') $(sysctl -n kern.sched_recommended_cores)"; /bin/sleep 300; done
