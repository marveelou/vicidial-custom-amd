#!/usr/bin/env python3
"""
amd_detector.py

Core answering-machine-detection engine for the ViciDial custom AMD project.

Design goals:
  * Same basic timing model as Asterisk's built-in AMD() app (initial silence,
    greeting length, after-greeting silence, word counting, total analysis
    time) so tuning intuition carries over from stock AMD.
  * PLUS an FFT-based end-of-greeting beep/tone detector. This is the actual
    accuracy win: stock AMD only ever looks at silence/word timing, so a
    voicemail greeting that "sounds" conversational in length gets called
    HUMAN. A real voicemail beep is a very distinctive, narrowband, and
    (critically) frequency-STABLE tone that human speech essentially never
    produces for more than a syllable, so detecting it directly is a much
    stronger signal than any timing heuristic.

No third-party dependencies beyond numpy (stdlib `wave` + `audioop` handle
the PCM decoding). Works against 8kHz mono PCM WAV files, which is what
Asterisk's RECORD FILE produces for a telephony channel.
"""

import audioop
import wave
import math

# NOTE: this deliberately avoids the stdlib `dataclasses` module (Python 3.7+
# only). vici-06 runs Python 3.6.15, which does not have it, and we don't
# want to add a pip/backport dependency just for this -- plain classes with
# __init__ do exactly the same job here.


class AMDParams:
    """Tunable detection parameters. Defaults are a stock-AMD-equivalent
    timing baseline plus the new beep/tone detector parameters."""

    def __init__(self,
                 initial_silence=2500,
                 greeting=1500,
                 after_greeting_silence=800,
                 total_analysis_time=5000,
                 min_word_length=100,
                 between_words_silence=50,
                 max_number_of_words=3,
                 max_word_length=5000,
                 silence_threshold=256,          # energy units, same scale as stock AMD
                 tone_min_duration_ms=150,       # shortest sustained tone we'll trust
                 tone_min_freq_hz=300.0,
                 tone_max_freq_hz=1700.0,
                 tone_peak_ratio=0.35,           # dominant-bin energy / total spectral energy
                 tone_freq_stability_hz=40.0,    # max drift between consecutive frames
                 tone_stable_frames=3,           # consecutive frames required
                 frame_ms=20):
        # --- stock-AMD-equivalent timing parameters (all in milliseconds) ---
        self.initial_silence = initial_silence
        self.greeting = greeting
        self.after_greeting_silence = after_greeting_silence
        self.total_analysis_time = total_analysis_time
        self.min_word_length = min_word_length
        self.between_words_silence = between_words_silence
        self.max_number_of_words = max_number_of_words
        self.max_word_length = max_word_length
        self.silence_threshold = silence_threshold

        # --- beep/tone detector parameters ---
        self.tone_min_duration_ms = tone_min_duration_ms
        self.tone_min_freq_hz = tone_min_freq_hz
        self.tone_max_freq_hz = tone_max_freq_hz
        self.tone_peak_ratio = tone_peak_ratio
        self.tone_freq_stability_hz = tone_freq_stability_hz
        self.tone_stable_frames = tone_stable_frames

        # --- frame size for analysis ---
        self.frame_ms = frame_ms


class AMDResult:
    def __init__(self, status, cause, run_time_ms, total_time_ms, detail=""):
        self.status = status              # HUMAN | MACHINE | NOTSURE
        self.cause = cause                 # e.g. "BEEPTONE-1850-1850", "HUMAN-1000-1000"
        self.run_time_ms = run_time_ms
        self.total_time_ms = total_time_ms
        self.detail = detail

    def amdcause_str(self):
        return f"{self.cause}-{self.run_time_ms}-{self.total_time_ms}"

    def amdstats_str(self):
        return f"{self.run_time_ms}-{self.total_time_ms}"


