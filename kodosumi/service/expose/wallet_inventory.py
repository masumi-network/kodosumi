"""Evidence for an empty selling wallet list.

The payment node reports most causes of an empty list as a normal 200
answer with no rows: an API key that is limited to another network, a node
that holds no payment source for this network, or a payment source that has
no selling wallet yet. The panel used to collapse every one of them into
"No selling wallets found for network X. Check your Masumi Payment API
token and configuration", which sends the operator looking for a wallet
that was never the problem.

This module keeps what the token could see next to what was asked for, so
the message can name the cause instead of guessing at it.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class WalletReport:
    """What one wallet listing saw while it ran.

    source_count and networks describe every payment source the token could
    read, before the network of the expose narrowed them down. problems
    holds the requests that did not answer, and stays empty on a clean run.
    """

    source_count: int = 0
    networks: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)

    def describe_empty(self, network: str) -> str:
        """Say why a wallet listing came back empty for one network."""
        if self.problems:
            return (
                f"The Masumi node did not answer every request for network "
                f"'{network}': {self.problems[0]}"
            )
        if not self.source_count:
            return (
                f"The Masumi API token sees no payment source at all. Either "
                f"it is limited to a network other than '{network}', or "
                f"KODO_MASUMI points at a different node than the one that "
                f"holds your payment sources."
            )
        if network not in self.networks:
            seen = ", ".join(self.networks) or "none"
            return (
                f"The Masumi API token sees {self.source_count} payment "
                f"source(s), on {seen}, and none on '{network}'. Either the "
                f"network limit of the token excludes '{network}', or "
                f"KODO_MASUMI points at a different node."
            )
        return (
            f"No payment source on '{network}' has a selling wallet yet. "
            f"Add one in the Masumi payment service, then reload this page."
        )


def record_problem(report: Optional[WalletReport], message: str) -> None:
    """Note a request that did not answer, when a report is being kept."""
    if report is not None:
        report.problems.append(message)
