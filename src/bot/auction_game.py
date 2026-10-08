"""Persistent Telegram player auction game."""
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
    con.executescript("""
    CREATE TABLE IF NOT EXISTS players (user_id INTEGER PRIMARY KEY, username TEXT NOT NULL, coins INTEGER NOT NULL DEFAULT 10000);
    CREATE TABLE IF NOT EXISTS actresses (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, base_price INTEGER NOT NULL, photo TEXT, sold_to INTEGER, sold_price INTEGER);
    CREATE TABLE IF NOT EXISTS game_state (chat_id INTEGER PRIMARY KEY, active INTEGER NOT NULL DEFAULT 0, current_actress INTEGER, high_bid INTEGER NOT NULL DEFAULT 0, high_bidder INTEGER, started_at INTEGER, poll_message_id INTEGER);
    CREATE TABLE IF NOT EXISTS teams (chat_id INTEGER NOT NULL, actress_id INTEGER NOT NULL, user_id INTEGER NOT NULL, price INTEGER NOT NULL, PRIMARY KEY(chat_id, actress_id));
    """)
    # Migrate older DBs safely.
    cols = {r[1] for r in con.execute("PRAGMA table_info(actresses)").fetchall()}
    if "photo" not in cols:
        con.execute("ALTER TABLE actresses ADD COLUMN photo TEXT")
    state_cols = {r[1] for r in con.execute("PRAGMA table_info(game_state)").fetchall()}
    if "poll_message_id" not in state_cols:
        con.execute("ALTER TABLE game_state ADD COLUMN poll_message_id INTEGER")
    return con


def ensure_player(con, user):
    username = user.username or getattr(user, "full_name", None) or f"user_{user.id}"
    con.execute("INSERT OR IGNORE INTO players(user_id,username,coins) VALUES(?,?,?)", (user.id, username, START_COINS))
    con.execute("UPDATE players SET username=? WHERE user_id=?", (username, user.id))
    return con.execute("SELECT * FROM players WHERE user_id=?", (user.id,)).fetchone()


def is_admin(update, context):
    user, chat = update.effective_user, update.effective_chat
    configured = str(ADMIN_CHAT_ID or "").strip()
    if configured and chat and str(chat.id) == configured:
        return True
    try:
        return context.bot.get_chat_member(chat.id, user.id).status in ("creator", "administrator")
    except Exception:
        return False


def _photo_from_reply(message):
    reply = getattr(message, "reply_to_message", None)
    if reply and getattr(reply, "photo", None):
        return reply.photo[-1].file_id
    return None


def _keyboard(item_id, admin=False):
    rows = [
        [InlineKeyboardButton("💰 +100", callback_data=f"auc:bid:{item_id}:100"),
         InlineKeyboardButton("💰 +500", callback_data=f"auc:bid:{item_id}:500"),
         InlineKeyboardButton("💰 +1000", callback_data=f"auc:bid:{item_id}:1000")],
        [InlineKeyboardButton("📊 Current Bid", callback_data=f"auc:info:{item_id}:0"),
         InlineKeyboardButton("🗳️ Vote", callback_data=f"auc:poll:{item_id}:0")],
    ]
    if admin:
        rows.append([InlineKeyboardButton("🔨 SOLD", callback_data=f"auc:sold:{item_id}:0"),
                     InlineKeyboardButton("❌ UNSOLD", callback_data=f"auc:unsold:{item_id}:0")])
    return InlineKeyboardMarkup(rows)


def _text(chat, item, bid, bidder):
    group = f"@{chat.username}" if chat.username else (chat.title or str(chat.id))
    who = "No bids yet" if not bidder else f"ID {bidder}"
    return ("┏━━━━━━━━━━━━━━━━━━━━┓\n"
            "   🔨 <b>AUCTION LIVE</b>\n"
            "┗━━━━━━━━━━━━━━━━━━━━┛\n\n"
            f"🎯 <b>Player:</b> {html.escape(str(item['name']))}\n"
            f"💰 <b>Base Price:</b> {item['base_price']} coins\n"
            f"🔥 <b>Current Bid:</b> {bid} coins\n"
            f"👑 <b>Highest Bidder:</b> {html.escape(who)}\n"
            f"👥 <b>Group:</b> {html.escape(group)}\n\n"
            "Tap a bid button or use <code>/bid amount</code>.\n"
            "━━━━━━━━━━━━━━━━━━━━")


