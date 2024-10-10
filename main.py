import asyncio
import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
from async_timeout import timeout
from functools import partial
from youtube_search import YoutubeSearch
import os
from dotenv import load_dotenv
import concurrent.futures
import json
import aiohttp
from datetime import datetime, time, timezone
import logging
import aiohttp
from bs4 import BeautifulSoup
import random
# Load environment variables
load_dotenv()

# Use yt_dlp instead of youtube_dl
ydl_opts = {
    'format': 'bestaudio/best',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'
}

ffmpeg_options = {
    'options': '-vn',
    'executable': r'C:\ffmpeg\bin\ffmpeg.exe'  # Adjust this path to where you installed ffmpeg
}

ytdl = yt_dlp.YoutubeDL(ydl_opts)

# Create a ThreadPoolExecutor
thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=10)



class VolumeControl(discord.ui.View):
    def __init__(self, initial_volume: int):
        super().__init__(timeout=60)
        self.value = initial_volume
        self.add_item(self.volume_select())

    def volume_select(self):
        select = discord.ui.Select(
            placeholder="Adjust Volume",
            options=[
                discord.SelectOption(label=f"{i*10}%", value=str(i*10)) for i in range(11)
            ]
        )
        select.callback = self.select_callback
        return select

    async def select_callback(self, interaction: discord.Interaction):
        self.value = int(interaction.data['values'][0])
        await interaction.response.defer()
        self.stop()
class SongSelect(discord.ui.View):
    def __init__(self, songs, author):
        super().__init__(timeout=60)
        self.songs = songs
        self.author = author
        self.selected_song = None
        
        for i, song in enumerate(songs[:10]):  
            button = discord.ui.Button(label=str(i+1), style=discord.ButtonStyle.primary)
            button.callback = self.create_callback(song)
            self.add_item(button)

    def create_callback(self, song):
        async def callback(interaction: discord.Interaction):
            if interaction.user != self.author:
                await interaction.response.send_message("You can't select this song.", ephemeral=True)
                return
            self.selected_song = song
            self.stop()
            await interaction.response.defer()
        return callback
