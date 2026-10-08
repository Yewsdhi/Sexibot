import json
import logging

from telegram import (
    Update,
)
from telegram.ext import CallbackContext

from src.bot import auction_game, bot_commands
from src.bot.bot_callbacks import (
    attend_callback,
    get_price_callback,
    price_increment_callback,
)

randomPImageUrl = "https://picsum.photos/1200"


def user_white_list():
    #     # white list of users
    #     # if update.effective_chat.username not in allowedUsernames:
    #     #     context.bot.send_message(chat_id=update.effective_chat.id, text="You are not allowed to use this bot")
    pass


def queryHandler(update: Update, context: CallbackContext):
    query = update.callback_query
    if not query:
        return
    data = query.data or ""
    query.answer()

    # Start-menu buttons
    if data.startswith("menu:"):
        action = data.split(":", 1)[1]
        chat_id = query.message.chat.id
        if action == "auction":
            auction_game.auction_start(update, context)
        elif action == "balance":
            auction_game.balance(update, context)
        elif action == "team":
            auction_game.team(update, context)
        elif action == "leaderboard":
            auction_game.leaderboard(update, context)
        elif action == "help":
            bot_commands.helpCommand(update, context)
        return

    # Legacy/API callbacks
    try:
        if "get_price_callback" in data:
            item_id = int(data.split("id=", 1)[1])
            get_price_callback(update=update, context=context, item_id=item_id)
            return
        if "attend_callback" in data:
            item_id = int(data.split("id=", 1)[1])
            attend_callback(update=update, context=context, item_id=item_id)
            return
        if "price_increment" in data:
            json_dict = "".join(data.split(" ")[1:])
            callback_data = json.loads(json_dict)
            price_increment_callback(update=update, context=context, item_id=callback_data["id"], price_increment=callback_data["val"])
            return
    except Exception:
        logging.exception("Legacy callback failed")
        query.answer("Something went wrong.", show_alert=True)
