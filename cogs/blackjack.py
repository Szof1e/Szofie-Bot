"""Blackjack — an interactive donut-betting card game.

Card logic (deck, hand values, dealer play, settlement) is pure and lives at
module level so it's unit-testable off-network. The Discord layer is a button
View that drives one hand to completion.
"""

from __future__ import annotations
import asyncio
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import discord
from discord import app_commands
from discord.ext import commands
from szofie import badges, gamelocks, ui
from szofie.betting import BetTransformer
from szofie.economy import record_casino_result
from szofie.plushies import plushie_perk

RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
SUITS = ["♠️", "♥️", "♦️", "♣️"]
HIDDEN = "🂠"


@dataclass
class Card:
    rank: str
    suit: str

    @property
    def value(self) -> int:
        if self.rank in ("J", "Q", "K"):
            return 10
        if self.rank == "A":
            return 11
        return int(self.rank)

    def __str__(self) -> str:
        return f"{self.rank}{self.suit}"


def fresh_deck(rng: random.Random) -> List[Card]:
    deck = [Card(r, s) for s in SUITS for r in RANKS]
    rng.shuffle(deck)
    return deck


def build_shoe(rng: random.Random, decks: int) -> List[Card]:
    """A shuffled shoe of `decks` full decks. Dealt down over many hands (not
    reshuffled each hand) so the card composition shifts — which is what makes
    card counting actually work."""
    shoe = [Card(r, s) for _ in range(max(1, decks)) for s in SUITS for r in RANKS]
    rng.shuffle(shoe)
    return shoe


def hand_value(cards: List[Card]) -> Tuple[int, bool]:
    """Return (best total, is_soft). Aces count 11 until that would bust."""
    total = sum((c.value for c in cards))
    aces = sum((1 for c in cards if c.rank == "A"))
    while total > 21 and aces:
        total -= 10
        aces -= 1
    soft = aces > 0 and total <= 21
    return (total, soft)


def total_label(cards: List[Card]) -> str:
    """Human total: soft hands show both values (e.g. '7 or 17') so players can
    see the Ace is worth 1 *or* 11 — and why a soft 17 + 6 becomes 13, not bust."""
    total, soft = hand_value(cards)
    if soft and total < 21:
        return f"{total - 10} or {total}"
    return str(total)


def is_blackjack(cards: List[Card]) -> bool:
    return len(cards) == 2 and hand_value(cards)[0] == 21


def render(cards: List[Card], hide_first: bool = False) -> str:
    if hide_first and cards:
        shown = [HIDDEN] + [str(c) for c in cards[1:]]
        return "  ".join(shown)
    return "  ".join((str(c) for c in cards))


def dealer_play(deck: List[Card], dealer: List[Card]) -> None:
    """Dealer draws until 17+ (stands on all 17s)."""
    while hand_value(dealer)[0] < 17:
        dealer.append(deck.pop())


