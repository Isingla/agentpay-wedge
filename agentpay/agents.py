"""Two deliberately dumb agents. The point is what the payment layer does around them."""
import random
import string

DOCS = [
    ("ledger", "A ledger records every transfer as a row. Balances are derived from it. "
               "Auditors replay the rows to check that nothing was invented or lost."),
    ("escrow", "An escrow holds funds until both sides have done their part. The buyer "
               "locks money first, and it moves only when the work is checked."),
    ("oracle", "An oracle is the referee that checks output against the spec. It decides "
               "pass or fail, which is what decides who gets paid."),
    ("caps", "Spend caps bound how much an agent may commit per hour. They live in the "
             "payment layer, so a buggy agent cannot talk its way past them."),
]


class Buyer:
    def __init__(self, layer, name, seller, price=100, ttl=60.0):
        self.layer, self.name, self.seller = layer, name, seller
        self.price, self.ttl, self.log, self.n = price, ttl, [], 0

    def spec_for(self, title, text):
        return {"kind": "summary", "max_words": 25, "must_include": [title], "source": text}

    def post(self, title, text):
        self.n += 1
        tid = f"{self.name}>{self.seller}:{self.n}:{title}"
        r = self.layer.post_task(tid, self.name, self.price, self.spec_for(title, text), self.ttl)
        if not r.ok:
            self.log.append((tid, "rejected", r.reason))
            return None
        a = self.layer.assign(tid, self.seller)
        self.log.append((tid, "assigned" if a.ok else "unassigned", a.reason))
        return tid

    def run(self, docs=DOCS):
        return [t for t in (self.post(title, text) for title, text in docs) if t]

    def settle_all(self, task_ids):
        return {t: self.layer.settle(t) for t in task_ids}


class Seller:
    """mode: 'honest' summarizes; 'garbage' returns noise; 'crash' takes work and vanishes."""

    def __init__(self, layer, name, mode="honest", seed=0):
        self.layer, self.name, self.mode = layer, name, mode
        self.rng = random.Random(seed)

    def do(self, spec):
        if self.mode == "garbage":
            return "".join(self.rng.choice(string.printable) for _ in range(40))
        src = spec["source"]
        first = src.split(". ")[0].rstrip(".") + "."
        return " ".join(first.split()[: spec.get("max_words", 25)])

    def work_once(self):
        done = []
        for t in self.layer.tasks_for(self.name):
            if self.mode == "crash":
                raise SystemExit("seller crashed mid-task")
            if self.layer.submit(t.task_id, self.name, self.do(t.spec)).ok:
                done.append(t.task_id)
        return done
