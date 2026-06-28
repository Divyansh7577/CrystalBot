import asyncio
import datetime
import json
import logging
import os
import re
import sys
import unicodedata
import uuid

import discord
from discord import app_commands
from discord.ext import commands

# ── LOGGING ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("discord_bot.main")

# ── DATA / STATE PERSISTENCE ─────────────────────────────────────────────────
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

_STATE_FILE = os.path.join(DATA_DIR, "runtime_state.json")
_CONFIG_FILE = os.path.join(DATA_DIR, "bot_config.json")
_MODLOGS_FILE = os.path.join(DATA_DIR, "modlogs_config.json")
_HISTORY_FILE = os.path.join(DATA_DIR, "modlogs_history.json")
PANELS_FILE = os.path.join(DATA_DIR, "ticket_panels.json")


def _load_json(filepath, default_factory=dict):
    if os.path.exists(filepath):
        try:
            with open(filepath) as f:
                return json.load(f)
        except Exception:
            return default_factory()
    return default_factory()


def _save_json(filepath, data):
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)


def get_bot_active() -> bool:
    return _load_json(_STATE_FILE, dict).get("active", True)


def set_bot_active(value: bool) -> None:
    _save_json(_STATE_FILE, {"active": bool(value)})


# ── GLOBAL CONFIG / SERVER LAYOUT ────────────────────────────────────────────
STAFF_ROLE_NAMES = ["👑 | Owner", "🥈 | Co-Owner", "🛠️ | Admin", "🛡️ | Moderator", "🤝 | Helper"]
SUPPORT_CATEGORY_NAME = "🎫┃SUPPORT AREA"
WELCOME_CHANNEL_NAME = "📢┃announcements"
GAMER_ROLE_NAME = "🎮 | Gamer"
STAFF_LOG_CHANNEL = "🪵┃staff-logs"

OPTION_EMOJIS = ["⚙️", "🚫", "🔍", "🤝", "🎮", "📋", "💬", "🔔", "🛠️", "📩"]
ACTION_EMOJI = {"warn": "⚠️", "timeout": "⏳", "kick": "👢", "ban": "🔨", "untimeout": "🕊️", "unban": "✅", "role": "🏷️"}
_MAX_ENTRIES = 2000

SERVER_LAYOUT_DATA = {
    "roles": [
        {"name": "👑 | Owner", "color": 0xffd700, "permissions": "administrator"},
        {"name": "🥈 | Co-Owner", "color": 0xc0c0c0, "permissions": "manage_server"},
        {"name": "🛠️ | Admin", "color": 0xff4500, "permissions": "moderate"},
        {"name": "🛡️ | Moderator", "color": 0x1e90ff, "permissions": "moderate"},
        {"name": "🤝 | Helper", "color": 0x32cd32, "permissions": "member"},
        {"name": "🎮 | Gamer", "color": 0x00ffff, "permissions": "member", "hoist": False},
    ],
    "categories": [
        {
            "name": "📌┃INFORMATION",
            "channels": [
                {"name": "📜┃rules", "readonly": True, "topic": "Server rules and regulations."},
                {"name": "📢┃announcements", "readonly": True, "topic": "Official announcements."},
                {"name": "🎁┃giveaways", "readonly": True, "topic": "Server giveaways and events."},
            ],
        },
        {
            "name": "💬┃CHATS",
            "channels": [
                {"name": "💬┃main-chat", "topic": "General chat for everyone."},
                {"name": "🏹┃mc-chat", "topic": "Discuss anything about Minecraft!"},
                {"name": "🤖┃bot-commands", "topic": "Spam bot commands here."},
            ],
        },
        {
            "name": "🪵┃STAFF ZONE",
            "staff_only": True,
            "channels": [
                {"name": "🪵┃staff-logs", "topic": "All automatic moderation actions log here."},
                {"name": "💬┃staff-chat", "topic": "Private lounge for staff."},
                {"name": "🔊┃Staff Meeting", "type": "voice"},
            ],
        },
        {
            "name": "🔊┃VOICE CHANNELS",
            "channels": [
                {"name": "🔊┃Duo VC 1", "type": "voice", "user_limit": 2},
                {"name": "🔊┃Duo VC 2", "type": "voice", "user_limit": 2},
                {"name": "🔊┃Squad VC 1", "type": "voice", "user_limit": 4},
                {"name": "🔊┃Squad VC 2", "type": "voice", "user_limit": 4},
                {"name": "🔊┃Penta VC 1", "type": "voice", "user_limit": 5},
            ],
        },
    ],
}


