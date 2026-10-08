"""Telegram group auction game with persistent player photos.

Safe/general player auction system.  Existing /addactress data is kept for
backward compatibility, while /addplayer is the preferred command.
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


def connect():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS players (
          user_id INTEGER PRIMARY KEY,
          username TEXT NOT NULL,
          coins INTEGER NOT NULL DEFAULT 10000
        );
        CREATE TABLE IF NOT EXISTS actresses (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          base_price INTEGER NOT NULL,
          photo TEXT,
          sold_to INTEGER,
          sold_price INTEGER
        );
        CREATE TABLE IF NOT EXISTS game_state (
          chat_id INTEGER PRIMARY KEY,
          active INTEGER NOT NULL DEFAULT 0,
          current_actress INTEGER,
          high_bid INTEGER NOT NULL DEFAULT 0,
          high_bidder INTEGER,
          started_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS teams (
          chat_id INTEGER NOT NULL,
          actress_id INTEGER NOT NULL,
          user_id INTEGER NOT NULL,
          price INTEGER NOT NULL,
          PRIMARY KEY(chat_id, actress_id)
        );
        """
    )
    return con


def ensure_player(con, user):
    username = user.username or user.full_name or f"user_{user.id}"
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


def _photo_from_reply(message):
    """Return Telegram photo file_id from a replied-to photo, if present."""
    reply = getattr(message, "reply_to_message", None)
    if reply and getattr(reply, "photo", None):
        return reply.photo[-1].file_id
    return None


def _auction_keyboard(item_id):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💰 +100", callback_data=f"auc:bid:{item_id}:100"),
                InlineKeyboardButton("💰 +500", callback_data=f"auc:bid:{item_id}:500"),
                InlineKeyboardButton("💰 +1000", callback_data=f"auc:bid:{item_id}:1000"),
            ],
            [InlineKeyboardButton("📊 Current Auction", callback_data=f"auc:info:{item_id}")],
        ]
    )


def _auction_text(chat, item, high_bid=None, high_bidder=None):
    group_name = f"@{chat.username}" if chat.username else (chat.title or str(chat.id))
    current = int(high_bid if high_bid is not None else item["base_price"])
    bidder = "No bids yet"
    if high_bidder:
        bidder = f"User ID {high_bidder}"
    return (
        "┏━━━━━━━━━━━━━━━━━━━━┓\n"
        "   🔨 <b>AUCTION LIVE</b>\n"
        "┗━━━━━━━━━━━━━━━━━━━━┛\n\n"
        f"🎯 <b>Player:</b> {html.escape(str(item['name']))}\n"
        f"🏷️ <b>Base Price:</b> {item['base_price']} coins\n"
        f"💰 <b>Current Bid:</b> {current} coins\n"
        f"👑 <b>Highest Bidder:</b> {html.escape(bidder)}\n"
        f"👥 <b>Group:</b> {html.escape(group_name)}\n\n"
        "⚡ Place your bid using the buttons or <code>/bid 500</code>.\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )


def _send_item(bot, chat, item, high_bid=None, high_bidder=None):
    text = _auction_text(chat, item, high_bid, high_bidder)
    markup = _auction_keyboard(item["id"])
    photo = item["photo"]
    if photo:
        try:
            bot.send_photo(chat_id=chat.id, photo=photo, caption=text, parse_mode="HTML", reply_markup=markup)
            return True
        except Exception:
            # A stale/invalid URL or file_id should not break the auction.
            pass
    bot.send_message(chat_id=chat.id, text=text, parse_mode="HTML", reply_markup=markup)
    return False


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
            item = con.execute("SELECT * FROM actresses WHERE id=?", (state["current_actress"],)).fetchone()
            if item:
                update.effective_message.reply_text("⚠️ Auction already LIVE hai. Current player ke liye /bid amount use karo.")
            return

        item = con.execute("SELECT * FROM actresses WHERE sold_to IS NULL ORDER BY id LIMIT 1").fetchone()
        if not item:
            update.effective_message.reply_text(
                "📭 Koi player available nahi hai.\n\n"
                "Photo reply karke:\n"
                "/addplayer Name | BasePrice\n\n"
                "Ya:\n/addplayer Name | BasePrice | PhotoURL"
            )
            return

        con.execute(
            "INSERT OR REPLACE INTO game_state(chat_id,active,current_actress,high_bid,high_bidder,started_at) VALUES(?,1,?,?,NULL,?)",
            (chat.id, item["id"], item["base_price"], int(time.time())),
        )
        con.commit()
        context.bot.send_message(
            chat_id=chat.id,
            text=(
                "🚀 <b>AUCTION STARTED</b>\n\n"
                "🎯 First player is now on auction!\n"
                "📸 The saved player photo is shown automatically.\n\n"
                "⚡ Bid fast and build your team."
            ),
            parse_mode="HTML",
        )
        _send_item(context.bot, chat, item, item["base_price"], None)
    finally:
        con.close()


def add_player(update: Update, context: CallbackContext):
    if not is_admin(update, context):
        update.effective_message.reply_text("⛔ Sirf group admin player add kar sakta hai.")
        return

    raw = " ".join(context.args).strip()
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 2 or not parts[0]:
        update.effective_message.reply_text(
            "📌 Format:\n/addplayer Name | BasePrice\n\n"
            "🖼️ Photo ke liye photo ko reply karke command bhejo.\n"
            "Example: /addplayer Player One | 1000\n\n"
            "URL bhi allowed hai:\n/addplayer Player One | 1000 | https://example.com/photo.jpg"
        )
        return

    try:
        price = int(parts[1])
        if price < 1:
            raise ValueError
    except ValueError:
        update.effective_message.reply_text("❌ Base price positive whole number hona chahiye.")
        return

    photo = parts[2] if len(parts) > 2 and parts[2] else None
    reply_photo = _photo_from_reply(update.effective_message)
    if reply_photo:
        photo = reply_photo

    if not photo:
        update.effective_message.reply_text(
            "🖼️ Player photo missing hai.\nPhoto ko reply karke /addplayer Name | BasePrice bhejo."
        )
        return

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
        f"✅ <b>Player Added</b>\n\n🎯 {html.escape(parts[0])}\n💰 Base: {price} coins\n📸 Photo: Saved\n\n"
        "Ab /auction karoge to ye photo group me automatically aayegi.",
        parse_mode="HTML",
    )


