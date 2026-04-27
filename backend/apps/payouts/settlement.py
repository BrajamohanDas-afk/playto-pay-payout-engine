import random
from dataclasses import dataclass
from hashlib import sha256
from uuid import uuid4

from django.conf import settings


@dataclass(frozen=True)
class SettlementOutcome:
    status: str
    reference: str = ""
    error: str = ""


class SettlementSimulator:
    SUCCESS = "success"
    FAILURE = "failure"
    HANG = "hang"

    def settle(self, payout) -> SettlementOutcome:
        mode = getattr(settings, "PAYOUT_SIMULATOR_MODE", "random")
        if mode == "always_success":
            return self._success()
        if mode in {"always_failed", "always_failure"}:
            return self._failure()
        if mode == "always_hang":
            return self._hang()
        if mode == "by_bank_account":
            return self._settle_by_bank_account(payout.bank_account_id)
        if mode != "random":
            return self._failure(error=f"Unsupported payout simulator mode: {mode}.")

        return self._settle_randomly()

    def _settle_randomly(self) -> SettlementOutcome:
        roll = random.random()
        if roll < 0.70:
            return self._success()
        if roll < 0.90:
            return self._failure()
        return self._hang()

    def _settle_by_bank_account(self, bank_account_id: str) -> SettlementOutcome:
        normalized = bank_account_id.lower()
        if "success" in normalized:
            return self._success()
        if "fail" in normalized or "reject" in normalized:
            return self._failure()
        if "hang" in normalized or "timeout" in normalized:
            return self._hang()

        bucket = sha256(normalized.encode()).digest()[0] % 10
        if bucket < 7:
            return self._success()
        if bucket < 9:
            return self._failure()
        return self._hang()

    def _success(self) -> SettlementOutcome:
        return SettlementOutcome(
            status=self.SUCCESS,
            reference=f"bank_{uuid4().hex[:16]}",
        )

    def _failure(
        self,
        error: str = "Bank rejected payout in simulator.",
    ) -> SettlementOutcome:
        return SettlementOutcome(
            status=self.FAILURE,
            error=error,
        )

    def _hang(self) -> SettlementOutcome:
        return SettlementOutcome(
            status=self.HANG,
            error="Bank settlement did not return before timeout.",
        )


default_simulator = SettlementSimulator()