def append_activity_entry(entry: dict) -> None:
    log_file = os.path.join(DATA_DIR, "activity_log.json")
    entries = _load_json(log_file, list)
    entries.append(entry)
    if len(entries) > _MAX_ENTRIES:
        entries = entries[-_MAX_ENTRIES:]
    _save_json(log_file, entries)


def _make_activity_entry(interaction: discord.Interaction, status: str, detail: str = "") -> dict:
    command_name = ""
    if interaction.command:
        parts = []
        if hasattr(interaction.command, "parent") and interaction.command.parent:
            parts.append(interaction.command.parent.name)
        parts.append(interaction.command.name)
        command_name = "/" + " ".join(parts)
    return {
        "id": str(uuid.uuid4()),
        "type": "error" if status == "error" else "interaction",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "guild_id": str(interaction.guild.id) if interaction.guild else None,
        "guild_name": interaction.guild.name if interaction.guild else "DM",
        "user_id": str(interaction.user.id),
        "user_tag": str(interaction.user),
        "command": command_name,
        "status": status,
        "detail": detail,
    }


def _get_history(guild_id: int, user_id: int) -> list:
    return _load_json(_HISTORY_FILE).get(f"{guild_id}:{user_id}", [])


def _record_action(guild_id: int, target, mod, action: str, **details) -> None:
    data = _load_json(_HISTORY_FILE)
    key = f"{guild_id}:{target.id}"
    data.setdefault(key, [])
    data[key].append({
        "action": action,
        "mod_id": mod.id,
        "mod_tag": str(mod),
        "target_tag": str(target),
        "timestamp": datetime.datetime.utcnow().isoformat(),
        **details,
    })
    _save_json(_HISTORY_FILE, data)


def _cb(text: str) -> str:
    return f"```\n{text}\n```"


def _user_block(m) -> str:
    return _cb(f"Username │ {m}\nUser ID  │ {m.id}")


def _mod_block(mod) -> str:
    return _cb(f"Username │ {mod}\nUser ID  │ {mod.id}")


def warn_embed(target, mod, reason: str) -> discord.Embed:
    e = discord.Embed(title="⚠️ Member Warned", color=discord.Color.yellow(), timestamp=datetime.datetime.utcnow())
    e.add_field(name="👤 User Info", value=_user_block(target), inline=False)
    e.add_field(name="🛠️ Moderator", value=_mod_block(mod), inline=False)
    e.add_field(name="📝 Reason", value=_cb(reason), inline=False)
    if target.display_avatar:
        e.set_thumbnail(url=target.display_avatar.url)
    return e


def timeout_embed(target, mod, duration_min: int, reason: str) -> discord.Embed:
    dur_str = (f"+ {duration_min} minutes" if duration_min < 60
               else f"+ {duration_min // 60} hours" if duration_min < 1440
               else f"+ {duration_min // 1440} days")
    e = discord.Embed(title="⏳ Member Timed Out", color=discord.Color.orange(), timestamp=datetime.datetime.utcnow())
    e.add_field(name="👤 User Info", value=_user_block(target), inline=False)
    e.add_field(name="🛠️ Moderator", value=_mod_block(mod), inline=False)
    e.add_field(name="⏱️ Duration", value=_cb(dur_str), inline=True)
    e.add_field(name="📝 Reason", value=_cb(reason), inline=True)
    if target.display_avatar:
        e.set_thumbnail(url=target.display_avatar.url)
    return e


def kick_embed(target, mod, reason: str, dm_ok: bool) -> discord.Embed:
    e = discord.Embed(title="👢 Member Kicked", color=discord.Color.red(), timestamp=datetime.datetime.utcnow())
    e.add_field(name="👤 User Info", value=_user_block(target), inline=False)
    e.add_field(name="🛠️ Moderator", value=_mod_block(mod), inline=False)
    e.add_field(name="📝 Reason", value=_cb(reason), inline=True)
    e.add_field(name="📩 DM Status", value=_cb("Delivered" if dm_ok else "Failed"), inline=True)
    if target.display_avatar:
        e.set_thumbnail(url=target.display_avatar.url)
    return e


