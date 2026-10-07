"""Player-to-player lending.

`/give` is an outright gift; a **loan** is expected back, optionally with
interest the lender sets (capped so it stays fair). The borrower must accept the
terms before any donuts move — you can't force debt on someone.

Loans live alongside balances in the shared economy document (data/economy/), so
they persist atomically with everything else. All donut movement uses the same
`bot.economy` store every other game cog shares.
"""

from __future__ import annotations
import datetime as dt
from typing import Any, Dict, List, Optional
import discord
from discord import app_commands
from discord.ext import commands
from szofie import nyx, ui
from szofie.betting import AmountParseError, BetTransformer, parse_amount
from szofie.combat import reset_generation


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse(iso: Optional[str]) -> Optional[dt.datetime]:
    if not iso:
        return None
    try:
        return dt.datetime.fromisoformat(iso)
    except ValueError:
        return None


def total_owed(loans: List[Dict[str, Any]]) -> int:
    return sum((int(l.get("owed", 0)) for l in loans))


def is_overdue(loan: Dict[str, Any], now: Optional[dt.datetime] = None) -> bool:
    due = _parse(loan.get("due_at"))
    return bool(due and int(loan.get("owed", 0)) > 0 and (due < (now or _now())))


class LoanOfferView(discord.ui.View):
    """Accept/Decline gate shown to the borrower. Only they can press it."""

    def __init__(
        self,
        cog: "Loans",
        lender: discord.Member,
        borrower: discord.Member,
        amount: int,
        interest: int,
        days: Optional[int],
    ) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.lender = lender
        self.borrower = borrower
        self.amount = amount
        self.interest = interest
        self.days = days
        self.message: Optional[discord.Message] = None
        self.resolved = False
        self.generations = (
            reset_generation(cog.user(lender.guild.id, lender.id)),
            reset_generation(cog.user(borrower.guild.id, borrower.id)),
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.borrower.id:
            await ui.respond(
                interaction, embed=ui.error_embed("Only the borrower can answer this offer."), ephemeral=True
            )
            return False
        if self.resolved:
            await ui.respond(
                interaction, embed=ui.warn_embed("This loan offer has already been answered."), ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        if self.resolved or self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(
                embed=ui.warn_embed("The loan offer expired — no donuts changed hands."), view=self
            )
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Accept loan", style=discord.ButtonStyle.success, emoji="🤝")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.resolved = True
        for child in self.children:
            child.disabled = True
        await interaction.response.defer()
        await self.cog.finalize_loan(interaction, self)
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary, emoji="❌")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.resolved = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            embed=ui.warn_embed(f"{self.borrower.display_name} declined the loan."), view=self
        )
        self.stop()