def add_actress(update: Update, context: CallbackContext):
    # Backward-compatible alias. The implementation is the same as /addplayer.
    add_player(update, context)


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
        state = con.execute("SELECT * FROM game_state WHERE chat_id=? AND active=1", (chat.id,)).fetchone()
        if not state:
            con.commit()
            msg.reply_text("⏸️ Abhi auction live nahi hai. Admin se /auction start karwao.")
            return
        minimum = int(state["high_bid"]) + 1
        if amount < minimum:
            msg.reply_text(f"📈 Minimum next bid {minimum} coins hai.")
            return
        if amount > player["coins"]:
            msg.reply_text(f"💸 Tumhare paas {player['coins']} coins hain. /balance check karo.")
            return
        if state["high_bidder"] == user.id:
            msg.reply_text("⚠️ Tum already highest bidder ho.")
            return
        item = con.execute("SELECT * FROM actresses WHERE id=?", (state["current_actress"],)).fetchone()
        con.execute("UPDATE game_state SET high_bid=?, high_bidder=? WHERE chat_id=?", (amount, user.id, chat.id))
        con.commit()
        msg.reply_text(
            f"🔥 <b>BID PLACED</b>\n🎯 {html.escape(item['name'])}\n💰 {amount} coins\n"
            f"👑 {html.escape(user.first_name or player['username'])}\n📈 Next bid: {amount + 1}",
            parse_mode="HTML",
        )
    finally:
        con.close()


def auction_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    if not query:
        return
    try:
        query.answer()
    except Exception:
        pass
    data = (query.data or "").split(":")
    if len(data) != 4 or data[0] != "auc":
        return
    action, item_id, extra = data[1], data[2], data[3]
    try:
        item_id = int(item_id)
    except ValueError:
        return
    if action == "info":
        con = connect()
        try:
            state = con.execute("SELECT * FROM game_state WHERE chat_id=? AND active=1", (query.message.chat.id,)).fetchone()
            if not state or state["current_actress"] != item_id:
                query.answer("Auction item no longer active.", show_alert=True)
                return
            query.answer(f"Current bid: {state['high_bid']} coins")
        finally:
            con.close()
        return
    if action != "bid":
        return
    try:
        increment = int(extra)
    except ValueError:
        return
    user = query.from_user
    chat_id = query.message.chat.id
    con = connect()
    try:
        state = con.execute("SELECT * FROM game_state WHERE chat_id=? AND active=1", (chat_id,)).fetchone()
        if not state or state["current_actress"] != item_id:
            query.answer("Auction is not active.", show_alert=True)
            return
        player = ensure_player(con, user)
        amount = int(state["high_bid"]) + increment
        if amount > player["coins"]:
            query.answer(f"You have only {player['coins']} coins.", show_alert=True)
            return
        if state["high_bidder"] == user.id:
            query.answer("You are already the highest bidder.", show_alert=True)
            return
        con.execute("UPDATE game_state SET high_bid=?, high_bidder=? WHERE chat_id=?", (amount, user.id, chat_id))
        con.commit()
        item = con.execute("SELECT * FROM actresses WHERE id=?", (item_id,)).fetchone()
        try:
            query.answer(f"Bid placed: {amount} coins")
            query.message.edit_caption(
                caption=_auction_text(query.message.chat, item, amount, user.id),
                parse_mode="HTML",
                reply_markup=_auction_keyboard(item_id),
            )
        except Exception:
            try:
                query.message.edit_text(
                    text=_auction_text(query.message.chat, item, amount, user.id),
                    parse_mode="HTML",
                    reply_markup=_auction_keyboard(item_id),
                )
            except Exception:
                pass
    finally:
        con.close()