def ban_embed(target, mod, reason: str, dm_ok: bool) -> discord.Embed:
    e = discord.Embed(title="🔨 Member Banned", color=discord.Color.dark_red(), timestamp=datetime.datetime.utcnow())
    e.add_field(name="👤 User Info", value=_user_block(target), inline=False)
    e.add_field(name="🛠️ Moderator", value=_mod_block(mod), inline=False)
    e.add_field(name="📝 Reason", value=_cb(reason), inline=True)
    e.add_field(name="📩 DM Status", value=_cb("Delivered" if dm_ok else "Failed"), inline=True)
    if target.display_avatar:
        e.set_thumbnail(url=target.display_avatar.url)
    return e


# ── TICKET SYSTEM ─────────────────────────────────────────────────────────────
class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim Ticket", emoji="🙋", style=discord.ButtonStyle.primary, custom_id="ticket_claim_btn")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name}"
        await interaction.response.edit_message(view=self)
        await interaction.channel.send(f"🙋 This ticket has been claimed by {interaction.user.mention}!")

    @discord.ui.button(label="Lock Ticket", emoji="🔒", style=discord.ButtonStyle.secondary, custom_id="ticket_lock_btn")
    async def lock_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        match = re.search(r"\((\d+)\)$", interaction.channel.topic or "")
        creator = interaction.guild.get_member(int(match.group(1))) if match else None
        if creator:
            await interaction.channel.set_permissions(creator, send_messages=False)
        await interaction.response.send_message(
            embed=discord.Embed(title="🔒 Ticket Locked",
                                 description=f"This ticket has been locked by {interaction.user.mention}.",
                                 color=discord.Color.orange()))

    @discord.ui.button(label="Unlock Ticket", emoji="🔓", style=discord.ButtonStyle.success, custom_id="ticket_unlock_btn")
    async def unlock_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        match = re.search(r"\((\d+)\)$", interaction.channel.topic or "")
        creator = interaction.guild.get_member(int(match.group(1))) if match else None
        if creator:
            await interaction.channel.set_permissions(
                creator, view_channel=True, send_messages=True,
                read_message_history=True, attach_files=True, embed_links=True)
        await interaction.response.send_message(
            embed=discord.Embed(title="🔓 Ticket Unlocked",
                                 description=f"This ticket has been unlocked by {interaction.user.mention}.",
                                 color=discord.Color.green()))

    @discord.ui.button(label="Close Ticket", emoji="⛔", style=discord.ButtonStyle.danger, custom_id="ticket_close_btn")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=discord.Embed(title="⛔ Closing Ticket",
                                 description="This ticket will be **deleted in 5 seconds**...",
                                 color=discord.Color.red()))
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete()
        except Exception:
            pass


class DynamicTicketView(discord.ui.View):
    def __init__(self, options: list):
        super().__init__(timeout=None)
        select_options = []
        for i, opt in enumerate(options):
            lbl = opt.strip()[:100]
            val = re.sub(r"[^a-z0-9\-]", "", opt.lower().strip().replace(" ", "-"))[:100] or f"option-{i}"
            has_emoji = any(unicodedata.category(ch) in ("So", "Sm") or 0x1F000 <= ord(ch) <= 0x1FFFF for ch in opt)
            emoji = None if has_emoji else OPTION_EMOJIS[i % len(OPTION_EMOJIS)]
            select_options.append(discord.SelectOption(label=lbl, value=val, emoji=emoji))

        select = discord.ui.Select(placeholder="📂 Select a category...", min_values=1, max_values=1,
                                    options=select_options, custom_id="dynamic_ticket_select")
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        # Direct inline check — runs synchronously as the first statement in
        # this callback, so there's no race against anything else.
        if not get_bot_active():
            return await interaction.response.send_message(
                "❌ System is currently turned OFF from maintenance mode.", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        user = interaction.user
        val = interaction.data["values"][0]
        lbl = val.replace("-", " ").title()

        cat = discord.utils.get(guild.categories, name=SUPPORT_CATEGORY_NAME)
        if not cat:
            ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
            for rname in STAFF_ROLE_NAMES:
                sr = discord.utils.get(guild.roles, name=rname)
                if sr:
                    ow[sr] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
            cat = await guild.create_category(name=SUPPORT_CATEGORY_NAME, overwrites=ow)

        ch_name = f"🎫┃{val[:28]}-{re.sub(r'[^a-z0-9-]', '', user.name.lower().replace(' ', '-'))[:20]}"
        existing = discord.utils.get(guild.text_channels, name=ch_name)
        if existing:
            return await interaction.followup.send(f"⚠️ You already have an open ticket: {existing.mention}", ephemeral=True)

        ow = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                               read_message_history=True, attach_files=True, embed_links=True),
        }
        for rname in STAFF_ROLE_NAMES:
            sr = discord.utils.get(guild.roles, name=rname)
            if sr:
                ow[sr] = discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                      read_message_history=True, manage_messages=True,
                                                      manage_channels=True)

        ch = await guild.create_text_channel(name=ch_name, category=cat, overwrites=ow,
                                              topic=f"Ticket | {lbl} | {user} ({user.id})")
        pings = " ".join(discord.utils.get(guild.roles, name=rn).mention
                          for rn in STAFF_ROLE_NAMES if discord.utils.get(guild.roles, name=rn))

        em = discord.Embed(title=f"🎫 Ticket — {lbl}",
                            description=f"Welcome {user.mention}! Staff will be with you shortly.\n\nDescribe your issue in detail.",
                            color=discord.Color.blurple())
        em.add_field(name="🛠️ Staff Actions", value="🙋 **Claim** · 🔒 **Lock** · 🔓 **Unlock** · ⛔ **Close**", inline=False)
        await ch.send(content=f"{user.mention} {pings}", embed=em, view=TicketControlView())
        await interaction.followup.send(f"✅ Your ticket is open: {ch.mention}", ephemeral=True)