class Loans(commands.Cog):
    """Lend and repay donuts."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.econ = bot.economy

    def cfg(self, guild_id: int):
        return self.bot.config.for_guild(guild_id)

    def user(self, guild_id: int, user_id: int) -> Dict[str, Any]:
        starting = int(self.cfg(guild_id).get("economy.starting_balance", 100))
        return self.econ.user(guild_id, user_id, starting)

    def emoji(self, cfg) -> str:
        return str(cfg.get("economy.currency_emoji", "🍩"))

    def name(self, cfg) -> str:
        return str(cfg.get("economy.currency_name", "donuts"))

    def money(self, cfg, amount: int) -> str:
        return f"{self.emoji(cfg)} **{ui.format_donuts(amount)}** {self.name(cfg)}"

    def shown_money(self, cfg, amount: int, lender_id: int, borrower_id: int) -> str:
        """Format the actual transaction amount for either participant."""
        return self.money(cfg, amount)

    async def persist(self, guild_id: int) -> None:
        await self.econ.save(guild_id)

    def _loans(self, guild_id: int) -> List[Dict[str, Any]]:
        doc = self.econ.store.load(guild_id)
        if "loans" not in doc or not isinstance(doc["loans"], list):
            doc["loans"] = []
        return doc["loans"]

    def _next_id(self, guild_id: int) -> int:
        doc = self.econ.store.load(guild_id)
        nid = int(doc.get("next_loan", 1))
        doc["next_loan"] = nid + 1
        return nid

    def active_for_borrower(self, guild_id: int, borrower_id: int) -> List[Dict[str, Any]]:
        return [
            l for l in self._loans(guild_id) if l.get("borrower") == borrower_id and int(l.get("owed", 0)) > 0
        ]

    async def _guard(self, interaction: discord.Interaction):
        await ui.defer_response(interaction)
        if interaction.guild_id is None:
            await ui.respond(
                interaction, embed=ui.error_embed("Lending only works in a server."), ephemeral=True
            )
            return None
        cfg = self.cfg(interaction.guild_id)
        if not cfg.get("economy.enabled", True):
            await ui.respond(
                interaction, embed=ui.error_embed("The economy is turned off here."), ephemeral=True
            )
            return None
        return cfg

    @app_commands.command(name="loan", description="Offer to lend someone donuts, with optional interest.")
    @app_commands.describe(
        user="Who to lend to",
        amount="Amount: number, 25k/2.5m/1b, half, or all",
        interest="Interest to charge, as a percent (optional)",
        days="Days until it's due (optional)",
    )
    async def loan(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        amount: app_commands.Transform[int, BetTransformer],
        interest: Optional[app_commands.Range[int, 0, 1000]] = 0,
        days: Optional[app_commands.Range[int, 1, 365]] = None,
    ) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        interest = int(interest or 0)
        if user.id == interaction.user.id:
            await ui.respond(interaction, embed=ui.error_embed("You can't lend to yourself."), ephemeral=True)
            return
        if user.bot:
            await ui.respond(interaction, embed=ui.error_embed("Bots don't take loans."), ephemeral=True)
            return
        lo = int(cfg.get("economy.loan_min_amount", 10))
        if amount < lo:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"The smallest loan is {self.money(cfg, lo)}."),
                ephemeral=True,
            )
            return
        if amount > 10000000:
            await ui.respond(
                interaction,
                embed=ui.error_embed("A single loan offer is capped at 🍩 **10,000,000**."),
                ephemeral=True,
            )
            return
        max_interest = int(cfg.get("economy.loan_max_interest", 50))
        if interest > max_interest:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Interest is capped at **{max_interest}%** here — keep it fair."),
                ephemeral=True,
            )
            return
        lender = self.user(interaction.guild_id, interaction.user.id)
        if lender["donuts"] < amount:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"You only have {self.money(cfg, lender['donuts'])} to lend."),
                ephemeral=True,
            )
            return
        existing = self.active_for_borrower(interaction.guild_id, user.id)
        max_active = int(cfg.get("economy.loan_max_active", 3))
        if any((is_overdue(l) for l in existing)):
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"{user.display_name} has an **overdue** loan — settle that first."),
                ephemeral=True,
            )
            return
        if len(existing) >= max_active:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"{user.display_name} already owes the maximum of **{max_active}** loans."
                ),
                ephemeral=True,
            )
            return
        repay = amount + amount * interest // 100
        embed = ui.base_embed(
            title="🤝 Loan offer",
            description=f"{interaction.user.mention} offers to lend {user.mention} {self.shown_money(cfg, amount, interaction.user.id, user.id)}.",
            color=cfg.color,
        )
        embed.add_field(name="Interest", value=f"{interest}%", inline=True)
        embed.add_field(
            name="To repay", value=self.shown_money(cfg, repay, interaction.user.id, user.id), inline=True
        )
        embed.add_field(name="Due", value=f"in {days} day(s)" if days else "no deadline", inline=True)
        embed.set_footer(text=f"{user.display_name}, accept within 3 minutes to receive the donuts.")
        view = LoanOfferView(self, interaction.user, user, amount, interest, days)
        await ui.respond(interaction, content=user.mention, embed=embed, view=view)
        view.message = await ui.response_message(interaction)

    async def finalize_loan(self, interaction: discord.Interaction, view: LoanOfferView) -> None:
        """Borrower accepted — move the donuts and record the debt."""
        cfg = self.cfg(interaction.guild_id)
        lender = self.user(interaction.guild_id, view.lender.id)
        borrower = self.user(interaction.guild_id, view.borrower.id)
        if (reset_generation(lender), reset_generation(borrower)) != view.generations:
            await interaction.edit_original_response(
                embed=ui.warn_embed("This offer ended when an account was reset. No loan was created."),
                view=view,
            )
            return
        existing = self.active_for_borrower(interaction.guild_id, view.borrower.id)
        if any((is_overdue(loan) for loan in existing)) or len(existing) >= int(
            cfg.get("economy.loan_max_active", 3)
        ):
            await interaction.edit_original_response(
                embed=ui.warn_embed("The borrower no longer qualifies for this offer. No donuts moved."),
                view=view,
            )
            return
        if lender["donuts"] < view.amount:
            await interaction.edit_original_response(
                embed=ui.error_embed(
                    f"{view.lender.display_name} no longer has {self.shown_money(cfg, view.amount, view.lender.id, view.borrower.id)} — offer void."
                ),
                view=view,
            )
            return
        lender["donuts"] -= view.amount
        borrower["donuts"] += view.amount
        repay = view.amount + view.amount * view.interest // 100
        due_at = (_now() + dt.timedelta(days=view.days)).isoformat() if view.days else None
        loan = {
            "id": self._next_id(interaction.guild_id),
            "lender": view.lender.id,
            "borrower": view.borrower.id,
            "principal": view.amount,
            "owed": repay,
            "interest": view.interest,
            "created_at": _now().isoformat(),
            "due_at": due_at,
        }
        self._loans(interaction.guild_id).append(loan)
        await self.persist(interaction.guild_id)
        gid = interaction.guild_id
        await self.bot.ledger.record(
            gid,
            view.lender.id,
            -view.amount,
            "loan-sent",
            after=lender["donuts"] + lender.get("bank", 0),
            other=view.borrower.id,
        )
        await self.bot.ledger.record(
            gid,
            view.borrower.id,
            view.amount,
            "loan-received",
            after=borrower["donuts"] + borrower.get("bank", 0),
            other=view.lender.id,
        )
        embed = ui.ok_embed(
            f"{view.borrower.mention} accepted. {self.shown_money(cfg, view.amount, view.lender.id, view.borrower.id)} handed over.\nThey owe {self.shown_money(cfg, repay, view.lender.id, view.borrower.id)} back"
            + (f", due <t:{int(_parse(due_at).timestamp())}:R>." if due_at else " (no deadline).")
            + f"\nRepay with `/repay {view.lender.display_name}`.",
            title="🤝 Loan agreed",
        )
        embed.color = cfg.color
        embed.set_footer(text=f"Loan #{loan['id']}")
        await interaction.edit_original_response(embed=embed, view=view)

    @app_commands.command(name="repay", description="Pay back donuts you borrowed from someone.")
    @app_commands.describe(
        user="Who you're repaying", amount="Number, 25k/2.5m/1b, half, or all; blank pays all"
    )
    async def repay(
        self, interaction: discord.Interaction, user: discord.Member, amount: Optional[str] = None
    ) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        loans = [
            l
            for l in self._loans(interaction.guild_id)
            if l.get("borrower") == interaction.user.id
            and l.get("lender") == user.id
            and (int(l.get("owed", 0)) > 0)
        ]
        if not loans:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"You don't owe {user.display_name} anything."),
                ephemeral=True,
            )
            return
        owed = total_owed(loans)
        borrower = self.user(interaction.guild_id, interaction.user.id)
        try:
            requested = parse_amount(amount, available=int(borrower["donuts"]), default_all=True)
        except AmountParseError as exc:
            await ui.respond(interaction, embed=ui.error_embed(str(exc)), ephemeral=True)
            return
        pay = min(requested, owed)
        if pay <= 0:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"You have no donuts to repay with. You owe {self.shown_money(cfg, owed, user.id, interaction.user.id)}."
                ),
                ephemeral=True,
            )
            return
        if borrower["donuts"] < pay:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"You only have {self.money(cfg, borrower['donuts'])}; that repays part of the {self.shown_money(cfg, owed, user.id, interaction.user.id)} you owe."
                ),
                ephemeral=True,
            )
            return
        lender = self.user(interaction.guild_id, user.id)
        borrower["donuts"] -= pay
        lender["donuts"] += pay
        remaining = pay
        loans.sort(key=lambda l: l.get("id", 0))
        for loan in loans:
            if remaining <= 0:
                break
            take = min(remaining, int(loan["owed"]))
            loan["owed"] = int(loan["owed"]) - take
            remaining -= take
        all_loans = self._loans(interaction.guild_id)
        all_loans[:] = [l for l in all_loans if int(l.get("owed", 0)) > 0]
        await self.persist(interaction.guild_id)
        gid = interaction.guild_id
        await self.bot.ledger.record(
            gid,
            interaction.user.id,
            -pay,
            "repay-sent",
            after=borrower["donuts"] + borrower.get("bank", 0),
            other=user.id,
        )
        await self.bot.ledger.record(
            gid,
            user.id,
            pay,
            "repay-received",
            after=lender["donuts"] + lender.get("bank", 0),
            other=interaction.user.id,
        )
        still = owed - pay
        desc = f"{interaction.user.mention} repaid {self.shown_money(cfg, pay, user.id, interaction.user.id)} to {user.mention}."
        desc += (
            f"\n*Paid in full — debt cleared.*"
            if still <= 0
            else f"\nStill owing: {self.shown_money(cfg, still, user.id, interaction.user.id)}."
        )
        await ui.respond(interaction, embed=ui.ok_embed(desc, title="💸 Repayment"))

    @app_commands.command(name="collect", description="Seize donuts from someone overdue on a loan to you.")
    @app_commands.describe(user="The borrower who owes you an overdue debt")
    async def collect(self, interaction: discord.Interaction, user: discord.Member) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        if user.id == interaction.user.id or user.bot:
            await ui.respond(
                interaction, embed=ui.error_embed("Pick a real borrower who owes you."), ephemeral=True
            )
            return
        gid, now = (interaction.guild_id, _now())
        loans = [
            l
            for l in self._loans(gid)
            if l.get("lender") == interaction.user.id
            and l.get("borrower") == user.id
            and (int(l.get("owed", 0)) > 0)
            and is_overdue(l, now)
        ]
        if not loans:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(
                    f"{user.display_name} has no **overdue** debt to you. You can only collect after the due date."
                ),
                ephemeral=True,
            )
            return
        lender = self.user(gid, interaction.user.id)
        cd = int(cfg.get("economy.collect_cooldown_minutes", 30)) * 60
        seen = lender.setdefault("collect_at", {})
        last = _parse(seen.get(str(user.id)))
        if last is not None and (now - last).total_seconds() < cd:
            ready = int(last.timestamp() + cd)
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"You just shook {user.display_name} down. Try again <t:{ready}:R>."),
                ephemeral=True,
            )
            return
        owed = total_owed(loans)
        borrower = self.user(gid, user.id)
        available = int(borrower.get("donuts", 0)) + max(0, int(borrower.get("bank", 0)))
        seize = min(owed, available)
        if seize <= 0:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"{user.display_name} is flat broke — nothing to collect yet."),
                ephemeral=True,
            )
            return
        from_wallet = min(seize, int(borrower.get("donuts", 0)))
        borrower["donuts"] = int(borrower.get("donuts", 0)) - from_wallet
        rest = seize - from_wallet
        if rest:
            borrower["bank"] = int(borrower.get("bank", 0)) - rest
        lender["donuts"] = int(lender.get("donuts", 0)) + seize
        seen[str(user.id)] = now.isoformat()
        remaining = seize
        loans.sort(key=lambda l: l.get("id", 0))
        for loan in loans:
            if remaining <= 0:
                break
            take = min(remaining, int(loan["owed"]))
            loan["owed"] = int(loan["owed"]) - take
            remaining -= take
        all_loans = self._loans(gid)
        all_loans[:] = [l for l in all_loans if int(l.get("owed", 0)) > 0]
        await self.persist(gid)
        await self.bot.ledger.record(
            gid,
            interaction.user.id,
            seize,
            "collect-received",
            after=lender["donuts"] + lender.get("bank", 0),
            other=user.id,
        )
        await self.bot.ledger.record(
            gid,
            user.id,
            -seize,
            "collect-paid",
            after=borrower["donuts"] + borrower.get("bank", 0),
            other=interaction.user.id,
        )
        still = owed - seize
        desc = f"{interaction.user.mention} collected {self.shown_money(cfg, seize, interaction.user.id, user.id)} from {user.mention}'s wallet and vault."
        desc += (
            "\n*Debt paid in full.*"
            if still <= 0
            else f"\nStill overdue: {self.shown_money(cfg, still, interaction.user.id, user.id)}."
        )
        await ui.respond(
            interaction,
            content=user.mention,
            embed=ui.base_embed(title="💰 Debt collected", description=desc, color=ui.COLOR_WARN),
        )

    @app_commands.command(name="loans", description="See who owes you and what you owe.")
    @app_commands.describe(user="Whose loans to view (defaults to you)")
    async def loans_cmd(
        self, interaction: discord.Interaction, user: Optional[discord.Member] = None
    ) -> None:
        nyx.protect_report(interaction, self.econ, (user or interaction.user).id)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        target = user or interaction.user
        target_state = self.user(interaction.guild_id, target.id)
        if nyx.hidden(target_state, interaction.user.id, target.id):
            await ui.respond(interaction, embed=nyx.censored_embed(), ephemeral=True)
            return
        all_loans = self._loans(interaction.guild_id)
        now = _now()
        lent = [l for l in all_loans if l.get("lender") == target.id and int(l.get("owed", 0)) > 0]
        borrowed = [l for l in all_loans if l.get("borrower") == target.id and int(l.get("owed", 0)) > 0]

        def render(loan: Dict[str, Any], other_key: str) -> str:
            who = f"<@{loan.get(other_key)}>"
            shown = int(loan.get("owed", 0))
            line = f"`#{loan.get('id')}` {who} — {self.money(cfg, shown)}"
            if loan.get("interest"):
                line += f" ({loan['interest']}% int.)"
            due = _parse(loan.get("due_at"))
            if due:
                tag = ":R>"
                line += f" · {('**OVERDUE** ' if is_overdue(loan, now) else 'due ')}<t:{int(due.timestamp())}{tag}"
            return line

        fields = []
        for label, entries, other_key in (
            ("Owed to them", lent, "borrower"),
            ("They owe", borrowed, "lender"),
        ):
            if not entries:
                continue
            shown_total = sum((int(loan.get("owed", 0)) for loan in entries))
            shown_total = shown_total
            fields.append(
                (
                    f"{label} ({self.money(cfg, shown_total)})",
                    "\n".join((render(loan, other_key) for loan in entries)),
                    False,
                )
            )
        pages = ui.field_pages(
            f"🤝 {target.display_name}'s loans",
            "" if fields else "*No active loans. Debt-free — for now.*",
            fields,
            color=cfg.color,
        )

        def page_guard(index):
            if nyx.hidden(self.user(interaction.guild_id, target.id), interaction.user.id, target.id):
                return nyx.censored_embed()
            return None

        view = ui.Paginator(pages, interaction.user.id, page_guard=page_guard) if len(pages) > 1 else None
        await ui.respond(
            interaction, embed=pages[0], view=view, ephemeral=user is None or nyx.active(target_state)
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Loans(bot))