def balance(update: Update, context: CallbackContext):
    con = connect()
    try:
        p = ensure_player(con, update.effective_user)
        con.commit()
        update.effective_message.reply_text(f"💰 {update.effective_user.first_name}, tumhara balance: {p['coins']} coins")
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
            text = "🏆 Tumhari team abhi empty hai. Live auction me /bid amount use karo."
        else:
            text = "🏆 <b>YOUR TEAM</b>\n\n" + "\n".join(
                f"🎯 {html.escape(r['name'])} — {r['price']} coins" for r in rows
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
        if not rows:
            update.effective_message.reply_text("📊 Abhi leaderboard empty hai.")
            return
        text = "🏆 <b>AUCTION LEADERBOARD</b>\n\n" + "\n".join(
            f"{i}. {html.escape(r['username'])} — {r['count']} players · {r['coins']} coins"
            for i, r in enumerate(rows, 1)
        )
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
        state = con.execute("SELECT * FROM game_state WHERE chat_id=? AND active=1", (chat.id,)).fetchone()
        if not state:
            update.effective_message.reply_text("Auction live nahi hai.")
            return
        item = con.execute("SELECT * FROM actresses WHERE id=?", (state["current_actress"],)).fetchone()
        if state["high_bidder"] is None:
            con.execute("UPDATE game_state SET active=0,current_actress=NULL,high_bid=0 WHERE chat_id=?", (chat.id,))
            con.commit()
            update.effective_message.reply_text(f"🔨 Auction ended. {item['name']} unsold raha.")
            return
        winner = ensure_player(con, type("User", (), {
            "id": state["high_bidder"], "username": None, "full_name": f"user_{state['high_bidder']}"
        })())
        price = int(state["high_bid"])
        if winner["coins"] < price:
            update.effective_message.reply_text("❌ Winner ke paas enough coins nahi hain. Auction cancel kiya.")
            con.execute("UPDATE game_state SET active=0,current_actress=NULL,high_bid=0,high_bidder=NULL WHERE chat_id=?", (chat.id,))
            con.commit()
            return
        con.execute("UPDATE players SET coins=coins-? WHERE user_id=?", (price, state["high_bidder"]))
        con.execute("UPDATE actresses SET sold_to=?,sold_price=? WHERE id=?", (state["high_bidder"], price, item["id"]))
        con.execute("INSERT OR REPLACE INTO teams(chat_id,actress_id,user_id,price) VALUES(?,?,?,?)", (chat.id, item["id"], state["high_bidder"], price))
        con.execute("UPDATE game_state SET active=0,current_actress=NULL,high_bid=0,high_bidder=NULL WHERE chat_id=?", (chat.id,))
        con.commit()
        winner_name = con.execute("SELECT username FROM players WHERE user_id=?", (state["high_bidder"],)).fetchone()["username"]
        update.effective_message.reply_text(
            f"🏆 <b>AUCTION SOLD</b>\n\n🎯 {html.escape(item['name'])}\n"
            f"👑 Winner: {html.escape(winner_name)}\n💰 Final price: {price} coins\n\n"
            "➡️ Admin: /auction for the next player.",
            parse_mode="HTML",
        )
    finally:
        con.close()


def give_coins(update: Update, context: CallbackContext):
    if not is_admin(update, context):
        update.effective_message.reply_text("⛔ Sirf admin coins de sakta hai.")
        return
    if not context.args:
        update.effective_message.reply_text("Reply to a user's message and use /givecoins 1000")
        return
    try:
        amount = int(context.args[-1])
        if amount <= 0:
            raise ValueError
    except ValueError:
        update.effective_message.reply_text("❌ Amount positive number hona chahiye.")
        return
    target = update.effective_message.reply_to_message.from_user if update.effective_message.reply_to_message else None
    if not target:
        update.effective_message.reply_text("👤 User ke message par reply karke /givecoins 1000 bhejo.")
        return
    con = connect()
    try:
        ensure_player(con, target)
        con.execute("UPDATE players SET coins=coins+? WHERE user_id=?", (amount, target.id))
        con.commit()
        update.effective_message.reply_text(f"✅ {amount} coins {target.first_name} ko de diye.")
    finally:
        con.close()
