import sys, wave
import numpy as np

def load(path):
    with wave.open(path) as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2, "mono s16 expected"
        sr = w.getframerate()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64)
    return a, sr

def db(x): return 20 * np.log10(max(x, 1e-9) / 32768.0)

def report(path, label=None):
    a, sr = load(path)
    fl = int(sr * 0.02)
    n = len(a) // fl
    fr = a[: n * fl].reshape(n, fl)
    rms = np.sqrt((fr ** 2).mean(axis=1))
    spec = np.abs(np.fft.rfft(a * np.hanning(len(a)))) ** 2
    f = np.fft.rfftfreq(len(a), 1 / sr)
    tot = spec.sum() + 1e-9
    band = lambda lo, hi: 100 * spec[(f >= lo) & (f < hi)].sum() / tot
    print("%-14s %5.1fs %dHz | overall rms %6.1f dBFS | peak %6.1f dBFS | DC %+5.0f | noise floor(p10) %6.1f dBFS | loudest 20ms %6.1f dBFS | clipped %d"
          % (label or path.split('/')[-1], len(a) / sr, sr, db(np.sqrt((a ** 2).mean())), db(np.abs(a).max()), a.mean(),
             db(np.percentile(rms, 10)), db(rms.max()), int((np.abs(a) >= 32760).sum())))
    print("%14s energy by band: <100Hz %.0f%% | 100-300 %.0f%% | 300-3400 (speech) %.0f%% | 3.4-8k %.0f%%   | 50Hz-hum bin vs median: %.1fx"
          % ("", band(0, 100), band(100, 300), band(300, 3400), band(3400, 8000),
             spec[(f > 48) & (f < 52)].max() / (np.median(spec) + 1e-9)))
    return a, sr, rms

if __name__ == "__main__":
    for p in sys.argv[1:]: report(p)
