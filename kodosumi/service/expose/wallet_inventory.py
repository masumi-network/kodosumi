"""Evidence for an empty or partial selling wallet list.

The payment node reports most causes of an empty list as a normal 200
answer with no rows: an API key that is limited to another network, a node
that holds no payment source for this network, or a payment source that has
no selling wallet yet. The panel used to collapse every one of them into
"No selling wallets found for network X. Check your Masumi Payment API
token and configuration", which sends the operator looking for a wallet
that was never the problem.

This module keeps what the token could see next to what was asked for, so
the message can name the cause instead of guessing at it. A list that is
only partly loaded gets its own sentence: a wallet may be missing from it
because one request failed, not because it does not exist.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class WalletReport:
    """What one wallet listing saw while it ran.

    source_count and networks describe every payment source the token could
    read. matched_count is how many of them survived the network filter and
    were actually asked for wallets, which is what separates "no source on
    this network" from "a source with no wallet". problems holds the
    requests that did not answer, and stays empty on a clean run.

    The network names here come from the payment node, so compare them
    against MasumiConfig.registry_network, never against the name of a
    KODO_MASUMI entry.
    """

    source_count: int = 0
    matched_count: int = 0
    networks: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)

    def describe_empty(self, registry_network: str) -> str:
        """Say why a wallet listing came back empty for one network."""
        if self.problems:
            return (
                f"The Masumi node did not answer every request for network "
                f"'{registry_network}': {self.problems[0]}"
            )
        if not self.source_count:
            return (
                f"The Masumi API token sees no payment source at all. Either "
                f"it is limited to a network other than '{registry_network}', "
                f"or KODO_MASUMI points at a different node than the one that "
                f"holds your payment sources."
            )
        if not self.matched_count:
            seen = ", ".join(self.networks) or "none"
            return (
                f"The Masumi API token sees {self.source_count} payment "
                f"source(s), on {seen}, and none on '{registry_network}'. "
                f"Either the network limit of the token excludes "
                f"'{registry_network}', or KODO_MASUMI points at a different "
                f"node."
            )
        return (
            f"No payment source on '{registry_network}' has a selling wallet "
            f"yet. Add one in the Masumi payment service, then reload this "
            f"page."
        )

    def describe_partial(self) -> Optional[str]:
        """Warn that a non-empty list may still be missing wallets.

        A wallet that is absent because its payment source could not be
        read looks exactly like a wallet that does not exist, so a list
        with a failed request behind it cannot be treated as complete.
        """
        if not self.problems:
            return None
        return (
            f"This wallet list may be incomplete: {self.problems[0]}"
        )


def record_problem(report: Optional[WalletReport], message: str) -> None:
    """Note a request that did not answer, when a report is being kept."""
    if report is not None:
        report.problems.append(message)
