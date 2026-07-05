#!/bin/bash
# Launch Wispr Local. Grant Accessibility + Microphone to the terminal you run this from.
cd "$(dirname "$0")"
exec /opt/miniconda3/bin/python3 flow.py
