"""A seller running as its own OS process, so a test can SIGKILL it mid-task.

usage: python -m agentpay.seller_worker DB_PATH SELLER_NAME STALL_SECONDS
Prints 'WORKING <task_id>' once it has picked up a task, then stalls before submitting.
"""
import sys
import time

from .agents import Seller
from .db import DB
from .layer import PaymentLayer


def main():
    path, name, stall = sys.argv[1], sys.argv[2], float(sys.argv[3])
    layer = PaymentLayer(DB(path))
    seller = Seller(layer, name)
    while True:
        tasks = layer.tasks_for(name)
        if tasks:
            t = tasks[0]
            print(f"WORKING {t.task_id}", flush=True)
            time.sleep(stall)                       # <- killed here
            layer.submit(t.task_id, name, seller.do(t.spec))
            print(f"SUBMITTED {t.task_id}", flush=True)
            return
        time.sleep(0.05)


if __name__ == "__main__":
    main()