def _read_wav_mono_pcm16(path):
    """Return (sample_rate, bytes) for a mono 16-bit PCM wav file.
    Converts from other common formats (stereo, 8-bit, alaw/ulaw-in-wav)
    if necessary, since Asterisk's RECORD FILE target format can vary by
    channel driver/codec."""
    with wave.open(path, "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sampwidth != 2:
        raw = audioop.lin2lin(raw, sampwidth, 2)
    if n_channels == 2:
        raw = audioop.tomono(raw, 2, 0.5, 0.5)

    return framerate, raw


def _frame_iter(raw_pcm16, sample_rate, frame_ms):
    frame_bytes = int(sample_rate * (frame_ms / 1000.0)) * 2  # 16-bit = 2 bytes/sample
    if frame_bytes <= 0:
        return
    for offset in range(0, len(raw_pcm16) - frame_bytes + 1, frame_bytes):
        yield raw_pcm16[offset:offset + frame_bytes]


def _dominant_frequency(frame_bytes, sample_rate):
    """Return (freq_hz, peak_ratio) of the strongest FFT bin in this frame.
    peak_ratio = energy of the dominant bin / total spectral energy
    (excluding DC) -- a clean single-frequency tone drives this very high;
    normal speech (broadband, multiple formants) keeps it low-to-moderate.
    """
    import numpy as np

    samples = np.frombuffer(frame_bytes, dtype=np.int16).astype(np.float64)
    if samples.size < 8:
        return 0.0, 0.0

    # Hann window to reduce spectral leakage
    window = np.hanning(samples.size)
    spec = np.fft.rfft(samples * window)
    mag = np.abs(spec)
    mag[0] = 0.0  # drop DC

    total_energy = float(np.sum(mag))
    if total_energy <= 0.0:
        return 0.0, 0.0

    peak_idx = int(np.argmax(mag))
    peak_energy = float(mag[peak_idx])
    freq_hz = peak_idx * sample_rate / samples.size

    return freq_hz, (peak_energy / total_energy)


def _detect_beep_tone(raw_pcm16, sample_rate, params: AMDParams):
    """Scan the whole buffer for a sustained, frequency-stable tone in the
    voicemail-beep range. Returns (found: bool, start_ms: int)."""
    frame_ms = params.frame_ms
    frame_samples = int(sample_rate * (frame_ms / 1000.0))
    needed_stable_frames = max(
        params.tone_stable_frames,
        math.ceil(params.tone_min_duration_ms / frame_ms),
    )

    stable_run = 0
    run_start_ms = None
    last_freq = None

    for i, frame in enumerate(_frame_iter(raw_pcm16, sample_rate, frame_ms)):
        freq, peak_ratio = _dominant_frequency(frame, sample_rate)
        in_band = params.tone_min_freq_hz <= freq <= params.tone_max_freq_hz
        strong_peak = peak_ratio >= params.tone_peak_ratio

        if in_band and strong_peak:
            if last_freq is not None and abs(freq - last_freq) <= params.tone_freq_stability_hz:
                stable_run += 1
            else:
                stable_run = 1
                run_start_ms = i * frame_ms
            last_freq = freq

            if stable_run >= needed_stable_frames:
                return True, run_start_ms if run_start_ms is not None else i * frame_ms
        else:
            stable_run = 0
            last_freq = None

    return False, 0


def _energy_silence_segments(raw_pcm16, sample_rate, params: AMDParams):
    """Classic energy-based silence/speech segmentation, same idea as stock
    AMD. Returns a list of (is_speech: bool, duration_ms: int) segments in
    chronological order."""
    frame_ms = params.frame_ms
    segments = []
    current_is_speech = None
    current_len_ms = 0

    for frame in _frame_iter(raw_pcm16, sample_rate, frame_ms):
        try:
            rms = audioop.rms(frame, 2)
        except audioop.error:
            rms = 0
        is_speech = rms >= params.silence_threshold

        if current_is_speech is None:
            current_is_speech = is_speech
            current_len_ms = frame_ms
        elif is_speech == current_is_speech:
            current_len_ms += frame_ms
        else:
            segments.append((current_is_speech, current_len_ms))
            current_is_speech = is_speech
            current_len_ms = frame_ms

    if current_is_speech is not None:
        segments.append((current_is_speech, current_len_ms))

    return segments


# NOTE (2026-08-06): a `_merge_short_gaps()` helper that reclassified silence
# segments shorter than between_words_silence as speech (to stop natural
# micro-pauses from over-counting "words") was tried TWICE here -- once
# applied to the whole segment list before greeting detection (round 2,
# reverted at 011749a: regressed accuracy 63.7% -> 57.4%), and once scoped to
# only the post-greeting/word-counting portion (round 4, reverted here:
# regressed accuracy 62.2% -> 57.2%). Both failed the same way: merging away
# short gaps can't tell "one human's fragmented continuous speech" apart
# from "one real machine greeting's multiple short-paused phrases" -- both
# collapse into fewer, longer "words" under this merge, so machine calls that
# used to correctly trip MAXWORDS instead fall through to the HUMAN default.
# Confirmed on real production data both times (custom=HUMAN/stock=MACHINE
# jumped to >55% of all disagreements each time). Gap-duration merging is not
# a viable lever for this problem on its own -- do not re-attempt it without
# a way to distinguish the two cases first (e.g. per-phrase spectral/pitch
# continuity, not just silence duration).


def _timing_based_decision(segments, params: AMDParams, total_time_ms):
    """Stock-AMD-equivalent decision from silence/speech segment timing.
    Used as the fallback when no beep tone is detected."""
    if not segments:
        # Confirmed against a real production Asterisk log (vici-06,
        # 2026-08-06): stock AMD treats a channel that stays completely
        # silent for the full initial_silence window as ANSWERING MACHINE
        # ("ANSWERING MACHINE: silenceDuration:2000 initialSilence:2000"),
        # not as an uncertain/human-leaning case. An empty segment list
        # here means the whole clip was too short to even measure -- keep
        # that genuinely-ambiguous case as NOTSURE.
        return AMDResult("NOTSURE", "NOAUDIODATA", total_time_ms, total_time_ms)

    idx = 0
    # 1) initial silence
    initial_silence_ms = 0
    if not segments[0][0]:
        initial_silence_ms = segments[0][1]
        idx = 1

    # Fix (2026-08-06, round 3): fold away leading noise blips (speech
    # segments shorter than min_word_length) -- together with whatever
    # silence immediately follows each one -- into initial_silence, before
    # picking "the greeting". Confirmed via a real production
    # disagreement (1786041228.3032616.wav, real stock outcome MACHINE,
    # our result was HUMAN): a 20ms noise blip was being treated AS the
    # greeting, and the genuine 1060ms gap before the real message (which
    # followed right after) was then misread as "after-greeting silence"
    # -- since 1060ms >= after_greeting_silence(1000ms), the call was
    # declared HUMAN at elapsed=1100ms, before ever analyzing the actual
    # 6-word message that followed. The old comment here claimed treating
    # a short blip as a short greeting "biases toward HUMAN, which is
    # safe" -- this real case proves that assumption wrong: it can skip
    # the real content entirely, not just bias toward a safe default.
    # This is deliberately narrower than the reverted between_words_silence
    # merge (which merged gaps everywhere and collapsed genuine multi-word
    # machine greetings into too few words, causing a much larger
    # regression) -- this only affects leading blips before the greeting
    # is ever identified.
    while (idx < len(segments) and segments[idx][0]
           and segments[idx][1] < params.min_word_length):
        initial_silence_ms += segments[idx][1]
        idx += 1
        if idx < len(segments) and not segments[idx][0]:
            initial_silence_ms += segments[idx][1]
            idx += 1

    if idx >= len(segments):
        # Nothing but silence for the entire recording. Mirrors stock
        # AMD's own confirmed real-world behavior: total non-response for
        # at least the configured initial_silence window is a MACHINE
        # determination (e.g. dead air / no greeting ever picked up), not
        # an uncertain case. A clip that's silent but SHORTER than the
        # configured threshold is genuinely inconclusive (likely just a
        # truncated recording) and stays NOTSURE.
        if initial_silence_ms >= params.initial_silence:
            return AMDResult("MACHINE", "SILENCE", initial_silence_ms, total_time_ms)
        return AMDResult("NOTSURE", "NOAUDIODATA", total_time_ms, total_time_ms)

    # 2) greeting (first speech segment)
    if not segments[idx][0]:
        # silence where we expected speech -- bail out NOTSURE
        return AMDResult("NOTSURE", "NOGREETING", total_time_ms, total_time_ms)

    greeting_ms = segments[idx][1]
    idx += 1
    elapsed = initial_silence_ms + greeting_ms

    if greeting_ms > params.greeting:
        return AMDResult("MACHINE", "LONGGREETING", greeting_ms, total_time_ms)

    # NOTE: greeting_ms can no longer be < params.min_word_length here --
    # the leading-noise-blip fold above (fix dated 2026-08-06, round 3)
    # already skips past any speech segment that short before we get this
    # far, so whatever we land on is either a real candidate greeting or
    # idx >= len(segments) (handled above).

    # 3) after-greeting silence
    if idx < len(segments) and not segments[idx][0]:
        agsilence_ms = segments[idx][1]
        elapsed += agsilence_ms
        idx += 1
        if agsilence_ms >= params.after_greeting_silence:
            # greeting ended cleanly and it was short -> HUMAN
            return AMDResult("HUMAN", "HUMAN", elapsed, total_time_ms)

    # 4) count subsequent words (speech segments) up to max_number_of_words
    word_count = 1  # the greeting itself counts as word #1, consistent with stock AMD semantics
    while idx < len(segments) and elapsed < params.total_analysis_time:
        is_speech, dur_ms = segments[idx]
        elapsed += dur_ms
        idx += 1
        if is_speech:
            if dur_ms > params.max_word_length:
                return AMDResult("MACHINE", "MAXWORDLENGTH", elapsed, total_time_ms)
            # Fix (2026-08-06): min_word_length was defined in AMDParams but
            # never actually enforced here, so every speech segment --
            # including short noise blips (breath sounds, line clicks,
            # plosives split off by the energy segmenter) -- counted as a
            # full "word". That systematically over-counted word_count on
            # completely normal human speech and was the dominant cause of
            # false MACHINE/MAXWORDS results confirmed against real
            # shadow-mode data (stock=HUMAN in the large majority of the
            # 162 disagreements in the 2026-08-06 548-file batch). Segments
            # shorter than min_word_length are noise, not words -- skip
            # them without counting or resetting the word count.
            if dur_ms < params.min_word_length:
                continue
            word_count += 1
            if word_count > params.max_number_of_words:
                return AMDResult("MACHINE", "MAXWORDS", elapsed, total_time_ms)
        else:
            if dur_ms >= params.after_greeting_silence:
                # a clean pause after a short exchange -- call it human
                return AMDResult("HUMAN", "HUMAN", elapsed, total_time_ms)

    if elapsed >= params.total_analysis_time:
        return AMDResult("NOTSURE", "TOOLONG", elapsed, total_time_ms)

    return AMDResult("HUMAN", "HUMAN", elapsed, total_time_ms)


def analyze_wav(path, params: AMDParams = None) -> AMDResult:
    """Main entry point: analyze a recorded WAV file and return an AMDResult
    with .status in {HUMAN, MACHINE, NOTSURE} plus .cause/.run_time_ms/.total_time_ms
    formatted the same way stock Asterisk AMD() populates AMDCAUSE/AMDSTATS."""
    params = params or AMDParams()

    sample_rate, raw_pcm16 = _read_wav_mono_pcm16(path)
    total_time_ms = int(len(raw_pcm16) / 2 / sample_rate * 1000)
    # cap analysis to total_analysis_time worth of audio, same as stock AMD
    max_samples = int(sample_rate * (params.total_analysis_time / 1000.0))
    analysis_pcm16 = raw_pcm16[: max_samples * 2]

    tone_found, tone_start_ms = _detect_beep_tone(analysis_pcm16, sample_rate, params)
    if tone_found:
        return AMDResult(
            "MACHINE",
            "BEEPTONE",
            tone_start_ms,
            total_time_ms,
            detail="FFT-detected sustained narrowband tone (voicemail beep)",
        )

    segments = _energy_silence_segments(analysis_pcm16, sample_rate, params)
    result = _timing_based_decision(segments, params, total_time_ms)
    return result


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("usage: amd_detector.py <path-to-wav> [json-params-overrides]", file=sys.stderr)
        sys.exit(2)

    p = AMDParams()
    if len(sys.argv) > 2:
        overrides = json.loads(sys.argv[2])
        for k, v in overrides.items():
            setattr(p, k, v)

    res = analyze_wav(sys.argv[1], p)
    print(f"AMDSTATUS={res.status}")
    print(f"AMDCAUSE={res.amdcause_str()}")
    print(f"AMDSTATS={res.amdstats_str()}")
    if res.detail:
        print(f"DETAIL={res.detail}")
