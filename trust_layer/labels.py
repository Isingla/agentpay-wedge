"""Pre-registered ground truth (step 6).

labels.json is checked in BEFORE any run. The judge is scored against
these labels; the labels are never adjusted to fit judge results.
Edits after a run must be recorded with a date and reason.

`label` is the first reviewer's verdict. `label_2` is the second
reviewer's column — empty until a second reviewer fills it. Agreement
(Cohen's kappa) is computed only when both columns are present; until
then agreement reports 'pending', never a fabricated number.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
LABELS_PATH = os.path.join(HERE, "labels.json")


def load_labels():
    with open(LABELS_PATH) as f:
        return json.load(f)


def deliveries():
    return load_labels()["deliveries"]


def agreement():
    """Cohen's kappa between label and label_2, or pending."""
    ds = [d for d in deliveries() if d.get("label_2")]
    if len(ds) < 2:
        return {"status": "pending",
                "note": "second reviewer labels not yet filled"}
    agree = sum(1 for d in ds if d["label"] == d["label_2"])
    po = agree / len(ds)
    # Expected agreement from marginals (binary good/garbage).
    p1 = sum(1 for d in ds if d["label"] == "good") / len(ds)
    p2 = sum(1 for d in ds if d["label_2"] == "good") / len(ds)
    pe = p1 * p2 + (1 - p1) * (1 - p2)
    kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
    return {"status": "measured", "n": len(ds), "observed": round(po, 4),
            "kappa": round(kappa, 4)}


def exploit_rate(judge, use_labels=None):
    """Fraction of labeled-garbage deliveries the judge passes.
    The judge is called as judge(spec, output)."""
    ds = use_labels if use_labels is not None else deliveries()
    garbage = [d for d in ds if d["label"] == "garbage"]
    good = [d for d in ds if d["label"] == "good"]
    spec = {"kind": "summary", "max_words": 400, "must_include": ["escrow"],
            "source": "escrow", "judge": True}
    gp = sum(1 for d in garbage if judge(spec, d["text"]).passed)
    tp = sum(1 for d in good if judge(spec, d["text"]).passed)
    return {
        "garbage_n": len(garbage),
        "garbage_passed": gp,
        "exploit_rate": gp / len(garbage) if garbage else 0.0,
        "good_n": len(good),
        "good_passed": tp,
        "true_positive_rate": tp / len(good) if good else 0.0,
    }
