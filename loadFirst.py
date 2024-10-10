import asyncio
import discord
from discord.ext import commands, tasks
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
from functools import partial
# Load environment variables use python 3.10 please 
load_dotenv()

ydl_opts = {
    'format': 'bestaudio/best',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
    'outtmpl': 'downloads/%(title)s.%(ext)s',
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
    'executable': r'C:\ffmpeg\bin\ffmpeg.exe'
}

ytdl = yt_dlp.YoutubeDL(ydl_opts)

# Create a ThreadPoolExecutor
thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        
        if 'entries' in data:
            data = data['entries'][0]

        filename = ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

class Song:
    def __init__(self, url, title=None):
        self.url = url
        self.title = title

class MusicPlayer:
    def __init__(self, ctx):
        self.bot = ctx.bot
        self._guild = ctx.guild
        self._channel = ctx.channel
        self._cog = ctx.cog

        self.queue = asyncio.Queue()
        self.next = asyncio.Event()

        self.np = None
        self.volume = .5
        self.current = None

        ctx.bot.loop.create_task(self.player_loop())

    async def player_loop(self):
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            self.next.clear()

            try:
                async with timeout(300):
                    source = await self.queue.get()
            except asyncio.TimeoutError:
                return self.destroy(self._guild)

            if not isinstance(source, YTDLSource):
                try:
                    source = await YTDLSource.from_url(source, loop=self.bot.loop, stream=False)
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
                await self._channel.send(f'An error occurred while playing the song: {str(e)}')
            finally:
                if source:
                    source.cleanup()
                self.current = None

                try:
                    await self.np.delete()
                except discord.HTTPException:
                    pass
    def play_next_song(self, error=None):
        if error:
            print(f"An error occurred: {error}")  # You might want to log this or send to a logging channel
        self.next.set()

    def destroy(self, guild):
        return self.bot.loop.create_task(self._cog.cleanup(guild))

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.players = {}
    @commands.command()
    async def search(self, ctx, *, query: str):
        # Cancel any existing search task for this user
        if ctx.author.id in self.search_tasks:
            self.search_tasks[ctx.author.id].cancel()

        try:
            results = await self.bot.loop.run_in_executor(thread_pool, YoutubeSearch, query, 5)
            results = results.to_dict()
            if not results:
                return await ctx.send('No videos found.')

            embed = discord.Embed(title="Search Results", description="Choose a song by number:")
            for i, video in enumerate(results, start=1):
                embed.add_field(name=f"{i}. {video['title']}", value=f"Duration: {video['duration']}", inline=False)

            message = await ctx.send(embed=embed)

            def check(m):
                return m.author == ctx.author and m.channel == ctx.channel and m.content.isdigit() and 1 <= int(m.content) <= 5

            try:
                # Create a task for waiting for the response
                wait_task = asyncio.create_task(self.bot.wait_for('message', check=check, timeout=60.0))
                self.search_tasks[ctx.author.id] = wait_task
                response = await wait_task

                selected = results[int(response.content) - 1]
                url = f"https://youtube.com{selected['url_suffix']}"

                await ctx.invoke(self.play, url=url)

            except asyncio.TimeoutError:
                await message.delete()
                await ctx.send('Search timed out after 1 minute.')
            except asyncio.CancelledError:
                await message.delete()
                await ctx.send('Last search cancelled due to a new search request.')
            finally:
                # Remove the task from the dictionary
                self.search_tasks.pop(ctx.author.id, None)

        except Exception as e:
            await ctx.send(f'An error occurred: {str(e)}')
            print(f"Search error details: {e}")
    
    async def cleanup(self, guild):
        try:
            await guild.voice_client.disconnect()
        except AttributeError:
            pass

        try:
            del self.players[guild.id]
        except KeyError:
            pass

    def get_player(self, ctx):
        try:
            player = self.players[ctx.guild.id]
        except KeyError:
            player = MusicPlayer(ctx)
            self.players[ctx.guild.id] = player

        return player

    @commands.command()
    async def join(self, ctx):
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()
        else:
            await ctx.send("You are not connected to a voice channel.")

    @commands.command()
    async def play(self, ctx, *, url):
        if not ctx.voice_client:
            await ctx.invoke(self.join)

        player = self.get_player(ctx)

        if 'list=' in url:
            await self.process_playlist(ctx, url, player)
        else:
            async with ctx.typing():
                try:
                    source = await YTDLSource.from_url(url, loop=self.bot.loop, stream=False)
                    await player.queue.put(source)
                    await ctx.send(f'Added to queue: {source.title}')
                except Exception as e:
                    await ctx.send(f'An error occurred while processing your song: {str(e)}')
    async def process_playlist(self, ctx, url, player):
        await ctx.send("Processing playlist. This may take a moment...")
        try:
            ydl_opts_playlist = {
                'extract_flat': 'in_playlist',
                'skip_download': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts_playlist) as ydl:
                result = await self.bot.loop.run_in_executor(thread_pool, ydl.extract_info, url, False)

            if 'entries' not in result:
                await ctx.send('Error: Could not find playlist entries.')
                return

            for entry in result['entries'][:10]:
                video_url = f"https://www.youtube.com/watch?v={entry['id']}"
                source = await YTDLSource.from_url(video_url, loop=self.bot.loop, stream=False)
                await player.queue.put(source)
            
            await ctx.send(f"Added {min(10, len(result['entries']))} songs from the playlist to the queue.")
            
            if len(result['entries']) > 10:
                await ctx.send("Note: Only the first 10 songs from the playlist were added to avoid overloading.")
        except Exception as e:
            await ctx.send(f'An error occurred while processing the playlist: {str(e)}')
            print(f"Playlist error details: {e}")

    @commands.command()
    async def pause(self, ctx):
        vc = ctx.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await ctx.send("Paused ⏸️")
        else:
            await ctx.send("Nothing is playing.")

    @commands.command()
    async def resume(self, ctx):
        vc = ctx.voice_client
        if vc and vc.is_paused():
            vc.resume()
            await ctx.send("Resumed ▶️")
        else:
            await ctx.send("The audio is not paused.")

    @commands.command()
    async def skip(self, ctx):
        vc = ctx.voice_client
        if vc and vc.is_playing():
            vc.stop()
            await ctx.send("Skipped ⏭️")
        else:
            await ctx.send("Nothing is playing.")

    @commands.command()
    async def queue(self, ctx):
        player = self.get_player(ctx)
        if player.queue.empty():
            return await ctx.send('There are currently no more queued songs.')

        upcoming = list(player.queue._queue)
        fmt = '\n'.join(f'`{i+1}.` {song}' for i, song in enumerate(upcoming))
        embed = discord.Embed(title=f'Upcoming - Next {len(upcoming)}', description=fmt)
        await ctx.send(embed=embed)

    @commands.command()
    async def now_playing(self, ctx):
        player = self.get_player(ctx)
        if player.current:
            await ctx.send(f'Now playing: {player.current.title}')
        else:
            await ctx.send('Nothing is currently playing.')

    @commands.command()
    async def volume(self, ctx, volume: int):
        vc = ctx.voice_client
        if vc:
            if 0 <= volume <= 100:
                player = self.get_player(ctx)
                player.volume = volume / 100
                vc.source.volume = volume / 100
                await ctx.send(f"Changed volume to {volume}%")
            else:
                await ctx.send("Please use a value between 0 and 100")
        else:
            await ctx.send("Not connected to a voice channel.")

    @commands.command()
    async def stop(self, ctx):
        vc = ctx.voice_client
        if vc:
            await self.cleanup(ctx.guild)
            await ctx.send("Stopped and disconnected.")
        else:
            await ctx.send("Not connected to a voice channel.")

    @commands.command()
    async def delete(self, ctx, number: int = None):
        if number is None:
            return await ctx.send('Please provide a number to delete a song from the queue. Usage: `!delete <number>`')

        player = self.get_player(ctx)
        if player.queue.empty():
            return await ctx.send('The queue is empty.')
        
        if number < 1 or number > player.queue.qsize():
            return await ctx.send(f'Please provide a valid number between 1 and {player.queue.qsize()}.')
        
        # Convert queue to a list, remove the item, and recreate the queue
        queue_list = list(player.queue._queue)
        removed_song = queue_list.pop(number - 1)
        player.queue._queue.clear()
        for song in queue_list:
            player.queue.put_nowait(song)
        
        await ctx.send(f'Removed song: **{removed_song}**')

    @commands.command()
    async def clear_queue(self, ctx):
        player = self.get_player(ctx)
        if player.queue.empty():
            await ctx.send("The queue is already empty.")
        else:
            # Clear the queue
            player.queue._queue.clear()
            await ctx.send("The queue has been cleared.")




intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')
    await bot.add_cog(Music(bot))

# Load the token from the environment variable
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
if not TOKEN:
    raise ValueError("No token found. Please set the DISCORD_BOT_TOKEN environment variable.")

bot.run(TOKEN)