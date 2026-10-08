"""Group auction game with photo cards and inline controls.

This module keeps the existing commands compatible while adding a richer,
photo-first auction UI. Use only non-explicit images for players/participants.
"""
import html
import os
import sqlite3
import time
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext

from src.common.settings.env import ADMIN_CHAT_ID

DB_PATH = os.getenv("AUCTION_GAME_DB", str(Path("data_base") / "auction_game.sqlite3"))
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
START_COINS = int(os.getenv("START_COINS", "10000"))
BOT_CREDIT = os.getenv("BOT_CREDIT", "AuctionBot")


def connect():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS players (
          user_id INTEGER PRIMARY KEY, username TEXT NOT NULL, coins INTEGER NOT NULL DEFAULT 10000
        );
        CREATE TABLE IF NOT EXISTS actresses (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, base_price INTEGER NOT NULL,
          photo TEXT, sold_to INTEGER, sold_price INTEGER
        );
        CREATE TABLE IF NOT EXISTS game_state (
          chat_id INTEGER PRIMARY KEY, active INTEGER NOT NULL DEFAULT 0,
          current_actress INTEGER, high_bid INTEGER NOT NULL DEFAULT 0,
          high_bidder INTEGER, started_at INTEGER,
          auction_message_id INTEGER, announcement_message_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS teams (
          chat_id INTEGER NOT NULL, actress_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
          price INTEGER NOT NULL, PRIMARY KEY(chat_id, actress_id)
        );
        """
    )
    # Safe migration for databases created by older versions.
    for col, typ in (("auction_message_id", "INTEGER"), ("announcement_message_id", "INTEGER")):
        try:
            con.execute(f"ALTER TABLE game_state ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass
    return con


def ensure_player(con, user):
    username = getattr(user, "username", None) or getattr(user, "full_name", None) or f"user_{user.id}"
    con.execute(
        "INSERT OR IGNORE INTO players(user_id, username, coins) VALUES (?, ?, ?)",
        (user.id, username, START_COINS),
    )
    con.execute("UPDATE players SET username=? WHERE user_id=?", (username, user.id))
    return con.execute("SELECT * FROM players WHERE user_id=?", (user.id,)).fetchone()


def is_admin(update, context):
    user = update.effective_user
    chat = update.effective_chat
    configured = str(ADMIN_CHAT_ID or "").strip()
    if configured and chat and str(chat.id) == configured:
        return True
    try:
        member = context.bot.get_chat_member(chat.id, user.id)
        return member.status in ("creator", "administrator")
    except Exception:
        return False


def _safe(value):
    return html.escape(str(value or ""))


def _group_name(chat):
    return f"@{chat.username}" if getattr(chat, "username", None) else (chat.title or str(chat.id))


def _auction_markup(chat_id, item_id):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💰 +100", callback_data=f"auction_bid:{chat_id}:{item_id}:100"),
                InlineKeyboardButton("💰 +500", callback_data=f"auction_bid:{chat_id}:{item_id}:500"),
            ],
            [
                InlineKeyboardButton("👤 My Balance", callback_data=f"auction_balance:{chat_id}"),
                InlineKeyboardButton("🏆 My Team", callback_data=f"auction_team:{chat_id}"),
            ],
            [InlineKeyboardButton("📊 Leaderboard", callback_data=f"auction_leaderboard:{chat_id}")],
        ]
    )


def _announcement_markup():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📖 GUIDE", callback_data="auction_guide")],
            [InlineKeyboardButton("👤 REGISTER / JOIN", callback_data="auction_register")],
        ]
    )


def _announcement_text(chat):
    return (
        "┏━━━━━━━━━━━━━━━━━━━━┓\n"
        "🚀 <b>AUCTION STARTED</b> 🚀\n"
        "┗━━━━━━━━━━━━━━━━━━━━┛\n\n"
        "🎮 <b>Game:</b> AUCTION\n"
        f"👥 <b>Group:</b> {_safe(_group_name(chat))}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔥 <b>STATUS: LIVE NOW</b>\n"
        "⏳ Auction has officially begun!\n\n"
        "🎯 <b>Objective:</b>\n"
        "➤ Bid wisely and build your dream team.\n"
        "➤ Compete for the top rank.\n"
        "➤ Keep your balance under control.\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ Play smart. Play fast. Win big.\n"
        "🏆 Good luck, warriors!\n\n"
        "🚀 <b>LET THE GAME BEGIN!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 Powered by @{_safe(BOT_CREDIT.lstrip('@'))}"
    )


def _auction_caption(chat, item, state, bidder_name=None):
    bidder = _safe(bidder_name or "No bid yet")
    return (
        "┏━━━━━━━━━━━━━━━━━━━━┓\n"
        f"🎭 <b>{_safe(item['name']).upper()}</b>\n"
        "┗━━━━━━━━━━━━━━━━━━━━┛\n\n"
        f"🟢 <b>STATUS:</b> LIVE\n"
        f"🏆 <b>Rank:</b> #{item['id']}\n"
        f"💰 <b>Base Price:</b> {item['base_price']}\n"
        f"🔥 <b>Current Bid:</b> {state['high_bid']}\n"
        f"👑 <b>Highest Bidder:</b> {bidder}\n\n"
        "📌 <b>How to play</b>\n"
        "➤ Use +100 or +500 to bid.\n"
        "➤ Highest valid bid wins when the admin ends the round.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 <i>Powered by auction game</i>"
    )


def _send_photo_or_text(context, chat_id, photo, caption, reply_markup=None):
    if photo:
        try:
            return context.bot.send_photo(
                chat_id=chat_id, photo=photo, caption=caption,
                parse_mode="HTML", reply_markup=reply_markup
            )
        except Exception:
            pass
    return context.bot.send_message(
        chat_id=chat_id, text=caption, parse_mode="HTML", reply_markup=reply_markup
    )


def auction_start(update: Update, context: CallbackContext):
    chat = update.effective_chat
    if not chat:
        return
    if not is_admin(update, context):
        update.effective_message.reply_text("⛔ Sirf group admin auction start kar sakta hai.")
        return

    con = connect()
    try:
        state = con.execute("SELECT * FROM game_state WHERE chat_id=?", (chat.id,)).fetchone()
        if state and state["active"]:
            update.effective_message.reply_text("⚠️ Auction already LIVE hai.")
            return
        item = con.execute(
            "SELECT * FROM actresses WHERE sold_to IS NULL ORDER BY id LIMIT 1"
        ).fetchone()
        if not item:
            update.effective_message.reply_text(
                "📭 Koi player available nahi hai.\n\n"
                "Use:\n/addplayer Name | BasePrice | PhotoURL\n\n"
                "Ya kisi photo ko reply karke:\n/addplayer Name | BasePrice"
            )
            return

        now = int(time.time())
        con.execute(
            """INSERT OR REPLACE INTO game_state
            (chat_id,active,current_actress,high_bid,high_bidder,started_at,auction_message_id,announcement_message_id)
            VALUES(?,1,?,?,NULL,?,NULL,NULL)""",
            (chat.id, item["id"], item["base_price"], now),
        )
        con.commit()

        announcement = _send_photo_or_text(
            context, chat.id, item["photo"], _announcement_text(chat), _announcement_markup()
        )
        auction_msg = _send_photo_or_text(
            context,
            chat.id,
            item["photo"],
            _auction_caption(chat, item, con.execute("SELECT * FROM game_state WHERE chat_id=?", (chat.id,)).fetchone()),
            _auction_markup(chat.id, item["id"]),
        )
        con.execute(
            "UPDATE game_state SET auction_message_id=?, announcement_message_id=? WHERE chat_id=?",
            (getattr(auction_msg, "message_id", None), getattr(announcement, "message_id", None), chat.id),
        )
        con.commit()
    finally:
        con.close()


def add_actress(update: Update, context: CallbackContext):
    if not is_admin(update, context):
        update.effective_message.reply_text("⛔ Sirf group admin ye command use kar sakta hai.")
        return
    raw = " ".join(context.args).strip()
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 2 or not parts[0]:
        update.effective_message.reply_text(
            "Format:\n/addplayer Name | BasePrice | PhotoURL(optional)\n\n"
            "Photo ke liye kisi image ko reply karke bhi use kar sakte ho."
        )
        return
    try:
        price = int(parts[1])
        if price < 1:
            raise ValueError
    except ValueError:
        update.effective_message.reply_text("Base price positive number hona chahiye.")
        return

    photo = parts[2] if len(parts) > 2 and parts[2] else None
    reply = update.effective_message.reply_to_message
    if not photo and reply and getattr(reply, "photo", None):
        photo = reply.photo[-1].file_id

    con = connect()
    try:
        con.execute(
            "INSERT INTO actresses(name,base_price,photo) VALUES(?,?,?)",
            (parts[0], price, photo),
        )
        con.commit()
    finally:
        con.close()
    update.effective_message.reply_text(
        f"✅ Player added: {parts[0]} — starting price {price} coins.\n"
        + ("🖼️ Photo saved." if photo else "⚠️ No photo saved.")
    )


# Backward-compatible command name.
add_player = add_actress


def bid(update: Update, context: CallbackContext):
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not context.args:
        msg.reply_text("Use: /bid 500")
        return
    try:
        amount = int(context.args[0])
        if amount < 1:
            raise ValueError
    except ValueError:
        msg.reply_text("❌ Bid positive whole number hona chahiye.")
        return

    con = connect()
    try:
        player = ensure_player(con, user)
        state = con.execute(
            "SELECT * FROM game_state WHERE chat_id=? AND active=1", (chat.id,)
        ).fetchone()
        if not state:
            con.commit(); msg.reply_text("⏸️ Abhi auction live nahi hai."); return
        if state["high_bidder"] == user.id:
            msg.reply_text("⚠️ Tum already highest bidder ho."); return
        minimum = int(state["high_bid"]) + 1
        if amount < minimum:
            msg.reply_text(f"📈 Minimum next bid {minimum} coins hai."); return
        if amount > player["coins"]:
            msg.reply_text(f"💸 Tumhare paas {player['coins']} coins hain."); return
        item = con.execute("SELECT * FROM actresses WHERE id=?", (state["current_actress"],)).fetchone()
        con.execute(
            "UPDATE game_state SET high_bid=?, high_bidder=? WHERE chat_id=?",
            (amount, user.id, chat.id),
        )
        con.commit()
        new_state = con.execute("SELECT * FROM game_state WHERE chat_id=?", (chat.id,)).fetchone()
        _refresh_auction_message(context, chat.id, new_state, item, user.first_name or player["username"])
        msg.reply_text(f"🔥 BID PLACED: {amount} coins — {item['name']}")
    finally:
        con.close()


def _refresh_auction_message(context, chat_id, state, item, bidder_name=None):
    message_id = state["auction_message_id"]
    if not message_id:
        return
    caption = _auction_caption(type("Chat", (), {"username": None, "title": str(chat_id)})(), item, state, bidder_name)
    try:
        if item["photo"]:
            context.bot.edit_message_caption(
                chat_id=chat_id, message_id=message_id, caption=caption,
                parse_mode="HTML", reply_markup=_auction_markup(chat_id, item["id"]),
            )
        else:
            context.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=caption,
                parse_mode="HTML", reply_markup=_auction_markup(chat_id, item["id"]),
            )
    except Exception:
        pass


def balance(update: Update, context: CallbackContext):
    con = connect()
    try:
        p = ensure_player(con, update.effective_user); con.commit()
        update.effective_message.reply_text(
            f"💰 {update.effective_user.first_name}, tumhara balance: {p['coins']} coins"
        )
    finally:
        con.close()


def team(update: Update, context: CallbackContext):
    con = connect()
    try:
        rows = con.execute(
            "SELECT a.name,t.price FROM teams t JOIN actresses a ON a.id=t.actress_id "
            "WHERE t.chat_id=? AND t.user_id=? ORDER BY a.name",
            (update.effective_chat.id, update.effective_user.id),
        ).fetchall()
        if not rows:
            text = "🎭 Tumhari team abhi empty hai."
        else:
            text = "🏆 <b>Your Dream Team</b>\n\n" + "\n".join(
                f"🎭 {html.escape(r['name'])} — {r['price']} coins" for r in rows
            )
        update.effective_message.reply_text(text, parse_mode="HTML")
    finally:
        con.close()


def leaderboard(update: Update, context: CallbackContext):
    con = connect()
    try:
        rows = con.execute(
            "SELECT p.username,p.coins,COUNT(t.actress_id) AS count FROM players p "
            "LEFT JOIN teams t ON t.user_id=p.user_id AND t.chat_id=? "
            "GROUP BY p.user_id ORDER BY count DESC,p.coins DESC LIMIT 10",
            (update.effective_chat.id,),
        ).fetchall()
        text = "🏆 <b>AUCTION LEADERBOARD</b>\n\n" + "\n".join(
            f"{i}. {html.escape(r['username'])} — {r['count']} players · {r['coins']} coins"
            for i, r in enumerate(rows, 1)
        ) or "No players yet."
        update.effective_message.reply_text(text, parse_mode="HTML")
    finally:
        con.close()


def end_auction(update: Update, context: CallbackContext):
    chat = update.effective_chat
    if not is_admin(update, context):
        update.effective_message.reply_text("⛔ Sirf group admin auction end kar sakta hai.")
        return
    con = connect()
    try:
        state = con.execute(
            "SELECT * FROM game_state WHERE chat_id=? AND active=1", (chat.id,)
        ).fetchone()
        if not state:
            update.effective_message.reply_text("Auction live nahi hai."); return
        item = con.execute("SELECT * FROM actresses WHERE id=?", (state["current_actress"],)).fetchone()
        if state["high_bidder"] is None:
            con.execute("UPDATE game_state SET active=0,current_actress=NULL WHERE chat_id=?", (chat.id,))
            con.commit()
            update.effective_message.reply_text(f"🔨 Auction ended. {item['name']} unsold raha/rahi.")
            return
        player = con.execute("SELECT * FROM players WHERE user_id=?", (state["high_bidder"],)).fetchone()
        if not player or player["coins"] < state["high_bid"]:
            update.effective_message.reply_text("❌ Winner ke paas enough coins nahi hain."); return
        con.execute("UPDATE players SET coins=coins-? WHERE user_id=?", (state["high_bid"], state["high_bidder"]))
        con.execute("UPDATE actresses SET sold_to=?,sold_price=? WHERE id=?", (state["high_bidder"], state["high_bid"], item["id"]))
        con.execute(
            "INSERT OR REPLACE INTO teams(chat_id,actress_id,user_id,price) VALUES(?,?,?,?)",
            (chat.id, item["id"], state["high_bidder"], state["high_bid"]),
        )
        con.execute("UPDATE game_state SET active=0,current_actress=NULL WHERE chat_id=?", (chat.id,))
        con.commit()
        winner = player["username"]
        update.effective_message.reply_text(
            f"🏆 <b>SOLD!</b>\n\n🎭 {html.escape(item['name'])}\n"
            f"💰 Price: <b>{state['high_bid']}</b> coins\n"
            f"👑 Winner: <b>{html.escape(winner)}</b>", parse_mode="HTML"
        )
    finally:
        con.close()


def give_coins(update: Update, context: CallbackContext):
    if not is_admin(update, context):
        update.effective_message.reply_text("⛔ Sirf group admin ye command use kar sakta hai."); return
    try:
        amount = int(context.args[0])
        if amount < 1: raise ValueError
    except (IndexError, ValueError):
        update.effective_message.reply_text("Use: reply karke /givecoins 1000"); return
    target = update.effective_message.reply_to_message.from_user if update.effective_message.reply_to_message else None
    if not target:
        update.effective_message.reply_text("Coins dene wale user ke message par reply karke /givecoins amount bhejo."); return
    con = connect()
    try:
        ensure_player(con, target)
        con.execute("UPDATE players SET coins=coins+? WHERE user_id=?", (amount, target.id))
        con.commit()
        update.effective_message.reply_text(f"✅ {amount} coins {target.first_name} ko de diye.")
    finally:
        con.close()


def callback_handler(update: Update, context: CallbackContext):
    q = update.callback_query
    if not q:
        return
    q.answer()
    data = q.data or ""
    if data == "auction_guide":
        q.message.reply_text("📖 GUIDE\n\n+100/+500 se bid karo. Highest valid bid winner hota hai. Admin /endauction se round finish karta hai.")
        return
    if data == "auction_register":
        con = connect()
        try:
            ensure_player(con, q.from_user); con.commit()
            p = con.execute("SELECT coins FROM players WHERE user_id=?", (q.from_user.id,)).fetchone()
            q.message.reply_text(f"✅ Registered!\n💰 Starting balance: {p['coins']} coins")
        finally:
            con.close()
        return
    try:
        parts = data.split(":")
        action = parts[0]
        if action == "auction_bid":
            _, chat_id, item_id, inc = parts
            chat_id, item_id, inc = int(chat_id), int(item_id), int(inc)
            con = connect()
            try:
                state = con.execute("SELECT * FROM game_state WHERE chat_id=? AND active=1", (chat_id,)).fetchone()
                item = con.execute("SELECT * FROM actresses WHERE id=?", (item_id,)).fetchone()
                if not state or not item or state["current_actress"] != item_id:
                    q.answer("Auction item ab active nahi hai.", show_alert=True); return
                player = ensure_player(con, q.from_user)
                amount = int(state["high_bid"]) + inc
                if state["high_bidder"] == q.from_user.id:
                    q.answer("Aap already highest bidder ho.", show_alert=True); return
                if amount > player["coins"]:
                    q.answer(f"Balance: {player['coins']} coins", show_alert=True); return
                con.execute("UPDATE game_state SET high_bid=?, high_bidder=? WHERE chat_id=?", (amount, q.from_user.id, chat_id))
                con.commit()
                new_state = con.execute("SELECT * FROM game_state WHERE chat_id=?", (chat_id,)).fetchone()
                _refresh_auction_message(context, chat_id, new_state, item, q.from_user.first_name)
                q.answer(f"Bid placed: {amount}")
            finally:
                con.close()
            return
        if action == "auction_balance":
            con = connect()
            try:
                p = ensure_player(con, q.from_user); con.commit()
                q.answer(f"Balance: {p['coins']} coins", show_alert=True)
            finally:
                con.close()
            return
        if action == "auction_team":
            con = connect()
            try:
                rows = con.execute("SELECT a.name,t.price FROM teams t JOIN actresses a ON a.id=t.actress_id WHERE t.chat_id=? AND t.user_id=?", (int(parts[1]), q.from_user.id)).fetchall()
                text = "🏆 Your Team\n" + ("\n".join(f"• {r['name']} — {r['price']}" for r in rows) if rows else "Empty")
                q.message.reply_text(text)
            finally:
                con.close()
            return
        if action == "auction_leaderboard":
            con = connect()
            try:
                rows = con.execute("SELECT p.username,p.coins,COUNT(t.actress_id) count FROM players p LEFT JOIN teams t ON t.user_id=p.user_id AND t.chat_id=? GROUP BY p.user_id ORDER BY count DESC,p.coins DESC LIMIT 10", (int(parts[1]),)).fetchall()
                q.message.reply_text("🏆 Leaderboard\n\n" + "\n".join(f"{i}. {r['username']} — {r['count']} players · {r['coins']} coins" for i,r in enumerate(rows,1)))
            finally:
                con.close()
            return
    except Exception:
        q.answer("Something went wrong.", show_alert=True)