def _send_item(bot, chat, item, bid, bidder=None, admin=False):
    markup = _keyboard(item["id"], admin)
    caption = _text(chat, item, bid, bidder)
    if item["photo"]:
        try:
            bot.send_photo(chat_id=chat.id, photo=item["photo"], caption=caption, parse_mode="HTML", reply_markup=markup)
            return True
        except Exception:
            pass
    bot.send_message(chat_id=chat.id, text=caption, parse_mode="HTML", reply_markup=markup)
    return False


def _next_unsold(con):
    return con.execute("SELECT * FROM actresses WHERE sold_to IS NULL ORDER BY id LIMIT 1").fetchone()


def auction_start(update, context):
    chat = update.effective_chat
    if not chat: return
    if not is_admin(update, context):
        update.effective_message.reply_text("⛔ Sirf group admin auction start kar sakta hai.")
        return
    con=connect()
    try:
        state=con.execute("SELECT * FROM game_state WHERE chat_id=?",(chat.id,)).fetchone()
        if state and state["active"]:
            update.effective_message.reply_text("⚠️ Auction already LIVE hai.")
            return
        item=_next_unsold(con)
        if not item:
            update.effective_message.reply_text("📭 Koi player available nahi hai.\n\nPhoto reply karke:\n/addplayer Name | BasePrice")
            return
        con.execute("INSERT OR REPLACE INTO game_state(chat_id,active,current_actress,high_bid,high_bidder,started_at) VALUES(?,1,?,?,NULL,?)",(chat.id,item["id"],item["base_price"],int(time.time())))
        con.commit()
        _send_item(context.bot,chat,item,item["base_price"],None,True)
    finally: con.close()


def add_player(update, context):
    if not is_admin(update, context):
        update.effective_message.reply_text("⛔ Sirf group admin player add kar sakta hai."); return
    parts=[p.strip() for p in " ".join(context.args).split("|")]
    if len(parts)<2 or not parts[0]:
        update.effective_message.reply_text("📌 /addplayer Name | BasePrice\n🖼️ Photo ko reply karke command bhejo."); return
    try: price=int(parts[1]); assert price>0
    except Exception:
        update.effective_message.reply_text("❌ Base price positive number hona chahiye."); return
    photo=_photo_from_reply(update.effective_message) or (parts[2] if len(parts)>2 and parts[2] else None)
    if not photo:
        update.effective_message.reply_text("🖼️ Photo missing hai. Photo ko reply karke /addplayer Name | BasePrice bhejo."); return
    con=connect()
    try:
        con.execute("INSERT INTO actresses(name,base_price,photo) VALUES(?,?,?)",(parts[0],price,photo)); con.commit()
    finally: con.close()
    update.effective_message.reply_text(f"✅ Player Added\n\n🎯 {html.escape(parts[0])}\n💰 Base: {price}\n📸 Photo saved.\n\n/auction se automatically photo ke saath auction start hoga.",parse_mode="HTML")


def add_actress(update, context): add_player(update, context)


def bid(update, context):
    msg,chat,user=update.effective_message,update.effective_chat,update.effective_user
    if not context.args: msg.reply_text("Use: /bid 500"); return
    try: amount=int(context.args[0]); assert amount>0
    except Exception: msg.reply_text("❌ Bid positive number hona chahiye."); return
    con=connect()
    try:
        p=ensure_player(con,user); state=con.execute("SELECT * FROM game_state WHERE chat_id=? AND active=1",(chat.id,)).fetchone()
        if not state: msg.reply_text("⏸️ Auction live nahi hai."); return
        minimum=int(state["high_bid"])+1
        if amount<minimum: msg.reply_text(f"📈 Minimum next bid {minimum} coins hai."); return
        if amount>p["coins"]: msg.reply_text(f"💸 Tumhare paas {p['coins']} coins hain."); return
        if state["high_bidder"]==user.id: msg.reply_text("⚠️ Tum already highest bidder ho."); return
        con.execute("UPDATE game_state SET high_bid=?,high_bidder=? WHERE chat_id=?",(amount,user.id,chat.id)); con.commit()
        msg.reply_text(f"🔥 <b>BID</b> {amount} coins by {html.escape(user.first_name or p['username'])}",parse_mode="HTML")
    finally: con.close()