def settle(
    player: List[Card],
    dealer: List[Card],
    bet: int,
    natural: bool,
    natural_pct: int = 300,
    bonus_777: int = 0,
    suited_bonus: int = 0,
    charlie_pct: int = 350,
    win_profit_pct: int = 135,
) -> Tuple[int, str]:
    """Return (payout, outcome). payout is the total returned to the player
    (0 = lost the bet, bet = push, stake + configured profit = win). A two-card blackjack pays
    `natural_pct`% of the bet as profit — 150 = 3:2, 200 = 2:1.

    Special-hand bonuses: three 7s is a `bonus_777`-to-1 jackpot that always
    wins; a suited natural pays `suited_bonus` extra percentage points. Five
    non-busting cards pay `charlie_pct`% profit without a dealer comparison."""
    pt = hand_value(player)[0]
    dt_ = hand_value(dealer)[0]
    if pt > 21:
        return (0, "bust")
    if bonus_777 and len(player) == 3 and all((c.rank == "7" for c in player)):
        return (bet + bet * bonus_777, "lucky777")
    if natural:
        if is_blackjack(dealer):
            return (bet, "push")
        if suited_bonus and player[0].suit == player[1].suit:
            return (bet + bet * (natural_pct + suited_bonus) // 100, "suited_blackjack")
        return (bet + bet * natural_pct // 100, "blackjack")
    if len(player) >= 5:
        return (bet + bet * max(0, int(charlie_pct)) // 100, "five_card_charlie")
    if dt_ > 21:
        return (bet + bet * max(0, int(win_profit_pct)) // 100, "dealer_bust")
    if pt > dt_:
        return (bet + bet * max(0, int(win_profit_pct)) // 100, "win")
    if pt < dt_:
        return (0, "lose")
    return (bet, "push")


class BlackjackReplayView(discord.ui.View):
    """Fast next-hand controls shown after every settled blackjack hand."""

    def __init__(self, cog: "Blackjack", user: discord.abc.User, bet: int, session: Dict[str, int]) -> None:
        super().__init__(timeout=120.0)
        self.cog = cog
        self.user = user
        self.bet = max(1, int(bet))
        self.session = session
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                embed=ui.error_embed("This isn't your table. Start your own with `/blackjack`."),
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Deal Again", style=discord.ButtonStyle.success, emoji="🃏")
    async def deal_again(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog._start_hand(interaction, self.bet, edit=True, session=self.session)

    @discord.ui.button(label="Rebet ×2", style=discord.ButtonStyle.primary, emoji="💰")
    async def rebet_double(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog._start_hand(interaction, self.bet * 2, edit=True, session=self.session)

    @discord.ui.button(label="Bet 25% wallet", style=discord.ButtonStyle.secondary)
    async def quarter(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        balance = int(self.cog.user(interaction.guild_id, interaction.user.id)["donuts"])
        await self.cog._start_hand(interaction, max(1, balance // 4), edit=True, session=self.session)

    @discord.ui.button(label="Bet 50% wallet", style=discord.ButtonStyle.secondary)
    async def half(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        balance = int(self.cog.user(interaction.guild_id, interaction.user.id)["donuts"])
        await self.cog._start_hand(interaction, max(1, balance // 2), edit=True, session=self.session)

    @discord.ui.button(label="Bet All", style=discord.ButtonStyle.danger)
    async def bet_all(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        balance = int(self.cog.user(interaction.guild_id, interaction.user.id)["donuts"])
        await self.cog._start_hand(interaction, balance, edit=True, session=self.session)


class BlackjackView(discord.ui.View):
    def __init__(
        self,
        cog: "Blackjack",
        interaction: discord.Interaction,
        bet: int,
        shoe: List[Card],
        session: Dict[str, int],
    ) -> None:
        super().__init__(timeout=90.0)
        self.cog = cog
        self.guild_id = interaction.guild_id
        self.user = interaction.user
        self.bet = bet
        self.session = session
        self.doubled = False
        self.finished = False
        from szofie.combat import reset_generation

        self.reset_generation = reset_generation(cog.user(self.guild_id, self.user.id))
        self.reshuffled = False
        self.message: Optional[discord.Message] = None
        self.deck = shoe
        self.player: List[Card] = [self.deck.pop(), self.deck.pop()]
        self.dealer: List[Card] = [self.deck.pop(), self.deck.pop()]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                embed=ui.error_embed("This isn't your hand. Start your own with `/blackjack`."),
                ephemeral=True,
            )
            return False
        if not self._current_generation():
            await interaction.response.send_message(
                embed=ui.warn_embed("This hand ended when the account was reset. Start a new hand."),
                ephemeral=True,
            )
            return False
        return True

    def _current_generation(self) -> bool:
        from szofie.combat import reset_generation

        if reset_generation(self.cog.user(self.guild_id, self.user.id)) == self.reset_generation:
            return True
        self.finished = True
        for child in self.children:
            child.disabled = True
        self.cog.active.discard(self.cog.hand_key(self.guild_id, self.user.id))
        self.stop()
        return False

    async def on_timeout(self) -> None:
        if not self.finished and self.message is not None:
            await self._resolve(timed_out=True)

    def _embed(self, *, reveal: bool, note: str = "", color: Optional[int] = None) -> discord.Embed:
        cfg = self.cog.cfg(self.guild_id)
        ptxt = total_label(self.player)
        embed = ui.base_embed(title="🃏 Blackjack", color=color if color is not None else cfg.color)
        embed.add_field(name=f"{self.user.display_name} — {ptxt}", value=render(self.player), inline=False)
        if reveal:
            dtxt = total_label(self.dealer)
            embed.add_field(name=f"Dealer — {dtxt}", value=render(self.dealer), inline=False)
        else:
            up = hand_value([self.dealer[1]])[0]
            embed.add_field(name=f"Dealer — {up}?", value=render(self.dealer, hide_first=True), inline=False)
        stake = self.bet * (2 if self.doubled else 1)
        footer = f"Bet: {ui.format_donuts(stake)} {self.cog.name(cfg)}  •  🂠 {len(self.deck)} cards left in the shoe"
        if note:
            embed.description = note
        elif self.reshuffled:
            embed.description = "🔄 Fresh shoe shuffled — the count resets."
        embed.set_footer(text=footer)
        return embed

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary, emoji="🃏")
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.finished or self.doubled or (not self._current_generation()):
            await interaction.response.defer()
            return
        self.player.append(self.deck.pop())
        self._disable_double()
        self.surrender.disabled = True
        if hand_value(self.player)[0] > 21:
            await interaction.response.defer()
            await self._resolve()
            return
        if len(self.player) >= 5:
            await interaction.response.defer()
            await self._resolve()
            return
        await interaction.response.edit_message(embed=self._embed(reveal=False), view=self)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary, emoji="✋")
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        await self._resolve()

    @discord.ui.button(label="Surrender", style=discord.ButtonStyle.danger, emoji="🏳️")
    async def surrender(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Early surrender: concede immediately and recover half the original stake."""
        if self.finished or self.doubled or len(self.player) != 2 or (not self._current_generation()):
            await interaction.response.defer()
            return
        self.finished = True
        await interaction.response.defer()
        user = self.cog.user(self.guild_id, self.user.id)
        refund = self.bet // 2
        user["donuts"] += refund
        net = -(self.bet - refund)
        self.cog.update_session(self.session, net)
        cfg = self.cog.cfg(self.guild_id)
        tour = self.cog.record_result(cfg, user, self.bet, net)
        for child in self.children:
            child.disabled = True
        await self.cog.persist(self.guild_id)
        tour_reward = int(tour.get("tour_reward", 0))
        await self.cog.log(
            self.guild_id,
            self.user.id,
            net,
            user,
            after_override=int(user.get("donuts", 0)) + int(user.get("bank", 0)) - tour_reward,
        )
        if tour_reward:
            await self.cog.log(self.guild_id, self.user.id, tour_reward, user, reason="casino-tour")
        self.cog.active.discard(self.cog.hand_key(self.guild_id, self.user.id))
        self.stop()
        await interaction.edit_original_response(
            embed=self._embed(
                reveal=True,
                note=f"**Early surrender.** Half your stake was returned.\nNet: {self.cog.emoji(cfg)} {ui.format_donuts(net)} · Balance: {ui.format_donuts(user['donuts'])}{self.cog.tour_note(cfg, tour, self.user.id)}",
                color=ui.COLOR_WARN,
            ),
            view=BlackjackReplayView(self.cog, self.user, self.bet, self.session),
        )

    @discord.ui.button(label="Double Down", style=discord.ButtonStyle.success, emoji="💰")
    async def double(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.finished or self.doubled or len(self.player) != 2 or (not self._current_generation()):
            await interaction.response.defer()
            return
        user = self.cog.user(self.guild_id, self.user.id)
        if user["donuts"] < self.bet:
            await interaction.response.send_message(
                embed=ui.error_embed("Not enough donuts to double."), ephemeral=True
            )
            return
        user["donuts"] -= self.bet
        self.doubled = True
        self.player.append(self.deck.pop())
        await interaction.response.defer()
        await self._resolve()

    def _disable_double(self) -> None:
        self.double.disabled = True

    async def _resolve(self, timed_out: bool = False) -> None:
        if self.finished or not self._current_generation():
            return
        self.finished = True
        cfg = self.cog.cfg(self.guild_id)
        user = self.cog.user(self.guild_id, self.user.id)
        stake = self.bet * (2 if self.doubled else 1)
        if hand_value(self.player)[0] <= 21 and len(self.player) < 5:
            dealer_play(self.deck, self.dealer)
        ace = plushie_perk(user, "blackjack")
        b777 = int(
            cfg.get(
                "economy.blackjack_ace_bonus_777" if ace else "economy.blackjack_bonus_777", 12 if ace else 9
            )
        )
        payout, outcome = settle(
            self.player,
            self.dealer,
            stake,
            natural=False,
            bonus_777=b777,
            charlie_pct=int(cfg.get("economy.blackjack_charlie_profit_pct", 350)),
            win_profit_pct=int(cfg.get("economy.blackjack_win_profit_pct", 135)),
        )
        user["donuts"] += payout
        net = payout - stake
        self.cog.update_session(self.session, net)
        tour = self.cog.record_result(cfg, user, stake, net, jackpot=outcome == "lucky777")
        blurbs = {
            "bust": ("You busted.", ui.COLOR_BAD),
            "lose": ("Dealer wins.", ui.COLOR_BAD),
            "win": ("You win!", ui.COLOR_OK),
            "dealer_bust": ("Dealer busts — you win!", ui.COLOR_OK),
            "five_card_charlie": (
                f"Five-card Charlie — {int(cfg.get('economy.blackjack_charlie_profit_pct', 350)) / 100:g}:1 win!",
                ui.COLOR_OK,
            ),
            "lucky777": ("🎰 LUCKY SEVENS!! Triple 7s — JACKPOT!", ui.COLOR_OK),
            "push": ("Push (tie) — your bet is returned.", ui.COLOR_WARN),
        }
        text, color = blurbs.get(outcome, ("Done.", cfg.color))
        if timed_out:
            text = "Timed out — stood automatically. " + text
        sign = f"+{ui.format_donuts(net)}" if net > 0 else f"{ui.format_donuts(net)}"
        note = f"**{text}**\nNet: {self.cog.emoji(cfg)} {sign} {self.cog.name(cfg)}  •  Balance: {ui.format_donuts(user['donuts'])}\n{self.cog.session_line(self.session, self.user.id)}{self.cog.tour_note(cfg, tour, self.user.id)}"
        for child in self.children:
            child.disabled = True
        await self.cog.persist(self.guild_id)
        self.cog.active.discard(self.cog.hand_key(self.guild_id, self.user.id))
        self.stop()
        embed = self._embed(reveal=True, note=note, color=color)
        replay = BlackjackReplayView(self.cog, self.user, self.bet, self.session)
        try:
            if self.message is not None:
                await self.message.edit(embed=embed, view=replay)
                replay.message = self.message
        except discord.HTTPException:
            pass
        tour_reward = int(tour.get("tour_reward", 0))
        await self.cog.log(
            self.guild_id,
            self.user.id,
            net,
            user,
            after_override=int(user.get("donuts", 0)) + int(user.get("bank", 0)) - tour_reward,
        )
        if tour_reward:
            await self.cog.log(self.guild_id, self.user.id, tour_reward, user, reason="casino-tour")
        channel = self.message.channel if self.message is not None else None
        events = ["big_score"] if net >= 5000 else []
        if outcome in ("win", "dealer_bust", "lucky777", "five_card_charlie"):
            events.append("card_shark")
        await self.cog.award(self.guild_id, channel, self.user, user, *events)


class Blackjack(commands.Cog):
    """Play blackjack for donuts."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.econ = bot.economy
        self.active: set[Tuple[int, int]] = set()
        self.shoes: Dict[Tuple[int, int], List[Card]] = {}
        self._deal_locks: Dict[Tuple[int, int], asyncio.Lock] = {}

    def hand_key(self, guild_id: int, user_id: int) -> Tuple[int, int]:
        return (self.econ.store.canonical_id(guild_id), user_id)

    def _prep_shoe(self, key: Tuple[int, int], cfg) -> Tuple[List[Card], bool]:
        """Return the player's shoe, reshuffling a fresh one if it's run past the
        cut card. Second value is True when a reshuffle happened this hand."""
        decks = max(1, int(cfg.get("economy.blackjack_decks", 2)))
        full = 52 * decks
        cut = full * int(cfg.get("economy.blackjack_shuffle_at_pct", 25)) // 100
        shoe = self.shoes.get(key)
        if not shoe or len(shoe) <= max(cut, 15):
            shoe = build_shoe(random, decks)
            self.shoes[key] = shoe
            return (shoe, True)
        return (shoe, False)

    def cfg(self, guild_id: int):
        return self.bot.config.for_guild(guild_id)

    def user(self, guild_id: int, user_id: int) -> Dict[str, Any]:
        starting = int(self.cfg(guild_id).get("economy.starting_balance", 100))
        return self.econ.user(guild_id, user_id, starting)

    def name(self, cfg) -> str:
        return str(cfg.get("economy.currency_name", "donuts"))

    def emoji(self, cfg) -> str:
        return str(cfg.get("economy.currency_emoji", "🍩"))

    async def persist(self, guild_id: int) -> None:
        await self.econ.save(guild_id)

    async def log(
        self, guild_id, user_id, delta, u, *, reason: str = "blackjack", after_override: Optional[int] = None
    ) -> None:
        if delta:
            await self.bot.ledger.record(
                guild_id,
                user_id,
                delta,
                reason,
                after=int(u.get("donuts", 0)) + int(u.get("bank", 0))
                if after_override is None
                else int(after_override),
            )

    def record_result(
        self, cfg, user: Dict[str, Any], wager: int, net: int, *, jackpot: bool = False
    ) -> Dict[str, Any]:
        return record_casino_result(
            user,
            "blackjack",
            wager,
            net,
            mode="standard",
            jackpot=jackpot,
            tour_enabled=bool(cfg.get("economy.casino_tour_enabled", True)),
            tour_required=int(cfg.get("economy.casino_tour_games", 3)),
            tour_reward=int(cfg.get("economy.casino_tour_reward", 500000000)),
            tour_wager_pct=int(cfg.get("economy.casino_tour_wager_pct", 3)),
            tour_streak_bonus_pct=int(cfg.get("economy.casino_tour_streak_bonus_pct", 10)),
            tour_streak_cap=int(cfg.get("economy.casino_tour_streak_cap", 7)),
            reputation_multiplier=1 + plushie_perk(user, "casino_rep") / 100,
        )

    def tour_note(self, cfg, result: Dict[str, Any], subject_id: Optional[int] = None) -> str:
        reward = int(result.get("tour_reward", 0))
        if reward > 0 and subject_id is not None:
            reward = reward
        weekly = int(result.get("weekly_reputation", 0))
        notes: List[str] = []
        if reward > 0:
            notes.append(
                f"🎟️ **Casino Tour complete!** {self.emoji(cfg)} **{ui.format_donuts(reward)}** {self.name(cfg)} awarded · daily streak **{int(result.get('tour_streak', 1))}**"
            )
        elif result.get("tour_required"):
            if result.get("tour_claimed"):
                notes.append("🎟️ **Daily Casino Tour:** reward already claimed. Resets at 00:00 UTC.")
            else:
                missing = ", ".join((game.title() for game in result.get("tour_missing", [])))
                notes.append(
                    f"🎟️ **Daily Casino Tour: {int(result.get('tour_progress', 0))}/{int(result['tour_required'])}** · Still needed: {missing}."
                )
        if weekly > 0:
            notes.append(f"🏆 **Weekly casino circuit complete!** +**{weekly} RP**")
        return "\n\n" + "\n".join(notes) if notes else ""

    @staticmethod
    def update_session(session: Dict[str, int], net: int) -> None:
        session["hands"] = int(session.get("hands", 0)) + 1
        session["net"] = int(session.get("net", 0)) + int(net)
        key = "wins" if net > 0 else "losses" if net < 0 else "pushes"
        session[key] = int(session.get(key, 0)) + 1

    @staticmethod
    def session_line(session: Dict[str, int], subject_id: Optional[int] = None) -> str:
        net = int(session.get("net", 0))
        if subject_id is not None:
            net = net
        signed = f"+{ui.format_donuts(net)}" if net > 0 else f"{ui.format_donuts(net)}"
        return f"📊 Session: **{int(session.get('hands', 0))}** hands · {int(session.get('wins', 0))}W/{int(session.get('losses', 0))}L/{int(session.get('pushes', 0))}P · net **{signed}**"

    async def award(self, guild_id, channel, member, user, *events) -> None:
        """Grant + announce badges (config-gated), shared shape with EconomyCog."""
        cfg = self.cfg(guild_id)
        if not cfg.get("achievements.enabled", True):
            return
        ch = channel if cfg.get("achievements.announce", True) else None
        newly = await badges.award(ch, member, user, cfg.color, *events)
        if newly:
            await self.persist(guild_id)

    @app_commands.command(name="blackjack", description="Play a hand of blackjack for donuts.")
    @app_commands.describe(bet="Bet: number, 25k/2.5m/1b, half, or all")
    async def blackjack(
        self, interaction: discord.Interaction, bet: app_commands.Transform[int, BetTransformer]
    ) -> None:
        await self._start_hand(interaction, int(bet))

    async def _start_hand(
        self,
        interaction: discord.Interaction,
        bet: int,
        *,
        edit: bool = False,
        session: Optional[Dict[str, int]] = None,
    ) -> None:
        """Serialize wagers for one player, including shared-economy servers."""
        if interaction.guild_id is None:
            await interaction.response.send_message(
                embed=ui.error_embed("Blackjack only works in a server."), ephemeral=True
            )
            return
        key = self.hand_key(interaction.guild_id, interaction.user.id)
        lock = self._deal_locks.setdefault(key, asyncio.Lock())
        if lock.locked():
            await interaction.response.send_message(
                embed=ui.error_embed("Your next hand is already being dealt."), ephemeral=True
            )
            return
        async with lock:
            await self._deal_hand(interaction, bet, edit=edit, session=session)

    async def _deal_hand(
        self,
        interaction: discord.Interaction,
        bet: int,
        *,
        edit: bool = False,
        session: Optional[Dict[str, int]] = None,
    ) -> None:
        """Validate, stake and deal one hand from either slash or replay buttons."""
        if interaction.guild_id is None:
            await interaction.response.send_message(
                embed=ui.error_embed("Blackjack only works in a server."), ephemeral=True
            )
            return
        cfg = self.cfg(interaction.guild_id)
        if not cfg.get("economy.enabled", True):
            await interaction.response.send_message(
                embed=ui.error_embed("The economy is turned off here."), ephemeral=True
            )
            return
        if gamelocks.is_locked("blackjack"):
            await interaction.response.send_message(
                embed=ui.warn_embed("🔒 **Blackjack** is locked down right now — check back later."),
                ephemeral=True,
            )
            return
        chan = cfg.get("economy.channel")
        if chan and interaction.channel_id != int(chan):
            await interaction.response.send_message(
                embed=ui.error_embed(f"Play in <#{int(chan)}>."), ephemeral=True
            )
            return
        key = self.hand_key(interaction.guild_id, interaction.user.id)
        if key in self.active:
            await interaction.response.send_message(
                embed=ui.error_embed("Finish your current hand first."), ephemeral=True
            )
            return
        user = self.user(interaction.guild_id, interaction.user.id)
        lo = int(cfg.get("economy.blackjack_min_bet", 10))
        if bet < lo:
            await interaction.response.send_message(
                embed=ui.error_embed(f"Minimum bet is {ui.format_donuts(lo)} — no ceiling above that."),
                ephemeral=True,
            )
            return
        if bet > user["donuts"]:
            await interaction.response.send_message(
                embed=ui.error_embed(
                    f"You only have {self.emoji(cfg)} {ui.format_donuts(user['donuts'])} {self.name(cfg)}. Claim more with `/daily` or `/work`."
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=not edit)
        user["donuts"] -= bet
        shoe, reshuffled = self._prep_shoe(key, cfg)
        if session is None:
            session = {"hands": 0, "net": 0, "wins": 0, "losses": 0, "pushes": 0}
        view = BlackjackView(self, interaction, bet, shoe, session)
        view.reshuffled = reshuffled
        if user["donuts"] < bet:
            view.double.disabled = True
        if is_blackjack(view.player) or is_blackjack(view.dealer):
            nat_pct = int(cfg.get("economy.blackjack_natural_pct", 300))
            ace = plushie_perk(user, "blackjack")
            suited = int(
                cfg.get(
                    "economy.blackjack_ace_suited_bonus" if ace else "economy.blackjack_suited_bonus",
                    75 if ace else 50,
                )
            )
            payout, outcome = settle(
                view.player,
                view.dealer,
                bet,
                natural=is_blackjack(view.player),
                natural_pct=nat_pct,
                suited_bonus=suited,
            )
            user["donuts"] += payout
            net = payout - bet
            self.update_session(session, net)
            tour = self.record_result(cfg, user, bet, net)
            await self.persist(interaction.guild_id)
            pays = f" ({nat_pct / 100:g}:1!)" if outcome == "blackjack" else ""
            text = {
                "blackjack": "Blackjack! 🎉" + pays,
                "suited_blackjack": "💎 Suited Blackjack — bonus payout!",
                "push": "Push (tie) — you both have blackjack, bet returned.",
                "lose": "Dealer has blackjack. Tough luck.",
            }.get(outcome, "Done.")
            won = outcome in ("blackjack", "suited_blackjack")
            color = ui.COLOR_OK if won else ui.COLOR_WARN if outcome == "push" else ui.COLOR_BAD
            view.finished = True
            for child in view.children:
                child.disabled = True
            sign = f"+{ui.format_donuts(net)}" if net > 0 else f"{ui.format_donuts(net)}"
            note = f"**{text}**\nNet: {self.emoji(cfg)} {sign} {self.name(cfg)}  •  Balance: {ui.format_donuts(user['donuts'])}\n{self.session_line(session, interaction.user.id)}{self.tour_note(cfg, tour, interaction.user.id)}"
            replay = BlackjackReplayView(self, interaction.user, bet, session)
            embed = view._embed(reveal=True, note=note, color=color)
            if edit:
                await interaction.edit_original_response(embed=embed, view=replay)
                replay.message = interaction.message
            else:
                await interaction.edit_original_response(embed=embed, view=replay)
                replay.message = await ui.response_message(interaction)
            tour_reward = int(tour.get("tour_reward", 0))
            await self.log(
                interaction.guild_id,
                interaction.user.id,
                net,
                user,
                after_override=int(user.get("donuts", 0)) + int(user.get("bank", 0)) - tour_reward,
            )
            if tour_reward:
                await self.log(
                    interaction.guild_id, interaction.user.id, tour_reward, user, reason="casino-tour"
                )
            events = ["twenty_one", "card_shark"] if won else []
            if net >= 5000:
                events.append("big_score")
            await self.award(interaction.guild_id, interaction.channel, interaction.user, user, *events)
            return
        self.active.add(key)
        await self.persist(interaction.guild_id)
        if edit:
            await interaction.edit_original_response(embed=view._embed(reveal=False), view=view)
            view.message = interaction.message
        else:
            await interaction.edit_original_response(embed=view._embed(reveal=False), view=view)
            view.message = await ui.response_message(interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Blackjack(bot))