# ── BOT CLASS ─────────────────────────────────────────────────────────────────
class MasterBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Re-register persistent views so ticket buttons/selects survive restarts.
        self.add_view(TicketControlView())
        panels = _load_json(PANELS_FILE)
        for key, opts in panels.items():
            self.add_view(DynamicTicketView(opts))
        logger.info("Persistent views hooked (%d ticket panel(s)).", len(panels))


bot = MasterBot()


# ── GLOBAL INTERACTION GATE (component interactions, not slash commands) ─────
# Slash commands are gated individually below via app_commands.check, which is
# race-free against the command tree's own dispatch. on_interaction here
# covers anything routed outside that path; ticket components also carry
# their own inline check (see DynamicTicketView._on_select) since that check
# runs synchronously inside the same callback and can't race itself.
@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.application_command:
        return  # handled by maintenance_check() on each command

    if not get_bot_active():
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "🛑 **System Maintenance:** The bot is currently OFF.", ephemeral=True
                )
        except Exception:
            pass


def maintenance_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not get_bot_active():
            await interaction.response.send_message(
                "🛑 **System Maintenance:** The bot core is currently OFF. Use `/maintenance` to turn it back on.",
                ephemeral=True,
            )
            return False
        return True

    return app_commands.check(predicate)


# ── ERROR HANDLING ────────────────────────────────────────────────────────────
@bot.tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        logger.info("Blocked command from %s: %s", interaction.user, error)
        return

    try:
        append_activity_entry(_make_activity_entry(interaction, status="error", detail=str(error)))
    except Exception:
        pass

    logger.exception("Unhandled app command error", exc_info=error)
    msg = f"❌ Something went wrong running that command: `{str(error)[:200]}`"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        logger.warning("Could not deliver error message — interaction likely expired.")


@bot.event
async def on_error(event_method, *args, **kwargs):
    logger.exception("Unhandled error in event handler: %s", event_method)


# ── LIFECYCLE ─────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    logger.info("⚡ Logged in as %s (ID: %s)", bot.user, bot.user.id)
    try:
        synced = await bot.tree.sync()
        logger.info("Synced %d application command(s).", len(synced))
    except Exception:
        logger.exception("Slash command sync failed.")

    cfg_data = _load_json(_CONFIG_FILE)
    for gid, data in cfg_data.items():
        nick = data.get("nickname")
        if not nick:
            continue
        guild = bot.get_guild(int(gid))
        if guild:
            try:
                await guild.me.edit(nick=nick)
            except Exception:
                pass