def auction_callback(update, context):
    q=update.callback_query
    if not q: return
    data=(q.data or "").split(":")
    if len(data)!=4 or data[0]!="auc": return
    action=data[1]
    try: item_id=int(data[2])
    except: return
    chat=q.message.chat; user=q.from_user
    con=connect()
    try:
        state=con.execute("SELECT * FROM game_state WHERE chat_id=? AND active=1",(chat.id,)).fetchone()
        if not state or state["current_actress"]!=item_id:
            q.answer("Auction is not active.",show_alert=True); return
        item=con.execute("SELECT * FROM actresses WHERE id=?",(item_id,)).fetchone()
        if not item:
            q.answer("Player not found.", show_alert=True)
            return
        if action=="info":
            q.answer(f"Current bid: {state['high_bid']} coins")
            return
        if action=="poll":
            try:
                poll = _send_poll_for_current(context.bot, chat, item)
                con.execute("UPDATE game_state SET poll_message_id=? WHERE chat_id=?", (poll.message_id, chat.id))
                con.commit()
                q.answer("Photo + vote poll created.")
            except Exception:
                q.answer("Could not create poll.", show_alert=True)
            return
        if action in ("sold","unsold"):
            if not _is_admin_user(context,chat.id,user.id): q.answer("Only group admin can do this.",show_alert=True); return
            if action=="sold":
                if state["high_bidder"] is None: q.answer("No bidder yet.",show_alert=True); return
                winner=ensure_player(con,type("U",(),{"id":state["high_bidder"],"username":None,"full_name":f"user_{state['high_bidder']}"})())
                price=int(state["high_bid"])
                if winner["coins"]<price: q.answer("Winner has insufficient coins.",show_alert=True); return
                _close_current_poll(context.bot, chat.id, state["poll_message_id"])
                con.execute("UPDATE players SET coins=coins-? WHERE user_id=?",(price,winner["user_id"]))
                con.execute("UPDATE actresses SET sold_to=?,sold_price=? WHERE id=?",(winner["user_id"],price,item_id))
                con.execute("INSERT OR REPLACE INTO teams(chat_id,actress_id,user_id,price) VALUES(?,?,?,?)",(chat.id,item_id,winner["user_id"],price))
                con.execute("UPDATE game_state SET active=0,current_actress=NULL,high_bid=0,high_bidder=NULL,poll_message_id=NULL WHERE chat_id=?",(chat.id,))
                con.commit(); q.answer("SOLD!")
                context.bot.send_message(chat.id,f"🏆 <b>SOLD</b>\n\n🎯 {html.escape(item['name'])}\n👑 Winner: {html.escape(winner['username'])}\n💰 Price: {price} coins",parse_mode="HTML")
            else:
                _close_current_poll(context.bot, chat.id, state["poll_message_id"])
                con.execute("UPDATE game_state SET active=0,current_actress=NULL,high_bid=0,high_bidder=NULL,poll_message_id=NULL WHERE chat_id=?",(chat.id,)); con.commit(); q.answer("UNSOLD")
                context.bot.send_message(chat.id,f"❌ <b>UNSOLD</b>\n\n🎯 {html.escape(item['name'])}",parse_mode="HTML")
            _start_next(context,chat)
            return
        if action!="bid": return
        try: inc=int(data[3])
        except: return
        p=ensure_player(con,user); amount=int(state["high_bid"])+inc
        if amount>p["coins"]: q.answer(f"You have only {p['coins']} coins.",show_alert=True); return
        if state["high_bidder"]==user.id: q.answer("You are already highest bidder.",show_alert=True); return
        con.execute("UPDATE game_state SET high_bid=?,high_bidder=? WHERE chat_id=?",(amount,user.id,chat.id)); con.commit()
        q.answer(f"Bid: {amount}")
        try: q.message.edit_caption(_text(chat,item,amount,user.id),parse_mode="HTML",reply_markup=_keyboard(item_id,False))
        except Exception: pass
    finally: con.close()


def _is_admin_user(context,chat_id,user_id):
    try: return context.bot.get_chat_member(chat_id,user_id).status in ("creator","administrator")
    except: return False


def _start_next(context,chat):
    con=connect()
    try:
        item=_next_unsold(con)
        if not item:
            context.bot.send_message(chat.id,"🎉 <b>AUCTION COMPLETE</b>\nAll players have been processed.",parse_mode="HTML"); return
        con.execute("INSERT OR REPLACE INTO game_state(chat_id,active,current_actress,high_bid,high_bidder,started_at) VALUES(?,1,?,?,NULL,?)",(chat.id,item["id"],item["base_price"],int(time.time()))); con.commit()
        context.bot.send_message(chat.id,"➡️ <b>NEXT PLAYER</b>",parse_mode="HTML")
        _send_item(context.bot,chat,item,item["base_price"],None,True)
    finally: con.close()


