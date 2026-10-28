"""Type stubs for the compiled classes (src/python.rs). The `ninjask` package
re-exports them; see `ninjask._extension` for where they come from."""

from typing import final

@final
class Dex:
    """A loaded format: species, moves, items, abilities and support levels."""

    @staticmethod
    def load(path: str) -> Dex:
        """Load an export written by refsim/export_format.js."""
    @property
    def format_id(self) -> str: ...
    def team_problems(self, packed: str) -> list[str]:
        """Reasons a packed team is not fully simulated (empty when it is)."""
    def move_support(self, move_id: str) -> str:
        """Support level of a move by id: "generic", "implemented" or "unsupported (...)"."""
    def species_ids(self) -> list[str]: ...
    def move_ids(self) -> list[str]: ...
    def generic_moves(self) -> list[str]: ...
    def implemented_moves(self) -> list[str]: ...
    def supported_abilities(self) -> list[str]: ...
    def supported_items(self) -> list[str]: ...

class Battle:
    """A battle between two packed teams. Sides are "p1" and "p2"; slots are
    named like "p1a"."""

    def __new__(cls, dex: Dex, team1: str, team2: str, seed: int, names: tuple[str, str] | None = None) -> Battle: ...
    @staticmethod
    def reference(dex: Dex, team1: str, team2: str, seed: str, names: tuple[str, str] | None = None) -> Battle:
        """Reference mode: Showdown's RNG, seeded like Showdown's ("sodium,<hex>")."""
    @staticmethod
    def from_projection(dex: Dex, projection: str, seed: int, names: tuple[str, str] | None = None) -> Battle:
        """Continue from a Showdown state projection (JSON text) with a new seed."""
    def set_rng_trace(self, on: bool) -> None: ...
    def rng_trace(self) -> list[str]:
        """The RNG draws recorded since the last call."""
    @property
    def rng_seed(self) -> str | None:
        """The RNG state as Showdown prints it (reference mode), else None."""
    def request(self, side: str) -> str | None:
        """The side's current request as Showdown JSON text, or None."""
    def needs_choice(self, side: str) -> bool: ...
    def choose(self, side: str, choice: str) -> None:
        """Submit a choice; raises ValueError with Showdown's message if invalid."""
    def log(self, viewer: str, start: int) -> list[str]:
        """The protocol log from line `start`, as `viewer` sees it ("p1", "p2" or the spectator)."""
    def log_len(self) -> int: ...
    def set_logging(self, on: bool) -> None: ...
    def show_team_sheets(self) -> None:
        """Open team sheets: log both teams' sets, as Showdown does at team preview."""
    def projection(self) -> str:
        """The full state as JSON, in the shape of the reference harness's projection."""
    def copy(self) -> Battle: ...
    def reseed(self, seed: int) -> None: ...
    def damage(self, attacker: str, move_id: str, target: str) -> str:
        """The damage calculation for a move from slot `attacker` against slot `target`, as JSON."""
    def order(self, a: str, plan_a: str, b: str, plan_b: str) -> tuple[float, float]:
        """`(first, tie)`: the chance that slot `a` acts before slot `b`, and that a speed tie decides it."""
    def speeds(self) -> str:
        """Each active Pokémon's effective Speed, `{"p1a": speed, ...}`, as JSON."""
    @property
    def ended(self) -> bool: ...
    @property
    def winner(self) -> str | None:
        """The winning player's name, or None."""
    @property
    def winner_side(self) -> str | None:
        """The winning side ("p1" or "p2"), or None for a tie or a battle still running."""
    @property
    def turn(self) -> int: ...

class Tracker:
    """What one player knows, built from its protocol lines and requests."""

    def __new__(cls, dex: Dex, side: str) -> Tracker: ...
    def feed(self, lines: list[str]) -> None:
        """Feed protocol lines from this player's stream."""
    def copy(self) -> Tracker: ...
    def feed_request(self, request: str) -> None:
        """Feed this player's request (JSON)."""
    def feed_battle(self, battle: Battle) -> None:
        """Feed this player's own side straight from an engine battle (self-play)."""
    def view(self) -> str:
        """The view as JSON."""
    def world(self, assumptions: str | None = None, seed: int = 0) -> Battle:
        """The battle this view describes, with `assumptions` (JSON) for the foe's hidden sets."""
