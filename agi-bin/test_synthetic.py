#!/usr/bin/env python3
"""
test_synthetic.py

Smoke test with no real call needed: builds two synthetic WAV files --
one shaped like a short human greeting-and-pause, one shaped like a
voicemail greeting ending in a beep tone -- and confirms amd_detector
classifies them HUMAN and MACHINE respectively.

Run:  python3 test_synthetic.py
"""

import math
import random
import struct
import sys
import wave
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from amd_detector import analyze_wav, AMDParams

SAMPLE_RATE = 8000


def _silence(ms):
    n = int(SAMPLE_RATE * ms / 1000)
    return [0] * n


def _noise(ms, amplitude=3000, seed=1):
    rnd = random.Random(seed)
    n = int(SAMPLE_RATE * ms / 1000)
    return [rnd.randint(-amplitude, amplitude) for _ in range(n)]


def _tone(ms, freq_hz=1000.0, amplitude=12000):
    n = int(SAMPLE_RATE * ms / 1000)
    return [
        int(amplitude * math.sin(2 * math.pi * freq_hz * (i / SAMPLE_RATE)))
        for i in range(n)
    ]


def _write_wav(path, samples):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def build_human_like(path):
    samples = []
    samples += _silence(300)          # initial silence
    samples += _noise(700, seed=1)    # short greeting: "Hello?"
    samples += _silence(1200)         # clean after-greeting silence -> HUMAN
    _write_wav(path, samples)


def build_machine_like(path):
    samples = []
    samples += _silence(300)          # initial silence
    samples += _noise(1800, seed=2)   # longer voicemail greeting
    samples += _tone(400, freq_hz=1400.0)  # the beep
    samples += _silence(300)
    _write_wav(path, samples)


def main():
    human_path = "/tmp/amd_test_human.wav"
    machine_path = "/tmp/amd_test_machine.wav"

    build_human_like(human_path)
    build_machine_like(machine_path)

    params = AMDParams()

    human_result = analyze_wav(human_path, params)
    machine_result = analyze_wav(machine_path, params)

    print(f"human-like  -> status={human_result.status:8s} cause={human_result.cause:12s} "
          f"detail={human_result.detail}")
    print(f"machine-like-> status={machine_result.status:8s} cause={machine_result.cause:12s} "
          f"detail={machine_result.detail}")

    ok = True
    if human_result.status != "HUMAN":
        print("FAIL: expected human-like sample to classify as HUMAN")
        ok = False
    if machine_result.status != "MACHINE":
        print("FAIL: expected machine-like sample to classify as MACHINE")
        ok = False

    if ok:
        print("PASS: synthetic smoke test succeeded")
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