def _send_poll_for_current(bot, chat, item, options=None):
    """Send the current player's photo first, then a native Telegram poll."""
    options = options or ["🔨 SOLD", "❌ UNSOLD"]
    caption = (
        f"🗳️ <b>AUCTION VOTE</b>\n\n"
        f"🎯 <b>{html.escape(str(item['name']))}</b>\n"
        "Vote below. Final results will be shown when the poll is closed."
    )
    if item["photo"]:
        try:
            bot.send_photo(chat_id=chat.id, photo=item["photo"], caption=caption, parse_mode="HTML")
        except Exception:
            bot.send_message(chat_id=chat.id, text=caption, parse_mode="HTML")
    else:
        bot.send_message(chat_id=chat.id, text=caption, parse_mode="HTML")
    return bot.send_poll(
        chat_id=chat.id,
        question=f"{item['name']} — Final Result?",
        options=options,
        is_anonymous=True,
        allows_multiple_answers=False,
    )


def _close_current_poll(bot, chat_id, poll_message_id):
    if not poll_message_id:
        return
    try:
        bot.stop_poll(chat_id=chat_id, message_id=int(poll_message_id))
    except Exception:
        pass


def create_vote_poll(update, context):
    """Create a native Telegram poll with the current player's photo."""
    chat = update.effective_chat
    if not chat:
        return
    if not is_admin(update, context):
        update.effective_message.reply_text("⛔ Sirf group admin poll create kar sakta hai.")
        return
    con = connect()
    try:
        state = con.execute("SELECT * FROM game_state WHERE chat_id=? AND active=1", (chat.id,)).fetchone()
        if not state:
            update.effective_message.reply_text("⏸️ Auction live nahi hai.")
            return
        item = con.execute("SELECT * FROM actresses WHERE id=?", (state["current_actress"],)).fetchone()
        if not item:
            update.effective_message.reply_text("❌ Current player nahi mila.")
            return
        poll = _send_poll_for_current(context.bot, chat, item)
        con.execute("UPDATE game_state SET poll_message_id=? WHERE chat_id=?", (poll.message_id, chat.id))
        con.commit()
        update.effective_message.reply_text("🗳️ Photo + poll created. Final result poll close hone par dikhega.")
    finally:
        con.close()

def balance(update,context):
    con=connect()
    try: p=ensure_player(con,update.effective_user); con.commit(); update.effective_message.reply_text(f"💰 Balance: {p['coins']} coins")
    finally: con.close()


def team(update,context):
    con=connect()
    try:
        rows=con.execute("SELECT a.name,t.price FROM teams t JOIN actresses a ON a.id=t.actress_id WHERE t.chat_id=? AND t.user_id=? ORDER BY a.name",(update.effective_chat.id,update.effective_user.id)).fetchall()
        text="🏆 <b>YOUR TEAM</b>\n\n"+"\n".join(f"🎯 {html.escape(r['name'])} — {r['price']} coins" for r in rows) if rows else "🏆 Team empty hai."
        update.effective_message.reply_text(text,parse_mode="HTML")
    finally: con.close()


def leaderboard(update,context):
    con=connect()
    try:
        rows=con.execute("SELECT p.username,p.coins,COUNT(t.actress_id) count FROM players p LEFT JOIN teams t ON t.user_id=p.user_id AND t.chat_id=? GROUP BY p.user_id ORDER BY count DESC,p.coins DESC LIMIT 10",(update.effective_chat.id,)).fetchall()
        text="🏆 <b>LEADERBOARD</b>\n\n"+"\n".join(f"{i}. {html.escape(r['username'])} — {r['count']} players · {r['coins']} coins" for i,r in enumerate(rows,1)) if rows else "📊 Leaderboard empty hai."
        update.effective_message.reply_text(text,parse_mode="HTML")
    finally: con.close()


