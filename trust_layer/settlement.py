"""Settlement gates: the step-5 defense, and its pre-fix baseline.

The core escrow (PaymentLayer.settle) releases on the oracle's verdict.
The trust layer adds one gate on top, WITHOUT modifying the core:

- TrustedSettle: a job that requires buyer approval settles ONLY when
  the approval arrived through the buyer's own authenticated channel
  (ApprovalChannel.approve, called by the buyer role). Approval-like
  text inside the delivery payload is untrusted input and is ignored.
  With this gate, the escrow flip rate after the fix is ZERO BY
  CONSTRUCTION — a design property, not a finding. State it once.
- VulnerableSettle: the PRE-FIX baseline, kept only to measure the
  before-rate. It treats approval-like text inside the delivery as
  approval. Never used on the production path.

The runner decides whether to call layer.settle() at all: if the gate
says no, the job is parked (no money moves), and the transcript records
`parked_no_approval`.
"""
from .sellers import contains_approval_text


class ApprovalChannel:
    """The buyer's own authenticated channel. Only the buyer role calls
    approve(); the seller cannot write to it."""

    def __init__(self):
        self._approved = set()

    def approve(self, task_id):
        self._approved.add(task_id)

    def is_approved(self, task_id):
        return task_id in self._approved


class VulnerableSettle:
    """Pre-fix baseline: approval read from the delivery payload."""

    name = "vulnerable"

    def should_settle(self, task_id, delivery, channel):
        return contains_approval_text(delivery)


class TrustedSettle:
    """Post-fix gate: approval read ONLY from the buyer channel."""

    name = "trusted"

    def should_settle(self, task_id, delivery, channel):
        return channel.is_approved(task_id)
