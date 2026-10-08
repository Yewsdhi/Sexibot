import logging

from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    Updater,
)

from src.bot import bot_commands, bot_handlers, auction_game
from src.bot.conversation_handlers import conv_handler
from src.bot.err_handler import error_handler
from src.bot.post_upload_conversation import post_upload_conversation
from src.common.settings.env import DEBUG, TOKEN

if not TOKEN:
    raise Exception(
        "Cant find bot token in env variables try to to specify it in .env file"
    )
updater = Updater(token=TOKEN)

LOG_FILE_NAME = "auction_bot.log"
format = "%(asctime)s [%(levelname)s]: %(message)s"
logger = logging.basicConfig(
    filename=LOG_FILE_NAME if not DEBUG else None,
    format=format,
    encoding="utf-8",
    level=logging.INFO,
)
if not DEBUG:
    logging.getLogger(logger).addHandler(logging.StreamHandler())


dispatcher = updater.dispatcher

# old commands
dispatcher.add_handler(CommandHandler("start", bot_commands.startCommand))
dispatcher.add_handler(CommandHandler("auction", auction_game.auction_start))
dispatcher.add_handler(CommandHandler("addactress", auction_game.add_actress))
dispatcher.add_handler(CommandHandler("bid", auction_game.bid))
dispatcher.add_handler(CommandHandler("balance", auction_game.balance))
dispatcher.add_handler(CommandHandler("team", auction_game.team))
dispatcher.add_handler(CommandHandler("leaderboard", auction_game.leaderboard))
dispatcher.add_handler(CommandHandler("endauction", auction_game.end_auction))
dispatcher.add_handler(CommandHandler("givecoins", auction_game.give_coins))
dispatcher.add_handler(CommandHandler("help", bot_commands.helpCommand))
dispatcher.add_handler(CommandHandler("get_image", bot_commands.get_image_Command))
dispatcher.add_handler(CommandHandler("docs", bot_commands.get_docs))

# new commands
dispatcher.add_handler(CommandHandler("post_from_id", bot_commands.post_from_id))


# dispatcher.add_handler(MessageHandler(Filters.is_automatic_forward, messageHandler))
dispatcher.add_handler(CallbackQueryHandler(bot_handlers.queryHandler))

dispatcher.add_handler(conv_handler)
dispatcher.add_handler(post_upload_conversation)
dispatcher.add_error_handler(error_handler)

updater.start_polling()