def end_auction(update,context):
    # Same as pressing SOLD/UNSOLD: admin command keeps backward compatibility.
    if not is_admin(update,context): update.effective_message.reply_text("⛔ Sirf admin auction end kar sakta hai."); return
    con=connect()
    try:
        state=con.execute("SELECT * FROM game_state WHERE chat_id=? AND active=1",(update.effective_chat.id,)).fetchone()
        if not state: update.effective_message.reply_text("Auction live nahi hai."); return
        if state["high_bidder"] is None:
            item=con.execute("SELECT name FROM actresses WHERE id=?",(state["current_actress"],)).fetchone(); con.execute("UPDATE game_state SET active=0,current_actress=NULL,high_bid=0,high_bidder=NULL WHERE chat_id=?",(update.effective_chat.id,)); con.commit(); update.effective_message.reply_text(f"❌ {item['name']} UNSOLD"); return
    finally: con.close()
    # Let callback-style finalization happen through direct DB logic by marking sold.
    _finalize_sold(update,context)


def _finalize_sold(update,context):
    chat=update.effective_chat; con=connect()
    try:
        state=con.execute("SELECT * FROM game_state WHERE chat_id=? AND active=1",(chat.id,)).fetchone()
        if not state or state["high_bidder"] is None: return
        item=con.execute("SELECT * FROM actresses WHERE id=?",(state["current_actress"],)).fetchone(); winner=con.execute("SELECT * FROM players WHERE user_id=?",(state["high_bidder"],)).fetchone(); price=int(state["high_bid"])
        if not winner or winner["coins"]<price: update.effective_message.reply_text("❌ Winner ke paas enough coins nahi hain."); return
        con.execute("UPDATE players SET coins=coins-? WHERE user_id=?",(price,winner["user_id"])); con.execute("UPDATE actresses SET sold_to=?,sold_price=? WHERE id=?",(winner["user_id"],price,item["id"])); con.execute("INSERT OR REPLACE INTO teams(chat_id,actress_id,user_id,price) VALUES(?,?,?,?)",(chat.id,item["id"],winner["user_id"],price)); con.execute("UPDATE game_state SET active=0,current_actress=NULL,high_bid=0,high_bidder=NULL,poll_message_id=NULL WHERE chat_id=?",(chat.id,)); con.commit()
        update.effective_message.reply_text(f"🏆 <b>SOLD</b>\n🎯 {html.escape(item['name'])}\n👑 {html.escape(winner['username'])}\n💰 {price} coins",parse_mode="HTML")
    finally: con.close()
    _start_next(context,chat)


def give_coins(update,context):
    if not is_admin(update,context): update.effective_message.reply_text("⛔ Sirf admin coins de sakta hai."); return
    if not context.args: update.effective_message.reply_text("Reply to user: /givecoins 1000"); return
    try: amount=int(context.args[-1]); assert amount>0
    except: update.effective_message.reply_text("❌ Invalid amount."); return
    target=update.effective_message.reply_to_message.from_user if update.effective_message.reply_to_message else None
    if not target: update.effective_message.reply_text("👤 User ke message par reply karke command bhejo."); return
    con=connect()
    try: ensure_player(con,target); con.execute("UPDATE players SET coins=coins+? WHERE user_id=?",(amount,target.id)); con.commit(); update.effective_message.reply_text(f"✅ {amount} coins added to {target.first_name}.")
    finally: con.close()


def list_players(update, context):
    con = connect()
    try:
        rows = con.execute("SELECT id,name,base_price,sold_to,sold_price FROM actresses ORDER BY id").fetchall()
        if not rows:
            update.effective_message.reply_text("📭 Player list empty hai.")
            return
        lines = ["📋 <b>PLAYER LIST</b>", ""]
        for r in rows:
            status = "SOLD" if r["sold_to"] else "AVAILABLE"
            lines.append(f"#{r['id']} • {html.escape(r['name'])} • {r['base_price']} coins • {status}")
        update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")
    finally:
        con.close()


def cancel_auction(update, context):
    if not is_admin(update, context):
        update.effective_message.reply_text("⛔ Sirf group admin auction cancel kar sakta hai.")
        return
    con = connect()
    try:
        state = con.execute("SELECT * FROM game_state WHERE chat_id=? AND active=1", (update.effective_chat.id,)).fetchone()
        if not state:
            update.effective_message.reply_text("⏸️ Auction live nahi hai.")
            return
        con.execute("UPDATE game_state SET active=0,current_actress=NULL,high_bid=0,high_bidder=NULL WHERE chat_id=?", (update.effective_chat.id,))
        con.commit()
        update.effective_message.reply_text("🛑 Auction cancel kar diya gaya.")
    finally:
        con.close()
