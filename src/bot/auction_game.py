"""Telegram group auction game with automatic player photos.

Photo workflow:
  1. Send/reply to a photo with: /addplayer Name | BasePrice
  2. The Telegram photo file_id is saved in SQLite.
  3. /auction automatically sends that saved photo to the group.

Legacy /addactress is kept as an alias for existing deployments.
"""
import os
import sqlite3
import time
from pathlib import Path

from telegram import Update
from telegram.ext import CallbackContext

from src.common.settings.env import ADMIN_CHAT_ID

DB_PATH = os.getenv("AUCTION_GAME_DB", str(Path("data_base") / "auction_game.sqlite3"))
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
START_COINS = int(os.getenv("START_COINS", "10000"))


def connect():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
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
    """)
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


def _auction_caption(chat, item):
    group_name = f"@{chat.username}" if chat.username else (chat.title or str(chat.id))
    return (
        "┏━━━━━━━━━━━━━━━━━━┓\n"
        "🚀 <b>AUCTION STARTED</b> 🚀\n"
        "┗━━━━━━━━━━━━━━━━━━┛\n\n"
        f"🎮 <b>Game:</b> AUCTION\n"
        f"👥 <b>Group:</b> {group_name}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🔥 <b>STATUS: LIVE NOW</b>\n\n"
        f"🎯 <b>NOW ON AUCTION</b>\n"
        f"👤 <b>{item['name']}</b>\n"
        f"💰 Starting bid: <b>{item['base_price']}</b> coins\n\n"
        "⚡ Use <b>/bid AMOUNT</b> to place your bid.\n"
        "💰 /balance  •  👥 /team  •  🏆 /leaderboard\n\n"
        "🔥 <b>Play smart. Bid fast. Win big!</b>"
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
        state = con.execute(
            "SELECT * FROM game_state WHERE chat_id=?", (chat.id,)
        ).fetchone()
        if state and state["active"]:
            update.effective_message.reply_text(
                "⚠️ Auction already LIVE hai. Current item ke liye /bid amount use karo."
            )
            return

        item = con.execute(
            "SELECT * FROM actresses WHERE sold_to IS NULL ORDER BY id LIMIT 1"
        ).fetchone()
        if not item:
            update.effective_message.reply_text(
                "📭 Koi player available nahi hai.\n\n"
                "Photo ko reply karke use karo:\n"
                "/addplayer Name | BasePrice\n\n"
                "Ya:\n/addplayer Name | BasePrice | PhotoURL"
            )
            return

        con.execute(
            """INSERT OR REPLACE INTO game_state
               (chat_id,active,current_actress,high_bid,high_bidder,started_at)
               VALUES(?,1,?,?,NULL,?)""",
            (chat.id, item["id"], item["base_price"], int(time.time())),
        )
        con.commit()

        caption = _auction_caption(chat, item)

        # Telegram file_id is the preferred method: fast and reliable on Heroku.
        if item["photo"]:
            try:
                context.bot.send_photo(
                    chat_id=chat.id,
                    photo=item["photo"],
                    caption=caption,
                    parse_mode="HTML",
                )
                return
            except Exception:
                # A broken/expired URL should never stop the auction.
                pass

        context.bot.send_message(
            chat_id=chat.id,
            text=caption,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    finally:
        con.close()


def add_player(update: Update, context: CallbackContext):
    """Add a player and automatically save a replied Telegram photo."""
    if not is_admin(update, context):
        update.effective_message.reply_text("⛔ Sirf group admin ye command use kar sakta hai.")
        return

    raw = " ".join(context.args).strip()
    parts = [p.strip() for p in raw.split("|")]

    if len(parts) < 2 or not parts[0]:
        update.effective_message.reply_text(
            "❌ Correct format:\n"
            "Photo ko reply karke:\n"
            "/addplayer Name | BasePrice\n\n"
            "Example:\n/addplayer Player One | 1000\n\n"
            "URL option:\n/addplayer Player One | 1000 | https://example.com/photo.jpg"
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

    # If command is a reply to a Telegram photo, always prefer its file_id.
    reply = update.effective_message.reply_to_message
    if reply and getattr(reply, "photo", None):
        try:
            photo = reply.photo[-1].file_id
        except Exception:
            pass

    con = connect()
    try:
        con.execute(
            "INSERT INTO actresses(name,base_price,photo) VALUES(?,?,?)",
            (parts[0], price, photo),
        )
        con.commit()
    finally:
        con.close()

    photo_status = "🖼️ Photo saved — auction me auto aayegi." if photo else "ℹ️ Photo nahi di gayi — auction text card se start hoga."
    update.effective_message.reply_text(
        f"✅ Player add ho gaya!\n\n"
        f"👤 {parts[0]}\n"
        f"💰 Base price: {price} coins\n"
        f"{photo_status}\n\n"
        f"Ab group me /auction karo."
    )


# Backward-compatible command name.
def add_actress(update: Update, context: CallbackContext):
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
        state = con.execute(
            "SELECT * FROM game_state WHERE chat_id=? AND active=1", (chat.id,)
        ).fetchone()
        if not state:
            con.commit()
            msg.reply_text("⏸️ Abhi auction live nahi hai. Admin se /auction start karwao.")
            return
        if state["high_bidder"] == user.id:
            msg.reply_text("⚠️ Tum already highest bidder ho.")
            return
        minimum = int(state["high_bid"]) + 1
        if amount < minimum:
            msg.reply_text(f"📈 Minimum next bid {minimum} coins hai.")
            return
        if amount > player["coins"]:
            msg.reply_text(f"💸 Tumhare paas {player['coins']} coins hain. /balance check karo.")
            return
        item = con.execute(
            "SELECT * FROM actresses WHERE id=?", (state["current_actress"],)
        ).fetchone()
        con.execute(
            "UPDATE game_state SET high_bid=?, high_bidder=? WHERE chat_id=?",
            (amount, user.id, chat.id),
        )
        con.commit()
        name = user.first_name or player["username"]
        msg.reply_text(
            f"🔥 BID PLACED!\n👤 {item['name']}\n💰 Bid: {amount} coins\n"
            f"👑 Highest bidder: {name}\nNext bid: {amount + 1} coins"
        )
    finally:
        con.close()


def balance(update: Update, context: CallbackContext):
    con = connect()
    try:
        p = ensure_player(con, update.effective_user)
        con.commit()
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
            text = "🎭 Tumhari team abhi empty hai. Live auction me /bid amount use karo."
        else:
            text = "🏆 <b>Your Dream Team</b>\n\n" + "\n".join(
                f"👤 {r['name']} — {r['price']} coins" for r in rows
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
            f"{i}. {r['username']} — {r['count']} players · {r['coins']} coins"
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
        state = con.execute(
            "SELECT * FROM game_state WHERE chat_id=? AND active=1", (chat.id,)
        ).fetchone()
        if not state:
            update.effective_message.reply_text("Auction live nahi hai.")
            return
        item = con.execute(
            "SELECT * FROM actresses WHERE id=?", (state["current_actress"],)
        ).fetchone()
        if state["high_bidder"] is None:
            con.execute(
                "UPDATE game_state SET active=0,current_actress=NULL WHERE chat_id=?",
                (chat.id,),
            )
            con.commit()
            update.effective_message.reply_text(f"🔨 Auction ended. {item['name']} unsold rahi.")
            return

        player = ensure_player(
            con,
            type("User", (), {
                "id": state["high_bidder"],
                "username": None,
                "full_name": f"user_{state['high_bidder']}"
            })(),
        )
        if player["coins"] < state["high_bid"]:
            update.effective_message.reply_text(
                "❌ Winner ke coins ab available nahi hain; auction cancel kiya."
            )
            con.execute(
                "UPDATE game_state SET active=0,current_actress=NULL WHERE chat_id=?",
                (chat.id,),
            )
            con.commit()
            return

        con.execute(
            "UPDATE players SET coins=coins-? WHERE user_id=?",
            (state["high_bid"], state["high_bidder"]),
        )
        con.execute(
            "UPDATE actresses SET sold_to=?,sold_price=? WHERE id=?",
            (state["high_bidder"], state["high_bid"], item["id"]),
        )
        con.execute(
            "INSERT OR REPLACE INTO teams(chat_id,actress_id,user_id,price) VALUES(?,?,?,?)",
            (chat.id, item["id"], state["high_bidder"], state["high_bid"]),
        )
        con.execute(
            "UPDATE game_state SET active=0,current_actress=NULL,high_bid=0,high_bidder=NULL WHERE chat_id=?",
            (chat.id,),
        )
        con.commit()
        winner = con.execute(
            "SELECT username FROM players WHERE user_id=?", (state["high_bidder"],)
        ).fetchone()["username"]
        update.effective_message.reply_text(
            f"🏆 <b>AUCTION RESULT</b>\n👤 {item['name']} sold!\n"
            f"👑 Winner: {winner}\n💰 Final price: {state['high_bid']} coins\n\n"
            "Admin can run /auction for next available player.",
            parse_mode="HTML",
        )
    finally:
        con.close()


def give_coins(update: Update, context: CallbackContext):
    if not is_admin(update, context):
        update.effective_message.reply_text("⛔ Sirf admin coins de sakta hai.")
        return
    if not context.args:
        update.effective_message.reply_text(
            "Use: user ke message par reply karke /givecoins 1000"
        )
        return
    try:
        amount = int(context.args[-1])
    except ValueError:
        update.effective_message.reply_text("Coins number me do.")
        return
    if amount <= 0:
        update.effective_message.reply_text("Amount positive hona chahiye.")
        return

    target = update.effective_message.reply_to_message.from_user if update.effective_message.reply_to_message else None
    if not target:
        update.effective_message.reply_text(
            "Coins dene wale user ke message par reply karke /givecoins 1000 bhejo."
        )
        return
    con = connect()
    try:
        ensure_player(con, target)
        con.execute(
            "UPDATE players SET coins=coins+? WHERE user_id=?", (amount, target.id)
        )
        con.commit()
        update.effective_message.reply_text(
            f"✅ {amount} coins {target.first_name} ko de diye."
        )
    finally:
        con.close()
