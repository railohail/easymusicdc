import discordoauth2
import os 
from flask import Flask, request, redirect
from dotenv import load_dotenv
load_dotenv()
client = discordoauth2.Client(
    int(os.getenv('DISCORD_CLIENT_ID')),
    secret=os.getenv('DISCORD_CLIENT_SECRET'),
    redirect=os.getenv('DISCORD_REDIRECT_URI'),
    bot_token=os.getenv('DISCORD_BOT_TOKEN')
)
app = Flask(__name__)

client.update_linked_roles_metadata([
    {
        "type": 2,
        "key": "level",
        "name": "Level",
        "description": "The level the user is on"
    },
    {
        "type": 7,
        "key": "supporter",
        "name": "Supporter",
        "description": "Spent money to help the game"   
    }
])
# client.update_linked_roles_metadata([])
@app.route('/')
def main():
  return redirect(client.generate_uri(scope=["identify", "connections", "guilds", "role_connections.write"]))

@app.route("/oauth2")
def oauth2():
    code = request.args.get("code")

    access = client.exchange_code(code)

    access.update_metadata("Platform Name", "Username",  level=69, supporter=True)

    identify = access.fetch_identify()
    connections = access.fetch_connections()
    guilds = access.fetch_guilds()

    return f"""{identify}<br><br>{connections}<br><br>{guilds}"""

app.run("0.0.0.0", 8080)