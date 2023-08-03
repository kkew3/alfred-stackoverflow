#!/bin/bash
if [ -d "$python3" ]; then python3="$python3/bin/python3"; fi
script="$1"
shift
"$python3" "$script" "$@"
