import requests
from telegram import (
    InputMediaPhoto,
    Update,
)
from telegram.ext import CallbackContext

from src.bot.post_upload_conversation import send_post_to_channel
from src.common.settings.env import API_ENDPOINT, MAIN_CHANNEL_ID, PORT

randomPImageUrl = "https://picsum.photos/1200"


def startCommand(update: Update, context: CallbackContext):
    """Send a polished welcome message for the Telegram auction game."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat:
        return

    first_name = user.first_name if user else "Warrior"
    bot_username = getattr(context.bot, "username", None) or "AuctionBot"

    text = (
        "╔══════════════════════════════╗\n"
        "        👑 <b>WELCOME TO AUCTION ARENA</b> 👑\n"
        "╚══════════════════════════════╝\n\n"
        f"🔥 Hey <b>{first_name}</b>, ready to build your dream team?\n\n"
        "🎭 <b>WHAT YOU CAN DO</b>\n"
        "💰 Bid for your favourite actresses\n"
        "🏆 Build the strongest dream team\n"
        "📊 Compete on the leaderboard\n"
        "⚔️ Fight for the top spot\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🎮 <b>QUICK COMMANDS</b>\n\n"
        "🔨 /auction — Start live photo auction <i>(Admin)</i>\n"
        "💸 /bid 500 — Place your bid\n"
        "💰 /balance — Check your coins\n"
        "👥 /team — View your dream team\n"
        "🏆 /leaderboard — See the rankings\n"
        "📖 /help — All commands\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚡ <b>Play smart. Bid wisely. Win big.</b>\n"
        "🔥 <b>LET THE AUCTION BEGIN!</b> 🔥\n\n"
        f"🤖 <i>Powered by @{bot_username}</i>"
    )

    context.bot.send_message(
        chat_id=chat.id,
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


def helpCommand(update: Update, context: CallbackContext):

    context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=("🎮 Auction Game Commands\n\n"
              "/auction — start auction (admin)\n"
              "/addplayer Name | Price | PhotoURL | Category | Vote% | LastFight | Rank — add player card (admin)\n"
              "/bid amount — place a bid\n"
              "/balance — check coins\n"
              "/team — view your team\n"
              "/leaderboard — rankings\n"
              "/endauction — finish current auction (admin)\n"
              "/givecoins @username amount OR reply with /givecoins amount — award coins (admin)."),
    )


import os

def get_docs(update: Update, context: CallbackContext):
    # WEB_URL/API_ENDPOINT can point to the public FastAPI URL on Heroku.
    docs_base = os.getenv("WEB_URL", "").strip().rstrip("/") or API_ENDPOINT.rstrip("/")
    docs_url = f"{docs_base}/docs"

    context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"API documentation:\n{docs_url}",
        disable_web_page_preview=True,
    )


def get_image_Command(update: Update, context: CallbackContext):

    context.bot.send_media_group(
        # chat_id=-1001663892384,
        chat_id=update.effective_chat.id,
        media=[
            InputMediaPhoto(requests.get(randomPImageUrl).content, caption="New image"),
        ],
    )


### NEW COMMANDS


def post_from_id(update: Update, context: CallbackContext):

    # get item id from message
    try:
        item_id = int(context.args[0])
    except IndexError:
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Please enter an id after command\n example: /post_from_id 123",
        )
    except ValueError:
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Id must be an integer \n example: /post_from_id 123",
        )

    # read item by id from DB

    headers = {
        "accept": "application/json",
    }

    params = {
        "id": item_id,
    }

    r = requests.get(
        url=f"{API_ENDPOINT}/auc_ext/item_by_id/",
        headers=headers,
        params=params,
    )
    if r.status_code != 200:
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="DB error",
        )
        raise ValueError  # TODO make custom error
    DB_data = r.json()
    # assert 1
    # fill up context.user_data w/ data from DB
    context.user_data["Title"] = DB_data["title"]
    context.user_data["Description"] = DB_data["description"]
    context.user_data["Price"] = DB_data["price"]

    photo_urls = DB_data["photo"].split("|")
    context.user_data["photos"] = [dict(file=i) for i in photo_urls]

    send_post_to_channel(
        update=update,
        context=context,
        chat_id=MAIN_CHANNEL_ID,
        item_id=item_id,
    )

    context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Post created successfully",
    )


def auctionStartCommand(update: Update, context: CallbackContext):
    """Post the styled auction-start announcement in the current chat."""
    chat = update.effective_chat
    if chat is None:
        return

    group_name = f"@{chat.username}" if getattr(chat, "username", None) else (chat.title or str(chat.id))
    announcement = (
        "┏━━━━━━━━━━━━━┓\n"
        "🚀 <b>AUCTION STARTED</b> 🚀\n"
        "┗━━━━━━━━━━━━━┛\n\n"
        "🎮 <b>Game:</b> AUCTION\n"
        f"👥 <b>Group:</b> {group_name}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🔥 <b>STATUS: LIVE NOW</b>\n"
        "⏳ Auction has officially begun!\n\n"
        "🎯 <b>Objective:</b>\n"
        "➤ Bid and Buy Actress wisely.\n"
        "➤ Make ur dream team.\n"
        "➤ Win the polling fights!\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚡ Play smart. Play fast. Win big.\n"
        "🏆 Good luck, warriors!\n\n"
        "🚀 <b>LET THE GAME BEGIN!</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🤖 Powered by @sonapup_bot\n"
        "━━━━━━━━━━━━━━━━━━"
    )
    context.bot.send_message(
        chat_id=chat.id,
        text=announcement,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