class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def create(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        partial_run = partial(ytdl.extract_info, url, download=not stream)
        data = await loop.run_in_executor(thread_pool, partial_run)
        
        if 'entries' in data:
            data = data['entries'][0]
        
        return cls(discord.FFmpegPCMAudio(data['url'], **ffmpeg_options), data=data)

class Song:
    def __init__(self, url, title=None):
        self.url = url
        self.title = title

class MusicPlayer:
    def __init__(self, interaction: discord.Interaction):
        self.bot = interaction.client
        self._guild = interaction.guild
        self._channel = interaction.channel
        self.queue = asyncio.Queue()
        self.next = asyncio.Event()
        self.np = None
        self.volume = .5
        self.current = None
        self.bot.loop.create_task(self.player_loop())

    async def player_loop(self):
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            self.next.clear()

            try:
                async with timeout(300):
                    song = await self.queue.get()
            except asyncio.TimeoutError:
                return self.destroy(self._guild)

            if not self._guild.voice_client:
                await self.ensure_voice_connected()
                if not self._guild.voice_client:
                    await self._channel.send("Failed to connect to voice channel. Skipping song.")
                    continue

            try:
                source = await YTDLSource.create(song.url, loop=self.bot.loop, stream=True)
            except Exception as e:
                await self._channel.send(f'There was an error processing your song.\n'
                                         f'```css\n[{e}]\n```')
                continue

            source.volume = self.volume
            self.current = source

            try:
                self._guild.voice_client.play(source, after=lambda e: self.bot.loop.call_soon_threadsafe(self.play_next_song, e))
                self.np = await self._channel.send(f'**Now Playing:** `{source.title}`')
                await self.next.wait()
            except Exception as e:
                logging.error(f"Error during playback: {e}")
                await self._channel.send(f"An error occurred during playback. Attempting to continue.")
                self.play_next_song(error=e)

    def play_next_song(self, error=None):
        if error:
            logging.error(f"Error in playback: {error}")
        self.next.set()


    async def ensure_voice_connected(self):
        if not self._guild.voice_client:
            try:
                await self._channel.send("Reconnecting to voice channel...")
                if self._guild.me.voice:
                    await self._guild.me.voice.channel.connect()
                else:
                    await self._channel.send("I'm not in a voice channel. Please use the join command first.")
            except asyncio.TimeoutError:
                await self._channel.send("Failed to reconnect to voice channel.")

    def destroy(self, guild):
        return self.bot.loop.create_task(self._guild.voice_client.disconnect() if self._guild.voice_client else None)
class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.players = {}

    async def cleanup(self, guild):
        try:
            await guild.voice_client.disconnect()
        except AttributeError:
            pass

        try:
            del self.players[guild.id]
        except KeyError:
            pass

    def get_player(self, interaction: discord.Interaction):
        try:
            player = self.players[interaction.guild_id]
        except KeyError:
            player = MusicPlayer(interaction)
            self.players[interaction.guild_id] = player

        return player

    @app_commands.command(name="join", description="Join the voice channel")
    async def join(self, interaction: discord.Interaction):
        if interaction.user.voice:
            channel = interaction.user.voice.channel
            try:
                await channel.connect()
                await interaction.response.send_message("Joined the voice channel.")
            except Exception as e:
                logging.error(f"Error joining voice channel: {e}")
                await interaction.response.send_message(f"An error occurred while joining the voice channel: {e}")
        else:
            await interaction.response.send_message("You are not connected to a voice channel.")

    @app_commands.command(name="play", description="Play a song or playlist")
    @app_commands.describe(query="The song or playlist you want to play")
    async def play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()

        if not interaction.guild.voice_client:
            if interaction.user.voice:
                await interaction.user.voice.channel.connect()
            else:
                await interaction.followup.send("You need to be in a voice channel to play music!")
                return

        player = self.get_player(interaction)

        if 'list=' in query:
            # Playlist handling
            await self.process_playlist(interaction, query, player)
        elif query.startswith('http'):
            # Direct URL handling
            song = Song(query)
            await player.queue.put(song)
            await interaction.followup.send(f'Song Added to queue: {query}')
        else:
            # Search and present options
            results = await self.bot.loop.run_in_executor(thread_pool, YoutubeSearch, query, 10)
            results = results.to_dict()

            if not results:
                await interaction.followup.send('No videos found.')
                return

            # Create embed with search results
            embed = discord.Embed(title="Search Results", description="Select a song to play:")
            for i, video in enumerate(results[:10], start=1):
                embed.add_field(name=f"{i}. {video['title']}", value=f"Duration: {video['duration']}", inline=False)

            view = SongSelect(results[:10], interaction.user)
            message = await interaction.followup.send(embed=embed, view=view)

            # Wait for button selection
            await view.wait()

            if view.selected_song:
                song_url = f"https://youtube.com{view.selected_song['url_suffix']}"
                song = Song(song_url, title=view.selected_song['title'])
                await player.queue.put(song)
                await message.edit(content=f"Added to queue: {view.selected_song['title']}", embed=None, view=None)
            else:
                await message.edit(content="Song selection timed out.", embed=None, view=None)

    async def process_playlist(self, interaction: discord.Interaction, url, player):
        await interaction.followup.send("Processing playlist. This may take a moment...")
        try:
            ydl_opts_playlist = {
                'extract_flat': 'in_playlist',
                'skip_download': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts_playlist) as ydl:
                result = await self.bot.loop.run_in_executor(thread_pool, ydl.extract_info, url, False)

            if 'entries' not in result:
                await interaction.followup.send('Error: Could not find playlist entries.')
                return

            for entry in result['entries'][:10]:
                video_url = f"https://www.youtube.com/watch?v={entry['id']}"
                song = Song(video_url, title=entry.get('title', 'Unknown Title'))
                await player.queue.put(song)
            
            await interaction.followup.send(f"Added {min(10, len(result['entries']))} songs from the playlist to the queue.")
            
            if len(result['entries']) > 10:
                await interaction.followup.send("Note: Only the first 10 songs from the playlist were added to avoid overloading.")
        except Exception as e:
            await interaction.followup.send(f'An error occurred while processing the playlist: {str(e)}')
            print(f"Playlist error details: {e}")
    @app_commands.command(name="playnext", description="Add a song to play next in the queue")
    @app_commands.describe(query="The song you want to play next")
    async def playnext(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()

        if not interaction.guild.voice_client:
            if interaction.user.voice:
                await interaction.user.voice.channel.connect()
            else:
                await interaction.followup.send("You need to be in a voice channel to play music!")
                return

        player = self.get_player(interaction)

        if query.startswith('http'):
            # Direct URL handling
            song = Song(query)
            await self.add_to_front_of_queue(player, song)
            await interaction.followup.send(f'Added to play next: {song.url}')
        else:
            # Search and present options
            results = await self.bot.loop.run_in_executor(None, YoutubeSearch, query, 5)
            results = results.to_dict()

            if not results:
                await interaction.followup.send('No videos found.')
                return

            # Create embed with search results
            embed = discord.Embed(title="Search Results", description="Select a song to play next:")
            for i, video in enumerate(results, start=1):
                embed.add_field(name=f"{i}. {video['title']}", value=f"Duration: {video['duration']}", inline=False)

            view = SongSelect(results, interaction.user)
            message = await interaction.followup.send(embed=embed, view=view)

            # Wait for button selection
            await view.wait()

            if view.selected_song:
                song_url = f"https://youtube.com{view.selected_song['url_suffix']}"
                song = Song(song_url, title=view.selected_song['title'])
                await self.add_to_front_of_queue(player, song)
                await message.edit(content=f"Added to play next: {view.selected_song['title']}", embed=None, view=None)
            else:
                await message.edit(content="Song selection timed out.", embed=None, view=None)

    async def add_to_front_of_queue(self, player, song):
        # Get all items from the queue
        items = []
        while not player.queue.empty():
            items.append(await player.queue.get())

        # Add the new song at the front
        await player.queue.put(song)

        # Put all other items back in the queue
        for item in items:
            await player.queue.put(item)
    @app_commands.command(name="pause", description="Pause the current song")
    async def pause(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message("Paused ⏸️")
        else:
            await interaction.response.send_message("Nothing is playing.")

    @app_commands.command(name="resume", description="Resume the paused song")
    async def resume(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message("Resumed ▶️")
        else:
            await interaction.response.send_message("The audio is not paused.")

    @app_commands.command(name="skip", description="Skip the current song")
    async def skip(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
            await interaction.response.send_message("Skipped ⏭️")
        else:
            await interaction.response.send_message("Nothing is playing.")

    @app_commands.command(name="queue", description="Show the current queue")
    async def queue(self, interaction: discord.Interaction):
        player = self.get_player(interaction)
        if player.queue.empty():
            return await interaction.response.send_message('There are currently no more queued songs.')

        upcoming = list(player.queue._queue)
        fmt = '\n'.join(f'`{i+1}.` **{song.title}**' for i, song in enumerate(upcoming))
        embed = discord.Embed(title=f'Upcoming - Next {len(upcoming)}', description=fmt)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="delete", description="Delete a song from the queue")
    @app_commands.describe(number="The number of the song to delete")
    async def delete(self, interaction: discord.Interaction, number: int):
        player = self.get_player(interaction)
        
        # Convert queue to a list
        queue_list = list(player.queue._queue)
        queue_size = len(queue_list)

        if queue_size == 0:
            return await interaction.response.send_message('The queue is empty.')
        
        if number < 1 or number > queue_size:
            return await interaction.response.send_message(f'Please provide a valid number between 1 and {queue_size}.')
        
        removed_song = queue_list.pop(number - 1)
        
        # Clear the queue and add back the songs
        player.queue._queue.clear()
        for song in queue_list:
            await player.queue.put(song)
        
        await interaction.response.send_message(f'Removed song: **{removed_song.title}**')
    @app_commands.command(name="now_playing", description="Show the currently playing song")
    async def now_playing(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            await interaction.response.send_message(f'Now playing: {vc.source.title}')
        else:
            await interaction.response.send_message('Nothing is currently playing.')

    @app_commands.command(name="volume", description="Adjust the volume of the music")
    async def volume(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc:
            return await interaction.response.send_message("I'm not currently in a voice channel.")

        player = self.get_player(interaction)
        
        # Check if audio is currently playing
        if not vc.is_playing():
            return await interaction.response.send_message("No audio is currently playing.")

        current_volume = int(player.volume * 100)

        view = VolumeControl(current_volume)
        await interaction.response.send_message(f"Current volume: {current_volume}%\nUse the dropdown to adjust:", view=view)

        await view.wait()

        if view.value is not None:
            player.volume = view.value / 100
            if vc.source:
                vc.source.volume = player.volume
            await interaction.edit_original_response(content=f"Volume set to {view.value}%", view=None)
        else:
            await interaction.edit_original_response(content="Volume adjustment timed out.", view=None)
    def get_player(self, interaction: discord.Interaction):
        try:
            player = self.players[interaction.guild_id]
        except KeyError:
            player = MusicPlayer(interaction)
            self.players[interaction.guild_id] = player
        return player

    @app_commands.command(name="clear_queue", description="Clear the current queue")
    async def clear_queue(self, interaction: discord.Interaction):
        player = self.get_player(interaction)
        if player.queue.empty():
            await interaction.response.send_message("The queue is already empty.")
        else:
            player.queue._queue.clear()
            await interaction.response.send_message("The queue has been cleared.")

    @app_commands.command(name="stop", description="Stop playing and clear the queue")
    async def stop(self, interaction: discord.Interaction):
        player = self.get_player(interaction)
        player.queue._queue.clear()
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.disconnect()
        del self.players[interaction.guild_id]
        await interaction.response.send_message("Stopped, cleared the queue, and disconnected.")

# Setup logging
logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')
    await bot.add_cog(Music(bot))
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"An error occurred while syncing commands: {e}")
# Load the token from the environment variable
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
if not TOKEN:
    raise ValueError("No token found. Please set the DISCORD_BOT_TOKEN environment variable.")

bot.run(TOKEN)