@bot.event
async def on_member_join(member: discord.Member):
    if not get_bot_active():
        return
    guild = member.guild
    role = discord.utils.get(guild.roles, name=GAMER_ROLE_NAME)
    if role:
        try:
            await member.add_roles(role)
        except Exception:
            pass
    ch = discord.utils.get(guild.text_channels, name=WELCOME_CHANNEL_NAME) or next(
        (c for c in guild.text_channels if "main-chat" in c.name or "general" in c.name), None)
    if ch:
        em = discord.Embed(title=f"👋 Welcome to {guild.name}!",
                            description=f"Hey {member.mention}, welcome! 🎉\n\n📜 Read rules in 📜┃rules\n🎮 Jump in at 🏹┃mc-chat",
                            color=discord.Color.green())
        if member.display_avatar:
            em.set_thumbnail(url=member.display_avatar.url)
        em.add_field(name="🏆 Member Count", value=f"**#{guild.member_count}**")
        em.add_field(name="📅 Account Created", value=discord.utils.format_dt(member.created_at, "D"))
        await ch.send(embed=em)


# ── COMMANDS: SETUP ───────────────────────────────────────────────────────────
@bot.tree.command(name="setup", description="Rebuild the complete professional gaming server setup from scratch.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@maintenance_check()
async def slash_setup(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild = interaction.guild
    bot_roles = {r.id for r in guild.me.roles}

    for channel in list(guild.channels):
        try:
            await channel.delete()
        except Exception:
            pass
    for role in list(guild.roles):
        if not role.is_default() and role.id not in bot_roles:
            try:
                await role.delete()
            except Exception:
                pass

    created_roles = {}
    for r_cfg in reversed(SERVER_LAYOUT_DATA["roles"]):
        perms = (discord.Permissions.all() if r_cfg.get("permissions") == "administrator"
                 else discord.Permissions(manage_guild=True, manage_roles=True) if r_cfg.get("permissions") == "manage_server"
                 else discord.Permissions(kick_members=True, ban_members=True, moderate_members=True) if r_cfg.get("permissions") == "moderate"
                 else discord.Permissions(view_channel=True, send_messages=True))
        r = await guild.create_role(name=r_cfg["name"], color=discord.Color(r_cfg["color"]),
                                     hoist=r_cfg.get("hoist", True), permissions=perms)
        created_roles[r_cfg["name"]] = r

    everyone = guild.default_role
    staff_objs = [created_roles[n] for n in STAFF_ROLE_NAMES if n in created_roles]

    for cat_cfg in SERVER_LAYOUT_DATA["categories"]:
        ow = {everyone: discord.PermissionOverwrite(view_channel=False)} if cat_cfg.get("staff_only") else {}
        if cat_cfg.get("staff_only"):
            for so in staff_objs:
                ow[so] = discord.PermissionOverwrite(view_channel=True, send_messages=True, connect=True)
        cat = await guild.create_category(name=cat_cfg["name"], overwrites=ow)

        for ch_cfg in cat_cfg["channels"]:
            if ch_cfg.get("type") == "voice":
                await guild.create_voice_channel(name=ch_cfg["name"], category=cat,
                                                  user_limit=ch_cfg.get("user_limit", 0))
            else:
                ch_ow = {everyone: discord.PermissionOverwrite(send_messages=False)} if ch_cfg.get("readonly") else {}
                if ch_cfg.get("readonly"):
                    for so in staff_objs:
                        ch_ow[so] = discord.PermissionOverwrite(send_messages=True)
                await guild.create_text_channel(name=ch_cfg["name"], category=cat,
                                                 topic=ch_cfg.get("topic", ""), overwrites=ch_ow)

    await interaction.followup.send("✅ Infrastructure compiled and setup complete!", ephemeral=True)


# ── COMMANDS: TICKETS ─────────────────────────────────────────────────────────
@bot.tree.command(name="setup_ticket", description="Deploy a custom drop-down ticketing pane onto a channel.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@maintenance_check()
async def slash_setup_ticket(interaction: discord.Interaction, channel: discord.TextChannel,
                              embed_title: str, embed_description: str, options_comma_separated: str):
    opts = [o.strip() for o in options_comma_separated.split(",") if o.strip()]
    if not opts or len(opts) > 25:
        return await interaction.response.send_message("❌ Configuration constraints failed.", ephemeral=True)

    em = discord.Embed(title=embed_title, description=embed_description, color=discord.Color.blurple())
    em.add_field(name="📂 Categories", value="\n".join(f"• **{o}**" for o in opts), inline=False)
    view = DynamicTicketView(opts)
    msg = await channel.send(embed=em, view=view)

    panels = _load_json(PANELS_FILE)
    panels[f"{interaction.guild.id}:{msg.id}"] = opts
    _save_json(PANELS_FILE, panels)

    bot.add_view(view, message_id=msg.id)
    await interaction.response.send_message("✅ Dynamic dropdown ticket terminal launched!", ephemeral=True)


# ── COMMANDS: MODERATION ──────────────────────────────────────────────────────
@bot.tree.command(name="warn", description="Issue an accountability notice to a member.")
@app_commands.default_permissions(moderate_members=True)
@app_commands.guild_only()
@maintenance_check()
async def slash_warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    if member.bot:
        return await interaction.response.send_message("❌ Target identification error: Bots bypass protocols.", ephemeral=True)
    em = warn_embed(member, interaction.user, reason)
    try:
        await member.send(embed=discord.Embed(title=f"⚠️ Warning notification: {interaction.guild.name}",
                                               description=f"Reason: {reason}", color=discord.Color.yellow()))
    except Exception:
        pass
    await interaction.response.send_message(embed=em)

    cfg_ch_id = _load_json(_MODLOGS_FILE).get(str(interaction.guild.id))
    log_ch = interaction.guild.get_channel(cfg_ch_id) if cfg_ch_id else discord.utils.get(interaction.guild.text_channels, name=STAFF_LOG_CHANNEL)
    if log_ch:
        await log_ch.send(embed=em)
    _record_action(interaction.guild.id, member, interaction.user, "warn", reason=reason)


@bot.tree.command(name="timeout", description="Temporarily restrict a user's typing access.")
@app_commands.default_permissions(moderate_members=True)
@app_commands.guild_only()
@maintenance_check()
async def slash_timeout(interaction: discord.Interaction, member: discord.Member,
                         duration_minutes: app_commands.Range[int, 1, 40320], reason: str = "No reason stated"):
    if member.bot or member.top_role >= interaction.user.top_role:
        return await interaction.response.send_message("❌ Action aborted: Hierarchy conflict.", ephemeral=True)
    until = datetime.datetime.utcnow() + datetime.timedelta(minutes=duration_minutes)
    await member.timeout(until, reason=reason)
    em = timeout_embed(member, interaction.user, duration_minutes, reason)
    await interaction.response.send_message(embed=em)

    cfg_ch_id = _load_json(_MODLOGS_FILE).get(str(interaction.guild.id))
    log_ch = interaction.guild.get_channel(cfg_ch_id) if cfg_ch_id else discord.utils.get(interaction.guild.text_channels, name=STAFF_LOG_CHANNEL)
    if log_ch:
        await log_ch.send(embed=em)
    _record_action(interaction.guild.id, member, interaction.user, "timeout", duration_minutes=duration_minutes, reason=reason)


@bot.tree.command(name="kick", description="Eject a problematic account from the matrix.")
@app_commands.default_permissions(kick_members=True)
@app_commands.guild_only()
@maintenance_check()
async def slash_kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason stated"):
    if member.bot or member.top_role >= interaction.user.top_role:
        return await interaction.response.send_message("❌ Exception: Role parity prevents ejection.", ephemeral=True)
    try:
        await member.send(embed=discord.Embed(title=f"👢 Account expulsion alert: {interaction.guild.name}",
                                               description=f"Reason: {reason}", color=discord.Color.red()))
    except Exception:
        pass
    em = kick_embed(member, interaction.user, reason, True)
    await member.kick(reason=reason)
    await interaction.response.send_message(embed=em)
    _record_action(interaction.guild.id, member, interaction.user, "kick", reason=reason)


@bot.tree.command(name="ban", description="Blacklist a profile permanently from returning.")
@app_commands.default_permissions(ban_members=True)
@app_commands.guild_only()
@maintenance_check()
async def slash_ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason stated"):
    if member.bot or member.top_role >= interaction.user.top_role:
        return await interaction.response.send_message("❌ Authorization denied: Upper bound hierarchy restriction.", ephemeral=True)
    try:
        await member.send(embed=discord.Embed(title=f"🔨 Core ban enforcement: {interaction.guild.name}",
                                               description=f"Reason: {reason}", color=discord.Color.dark_red()))
    except Exception:
        pass
    em = ban_embed(member, interaction.user, reason, True)
    await member.ban(reason=reason, delete_message_days=0)
    await interaction.response.send_message(embed=em)

    cfg_ch_id = _load_json(_MODLOGS_FILE).get(str(interaction.guild.id))
    log_ch = interaction.guild.get_channel(cfg_ch_id) if cfg_ch_id else discord.utils.get(interaction.guild.text_channels, name=STAFF_LOG_CHANNEL)
    if log_ch:
        await log_ch.send(embed=em)
    _record_action(interaction.guild.id, member, interaction.user, "ban", reason=reason)


@bot.tree.command(name="role", description="Toggle key priority roles onto user profiles.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@app_commands.choices(role_name=[app_commands.Choice(name=n, value=n) for n in STAFF_ROLE_NAMES + [GAMER_ROLE_NAME]])
@maintenance_check()
async def slash_role(interaction: discord.Interaction, member: discord.Member, role_name: str):
    r = discord.utils.get(interaction.guild.roles, name=role_name)
    if not r:
        return await interaction.response.send_message("❌ Target entity missing. Run `/setup` first.", ephemeral=True)

    if r in member.roles:
        await member.remove_roles(r)
        act, col, emj = "removed from", discord.Color.red(), "➖"
    else:
        await member.add_roles(r)
        act, col, emj = "assigned to", discord.Color.green(), "➕"

    em = discord.Embed(title=f"{emj} Role Hierarchy Shift", description=f"**{r.name}** was {act} {member.mention}.", color=col)
    await interaction.response.send_message(embed=em, ephemeral=True)


@bot.tree.command(name="modlogs", description="Audit past behavioral records on an account.")
@app_commands.default_permissions(moderate_members=True)
@app_commands.guild_only()
@maintenance_check()
async def slash_modlogs(interaction: discord.Interaction, member: discord.Member):
    records = _get_history(interaction.guild.id, member.id)
    em = discord.Embed(title=f"📋 Historical Audit — {member.display_name}", color=discord.Color.blurple())
    if not records:
        em.description = "✅ Impeccable standing: Clean database log."
        return await interaction.response.send_message(embed=em, ephemeral=True)

    lines = []
    for i, r in enumerate(records[-10:], start=max(1, len(records) - 9)):
        lines.append(f"`#{i}` {ACTION_EMOJI.get(r['action'], '❓')} **{r['action'].upper()}** — {r['timestamp'][:10]} by `{r['mod_tag']}`\n> Details: {r.get('reason', 'None')}")
    em.description = "\n\n".join(lines)
    await interaction.response.send_message(embed=em, ephemeral=True)


@bot.tree.command(name="bot_config", description="Modify system environment names natively.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@maintenance_check()
async def slash_bot_config(interaction: discord.Interaction, nickname: str = ""):
    new_nick = nickname.strip() or None
    await interaction.guild.me.edit(nick=new_nick)
    data = _load_json(_CONFIG_FILE)
    if new_nick:
        data.setdefault(str(interaction.guild.id), {})["nickname"] = new_nick
    else:
        data.get(str(interaction.guild.id), {}).pop("nickname", None)
    _save_json(_CONFIG_FILE, data)
    await interaction.response.send_message("⚙️ Internal variable synchronized!", ephemeral=True)


@bot.tree.command(name="help", description="Render the system core operational command ledger.")
async def slash_help(interaction: discord.Interaction):
    em = discord.Embed(
        title="🎮 Core Command Index Terminal",
        description="`/setup` — Recompile full grid configuration.\n`/setup_ticket` — Mount a dropdown support anchor.\n`/role` — Direct priority role injection.\n`/warn` · `/timeout` · `/kick` · `/ban` — Protocol enforcement units.\n`/modlogs` — Check profile baseline behaviors.\n`/maintenance` — Flip the master ON/OFF switch.",
        color=discord.Color.blurple())
    await interaction.response.send_message(embed=em, ephemeral=True)


# ── COMMANDS: MAINTENANCE TOGGLE ──────────────────────────────────────────────
@bot.tree.command(name="maintenance", description="Toggle the bot's master ON/OFF maintenance switch.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
# Intentionally NOT gated by maintenance_check() — otherwise once the bot is
# OFF, nobody could ever turn it back ON.
async def slash_maintenance(interaction: discord.Interaction):
    new_state = not get_bot_active()
    set_bot_active(new_state)
    status = "🟢 ON" if new_state else "🔴 OFF"
    await interaction.response.send_message(f"System status is now **{status}**.", ephemeral=True)


# ── ENTRYPOINT ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        logger.error("DISCORD_TOKEN environment variable is not set. Exiting.")
        sys.exit(1)

    try:
        bot.run(TOKEN)
    except Exception:
        logger.exception("Fatal error while running the bot.")
        sys.exit(1)
